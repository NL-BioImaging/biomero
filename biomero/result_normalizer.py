"""Optional CPU result normalization with durable Slurm submission adoption."""

import hashlib
import io
import json
import logging
import posixpath
import re
import shlex
import time
from uuid import UUID

TASK_NAME = "_SLURM_Result_Normalizer"
logger = logging.getLogger(__name__)


def image_spec(client):
    image = client.result_normalizer_image.removeprefix('docker://')
    if '@sha256:' in image:
        source, digest = image.split('@sha256:', 1)
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid image digest')
        version = 'sha256:' + digest
    else:
        source, separator, version = image.rpartition(':')
        if not separator or '/' in version or version in ('latest', 'main', 'master', ''):
            raise ValueError('Result normalizer requires an explicit image version')
    return {
        'kind': 'result-normalizer', 'name': 'biomero-shallower',
        'version': version, 'source_type': 'registry', 'source': source,
        'destination': posixpath.join(client.slurm_converters_path,
                                     'shallower-' + hashlib.sha256(image.encode()).hexdigest()[:24] + '.sif'),
    }


def build_command(client, output, sif, manifest, task_id, *, recovery=False):
    quote = shlex.quote
    if (isinstance(client.result_normalizer_workers, bool)
            or not isinstance(client.result_normalizer_workers, int)
            or client.result_normalizer_workers < 1):
        raise ValueError('Result normalizer worker count must be positive')
    params = [f'--cpus-per-task={client.result_normalizer_workers}']
    if client.result_normalizer_partition:
        params.append('--partition=' + quote(client.result_normalizer_partition))
    # Explicit CPU allowlist: never inherit workflow GPU or array flags.
    for param in client.slurm_global_job_params:
        flag, _, value = param.strip().partition('=')
        if flag in ('--mem', '--time', '--account', '--reservation', '--qos'):
            params.append(flag + '=' + quote(value))
    runtime = ('runtime=$(command -v apptainer || command -v singularity); '
               'test -n "$runtime"; exec "$runtime" exec --containall --cleanenv '
               '--env SLURM_JOB_ID="$SLURM_JOB_ID" '
               f'--bind {quote(output + ":/results:rw")} '
               f'--bind {quote(manifest + ":/canonical.json:ro")} '
               f'{quote(sif)} biomero-shallower normalize-tree '
               '--returned-zarr /results --canonical-inputs /canonical.json '
               '--contract-version 1 --failure-policy keep-full '
               '--report /results/.biomero-shallow-batch.json '
               f'--identity-workers {client.result_normalizer_workers} '
               f'--image {quote(client.result_normalizer_image)} '
               f'--task-id {quote(task_id)}')
    if recovery:
        runtime += ' --recover-only'
    return ('sbatch --parsable --export=NONE ' + ' '.join(params) +
            f' --job-name=biomero-normalizer-{task_id} --output={quote(manifest + ".%j.log")}' +
            ' --wrap=' + quote(runtime))


def _batch(client, raw, canonical):
    from biomero_schema.shallower import ShallowBatchReport
    batch = ShallowBatchReport.from_dict(json.loads(raw))
    if (batch.canonical_inputs != canonical or batch.image != client.result_normalizer_image
            or batch.tool_version != client.result_normalizer_version):
        raise ValueError('Result normalizer report does not match configuration/input')
    if any(receipt.image != batch.image or receipt.tool_version != batch.tool_version
           for receipt in batch.receipts):
        raise ValueError('Inconsistent helper receipts')
    return batch


def completed_receipts(client, workflow_id, canonical):
    if not client.remote_shallow_zarr or not client.track_workflows:
        return ()
    workflow = client.workflowTracker.repository.get(UUID(str(workflow_id)))
    receipts = []
    for task_id in workflow.tasks:
        task = client.workflowTracker.repository.get(task_id)
        if task.task_name == TASK_NAME and task.result_message:
            receipts.extend(_batch(client, task.result_message, canonical).receipts)
    return tuple(receipts)


def _submit_once(client, command, state):
    # An intent without a job ID is ambiguous: never blindly submit again.
    # The unique job name and sacct allow an administrator to reconcile it.
    quote = shlex.quote
    name = re.search(r'--job-name=(\S+)', command).group(1)
    script = (
        f'if test -s {quote(state + ".job")}; then cat {quote(state + ".job")}; '
        f'elif test -e {quote(state + ".intent")}; then '
        f'jobs=$(sacct -n -X --name={quote(name)} --format=JobIDRaw '
        f'-S "$(cat {quote(state + ".intent")})" | awk \'NF {{print $1}}\' | sort -u); '
        'case "$jobs" in ""|*[!0-9]*) '
        'echo "Unresolved normalizer submission intent" >&2; exit 75;; esac; '
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
        raise RuntimeError('Normalizer submission is unresolved; preserve results and resume after reconciliation')
    return int(result.stdout.strip())


def _wait(client, job_id):
    while True:
        statuses, _ = client.check_job_status([job_id])
        state = statuses.get(job_id, 'UNKNOWN')
        if state in ('PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED', 'REQUEUED'):
            time.sleep(15)
            continue
        if state == 'UNKNOWN':
            raise RuntimeError('Normalizer job state unavailable; retry retrieval to adopt it')
        return state


def run(client, data_path, workflow_id, canonical):
    if not client.remote_shallow_zarr or canonical is None or not canonical.inputs:
        return None
    if not client.track_workflows:
        return None
    tracker = client.workflowTracker
    workflow_id = UUID(str(workflow_id))
    workflow = tracker.repository.get(workflow_id)
    tasks = [tracker.repository.get(task_id) for task_id in workflow.tasks]
    matches = [task for task in tasks if task.task_name == TASK_NAME and task.input_data == data_path]
    if len(matches) > 1:
        raise RuntimeError('Ambiguous result normalizer tasks')
    if matches and matches[0].result_message:
        return _batch(client, matches[0].result_message, canonical)
    if matches and matches[0].job_ids:
        report_path = posixpath.join(data_path, 'data/out/.biomero-shallow-batch.json')
        found = client.run_commands([
            f'if test -f {shlex.quote(report_path)}; then cat {shlex.quote(report_path)}; fi'])
        if found.ok and found.stdout.strip():
            try:
                batch = _batch(client, found.stdout, canonical)
                if any(receipt.task_id != matches[0].id
                       or int(receipt.slurm_job_id) != int(matches[0].job_ids[0])
                       for receipt in batch.receipts):
                    raise ValueError('Report belongs to another task')
            except (ValueError, KeyError):
                pass  # Partial/stale report: poll the recorded job below.
            else:
                tracker.complete_task(matches[0].id, json.dumps(batch.to_dict()))
                return batch
    spec = image_spec(client)
    if not matches:
        # Acquisition cannot modify result data. Failure here safely keeps full output.
        try:
            pull_id = client._submit_image_pull_array([spec])
            if pull_id and _wait(client, pull_id) != 'COMPLETED':
                return None
            ready, pending = client._partition_existing_images([spec])
            if pending or not ready:
                return None
        except Exception:
            logger.exception('Result normalizer image unavailable; retaining full results')
            return None
        task_id = tracker.add_task_to_workflow(
            workflow_id, TASK_NAME, client.result_normalizer_version, data_path,
            {'image': client.result_normalizer_image, 'contract': 1})
        tracker.start_task(task_id)
        task = tracker.repository.get(task_id)
    else:
        task = matches[0]
        task_id = task.id
        if task.params['image'] != client.result_normalizer_image:
            raise ValueError('Resume requires the originally configured helper image')
    state_dir = posixpath.join(data_path, '.biomero-normalizer', str(task_id))
    client.run_commands(['mkdir -p ' + shlex.quote(state_dir)])
    manifest = posixpath.join(state_dir, 'canonical.json')
    client.put(io.StringIO(json.dumps(canonical.to_dict())), manifest)
    output = posixpath.join(data_path, 'data/out')
    command = build_command(client, output, spec['destination'], manifest, str(task_id))
    job_id = int(task.job_ids[0]) if task.job_ids else _submit_once(client, command, state_dir + '/normalize')
    if job_id is None:
        from biomero_schema.shallower import ShallowBatchReport
        batch = ShallowBatchReport(schema=1, canonicalInputs=canonical,
                                   image=client.result_normalizer_image,
                                   toolVersion=client.result_normalizer_version,
                                   result='complete', receipts=())
        tracker.complete_task(task_id, json.dumps(batch.to_dict()))
        logger.warning('Normalizer submission rejected; retaining full results')
        return batch
    if not task.job_ids:
        tracker.add_job_id(task_id, job_id)
    status = _wait(client, job_id)
    if status != 'COMPLETED':
        # Recover in the same configured image on CPU. Completed stores retain
        # receipts; interrupted stores roll back before any archive is allowed.
        recovery = build_command(client, output, spec['destination'], manifest,
                                 str(task_id), recovery=True)
        recovery_id = _submit_once(client, recovery, state_dir + '/recovery')
        if recovery_id is None:
            raise RuntimeError('Normalizer recovery submission rejected; output preserved')
        if recovery_id not in task.job_ids:
            tracker.add_job_id(task_id, recovery_id)
        if _wait(client, recovery_id) != 'COMPLETED':
            raise RuntimeError('Normalizer recovery failed; output preserved for recovery')
    report = client.run_commands(['cat ' + shlex.quote(output + '/.biomero-shallow-batch.json')])
    if not report.ok:
        raise RuntimeError('Missing normalizer report; cannot archive safely')
    batch = _batch(client, report.stdout, canonical)
    if any(receipt.task_id != task_id or int(receipt.slurm_job_id) != job_id
           for receipt in batch.receipts):
        raise ValueError('Report belongs to a different helper task/job')
    tracker.complete_task(task_id, json.dumps(batch.to_dict()))
    return batch
