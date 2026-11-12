"""Optional CPU result shallowing before result archiving.

SlurmClient owns remote execution, image acquisition, job submission and
scheduling policy; SlurmJob owns monitoring. This module coordinates the
shallower's manifest, durable submission records, recovery command and receipt
validation.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import posixpath
import re
import shlex
from typing import (
    Any, Callable, Dict, NamedTuple, Optional, Tuple, TYPE_CHECKING)
from uuid import UUID, uuid4

from packaging.version import InvalidVersion, Version

from . import remote_scripts
from .constants import (
    MANIFEST_LOCK_WAIT_SECONDS, REMOTE_SHALLOWER_TASK_NAME,
    SUBMISSION_REJECTED_EXIT_CODE)
from .slurm_client import SlurmClient, SlurmJob

if TYPE_CHECKING:
    from biomero_schema.shallower import RemoteShallowReceipt, ShallowBatchReport
    from biomero_schema.zarr import CanonicalInputManifest
    from .eventsourcing import Task

logger = logging.getLogger(__name__)

# Contract between BIOMERO and the biomero-shallower helper image: the helper
# declares the versions it supports as OCI labels, validated before use.
CAPABILITY_SCHEMA = 1
REQUIRED_RUNTIME_CONTRACT = 1
REQUIRED_MANIFEST_SCHEMA = 2
BATCH_REPORT_SCHEMA = 1
OCI_VERSION_LABEL = "org.opencontainers.image.version"
CAPABILITY_LABELS = {
    "schema": "org.biomeroproject.shallower.capability-schema",
    "contracts": "org.biomeroproject.shallower.runtime-contracts",
    "manifest_schemas": "org.biomeroproject.shallower.manifest-schemas",
    "migrations": "org.biomeroproject.shallower.migrations",
}

# How the helper is invoked inside its container.
HELPER_EXECUTABLE = "biomero-shallower"
HELPER_COMMAND = "normalize-tree"
FAILURE_POLICY = "keep-full"
CONTAINER_RESULTS_PATH = "/results"
CONTAINER_MANIFEST_PATH = "/canonical.json"
CONTAINER_RUNTIME_LOOKUP = (
    "runtime=$(command -v apptainer || command -v singularity)")

# Where the helper reads and writes on the Slurm cluster, relative to the
# workflow data folder.
RESULTS_SUBPATH = "data/out"
REPORT_FILENAME = ".biomero-shallow-batch.json"
STATE_DIRNAME = ".biomero-shallower"
MANIFEST_FILENAME = "canonical.json"

# Submission state files per operation, in the task state directory.
SHALLOW_STATE_NAME = "shallow"
RECOVERY_STATE_NAME = "recovery"
SUBMISSION_MARKER_SUFFIXES = (
    remote_scripts.SUBMISSION_STATE_FILES["intent_file"],
    remote_scripts.SUBMISSION_STATE_FILES["job_file"])

POLLING_INTERVAL_SECONDS = 15
COMPLETED_STATE = "COMPLETED"

# Image acquisition specification and file naming.
IMAGE_KIND = "result-shallower"
IMAGE_NAME = "biomero-shallower"
IMAGE_SOURCE_TYPE = "registry"
SIF_FILENAME_PREFIX = "shallower-"
SIF_FILENAME_SUFFIX = ".sif"
SIF_FILENAME_HASH_LENGTH = 24

# Slurm job names: ``<prefix><operation>-<task id>``.
JOB_NAME_PREFIX = "biomero-"
SHALLOW_JOB_OPERATION = "shallower"
RECOVERY_JOB_OPERATION = "recovery"

# Run the helper without the host environment or home directory.
CONTAINER_ISOLATION_FLAGS = "--containall --cleanenv"

# Keys of the parameters recorded on the helper task.
TASK_PARAM_IMAGE = "image"
TASK_PARAM_CONTRACT = "contract"
TASK_PARAM_SIF = "sif"
TASK_PARAM_CANONICAL_SHA256 = "canonical_sha256"


class HelperSettings(NamedTuple):
    """Values the entry point can change, threaded to the steps that use them.

    Each value defaults to the module constant of the same meaning.
    """

    contract_version: int = REQUIRED_RUNTIME_CONTRACT
    manifest_schema: int = REQUIRED_MANIFEST_SCHEMA
    polling_interval: int = POLLING_INTERVAL_SECONDS
    lock_wait_seconds: int = MANIFEST_LOCK_WAIT_SECONDS


class HelperJob(NamedTuple):
    """The task, image and remote paths of one helper (shallower) job.

    ``task`` is the snapshot read when the job was started or resumed; read the
    task again with ``task_id`` where recorded job IDs may have changed.
    """

    task_id: Any
    task: Task
    image: str
    sif_path: str
    output_path: str
    manifest_path: str
    state_dir: str


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
        version = f"sha256:{digest}"
    else:
        source, separator, version = image.rpartition(":")
        if not separator or "/" in version or not version:
            raise ValueError("Result shallower requires an explicit image version")
    image_hash = hashlib.sha256(image.encode()).hexdigest()
    sif_filename = (f"{SIF_FILENAME_PREFIX}"
                    f"{image_hash[:SIF_FILENAME_HASH_LENGTH]}"
                    f"{SIF_FILENAME_SUFFIX}")
    spec = {
        "kind": IMAGE_KIND, "name": IMAGE_NAME,
        "version": version, "source_type": IMAGE_SOURCE_TYPE,
        "source": source,
        "destination": posixpath.join(
            slurm_client.slurm_converters_path, sif_filename),
    }
    logger.debug(f"Remote shallower image spec: source={source} "
                 f"version={version} destination={spec['destination']}")
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
    inspect_labels_cmd = (
        f'{CONTAINER_RUNTIME_LOOKUP}; test -n "$runtime" && '
        f'"$runtime" inspect --json --labels {shlex.quote(sif_path)}')
    result = slurm_client.run_commands([inspect_labels_cmd])
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
        version = labels[OCI_VERSION_LABEL]
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


def _parse_capability_label(labels: Dict[str, Any], name: str, *,
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


def _check_configured_version(actual_version: str,
                              configured_version: Optional[str]) -> None:
    """Raise unless the installed version equals the configured one, if any.

    Args:
        actual_version (str): The installed tool version.
        configured_version (Optional[str]): The expected version, if any.

    Raises:
        ValueError: If the configured version is not a valid version.
        RuntimeError: If the versions differ.
    """
    if not configured_version:
        return
    try:
        versions_equal = Version(actual_version) == Version(configured_version)
    except InvalidVersion as error:
        raise ValueError(
            f"Invalid configured remote_shallower_version: "
            f"{configured_version}"
        ) from error
    if not versions_equal:
        raise RuntimeError(
            f"Installed remote Shallower {actual_version} does not match "
            f"configured version {configured_version}; run SLURM Init "
            "and verify the selected helper image")


def _check_capabilities(labels: Dict[str, Any], actual_version: str,
                        runtime_contract: int,
                        manifest_schema: int) -> None:
    """Raise unless the helper supports the required contracts and schemas.

    Args:
        labels (Dict[str, Any]): The OCI labels of the image.
        actual_version (str): The installed tool version, for messages.
        runtime_contract (int): The runtime contract this runtime requires.
        manifest_schema (int): The shallow manifest schema it requires.

    Raises:
        RuntimeError: If a capability is missing or unsupported.
        ValueError: If a capability label is missing or invalid.
    """
    if labels.get(CAPABILITY_LABELS["schema"]) != str(CAPABILITY_SCHEMA):
        raise RuntimeError(
            "Installed remote Shallower has missing or unsupported capability "
            "metadata; update the helper image and run SLURM Init")
    contracts = _parse_capability_label(labels, "contracts", integers=True)
    manifest_schemas = _parse_capability_label(
        labels, "manifest_schemas", integers=True)
    if runtime_contract not in contracts:
        raise RuntimeError(
            f"Installed remote Shallower {actual_version} does not support "
            f"runtime contract {runtime_contract}")
    if manifest_schema not in manifest_schemas:
        raise RuntimeError(
            f"Installed remote Shallower {actual_version} does not emit "
            f"shallow manifest schema {manifest_schema}")


def validate_installed_tool(slurm_client: SlurmClient, sif_path: str, *,
                            expected_version: Optional[str] = None,
                            runtime_contract: int = REQUIRED_RUNTIME_CONTRACT,
                            manifest_schema: int = REQUIRED_MANIFEST_SCHEMA
                            ) -> str:
    """Fail closed unless the installed SIF satisfies this runtime contract.

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        sif_path (str): Remote path of the installed helper image.
        expected_version (Optional[str]): Version the image must have; defaults
            to the configured ``remote_shallower_version``.
        runtime_contract (int): Runtime contract the image must support.
        manifest_schema (int): Shallow manifest schema the image must emit.

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
    _check_configured_version(actual_version, configured_version)
    _check_capabilities(
        labels, actual_version, runtime_contract, manifest_schema)
    logger.debug(f"Installed remote shallower {actual_version} at {sif_path} "
                 f"satisfies runtime contract {runtime_contract} and "
                 f"manifest schema {manifest_schema} (configured version: "
                 f"{configured_version or 'any'})")
    return actual_version


def helper_job_name(task_id: Any, *, recovery: bool = False) -> str:
    """Keep submission reconciliation specific to one task and operation.

    Args:
        task_id (Any): ID of the helper task.
        recovery (bool): Name the recovery job instead of the shallowing job
            (default False).

    Returns:
        str: The Slurm job name.
    """
    operation = RECOVERY_JOB_OPERATION if recovery else SHALLOW_JOB_OPERATION
    return f"{JOB_NAME_PREFIX}{operation}-{task_id}"


def _build_container_command(worker_count: int, output_path: str,
                             sif_path: str, manifest_path: str, task_id: str,
                             image: str, recovery: bool,
                             contract_version: int) -> str:
    """Build the shell command running the helper in its container.

    The command looks up Apptainer or Singularity on the compute node and
    executes the helper in the image with a clean environment. The results
    folder is writable in the container; the canonical manifest is read-only.

    Args:
        worker_count (int): Number of identity workers, already validated.
        output_path (str): Result directory to shallow.
        sif_path (str): Path of the helper image.
        manifest_path (str): Path of the canonical input manifest.
        task_id (str): ID of the helper task.
        image (str): Image reference recorded for the task.
        recovery (bool): Only recover an interrupted run.
        contract_version (int): Runtime contract the helper must follow.

    Returns:
        str: The shell command to run inside the Slurm job.
    """
    quote = shlex.quote
    results_bind = quote(f"{output_path}:{CONTAINER_RESULTS_PATH}:rw")
    manifest_bind = quote(f"{manifest_path}:{CONTAINER_MANIFEST_PATH}:ro")
    report_path = posixpath.join(CONTAINER_RESULTS_PATH, REPORT_FILENAME)
    container_exec = (
        f'exec "$runtime" exec {CONTAINER_ISOLATION_FLAGS} '
        '--env SLURM_JOB_ID="$SLURM_JOB_ID" '
        f"--bind {results_bind} --bind {manifest_bind} {quote(sif_path)}")
    helper_arguments = (
        f"{HELPER_EXECUTABLE} {HELPER_COMMAND} "
        f"--returned-zarr {CONTAINER_RESULTS_PATH} "
        f"--canonical-inputs {CONTAINER_MANIFEST_PATH} "
        f"--contract-version {contract_version} "
        f"--failure-policy {FAILURE_POLICY} --report {report_path} "
        f"--identity-workers {worker_count} "
        f"--image {quote(image)} --task-id {quote(task_id)}")
    command = (f'{CONTAINER_RUNTIME_LOOKUP}; test -n "$runtime"; '
               f"{container_exec} {helper_arguments}")
    if recovery:
        command += " --recover-only"
    return command


def build_helper_command(slurm_client: SlurmClient, output_path: str,
                         sif_path: str, manifest_path: str, task_id: str, *,
                         recovery: bool = False, image: Optional[str] = None,
                         contract_version: int = REQUIRED_RUNTIME_CONTRACT
                         ) -> str:
    """Build the shallowing or recovery job using one helper resource policy.

    Paths refer to the remote filesystem. The job is submitted through the
    SlurmClient, so the configured site defaults apply.

    Args:
        slurm_client (SlurmClient): The Slurm client holding the configuration.
        output_path (str): Result directory to shallow.
        sif_path (str): Path of the helper image.
        manifest_path (str): Path of the canonical input manifest.
        task_id (str): ID of the helper task.
        recovery (bool): Build the recovery command instead of the shallowing
            command (default False).
        image (Optional[str]): Image reference recorded for the task; defaults
            to the configured ``remote_shallower_image``.
        contract_version (int): Runtime contract the helper must follow.

    Returns:
        str: The ``sbatch`` command.

    Raises:
        ValueError: If the configured worker count is not positive.
    """
    image = image if image is not None else slurm_client.remote_shallower_image
    worker_count = slurm_client.remote_shallower_workers
    if (isinstance(worker_count, bool) or not isinstance(worker_count, int)
            or worker_count < 1):
        raise ValueError("Result shallower worker count must be positive")
    job_params = slurm_client.get_shallower_job_params()
    container_command = _build_container_command(
        worker_count, output_path, sif_path, manifest_path, task_id, image,
        recovery, contract_version)
    sbatch_command = slurm_client.build_sbatch_wrap_command(
        container_command,
        job_name=helper_job_name(task_id, recovery=recovery),
        output_path=f"{manifest_path}.%j.log",
        job_params=job_params)
    logger.debug(f"Built remote shallower "
                 f"{'recovery' if recovery else 'shallowing'} command for "
                 f"task {task_id}: image={image} workers={worker_count} "
                 f"sbatch params={job_params}")
    return sbatch_command


def sha256_of_json(payload: Any) -> str:
    """Fingerprint JSON content independently of key order and whitespace.

    Args:
        payload (Any): A JSON-serializable value.

    Returns:
        str: The hex SHA-256 of the canonical JSON encoding.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_task_canonical_inputs(
        task: Task, canonical: CanonicalInputManifest) -> None:
    """Raise unless the canonical inputs equal those recorded on the task.

    Args:
        task (Any): The recorded helper task.
        canonical (Any): The canonical input manifest of the workflow.

    Raises:
        ValueError: If the task recorded a different canonical manifest.
    """
    recorded_sha256 = task.params.get(TASK_PARAM_CANONICAL_SHA256)
    if recorded_sha256 and recorded_sha256 != sha256_of_json(canonical.to_dict()):
        raise ValueError("Canonical manifest differs from the recorded task")


def parse_batch_report(slurm_client: SlurmClient, report_json: str,
                       canonical: CanonicalInputManifest, *,
                       task: Optional[Task] = None) -> ShallowBatchReport:
    """Parse a helper batch report and check its input, image and tool version.

    When a task is supplied, its recorded image/version and submission own the
    report; current deployment defaults must not invalidate historical results.
    Import the optional schema dependency only when shallowing is used.

    Args:
        slurm_client (SlurmClient): The Slurm client holding the configuration.
        report_json (str): The JSON batch report written by the helper.
        canonical (CanonicalInputManifest): The canonical input manifest the
            report must match.
        task (Optional[Task]): The recorded helper task owning the report.

    Returns:
        ShallowBatchReport: The parsed batch report.

    Raises:
        ValueError: If the report does not match the configuration, input,
            task or job.
    """
    from biomero_schema.shallower import ShallowBatchReport
    batch = ShallowBatchReport.from_dict(json.loads(report_json))
    expected_image = (task.params[TASK_PARAM_IMAGE] if task
                      else slurm_client.remote_shallower_image)
    expected_version = (task.task_version if task
                        else slurm_client.remote_shallower_version)
    if (batch.canonical_inputs != canonical
            or batch.image != expected_image
            or batch.tool_version != expected_version):
        raise ValueError(
            "Result shallower report does not match configuration/input")
    if any(receipt.image != batch.image
           or receipt.tool_version != batch.tool_version
           for receipt in batch.receipts):
        raise ValueError("Inconsistent helper receipts")
    if task is not None:
        verify_task_canonical_inputs(task, canonical)
        if any(receipt.task_id != task.id or not task.job_ids
               or int(receipt.slurm_job_id) != int(task.job_ids[0])
               for receipt in batch.receipts):
            raise ValueError("Report belongs to a different helper task/job")
    return batch


def _read_existing_manifest(slurm_client: SlurmClient,
                            manifest_path: str) -> str:
    """Read the remote manifest unless it is absent; refuse a symbolic link.

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        manifest_path (str): Remote path of the canonical input manifest.

    Returns:
        str: The manifest content, empty if it does not exist.

    Raises:
        RuntimeError: If the manifest cannot be read.
    """
    target = shlex.quote(manifest_path)
    read_manifest_cmd = (
        f"if test -f {target} && ! test -L {target}; then cat {target}; "
        f"elif test -e {target} || test -L {target}; then exit 1; fi")
    result = slurm_client.run_commands([read_manifest_cmd], log_stdout=False)
    if not result.ok:
        raise RuntimeError("Cannot read canonical manifest; output preserved")
    return result.stdout


def _verify_manifest_content(manifest_json: str, payload: Any) -> None:
    """Raise unless the remote manifest content equals the workflow input.

    Args:
        manifest_json (str): The manifest content read from the cluster.
        payload (Any): The JSON-serializable manifest of the workflow.

    Raises:
        ValueError: If the contents differ.
    """
    if sha256_of_json(json.loads(manifest_json)) != sha256_of_json(payload):
        raise ValueError("Remote canonical manifest differs from workflow input")


def _publish_manifest(slurm_client: SlurmClient, manifest_path: str,
                      payload: Any,
                      lock_wait_seconds: int = MANIFEST_LOCK_WAIT_SECONDS
                      ) -> None:
    """Upload the manifest to a staging file and publish it atomically.

    The configured lock command (``slurm_lock_cmd``, ``flock`` by default)
    takes an exclusive lock on the manifest's lock file, so concurrent
    processes publish one at a time; ``ln`` hard-links the staging file to the
    final name, which fails instead of overwriting. SlurmClient checks that the
    lock command exists during Slurm setup (``check_remote_commands_available``).

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        manifest_path (str): Remote path of the canonical input manifest.
        payload (Any): The JSON-serializable manifest content.
        lock_wait_seconds (int): How long to wait for the lock.

    Raises:
        RuntimeError: If the manifest cannot be published, or a submission
            exists without a manifest.
        ValueError: If the published manifest differs from the input.
    """
    quote = shlex.quote
    target = quote(manifest_path)
    staging_path = f"{manifest_path}.{uuid4().hex}.tmp"
    state_dir = posixpath.dirname(manifest_path)
    submission_state_exists_cmd = " || ".join(
        f"test -e {quote(posixpath.join(state_dir, operation + suffix))}"
        for operation in (SHALLOW_STATE_NAME, RECOVERY_STATE_NAME)
        for suffix in SUBMISSION_MARKER_SUFFIXES)
    publish_script = (
        f"set -eu; if test -L {target}; then exit 1; fi; "
        f"if test -e {target}; then cat {target}; "
        f"elif {submission_state_exists_cmd}; then "
        'echo "Missing manifest after submission intent" >&2; exit 1; '
        f"else ln {quote(staging_path)} {target}; cat {target}; fi")
    publish_manifest_cmd = remote_scripts.locked_script(
        slurm_client.slurm_lock_cmd, f"{manifest_path}.lock", publish_script,
        wait_seconds=lock_wait_seconds)
    try:
        slurm_client.put(io.StringIO(json.dumps(payload)), staging_path)
        published = slurm_client.run_commands(
            [publish_manifest_cmd], log_stdout=False)
        if not published.ok:
            raise RuntimeError("Cannot publish canonical manifest; output preserved")
        _verify_manifest_content(published.stdout, payload)
        logger.info(f"Published canonical manifest {manifest_path} for "
                    "remote shallowing")
    finally:
        _remove_staging_file(slurm_client, staging_path)


def _remove_staging_file(slurm_client: SlurmClient, staging_path: str) -> None:
    """Remove the unique staging file owned by the calling publication.

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        staging_path (str): Remote path of the staging file.
    """
    remove_staging_cmd = f"rm -f -- {shlex.quote(staging_path)}"
    try:
        cleanup = slurm_client.run_commands([remove_staging_cmd])
        if not cleanup.ok:
            logger.warning(f"Could not remove manifest staging file {staging_path}")
    except Exception:
        logger.warning(f"Could not remove manifest staging file {staging_path}",
                       exc_info=True)


def publish_or_verify_manifest(
        slurm_client: SlurmClient, manifest_path: str,
        canonical: CanonicalInputManifest, *, submitted: bool,
        lock_wait_seconds: int = MANIFEST_LOCK_WAIT_SECONDS) -> None:
    """Verify an existing manifest or publish a new one without overwriting.

    A sibling temporary file is fully uploaded before an atomic hard-link
    publishes it under a lock. Readers never see a partly uploaded manifest.
    Existing content is verified, not replaced on resume.

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        manifest_path (str): Remote path of the canonical input manifest.
        canonical (CanonicalInputManifest): The canonical input manifest to
            publish or verify.
        submitted (bool): Whether the helper job was already submitted, which
            makes a missing manifest an error.
        lock_wait_seconds (int): How long to wait for the publication lock.

    Raises:
        RuntimeError: If the manifest cannot be read or published, or is
            missing for a submitted job.
        ValueError: If an existing manifest differs from the workflow input.
    """
    payload = canonical.to_dict()
    existing_manifest = _read_existing_manifest(slurm_client, manifest_path)
    if existing_manifest.strip():
        _verify_manifest_content(existing_manifest, payload)
        logger.debug(f"Existing canonical manifest {manifest_path} verified; "
                     "not uploading again")
        return
    if submitted:
        raise RuntimeError("Missing canonical manifest for submitted job; output preserved")
    _publish_manifest(slurm_client, manifest_path, payload, lock_wait_seconds)


def completed_receipts(
        slurm_client: SlurmClient, workflow_id: Any,
        canonical: CanonicalInputManifest) -> Tuple[RemoteShallowReceipt, ...]:
    """Read persisted helper receipts without contacting the cluster.

    Args:
        slurm_client (SlurmClient): The Slurm client with the workflow tracker.
        workflow_id (Any): ID of the workflow.
        canonical (CanonicalInputManifest): The canonical input manifest the
            receipts must match.

    Returns:
        Tuple[RemoteShallowReceipt, ...]: The receipts of completed helper
            tasks; empty when remote shallowing or workflow tracking is
            disabled.
    """
    if not slurm_client.remote_shallow_zarr or not slurm_client.track_workflows:
        logger.debug(f"No remote shallower receipts for workflow {workflow_id}: "
                     f"remote_shallow_zarr={slurm_client.remote_shallow_zarr} "
                     f"track_workflows={slurm_client.track_workflows}")
        return ()
    receipts = []
    for task in slurm_client.get_workflow_tasks(
            workflow_id, task_name=REMOTE_SHALLOWER_TASK_NAME):
        if task.result_message:
            receipts.extend(parse_batch_report(
                slurm_client, task.result_message, canonical,
                task=task).receipts)
    logger.debug(f"Read {len(receipts)} remote shallower receipts for "
                 f"workflow {workflow_id}")
    return tuple(receipts)


def wait_for_helper_job(slurm_client: SlurmClient, job_id: int,
                        heartbeat: Optional[Callable[[], None]] = None,
                        polling_interval: int = POLLING_INTERVAL_SECONDS
                        ) -> str:
    """Monitor an adopted job without analysis-log or task-status updates.

    The helper task is completed after report validation, not merely because
    its Slurm process exited successfully.

    Args:
        slurm_client (SlurmClient): The Slurm client used to poll the job.
        job_id (int): The Slurm job ID.
        heartbeat (Optional[Callable[[], None]]): Callback invoked before each
            poll.
        polling_interval (int): Seconds between status polls.

    Returns:
        str: The final state of the Slurm job.
    """
    logger.info(f"Waiting for remote shallower Slurm job {job_id}")
    job = SlurmJob.from_job_id(job_id, slurm_polling_interval=polling_interval)
    state = job.wait_for_completion(
        slurm_client, heartbeat=heartbeat, track_progress=False,
        update_task=False, strict_status=True)
    outcome = COMPLETED_STATE if job.completed() else state
    logger.info(f"Remote shallower Slurm job {job_id} finished with state "
                f"{outcome}")
    return outcome


def _shallowing_applies(slurm_client: SlurmClient, workflow_id: Any,
                        canonical: Any) -> bool:
    """Decide whether this workflow is shallowed on the cluster, and log why not.

    Args:
        slurm_client (SlurmClient): The Slurm client holding the configuration.
        workflow_id (Any): ID of the workflow.
        canonical (Any): The canonical input manifest of the workflow.

    Returns:
        bool: True if remote shallowing is enabled, tracked and has inputs.
    """
    if not slurm_client.remote_shallow_zarr:
        logger.debug(f"Remote shallowing skipped for workflow {workflow_id}: "
                     "remote_shallow_zarr is disabled")
        return False
    if canonical is None or not canonical.inputs:
        logger.debug(f"Remote shallowing skipped for workflow {workflow_id}: "
                     "no canonical inputs")
        return False
    if not slurm_client.track_workflows:
        logger.warning(f"Remote shallowing skipped for workflow {workflow_id}: "
                       "track_workflows is disabled; results stay full")
        return False
    return True


def _find_existing_task(
        slurm_client: SlurmClient, workflow_id: Any, data_path: str,
        canonical: CanonicalInputManifest) -> Optional[Task]:
    """Find the helper task already recorded for this data folder, if any.

    Args:
        slurm_client (SlurmClient): The Slurm client with the workflow tracker.
        workflow_id (Any): ID of the workflow.
        data_path (str): Remote data folder of the workflow results.
        canonical (CanonicalInputManifest): The canonical input manifest of
            the workflow.

    Returns:
        Optional[Task]: The recorded helper task, or None for a first run.

    Raises:
        RuntimeError: If more than one helper task exists for the folder.
        ValueError: If the task recorded different canonical inputs.
    """
    recorded_tasks = slurm_client.get_workflow_tasks(
        workflow_id, task_name=REMOTE_SHALLOWER_TASK_NAME,
        input_data=data_path)
    if len(recorded_tasks) > 1:
        raise RuntimeError("Ambiguous result shallower tasks")
    if not recorded_tasks:
        return None
    recorded_task = recorded_tasks[0]
    verify_task_canonical_inputs(recorded_task, canonical)
    return recorded_task


def _adopt_report_of_finished_job(
        slurm_client: SlurmClient, task: Task, data_path: str,
        canonical: CanonicalInputManifest) -> Optional[ShallowBatchReport]:
    """Complete the task from a valid report a submitted helper already wrote.

    Args:
        slurm_client (SlurmClient): The Slurm client with the workflow tracker.
        task (Task): The recorded helper task, with a recorded job.
        data_path (str): Remote data folder of the workflow results.
        canonical (CanonicalInputManifest): The canonical input manifest of
            the workflow.

    Returns:
        Optional[ShallowBatchReport]: The report, or None if it is absent,
            partial or stale and the recorded job must be polled instead.
    """
    report_path = posixpath.join(data_path, RESULTS_SUBPATH, REPORT_FILENAME)
    quoted_report_path = shlex.quote(report_path)
    read_report_cmd = (f"if test -f {quoted_report_path}; "
                       f"then cat {quoted_report_path}; fi")
    found = slurm_client.run_commands([read_report_cmd])
    if not (found.ok and found.stdout.strip()):
        return None
    try:
        batch = parse_batch_report(
            slurm_client, found.stdout, canonical, task=task)
    except (ValueError, KeyError):
        # Partial/stale report: poll the recorded job instead.
        logger.warning(f"Existing shallower report {report_path} was rejected "
                       "as partial or stale; polling recorded job",
                       exc_info=True)
        return None
    logger.info(f"Adopted existing shallower report {report_path} for task "
                f"{task.id}; completing task")
    slurm_client.workflowTracker.complete_task(
        task.id, json.dumps(batch.to_dict()))
    return batch


def _reuse_recorded_outcome(
        slurm_client: SlurmClient, task: Task, data_path: str,
        canonical: CanonicalInputManifest) -> Optional[ShallowBatchReport]:
    """Return the report of an already completed or finished helper task.

    Args:
        slurm_client (SlurmClient): The Slurm client with the workflow tracker.
        task (Task): The recorded helper task.
        data_path (str): Remote data folder of the workflow results.
        canonical (CanonicalInputManifest): The canonical input manifest of
            the workflow.

    Returns:
        Optional[ShallowBatchReport]: The report, or None if the task still
            has to run.
    """
    shallower_already_completed = bool(task.result_message)
    helper_job_was_submitted = bool(task.job_ids)
    if shallower_already_completed:
        logger.info(f"Remote shallower task {task.id} already completed; "
                    "reusing its receipts")
        return parse_batch_report(
            slurm_client, task.result_message, canonical, task=task)
    if helper_job_was_submitted:
        return _adopt_report_of_finished_job(
            slurm_client, task, data_path, canonical)
    return None


def _ensure_image_installed(slurm_client: SlurmClient,
                            image_specification: Dict[str, str]) -> None:
    """Raise unless the helper image is installed; only Init acquires images.

    Args:
        slurm_client (SlurmClient): The Slurm client used to inspect images.
        image_specification (Dict[str, str]): The helper image specification.

    Raises:
        RuntimeError: If the image is missing or invalid.
    """
    ready, pending = slurm_client._partition_existing_images(
        [image_specification])
    if pending or not ready:
        raise RuntimeError(
            f'Remote shallower image missing or invalid: '
            f'{image_specification["destination"]}. '
            "Run SLURM_Init_environment and verify image setup with "
            "SLURM_check_setup before retrying.")


def _start_or_resume_helper(
        slurm_client: SlurmClient, workflow_id: Any, data_path: str,
        canonical: CanonicalInputManifest, existing_task: Optional[Task],
        settings: HelperSettings) -> HelperJob:
    """Validate the installed helper and start, or resume, its tracked task.

    A resumed task keeps the image, version and image path it recorded, so a
    configuration change does not invalidate historical results.

    Args:
        slurm_client (SlurmClient): The Slurm client with the workflow tracker.
        workflow_id (Any): ID of the workflow.
        data_path (str): Remote data folder of the workflow results.
        canonical (CanonicalInputManifest): The canonical input manifest of
            the workflow.
        existing_task (Optional[Task]): The recorded helper task, if any.
        settings (HelperSettings): The contract and schema versions to require.

    Returns:
        HelperJob: The task with its image and remote paths.

    Raises:
        RuntimeError: If the image is missing or the installed tool does not
            satisfy the runtime contract.
    """
    image = (existing_task.params[TASK_PARAM_IMAGE] if existing_task
             else slurm_client.remote_shallower_image)
    image_specification = image_spec(slurm_client, image=image)
    if existing_task:
        image_specification["destination"] = existing_task.params.get(
            TASK_PARAM_SIF, image_specification["destination"])
    _ensure_image_installed(slurm_client, image_specification)
    expected_version = (existing_task.task_version if existing_task
                        else slurm_client.remote_shallower_version)
    installed_version = validate_installed_tool(
        slurm_client, image_specification["destination"],
        expected_version=expected_version,
        runtime_contract=settings.contract_version,
        manifest_schema=settings.manifest_schema)
    if existing_task:
        task = existing_task
        logger.info(f"Resuming remote shallower task {task.id} "
                    f"(jobs recorded: {list(task.job_ids)})")
    else:
        tracker = slurm_client.workflowTracker
        task_id = tracker.add_task_to_workflow(
            UUID(str(workflow_id)), REMOTE_SHALLOWER_TASK_NAME,
            installed_version, data_path,
            {TASK_PARAM_IMAGE: image,
             TASK_PARAM_CONTRACT: settings.contract_version,
             TASK_PARAM_SIF: image_specification["destination"],
             TASK_PARAM_CANONICAL_SHA256: sha256_of_json(canonical.to_dict())})
        tracker.start_task(task_id)
        task = slurm_client.get_task(task_id)
        logger.info(f"Started remote shallower task {task_id} (workflow "
                    f"{workflow_id}, image {image}, tool version "
                    f"{installed_version}, {len(canonical.inputs)} inputs)")
    state_dir = posixpath.join(data_path, STATE_DIRNAME, str(task.id))
    return HelperJob(
        task_id=task.id, task=task, image=image,
        sif_path=task.params.get(
            TASK_PARAM_SIF, image_specification["destination"]),
        output_path=posixpath.join(data_path, RESULTS_SUBPATH),
        manifest_path=posixpath.join(state_dir, MANIFEST_FILENAME),
        state_dir=state_dir)


def _prepare_remote_state(slurm_client: SlurmClient, helper: HelperJob,
                          canonical: CanonicalInputManifest,
                          settings: HelperSettings) -> None:
    """Create the task state directory and publish or verify the manifest.

    Args:
        slurm_client (SlurmClient): The Slurm client used to run commands.
        helper (HelperJob): The helper task with its remote paths.
        canonical (CanonicalInputManifest): The canonical input manifest of
            the workflow.
        settings (HelperSettings): The manifest lock wait.

    Raises:
        RuntimeError: If the state directory or manifest cannot be prepared.
    """
    make_state_dir_cmd = f"mkdir -p {shlex.quote(helper.state_dir)}"
    if not slurm_client.run_commands([make_state_dir_cmd]).ok:
        raise RuntimeError(
            f"Cannot prepare shallower state directory {helper.state_dir}")
    publish_or_verify_manifest(
        slurm_client, helper.manifest_path, canonical,
        submitted=bool(helper.task.job_ids),
        lock_wait_seconds=settings.lock_wait_seconds)


def _submit_helper_job(slurm_client: SlurmClient, sbatch_command: str,
                       state_prefix: str, job_name: str) -> Optional[int]:
    """Submit a helper job once, or adopt the one an earlier call submitted.

    A rejected submission is forgotten, so that the caller falls back to full
    results. Ambiguous submission state raises instead of risking a duplicate
    job.

    Args:
        slurm_client (SlurmClient): The Slurm client used to submit the job.
        sbatch_command (str): The ``sbatch`` command of the helper job.
        state_prefix (str): Remote path prefix of the submission receipts.
        job_name (str): Slurm job name of the submission.

    Returns:
        Optional[int]: The Slurm job ID, or None if sbatch rejected the job.

    Raises:
        RuntimeError: If the submission state cannot be resolved.
    """
    result = slurm_client.submit_job_once(
        sbatch_command, state_prefix, job_name=job_name,
        raise_on_rejection=False, verify_command_unchanged=False)
    if result.exited == SUBMISSION_REJECTED_EXIT_CODE:
        logger.warning(f"sbatch rejected helper job {job_name}")
        return None
    job_id = slurm_client.extract_job_id(result)
    if job_id < 0:
        raise RuntimeError(
            f"Submission of {job_name} is unresolved; preserve results and "
            "resume after reconciliation")
    return job_id


def _submit_or_adopt_helper_job(slurm_client: SlurmClient, helper: HelperJob,
                                settings: HelperSettings) -> Optional[int]:
    """Adopt the recorded helper job, or submit it exactly once.

    Args:
        slurm_client (SlurmClient): The Slurm client used to submit the job.
        helper (HelperJob): The helper task with its remote paths.
        settings (HelperSettings): The runtime contract version to run with.

    Returns:
        Optional[int]: The Slurm job ID, or None if sbatch rejected the job.
    """
    task = helper.task
    sbatch_command = build_helper_command(
        slurm_client, helper.output_path, helper.sif_path,
        helper.manifest_path, str(task.id), image=helper.image,
        contract_version=settings.contract_version)
    if task.job_ids:
        logger.debug(f"Adopting recorded shallower job {task.job_ids[0]}; "
                     "no new submission")
        return int(task.job_ids[0])
    return _submit_helper_job(
        slurm_client, sbatch_command,
        posixpath.join(helper.state_dir, SHALLOW_STATE_NAME),
        helper_job_name(task.id))


def _complete_with_full_results(
        slurm_client: SlurmClient, helper: HelperJob,
        canonical: CanonicalInputManifest) -> ShallowBatchReport:
    """Complete the task with an empty report after a rejected submission.

    The caller then keeps the full results instead of shallow ones.

    Args:
        slurm_client (SlurmClient): The Slurm client with the workflow tracker.
        helper (HelperJob): The helper task whose submission was rejected.
        canonical (CanonicalInputManifest): The canonical input manifest of
            the workflow.

    Returns:
        ShallowBatchReport: A report without receipts.
    """
    from biomero_schema.shallower import ShallowBatchReport
    batch = ShallowBatchReport(
        schema=BATCH_REPORT_SCHEMA, canonicalInputs=canonical,
        image=helper.image, toolVersion=helper.task.task_version,
        result="complete", receipts=())
    slurm_client.workflowTracker.complete_task(
        helper.task_id, json.dumps(batch.to_dict()))
    logger.warning(f"Shallower submission rejected for task {helper.task_id}; "
                   "retaining full results (empty receipts)")
    return batch


def _recover_after_failed_job(slurm_client: SlurmClient, helper: HelperJob,
                              heartbeat: Optional[Callable[[], None]],
                              settings: HelperSettings) -> None:
    """Run the recovery job in the same image on CPU, adopting a recorded one.

    Completed stores retain receipts; interrupted stores roll back before any
    archive is allowed.

    Args:
        slurm_client (SlurmClient): The Slurm client used to submit the job.
        helper (HelperJob): The helper task with its remote paths.
        heartbeat (Optional[Callable[[], None]]): Callback invoked during waits.
        settings (HelperSettings): The contract version and polling interval.

    Raises:
        RuntimeError: If the recovery submission is rejected or the recovery
            job fails.
    """
    task_id = helper.task_id
    recorded_job_ids = slurm_client.get_task(task_id).job_ids
    recovery_command = build_helper_command(
        slurm_client, helper.output_path, helper.sif_path,
        helper.manifest_path, str(task_id), recovery=True, image=helper.image,
        contract_version=settings.contract_version)
    recovery_state_prefix = posixpath.join(
        helper.state_dir, RECOVERY_STATE_NAME)
    # Recorded IDs remain authoritative on resume.
    if len(recorded_job_ids) > 1:
        recovery_job_id = int(recorded_job_ids[1])
    else:
        recovery_job_id = _submit_helper_job(
            slurm_client, recovery_command, recovery_state_prefix,
            helper_job_name(task_id, recovery=True))
    if recovery_job_id is None:
        raise RuntimeError(
            f"Shallower recovery submission rejected for task {task_id}; "
            "output preserved")
    if recovery_job_id not in recorded_job_ids:
        slurm_client.workflowTracker.add_job_id(task_id, recovery_job_id)
        slurm_client.remove_submission_receipts(recovery_state_prefix)
    recovery_state = wait_for_helper_job(
        slurm_client, recovery_job_id, heartbeat, settings.polling_interval)
    if recovery_state != COMPLETED_STATE:
        raise RuntimeError(
            f"Shallower recovery job {recovery_job_id} failed for task "
            f"{task_id}; output preserved for recovery")


def _complete_from_report(
        slurm_client: SlurmClient, helper: HelperJob,
        canonical: CanonicalInputManifest) -> ShallowBatchReport:
    """Validate the report the helper wrote and complete the task with it.

    Args:
        slurm_client (SlurmClient): The Slurm client with the workflow tracker.
        helper (HelperJob): The helper task with its remote paths.
        canonical (CanonicalInputManifest): The canonical input manifest of
            the workflow.

    Returns:
        ShallowBatchReport: The validated report.

    Raises:
        RuntimeError: If the report is missing.
        ValueError: If the report is invalid.
    """
    report_path = posixpath.join(helper.output_path, REPORT_FILENAME)
    read_report_cmd = f"cat {shlex.quote(report_path)}"
    report = slurm_client.run_commands([read_report_cmd])
    if not report.ok:
        raise RuntimeError(
            f"Missing shallower report for task {helper.task_id}; cannot "
            "archive safely")
    task = slurm_client.get_task(helper.task_id)
    batch = parse_batch_report(slurm_client, report.stdout, canonical, task=task)
    slurm_client.workflowTracker.complete_task(
        helper.task_id, json.dumps(batch.to_dict()))
    logger.info(f"Remote shallower task {helper.task_id} completed with "
                f"{len(batch.receipts)} receipts")
    return batch


def shallow_workflow_results(
        slurm_client: SlurmClient, data_path: str, workflow_id: Any,
        canonical: CanonicalInputManifest,
        heartbeat: Optional[Callable[[], None]] = None, *,
        contract_version: int = REQUIRED_RUNTIME_CONTRACT,
        manifest_schema: int = REQUIRED_MANIFEST_SCHEMA,
        polling_interval: int = POLLING_INTERVAL_SECONDS,
        lock_wait_seconds: int = MANIFEST_LOCK_WAIT_SECONDS
        ) -> Optional[ShallowBatchReport]:
    """Shallow the workflow results on the cluster, or resume that work.

    Only initialization acquires container images. A rejected shallowing
    submission returns an empty-receipt report so the caller can retain full
    results. Callers must not archive potentially incomplete output.

    Args:
        slurm_client (SlurmClient): The Slurm client with the workflow tracker.
        data_path (str): Remote data folder of the workflow results.
        workflow_id (Any): ID of the workflow.
        canonical (CanonicalInputManifest): The canonical input manifest of
            the workflow.
        heartbeat (Optional[Callable[[], None]]): Callback invoked during job
            waits.
        contract_version (int): Runtime contract the helper must follow.
        manifest_schema (int): Shallow manifest schema the helper must emit.
        polling_interval (int): Seconds between job status polls.
        lock_wait_seconds (int): How long to wait for the manifest lock.

    Returns:
        Optional[ShallowBatchReport]: The validated report, or None when
            shallowing is disabled or inapplicable.

    Raises:
        RuntimeError: If the image is missing, the submission or recovery is
            unresolved or failed, or the report is missing.
        ValueError: If the installed tool or the report is invalid.
    """
    if not _shallowing_applies(slurm_client, workflow_id, canonical):
        return None
    settings = HelperSettings(
        contract_version, manifest_schema, polling_interval, lock_wait_seconds)
    existing_task = _find_existing_task(
        slurm_client, workflow_id, data_path, canonical)
    if existing_task is not None:
        recorded_batch = _reuse_recorded_outcome(
            slurm_client, existing_task, data_path, canonical)
        if recorded_batch is not None:
            return recorded_batch
    helper = _start_or_resume_helper(
        slurm_client, workflow_id, data_path, canonical, existing_task,
        settings)
    _prepare_remote_state(slurm_client, helper, canonical, settings)
    job_id = _submit_or_adopt_helper_job(slurm_client, helper, settings)
    if job_id is None:
        return _complete_with_full_results(slurm_client, helper, canonical)
    if not helper.task.job_ids:
        slurm_client.workflowTracker.add_job_id(helper.task_id, job_id)
        logger.debug(f"Recorded shallower job {job_id} on task "
                     f"{helper.task_id}")
        slurm_client.remove_submission_receipts(
            posixpath.join(helper.state_dir, SHALLOW_STATE_NAME))
    status = wait_for_helper_job(
        slurm_client, job_id, heartbeat, polling_interval)
    if status != COMPLETED_STATE:
        logger.warning(f"Remote shallower job {job_id} ended as {status}; "
                       "running recovery to keep results consistent")
        _recover_after_failed_job(slurm_client, helper, heartbeat, settings)
    return _complete_from_report(slurm_client, helper, canonical)
