"""Optional CPU result shallowing before result archiving.

SlurmClient owns remote execution, image acquisition and scheduling policy;
SlurmJob owns monitoring. This module coordinates the shallower's manifest,
durable submission records, recovery command and receipt validation.
"""

import hashlib
import io
import json
import logging
import posixpath
import re
import shlex
from typing import Any, Callable, Dict, Optional, Tuple
from uuid import UUID, uuid4

from invoke.exceptions import UnexpectedExit
from packaging.version import InvalidVersion, Version

from .slurm_client import SlurmClient, SlurmJob
from . import ssh_recovery

TASK_NAME = "_SLURM_Remote_Shallower"
CAPABILITY_SCHEMA = 1
REQUIRED_RUNTIME_CONTRACT = 1
REQUIRED_MANIFEST_SCHEMA = 2
CAPABILITY_LABELS = {
    "schema": "org.biomeroproject.shallower.capability-schema",
    "contracts": "org.biomeroproject.shallower.runtime-contracts",
    "manifest_schemas": "org.biomeroproject.shallower.manifest-schemas",
    "migrations": "org.biomeroproject.shallower.migrations",
}
logger = logging.getLogger(__name__)


def image_spec(slurm_client: SlurmClient, *,
               image: Optional[str] = None) -> Dict[str, str]:
    """Describe the configured helper image for SlurmClient image acquisition.

    Args:
        slurm_client (SlurmClient): The Slurm client holding the configuration.
        image (Optional[str]): Image reference to describe instead of the
            configured ``remote_shallower_image``.

    Returns:
        Dict[str, str]: The image specification for image acquisition.

    Raises:
        ValueError: If no image is configured, or its digest or version is
            missing or invalid.
    """
    image = image if image is not None else slurm_client.remote_shallower_image
    if not image:
        raise ValueError("Configure remote_shallower_image in [SLURM] before "
                         "initializing remote shallowing")
    image = image.removeprefix("docker://")
    if "@sha256:" in image:
        source, digest = image.split("@sha256:", 1)
        if not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("Invalid image digest")
        version = "sha256:" + digest
    else:
        source, separator, version = image.rpartition(":")
        if not separator or "/" in version or not version:
            raise ValueError("Result shallower requires an explicit image version")
    spec = {
        "kind": "result-shallower", "name": "biomero-shallower",
        "version": version, "source_type": "registry", "source": source,
        "destination": posixpath.join(
            slurm_client.slurm_converters_path,
            "shallower-" + hashlib.sha256(image.encode()).hexdigest()[:24]
            + ".sif"),
    }
    logger.debug("Remote shallower image spec: source=%s version=%s "
                 "destination=%s", source, version, spec["destination"])
    return spec


def _installed_labels(slurm_client: SlurmClient,
                      sif_path: str) -> Dict[str, Any]:
    """Read OCI labels without executing the helper payload.

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        sif_path (str): Remote path of the installed helper image.

    Returns:
        Dict[str, Any]: The OCI labels of the image.

    Raises:
        RuntimeError: If the image cannot be inspected.
        ValueError: If the image has no usable labels.
    """
    result = slurm_client.run_commands([
        "runtime=$(command -v apptainer || command -v singularity); "
        'test -n "$runtime" && "$runtime" inspect --json --labels '
        + shlex.quote(sif_path)])
    if not result.ok:
        raise RuntimeError(
            f"Cannot inspect installed shallower metadata of {sif_path}")
    try:
        labels = json.loads(result.stdout)["data"]["attributes"]["labels"]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(
            "Installed shallower has no usable OCI labels"
        ) from error
    if not isinstance(labels, dict):
        raise ValueError("Installed shallower has no usable OCI labels")
    return labels


def _version_from_labels(labels: Dict[str, Any]) -> str:
    """Return the OCI version label of the installed helper.

    Args:
        labels (Dict[str, Any]): The OCI labels of the image.

    Returns:
        str: The version from the label.

    Raises:
        ValueError: If the label is missing or not a valid version.
    """
    try:
        version = labels["org.opencontainers.image.version"]
        if not isinstance(version, str) or not version.strip():
            raise ValueError("Empty version")
        Version(version.strip())
    except (InvalidVersion, KeyError, TypeError, ValueError) as error:
        raise ValueError("Installed helper has no usable OCI version label; "
                         "set remote_shallower_version explicitly") from error
    return version.strip()


def installed_tool_version(slurm_client: SlurmClient, sif_path: str) -> str:
    """Read the installed helper's version without executing its payload.

    A floating image tag is not a tool version. Persist the OCI version label
    before submission so receipt validation and recovery use a concrete value.

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        sif_path (str): Remote path of the installed helper image.

    Returns:
        str: The installed tool version.
    """
    return _version_from_labels(_installed_labels(slurm_client, sif_path))


def _csv_capability(labels: Dict[str, Any], name: str, *,
                    integers: bool = False) -> Tuple[Any, ...]:
    """Parse a comma-separated capability label.

    Args:
        labels (Dict[str, Any]): The OCI labels of the image.
        name (str): Key in ``CAPABILITY_LABELS`` of the label to read.
        integers (bool): Convert the entries to integers (default False).

    Returns:
        Tuple[Any, ...]: The declared entries.

    Raises:
        ValueError: If the label is missing, empty or not valid.
    """
    label = CAPABILITY_LABELS[name]
    value = labels.get(label)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Installed shallower does not declare {label}; install a "
            "compatible helper and run SLURM Init again")
    entries = tuple(part.strip() for part in value.split(",") if part.strip())
    if not entries:
        raise ValueError(f"Installed shallower declares an empty {label}")
    if not integers:
        return entries
    try:
        return tuple(int(part) for part in entries)
    except ValueError as error:
        raise ValueError(f"Installed shallower has invalid {label}") from error


def validate_installed_tool(slurm_client: SlurmClient, sif_path: str, *,
                            expected_version: Optional[str] = None) -> str:
    """Fail closed unless the installed SIF satisfies this runtime contract.

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        sif_path (str): Remote path of the installed helper image.
        expected_version (Optional[str]): Version the image must have; defaults
            to the configured ``remote_shallower_version``.

    Returns:
        str: The installed tool version.

    Raises:
        ValueError: If the labels or the configured version are invalid.
        RuntimeError: If the version, capability schema, runtime contract or
            manifest schema is not supported.
    """
    labels = _installed_labels(slurm_client, sif_path)
    actual_version = _version_from_labels(labels)
    configured_version = (
        expected_version
        if expected_version is not None
        else slurm_client.remote_shallower_version
    )
    if configured_version:
        try:
            matches = Version(actual_version) == Version(configured_version)
        except InvalidVersion as error:
            raise ValueError(
                f"Invalid configured remote_shallower_version: "
                f"{configured_version}"
            ) from error
        if not matches:
            raise RuntimeError(
                f"Installed remote Shallower {actual_version} does not match "
                f"configured version {configured_version}; run SLURM Init "
                "and verify the selected helper image")
    if labels.get(CAPABILITY_LABELS["schema"]) != str(CAPABILITY_SCHEMA):
        raise RuntimeError(
            "Installed remote Shallower has missing or unsupported capability "
            "metadata; update the helper image and run SLURM Init")
    contracts = _csv_capability(labels, "contracts", integers=True)
    manifest_schemas = _csv_capability(
        labels, "manifest_schemas", integers=True
    )
    if REQUIRED_RUNTIME_CONTRACT not in contracts:
        raise RuntimeError(
            f"Installed remote Shallower {actual_version} does not support "
            f"runtime contract {REQUIRED_RUNTIME_CONTRACT}")
    if REQUIRED_MANIFEST_SCHEMA not in manifest_schemas:
        raise RuntimeError(
            f"Installed remote Shallower {actual_version} does not emit "
            f"shallow manifest schema {REQUIRED_MANIFEST_SCHEMA}")
    logger.debug("Installed remote shallower %s at %s satisfies runtime "
                 "contract %s and manifest schema %s (configured version: %s)",
                 actual_version, sif_path, REQUIRED_RUNTIME_CONTRACT,
                 REQUIRED_MANIFEST_SCHEMA, configured_version or "any")
    return actual_version


def _job_name(task_id: Any, *, recovery: bool = False) -> str:
    """Keep submission reconciliation specific to one task and operation.

    Args:
        task_id (Any): ID of the helper task.
        recovery (bool): Name the recovery job instead of the shallowing job
            (default False).

    Returns:
        str: The Slurm job name.
    """
    operation = "recovery" if recovery else "shallower"
    return f"biomero-{operation}-{task_id}"


def build_command(slurm_client: SlurmClient, output: str, sif_path: str,
                  manifest: str, task_id: str, *, recovery: bool = False,
                  image: Optional[str] = None) -> str:
    """Build shallowing or recovery using the same helper resource policy.

    Paths refer to the remote filesystem. The result directory is writable in
    the container; the canonical input manifest is mounted read-only.

    Args:
        slurm_client (SlurmClient): The Slurm client holding the configuration.
        output (str): Result directory to shallow.
        sif_path (str): Path of the helper image.
        manifest (str): Path of the canonical input manifest.
        task_id (str): ID of the helper task.
        recovery (bool): Build the recovery command instead of the shallowing
            command (default False).
        image (Optional[str]): Image reference recorded for the task; defaults
            to the configured ``remote_shallower_image``.

    Returns:
        str: The ``sbatch`` command.

    Raises:
        ValueError: If the configured worker count is not positive.
    """
    quote = shlex.quote
    image = image if image is not None else slurm_client.remote_shallower_image
    if (isinstance(slurm_client.remote_shallower_workers, bool)
            or not isinstance(slurm_client.remote_shallower_workers, int)
            or slurm_client.remote_shallower_workers < 1):
        raise ValueError("Result shallower worker count must be positive")
    params = slurm_client.get_shallower_job_params()
    runtime = ("runtime=$(command -v apptainer || command -v singularity); "
               'test -n "$runtime"; exec "$runtime" exec --containall --cleanenv '
               '--env SLURM_JOB_ID="$SLURM_JOB_ID" '
               f'--bind {quote(output + ":/results:rw")} '
               f'--bind {quote(manifest + ":/canonical.json:ro")} '
               f"{quote(sif_path)} biomero-shallower normalize-tree "
               "--returned-zarr /results --canonical-inputs /canonical.json "
               "--contract-version 1 --failure-policy keep-full "
               "--report /results/.biomero-shallow-batch.json "
               f"--identity-workers {slurm_client.remote_shallower_workers} "
               f"--image {quote(image)} "
               f"--task-id {quote(task_id)}")
    if recovery:
        runtime += " --recover-only"
    logger.debug("Built remote shallower %s command for task %s: image=%s "
                 "workers=%s sbatch params=%s",
                 "recovery" if recovery else "shallowing", task_id, image,
                 slurm_client.remote_shallower_workers, params)
    return ("sbatch --parsable --export=NONE " + " ".join(params) +
            f" --job-name={quote(_job_name(task_id, recovery=recovery))}"
            f' --output={quote(manifest + ".%j.log")}' +
            " --wrap=" + quote(runtime))


def _batch(slurm_client: SlurmClient, raw: str, canonical: Any, *,
           task: Any = None) -> Any:
    """Parse a batch report and check its input, image and tool version.

    When a task is supplied, its recorded image/version and submission own the
    report; current deployment defaults must not invalidate historical results.
    Import the optional schema dependency only when shallowing is used.

    Args:
        slurm_client (SlurmClient): The Slurm client holding the configuration.
        raw (str): The JSON batch report.
        canonical (Any): The canonical input manifest the report must match.
        task (Any): The recorded helper task owning the report, if any.

    Returns:
        Any: The parsed batch report.

    Raises:
        ValueError: If the report does not match the configuration, input,
            task or job.
    """
    from biomero_schema.shallower import ShallowBatchReport
    batch = ShallowBatchReport.from_dict(json.loads(raw))
    image = task.params["image"] if task else slurm_client.remote_shallower_image
    version = task.task_version if task else slurm_client.remote_shallower_version
    if (batch.canonical_inputs != canonical
            or batch.image != image or batch.tool_version != version):
        raise ValueError(
            "Result shallower report does not match configuration/input")
    if any(receipt.image != batch.image
           or receipt.tool_version != batch.tool_version
           for receipt in batch.receipts):
        raise ValueError("Inconsistent helper receipts")
    if task is not None:
        _check_canonical(task, canonical)
        if any(receipt.task_id != task.id or not task.job_ids
               or int(receipt.slurm_job_id) != int(task.job_ids[0])
               for receipt in batch.receipts):
            raise ValueError("Report belongs to a different helper task/job")
    return batch


def _canonical_digest(payload: Any) -> str:
    """Fingerprint JSON content independently of key order and whitespace."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _check_canonical(task: Any, canonical: Any) -> None:
    """Raise ValueError if the manifest differs from the one on the task."""
    expected = task.params.get("canonical_sha256")
    if expected and expected != _canonical_digest(canonical.to_dict()):
        raise ValueError("Canonical manifest differs from the recorded task")


def _prepare_manifest(slurm_client: SlurmClient, manifest: str,
                      canonical: Any, *, submitted: bool) -> None:
    """Verify an existing manifest or publish a new one without overwriting.

    A sibling temporary file is fully uploaded before an atomic hard-link
    publishes it under a lock. Readers never see a partly uploaded manifest.
    Existing content is verified, not replaced on resume.

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        manifest (str): Remote path of the canonical input manifest.
        canonical (Any): The canonical input manifest to publish or verify.
        submitted (bool): Whether the helper job was already submitted, which
            makes a missing manifest an error.

    Raises:
        RuntimeError: If the manifest cannot be read or published, or is
            missing for a submitted job.
        ValueError: If an existing manifest differs from the workflow input.
    """
    quote = shlex.quote
    target = quote(manifest)
    payload = canonical.to_dict()

    def verify(raw: str) -> None:
        if _canonical_digest(json.loads(raw)) != _canonical_digest(payload):
            raise ValueError("Remote canonical manifest differs from workflow input")

    found = slurm_client.run_commands([
        f"if test -f {target} && ! test -L {target}; then cat {target}; "
        f"elif test -e {target} || test -L {target}; then exit 1; fi"],
        log_stdout=False)
    if not found.ok:
        raise RuntimeError("Cannot read canonical manifest; output preserved")
    if found.stdout.strip():
        verify(found.stdout)
        logger.debug("Existing canonical manifest %s verified; not "
                     "uploading again", manifest)
        return
    if submitted:
        raise RuntimeError("Missing canonical manifest for submitted job; output preserved")

    temporary = manifest + "." + uuid4().hex + ".tmp"
    state_dir = posixpath.dirname(manifest)
    guards = " || ".join(
        "test -e " + quote(posixpath.join(state_dir, operation + suffix))
        for operation in ("shallow", "recovery")
        for suffix in (".intent", ".job"))
    script = (
        f"set -eu; if test -L {target}; then exit 1; fi; "
        f"if test -e {target}; then cat {target}; "
        f"elif {guards}; then "
        'echo "Missing manifest after submission intent" >&2; exit 1; '
        f"else ln {quote(temporary)} {target}; cat {target}; fi")
    try:
        slurm_client.put(io.StringIO(json.dumps(payload)), temporary)
        published = slurm_client.run_commands([
            f'flock -w 30 {quote(manifest + ".lock")} sh -c {quote(script)}'],
            log_stdout=False)
        if not published.ok:
            raise RuntimeError("Cannot publish canonical manifest; output preserved")
        verify(published.stdout)
        logger.info("Published canonical manifest %s for remote shallowing",
                    manifest)
    finally:
        # Only the unique staging file owned by this call is removed.
        try:
            cleanup = slurm_client.run_commands(["rm -f -- " + quote(temporary)])
            if not cleanup.ok:
                logger.warning("Could not remove manifest staging file %s", temporary)
        except Exception:
            logger.warning("Could not remove manifest staging file %s",
                           temporary, exc_info=True)


def completed_receipts(slurm_client: SlurmClient, workflow_id: Any,
                       canonical: Any) -> Tuple[Any, ...]:
    """Read persisted helper receipts without contacting the cluster.

    Args:
        slurm_client (SlurmClient): The Slurm client with the workflow tracker.
        workflow_id (Any): ID of the workflow.
        canonical (Any): The canonical input manifest the receipts must match.

    Returns:
        Tuple[Any, ...]: The receipts of completed helper tasks; empty when
            remote shallowing or workflow tracking is disabled.
    """
    if not slurm_client.remote_shallow_zarr or not slurm_client.track_workflows:
        logger.debug("No remote shallower receipts for workflow %s: "
                     "remote_shallow_zarr=%s track_workflows=%s", workflow_id,
                     slurm_client.remote_shallow_zarr, slurm_client.track_workflows)
        return ()
    workflow = slurm_client.workflowTracker.repository.get(UUID(str(workflow_id)))
    receipts = []
    for task_id in workflow.tasks:
        task = slurm_client.workflowTracker.repository.get(task_id)
        if task.task_name == TASK_NAME and task.result_message:
            receipts.extend(_batch(slurm_client, task.result_message, canonical,
                                   task=task).receipts)
    logger.debug("Read %d remote shallower receipts for workflow %s",
                 len(receipts), workflow_id)
    return tuple(receipts)


def _submit_once(slurm_client: SlurmClient, command: str, state: str, *,
                 job_name: str) -> Optional[int]:
    """Submit or adopt a job under a remote filesystem lock.

    Ambiguous submission state raises instead of risking a duplicate job.

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        command (str): The ``sbatch`` command to run.
        state (str): Remote filename prefix for the lock, submission intent
            and recorded job ID.
        job_name (str): Operation-specific identity used for reconciliation,
            not a value inferred by parsing shell commands.

    Returns:
        Optional[int]: The Slurm job ID, or None for a rejected submission.

    Raises:
        UnexpectedExit: If the submission command lost its SSH connection.
        ssh_recovery.RecoveryPending: If another process holds the submission.
        RuntimeError: If the submission state cannot be resolved.
    """
    # Reuse the same reconciliation and receipt publication as analysis jobs.
    # Keep the helper's existing rejected-submission fallback to full results.
    quote = shlex.quote
    recovering = ssh_recovery.enabled(slurm_client)
    logger.info("Submitting remote shallower job %s (state %s, "
                "SSH recovery %s)", job_name, state,
                "enabled" if recovering else "disabled")
    find_job = ssh_recovery.reconcile_submission(state, job_name)
    publish = ssh_recovery.publish_job_id(state)
    script = (
        f'if test -s {quote(state + ".job")}; then cat {quote(state + ".job")}; '
        f'elif test -e {quote(state + ".intent")}; then '
        f'job=$({find_job}) || exit 1; {publish}printf "%s\\n" "$job"; '
        f'else date +%Y-%m-%dT%H:%M:%S > {quote(state + ".intent")}; '
        f'job=$({command}) || {{ rm -f {quote(state + ".intent")}; '
        'printf "NOT_SUBMITTED\\n"; exit 0; }; '
        "job=${job%%;*}; "
        f"{publish}"
        'printf "%s\\n" "$job"; fi'
    )
    result = slurm_client.run_commands([
        ssh_recovery.locked_command(
            state + ".lock", script, wait=5 if recovering else 30)],
        **({"warn": True} if recovering else {}))
    if recovering and result.exited in (-1, 200):
        if result.exited == -1:
            raise UnexpectedExit(result)
        logger.info("Shallower submission %s is held by another process; "
                    "waiting", job_name)
        raise ssh_recovery.RecoveryPending(
            "Waiting for the existing shallower submission")
    if result.ok and result.stdout.strip() == "NOT_SUBMITTED":
        logger.warning("sbatch rejected remote shallower job %s", job_name)
        return None
    if not result.ok or not result.stdout.strip().isdigit():
        # ``exited`` is only guaranteed on Fabric results; tolerate others.
        raise RuntimeError(
            f"Shallower submission {job_name} is unresolved (exit "
            f'{getattr(result, "exited", None)}); preserve results and resume '
            "after reconciliation")
    logger.info("Remote shallower job %s is Slurm job %s", job_name,
                result.stdout.strip())
    return int(result.stdout.strip())


def _wait(slurm_client: SlurmClient, job_id: int,
          heartbeat: Optional[Callable[[], None]] = None) -> str:
    """Monitor an adopted job without analysis-log or task-status updates.

    The helper task is completed by ``run`` after report validation, not merely
    because its Slurm process exited successfully.

    Args:
        slurm_client (SlurmClient): The Slurm client used to poll the job.
        job_id (int): The Slurm job ID.
        heartbeat (Optional[Callable[[], None]]): Callback invoked before each
            poll.

    Returns:
        str: The final state of the Slurm job.
    """
    logger.info("Waiting for remote shallower Slurm job %s", job_id)
    job = SlurmJob.from_job_id(job_id, slurm_polling_interval=15)
    state = job.wait_for_completion(
        slurm_client, heartbeat=heartbeat, track_progress=False, update_task=False,
        strict_status=True)
    outcome = "COMPLETED" if job.completed() else state
    logger.info("Remote shallower Slurm job %s finished with state %s",
                job_id, outcome)
    return outcome


def run(slurm_client: SlurmClient, data_path: str, workflow_id: Any,
        canonical: Any,
        heartbeat: Optional[Callable[[], None]] = None) -> Optional[Any]:
    """Run or resume shallowing and return the validated batch report.

    Only initialization acquires container images. A rejected shallowing
    submission returns an empty-receipt batch so the caller can retain full
    results. Callers must not archive potentially incomplete output.

    Args:
        slurm_client (SlurmClient): The Slurm client with the workflow tracker.
        data_path (str): Remote data folder of the workflow results.
        workflow_id (Any): ID of the workflow.
        canonical (Any): The canonical input manifest of the workflow.
        heartbeat (Optional[Callable[[], None]]): Callback invoked during job
            waits.

    Returns:
        Optional[Any]: The validated batch report, or None when shallowing is
            disabled or inapplicable.

    Raises:
        RuntimeError: If the image is missing, the submission or recovery is
            unresolved or failed, or the report is missing.
        ValueError: If the installed tool or the report is invalid.
    """
    if not slurm_client.remote_shallow_zarr:
        logger.debug("Remote shallowing skipped for workflow %s: "
                     "remote_shallow_zarr is disabled", workflow_id)
        return None
    if canonical is None or not canonical.inputs:
        logger.debug("Remote shallowing skipped for workflow %s: no "
                     "canonical inputs", workflow_id)
        return None
    if not slurm_client.track_workflows:
        logger.warning("Remote shallowing skipped for workflow %s: "
                       "track_workflows is disabled; results stay full",
                       workflow_id)
        return None
    tracker = slurm_client.workflowTracker
    workflow_id = UUID(str(workflow_id))
    workflow = tracker.repository.get(workflow_id)
    tasks = [tracker.repository.get(task_id) for task_id in workflow.tasks]
    matches = [task for task in tasks
               if task.task_name == TASK_NAME and task.input_data == data_path]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous result shallower tasks")
    if matches:
        _check_canonical(matches[0], canonical)
    if matches and matches[0].result_message:
        logger.info("Remote shallower task %s already completed; reusing its "
                    "receipts", matches[0].id)
        return _batch(slurm_client, matches[0].result_message, canonical,
                      task=matches[0])
    if matches and matches[0].job_ids:
        report_path = posixpath.join(
            data_path, "data/out/.biomero-shallow-batch.json")
        found = slurm_client.run_commands([
            f"if test -f {shlex.quote(report_path)}; "
            f"then cat {shlex.quote(report_path)}; fi"])
        if found.ok and found.stdout.strip():
            try:
                batch = _batch(slurm_client, found.stdout, canonical, task=matches[0])
            except (ValueError, KeyError):
                # Partial/stale report: poll the recorded job below.
                logger.debug("Existing shallower report %s is partial or "
                             "stale; polling recorded job", report_path,
                             exc_info=True)
            else:
                logger.info("Adopted existing shallower report %s for task "
                            "%s; completing task", report_path, matches[0].id)
                tracker.complete_task(
                    matches[0].id, json.dumps(batch.to_dict()))
                return batch
    image = matches[0].params["image"] if matches else slurm_client.remote_shallower_image
    spec = image_spec(slurm_client, image=image)
    if matches:
        spec["destination"] = matches[0].params.get("sif", spec["destination"])
    ready, pending = slurm_client._partition_existing_images([spec])
    if pending or not ready:
        raise RuntimeError(
            f'Remote shallower image missing or invalid: {spec["destination"]}. '
            "Run SLURM_Init_environment and verify image setup with "
            "SLURM_check_setup before retrying.")
    expected_version = (
        matches[0].task_version if matches else slurm_client.remote_shallower_version
    )
    installed_version = validate_installed_tool(
        slurm_client,
        spec["destination"],
        expected_version=expected_version,
    )
    if not matches:
        version = installed_version
        task_id = tracker.add_task_to_workflow(
            workflow_id, TASK_NAME, version, data_path,
            {"image": image, "contract": 1, "sif": spec["destination"],
             "canonical_sha256": _canonical_digest(canonical.to_dict())})
        tracker.start_task(task_id)
        task = tracker.repository.get(task_id)
        logger.info("Started remote shallower task %s (workflow %s, image %s, "
                    "tool version %s, %d inputs)", task_id, workflow_id, image,
                    version, len(canonical.inputs))
    else:
        task = matches[0]
        task_id = task.id
        logger.info("Resuming remote shallower task %s (jobs recorded: %s)",
                    task_id, list(task.job_ids))
    sif_path = task.params.get("sif", spec["destination"])
    state_dir = posixpath.join(data_path, ".biomero-shallower", str(task_id))
    prepared = slurm_client.run_commands(["mkdir -p " + shlex.quote(state_dir)])
    if not prepared.ok:
        raise RuntimeError(
            f"Cannot prepare shallower state directory {state_dir}")
    manifest = posixpath.join(state_dir, "canonical.json")
    _prepare_manifest(slurm_client, manifest, canonical, submitted=bool(task.job_ids))
    output = posixpath.join(data_path, "data/out")
    command = build_command(
        slurm_client, output, sif_path, manifest, str(task_id), image=image)
    if task.job_ids:
        logger.debug("Adopting recorded shallower job %s; no new submission",
                     task.job_ids[0])
    job_id = (int(task.job_ids[0]) if task.job_ids
              else _submit_once(slurm_client, command,
                                state_dir + "/shallow",
                                job_name=_job_name(task_id)))
    if job_id is None:
        from biomero_schema.shallower import ShallowBatchReport
        batch = ShallowBatchReport(schema=1, canonicalInputs=canonical,
                                   image=image, toolVersion=task.task_version,
                                   result="complete", receipts=())
        tracker.complete_task(task_id, json.dumps(batch.to_dict()))
        logger.warning("Shallower submission rejected for task %s; retaining "
                       "full results (empty receipts)", task_id)
        return batch
    if not task.job_ids:
        tracker.add_job_id(task_id, job_id)
        task = tracker.repository.get(task_id)
        logger.debug("Recorded shallower job %s on task %s", job_id, task_id)
    status = _wait(slurm_client, job_id, heartbeat)
    if status != "COMPLETED":
        logger.warning("Remote shallower job %s ended as %s; running "
                       "recovery to keep results consistent", job_id, status)
        # Recover in the same configured image on CPU. Completed stores retain
        # receipts; interrupted stores roll back before any archive is allowed.
        recovery = build_command(slurm_client, output, sif_path, manifest,
                                 str(task_id), recovery=True, image=image)
        # Recorded IDs remain authoritative on resume.
        recovery_id = (int(task.job_ids[1]) if len(task.job_ids) > 1
                       else _submit_once(
                           slurm_client, recovery, state_dir + "/recovery",
                           job_name=_job_name(task_id, recovery=True)))
        if recovery_id is None:
            raise RuntimeError(
                f"Shallower recovery submission rejected for task {task_id}; "
                "output preserved")
        if recovery_id not in task.job_ids:
            tracker.add_job_id(task_id, recovery_id)
        if _wait(slurm_client, recovery_id, heartbeat) != "COMPLETED":
            raise RuntimeError(
                f"Shallower recovery job {recovery_id} failed for task "
                f"{task_id}; output preserved for recovery")
    report = slurm_client.run_commands([
        "cat " + shlex.quote(output + "/.biomero-shallow-batch.json")])
    if not report.ok:
        raise RuntimeError(
            f"Missing shallower report for task {task_id}; cannot archive "
            "safely")
    batch = _batch(slurm_client, report.stdout, canonical, task=task)
    tracker.complete_task(task_id, json.dumps(batch.to_dict()))
    logger.info("Remote shallower task %s completed with %d receipts",
                task_id, len(batch.receipts))
    return batch
