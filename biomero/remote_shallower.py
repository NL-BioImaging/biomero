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
from uuid import UUID, uuid4

from packaging.version import InvalidVersion, Version

from .slurm_client import SlurmJob

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


def image_spec(client, *, image=None):
    """Describe the configured helper image for SlurmClient image acquisition."""
    image = image if image is not None else client.remote_shallower_image
    if not image:
        raise ValueError('Configure remote_shallower_image in [SLURM] before '
                         'initializing remote shallowing')
    image = image.removeprefix('docker://')
    if '@sha256:' in image:
        source, digest = image.split('@sha256:', 1)
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid image digest')
        version = 'sha256:' + digest
    else:
        source, separator, version = image.rpartition(':')
        if not separator or '/' in version or not version:
            raise ValueError('Result shallower requires an explicit image version')
    return {
        'kind': 'result-shallower', 'name': 'biomero-shallower',
        'version': version, 'source_type': 'registry', 'source': source,
        'destination': posixpath.join(
            client.slurm_converters_path,
            'shallower-' + hashlib.sha256(image.encode()).hexdigest()[:24]
            + '.sif'),
    }


def _installed_labels(client, sif):
    """Read OCI labels without executing the helper payload."""
    result = client.run_commands([
        'runtime=$(command -v apptainer || command -v singularity); '
        'test -n "$runtime" && "$runtime" inspect --json --labels '
        + shlex.quote(sif)])
    if not result.ok:
        raise RuntimeError('Cannot inspect installed shallower metadata')
    try:
        labels = json.loads(result.stdout)['data']['attributes']['labels']
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(
            'Installed shallower has no usable OCI labels'
        ) from error
    if not isinstance(labels, dict):
        raise ValueError('Installed shallower has no usable OCI labels')
    return labels


def _version_from_labels(labels):
    try:
        version = labels['org.opencontainers.image.version']
        if not isinstance(version, str) or not version.strip():
            raise ValueError('Empty version')
        Version(version.strip())
    except (InvalidVersion, KeyError, TypeError, ValueError) as error:
        raise ValueError('Installed helper has no usable OCI version label; '
                         'set remote_shallower_version explicitly') from error
    return version.strip()


def installed_tool_version(client, sif):
    """Read the installed helper's version without executing its payload.

    A floating image tag is not a tool version. Persist the OCI version label
    before submission so receipt validation and recovery use a concrete value.
    """
    return _version_from_labels(_installed_labels(client, sif))


def _csv_capability(labels, name, *, integers=False):
    label = CAPABILITY_LABELS[name]
    value = labels.get(label)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f'Installed shallower does not declare {label}; install a '
            'compatible helper and run SLURM Init again')
    entries = tuple(part.strip() for part in value.split(',') if part.strip())
    if not entries:
        raise ValueError(f'Installed shallower declares an empty {label}')
    if not integers:
        return entries
    try:
        return tuple(int(part) for part in entries)
    except ValueError as error:
        raise ValueError(f'Installed shallower has invalid {label}') from error


def validate_installed_tool(client, sif, *, expected_version=None):
    """Fail closed unless the installed SIF satisfies this runtime contract."""
    labels = _installed_labels(client, sif)
    actual_version = _version_from_labels(labels)
    configured_version = (
        expected_version
        if expected_version is not None
        else client.remote_shallower_version
    )
    if configured_version:
        try:
            matches = Version(actual_version) == Version(configured_version)
        except InvalidVersion as error:
            raise ValueError(
                f'Invalid configured remote_shallower_version: '
                f'{configured_version}'
            ) from error
        if not matches:
            raise RuntimeError(
                f'Installed remote Shallower {actual_version} does not match '
                f'configured version {configured_version}; run SLURM Init '
                'and verify the selected helper image')
    if labels.get(CAPABILITY_LABELS['schema']) != str(CAPABILITY_SCHEMA):
        raise RuntimeError(
            'Installed remote Shallower has missing or unsupported capability '
            'metadata; update the helper image and run SLURM Init')
    contracts = _csv_capability(labels, 'contracts', integers=True)
    manifest_schemas = _csv_capability(
        labels, 'manifest_schemas', integers=True
    )
    if REQUIRED_RUNTIME_CONTRACT not in contracts:
        raise RuntimeError(
            f'Installed remote Shallower {actual_version} does not support '
            f'runtime contract {REQUIRED_RUNTIME_CONTRACT}')
    if REQUIRED_MANIFEST_SCHEMA not in manifest_schemas:
        raise RuntimeError(
            f'Installed remote Shallower {actual_version} does not emit '
            f'shallow manifest schema {REQUIRED_MANIFEST_SCHEMA}')
    return actual_version


def _job_name(task_id, *, recovery=False):
    """Keep submission reconciliation specific to one task and operation."""
    operation = 'recovery' if recovery else 'shallower'
    return f'biomero-{operation}-{task_id}'


def build_command(client, output, sif, manifest, task_id, *, recovery=False,
                  image=None):
    """Build shallowing or recovery using the same helper resource policy.

    Paths refer to the remote filesystem. The result directory is writable in
    the container; the canonical input manifest is mounted read-only.
    """
    quote = shlex.quote
    image = image if image is not None else client.remote_shallower_image
    if (isinstance(client.remote_shallower_workers, bool)
            or not isinstance(client.remote_shallower_workers, int)
            or client.remote_shallower_workers < 1):
        raise ValueError('Result shallower worker count must be positive')
    params = client.get_shallower_job_params()
    runtime = ('runtime=$(command -v apptainer || command -v singularity); '
               'test -n "$runtime"; exec "$runtime" exec --containall --cleanenv '
               '--env SLURM_JOB_ID="$SLURM_JOB_ID" '
               f'--bind {quote(output + ":/results:rw")} '
               f'--bind {quote(manifest + ":/canonical.json:ro")} '
               f'{quote(sif)} biomero-shallower normalize-tree '
               '--returned-zarr /results --canonical-inputs /canonical.json '
               '--contract-version 1 --failure-policy keep-full '
               '--report /results/.biomero-shallow-batch.json '
               f'--identity-workers {client.remote_shallower_workers} '
               f'--image {quote(image)} '
               f'--task-id {quote(task_id)}')
    if recovery:
        runtime += ' --recover-only'
    return ('sbatch --parsable --export=NONE ' + ' '.join(params) +
            f' --job-name={quote(_job_name(task_id, recovery=recovery))}'
            f' --output={quote(manifest + ".%j.log")}' +
            ' --wrap=' + quote(runtime))


def _batch(client, raw, canonical, *, task=None):
    """Parse a batch report and check its input, image and tool version.

    When a task is supplied, its recorded image/version and submission own the
    report; current deployment defaults must not invalidate historical results.
    Import the optional schema dependency only when shallowing is used.
    """
    from biomero_schema.shallower import ShallowBatchReport
    batch = ShallowBatchReport.from_dict(json.loads(raw))
    image = task.params['image'] if task else client.remote_shallower_image
    version = task.task_version if task else client.remote_shallower_version
    if (batch.canonical_inputs != canonical
            or batch.image != image or batch.tool_version != version):
        raise ValueError(
            'Result shallower report does not match configuration/input')
    if any(receipt.image != batch.image
           or receipt.tool_version != batch.tool_version
           for receipt in batch.receipts):
        raise ValueError('Inconsistent helper receipts')
    if task is not None:
        _check_canonical(task, canonical)
        if any(receipt.task_id != task.id or not task.job_ids
               or int(receipt.slurm_job_id) != int(task.job_ids[0])
               for receipt in batch.receipts):
            raise ValueError('Report belongs to a different helper task/job')
    return batch


def _canonical_digest(payload):
    """Fingerprint JSON content independently of key order and whitespace."""
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _check_canonical(task, canonical):
    expected = task.params.get('canonical_sha256')
    if expected and expected != _canonical_digest(canonical.to_dict()):
        raise ValueError('Canonical manifest differs from the recorded task')


def _prepare_manifest(client, manifest, canonical, *, submitted):
    """Verify an existing manifest or publish a new one without overwriting.

    A sibling temporary file is fully uploaded before an atomic hard-link
    publishes it under a lock. Readers never see a partly uploaded manifest.
    Existing content is verified, not replaced on resume.
    """
    quote = shlex.quote
    target = quote(manifest)
    payload = canonical.to_dict()

    def verify(raw):
        if _canonical_digest(json.loads(raw)) != _canonical_digest(payload):
            raise ValueError('Remote canonical manifest differs from workflow input')

    found = client.run_commands([
        f'if test -f {target} && ! test -L {target}; then cat {target}; '
        f'elif test -e {target} || test -L {target}; then exit 1; fi'],
        log_stdout=False)
    if not found.ok:
        raise RuntimeError('Cannot read canonical manifest; output preserved')
    if found.stdout.strip():
        verify(found.stdout)
        return
    if submitted:
        raise RuntimeError('Missing canonical manifest for submitted job; output preserved')

    temporary = manifest + '.' + uuid4().hex + '.tmp'
    state_dir = posixpath.dirname(manifest)
    guards = ' || '.join(
        'test -e ' + quote(posixpath.join(state_dir, operation + suffix))
        for operation in ('shallow', 'recovery')
        for suffix in ('.intent', '.job'))
    script = (
        f'set -eu; if test -L {target}; then exit 1; fi; '
        f'if test -e {target}; then cat {target}; '
        f'elif {guards}; then '
        'echo "Missing manifest after submission intent" >&2; exit 1; '
        f'else ln {quote(temporary)} {target}; cat {target}; fi')
    try:
        client.put(io.StringIO(json.dumps(payload)), temporary)
        published = client.run_commands([
            f'flock -w 30 {quote(manifest + ".lock")} sh -c {quote(script)}'],
            log_stdout=False)
        if not published.ok:
            raise RuntimeError('Cannot publish canonical manifest; output preserved')
        verify(published.stdout)
    finally:
        # Only the unique staging file owned by this call is removed.
        try:
            cleanup = client.run_commands(['rm -f -- ' + quote(temporary)])
            if not cleanup.ok:
                logger.warning('Could not remove manifest staging file %s', temporary)
        except Exception:
            logger.warning('Could not remove manifest staging file %s',
                           temporary, exc_info=True)


def completed_receipts(client, workflow_id, canonical):
    """Read persisted helper receipts without contacting the cluster."""
    if not client.remote_shallow_zarr or not client.track_workflows:
        return ()
    workflow = client.workflowTracker.repository.get(UUID(str(workflow_id)))
    receipts = []
    for task_id in workflow.tasks:
        task = client.workflowTracker.repository.get(task_id)
        if task.task_name == TASK_NAME and task.result_message:
            receipts.extend(_batch(client, task.result_message, canonical,
                                   task=task).receipts)
    return tuple(receipts)


def _submit_once(client, command, state, *, job_name):
    """Submit or adopt a job under a remote filesystem lock.

    ``state`` is the remote filename prefix for the lock, submission intent
    and recorded job ID. Return the job ID, or None for a rejected submission.
    ``job_name`` is the operation-specific identity used for reconciliation,
    not a value inferred by parsing shell commands. Ambiguous submission state
    raises instead of risking a duplicate job.
    """
    # An intent without a job ID is ambiguous: never blindly submit again.
    # The unique job name and sacct allow an administrator to reconcile it.
    quote = shlex.quote
    script = (
        f'if test -s {quote(state + ".job")}; then cat {quote(state + ".job")}; '
        f'elif test -e {quote(state + ".intent")}; then '
        f'jobs=$(sacct -n -X --name={quote(job_name)} --format=JobIDRaw '
        f'-S "$(cat {quote(state + ".intent")})" | awk \'NF {{print $1}}\' | sort -u); '
        'case "$jobs" in ""|*[!0-9]*) '
        'echo "Unresolved shallower submission intent" >&2; exit 75;; esac; '
        f'printf "%s\\n" "$jobs" > {quote(state + ".job")}; printf "%s\\n" "$jobs"; '
        f'else date +%Y-%m-%dT%H:%M:%S > {quote(state + ".intent")}; '
        f'job=$({command}) || {{ rm -f {quote(state + ".intent")}; '
        'printf "NOT_SUBMITTED\\n"; exit 0; }; '
        'job=${job%%;*}; '
        f'printf "%s\\n" "$job" > {quote(state + ".job.tmp")}; '
        f'mv {quote(state + ".job.tmp")} {quote(state + ".job")}; '
        'printf "%s\\n" "$job"; fi'
    )
    result = client.run_commands([
        f'flock -w 30 {quote(state + ".lock")} sh -c {quote(script)}'])
    if result.ok and result.stdout.strip() == 'NOT_SUBMITTED':
        return None
    if not result.ok or not result.stdout.strip().isdigit():
        raise RuntimeError(
            'Shallower submission is unresolved; preserve results and resume '
            'after reconciliation')
    return int(result.stdout.strip())


def _wait(client, job_id, heartbeat=None):
    """Monitor an adopted job without analysis-log or task-status updates.

    The helper task is completed by ``run`` after report validation, not merely
    because its Slurm process exited successfully.
    """
    job = SlurmJob.from_job_id(job_id, slurm_polling_interval=15)
    state = job.wait_for_completion(
        client, heartbeat=heartbeat, track_progress=False, update_task=False,
        strict_status=True)
    return 'COMPLETED' if job.completed() else state


def run(client, data_path, workflow_id, canonical, heartbeat=None):
    """Run or resume shallowing and return the validated batch report.

    Disabled/inapplicable shallowing returns None. Missing images raise an
    actionable setup error; only initialization acquires container images.
    A rejected shallowing submission returns an empty-receipt batch so the
    caller can retain full results. Unresolved submission, recovery or report
    validation raises; callers must not archive potentially incomplete output.
    """
    if not client.remote_shallow_zarr or canonical is None or not canonical.inputs:
        return None
    if not client.track_workflows:
        return None
    tracker = client.workflowTracker
    workflow_id = UUID(str(workflow_id))
    workflow = tracker.repository.get(workflow_id)
    tasks = [tracker.repository.get(task_id) for task_id in workflow.tasks]
    matches = [task for task in tasks
               if task.task_name == TASK_NAME and task.input_data == data_path]
    if len(matches) > 1:
        raise RuntimeError('Ambiguous result shallower tasks')
    if matches:
        _check_canonical(matches[0], canonical)
    if matches and matches[0].result_message:
        return _batch(client, matches[0].result_message, canonical,
                      task=matches[0])
    if matches and matches[0].job_ids:
        report_path = posixpath.join(
            data_path, 'data/out/.biomero-shallow-batch.json')
        found = client.run_commands([
            f'if test -f {shlex.quote(report_path)}; '
            f'then cat {shlex.quote(report_path)}; fi'])
        if found.ok and found.stdout.strip():
            try:
                batch = _batch(client, found.stdout, canonical, task=matches[0])
            except (ValueError, KeyError):
                pass  # Partial/stale report: poll the recorded job below.
            else:
                tracker.complete_task(
                    matches[0].id, json.dumps(batch.to_dict()))
                return batch
    image = matches[0].params['image'] if matches else client.remote_shallower_image
    spec = image_spec(client, image=image)
    if matches:
        spec['destination'] = matches[0].params.get('sif', spec['destination'])
    ready, pending = client._partition_existing_images([spec])
    if pending or not ready:
        raise RuntimeError(
            f'Remote shallower image missing or invalid: {spec["destination"]}. '
            'Run SLURM_Init_environment and verify image setup with '
            'SLURM_check_setup before retrying.')
    expected_version = (
        matches[0].task_version if matches else client.remote_shallower_version
    )
    installed_version = validate_installed_tool(
        client,
        spec['destination'],
        expected_version=expected_version,
    )
    if not matches:
        version = installed_version
        task_id = tracker.add_task_to_workflow(
            workflow_id, TASK_NAME, version, data_path,
            {'image': image, 'contract': 1, 'sif': spec['destination'],
             'canonical_sha256': _canonical_digest(canonical.to_dict())})
        tracker.start_task(task_id)
        task = tracker.repository.get(task_id)
    else:
        task = matches[0]
        task_id = task.id
    sif = task.params.get('sif', spec['destination'])
    state_dir = posixpath.join(data_path, '.biomero-shallower', str(task_id))
    prepared = client.run_commands(['mkdir -p ' + shlex.quote(state_dir)])
    if not prepared.ok:
        raise RuntimeError('Cannot prepare shallower state directory')
    manifest = posixpath.join(state_dir, 'canonical.json')
    _prepare_manifest(client, manifest, canonical, submitted=bool(task.job_ids))
    output = posixpath.join(data_path, 'data/out')
    command = build_command(
        client, output, sif, manifest, str(task_id), image=image)
    job_id = (int(task.job_ids[0]) if task.job_ids
              else _submit_once(client, command,
                                state_dir + '/shallow',
                                job_name=_job_name(task_id)))
    if job_id is None:
        from biomero_schema.shallower import ShallowBatchReport
        batch = ShallowBatchReport(schema=1, canonicalInputs=canonical,
                                   image=image, toolVersion=task.task_version,
                                   result='complete', receipts=())
        tracker.complete_task(task_id, json.dumps(batch.to_dict()))
        logger.warning('Shallower submission rejected; retaining full results')
        return batch
    if not task.job_ids:
        tracker.add_job_id(task_id, job_id)
        task = tracker.repository.get(task_id)
    status = _wait(client, job_id, heartbeat)
    if status != 'COMPLETED':
        # Recover in the same configured image on CPU. Completed stores retain
        # receipts; interrupted stores roll back before any archive is allowed.
        recovery = build_command(client, output, sif, manifest,
                                 str(task_id), recovery=True, image=image)
        # Recorded IDs remain authoritative on resume.
        recovery_id = (int(task.job_ids[1]) if len(task.job_ids) > 1
                       else _submit_once(
                           client, recovery, state_dir + '/recovery',
                           job_name=_job_name(task_id, recovery=True)))
        if recovery_id is None:
            raise RuntimeError(
                'Shallower recovery submission rejected; output preserved')
        if recovery_id not in task.job_ids:
            tracker.add_job_id(task_id, recovery_id)
        if _wait(client, recovery_id, heartbeat) != 'COMPLETED':
            raise RuntimeError(
                'Shallower recovery failed; output preserved for recovery')
    report = client.run_commands([
        'cat ' + shlex.quote(output + '/.biomero-shallow-batch.json')])
    if not report.ok:
        raise RuntimeError('Missing shallower report; cannot archive safely')
    batch = _batch(client, report.stdout, canonical, task=task)
    tracker.complete_task(task_id, json.dumps(batch.to_dict()))
    return batch
