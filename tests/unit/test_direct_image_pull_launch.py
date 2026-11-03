"""Exercise the actual direct-pull launcher with multiple Bash workers."""
import os
from pathlib import Path
import shutil
import subprocess
import time
from unittest.mock import Mock

import pytest
from fabric import Result

from .test_slurm_client import slurm_client, set_env_vars  # noqa: F401


@pytest.mark.skipif(os.name != 'posix' or not shutil.which('bash'),
                    reason='requires POSIX Bash background processes')
def test_direct_launcher_starts_all_images_without_waiting(slurm_client,
                                                          tmp_path):
    client = slurm_client
    root = tmp_path / 'scripts with spaces'
    root.mkdir()
    client.slurm_script_path = str(root)
    client.slurm_image_pull_via_sbatch = False
    specs = [dict(kind='workflow', name=f'image{index}', version='v1',
                  source_type='docker', source=f'example/image{index}:v1',
                  destination=str(tmp_path / f'image {index}.sif'))
             for index in range(3)]
    client._partition_existing_images = Mock(return_value=([], specs))
    release = root / 'release-workers'

    def put(source, destination):
        contents = source.read()
        if destination.endswith('/pull_images.sh'):
            # Lightweight real processes stand in for image acquisition; keep
            # the production launcher, quoting, staging and publication path.
            contents = '''#!/bin/bash
set -eu
printf '%s\\n' "$SLURM_ARRAY_TASK_ID" > "$2/started-$SLURM_ARRAY_TASK_ID"
for ((attempt=0; attempt<500; attempt++)); do
    [ -e "$(dirname "$0")/release-workers" ] && break
    sleep 0.01
done
printf '%s\\n' "$SLURM_ARRAY_TASK_ID" > "$2/done-$SLURM_ARRAY_TASK_ID"
'''
        Path(destination).write_text(contents)

    def run(command, env=None, **kwargs):
        result = subprocess.run(['bash', '-c', command], capture_output=True,
                                text=True, timeout=5,
                                env={**os.environ, **(env or {})})
        return Result(connection=None, command=command, stdout=result.stdout,
                      stderr=result.stderr, exited=result.returncode)

    client.put = put
    client.run = run
    try:
        assert client._submit_image_pull_array(specs) is None
        status = Path((root / 'image-pulls' / 'latest').read_text().strip())
        deadline = time.monotonic() + 4
        while len(list(status.glob('started-*'))) < 3:
            assert time.monotonic() < deadline, 'not all image workers started'
            time.sleep(0.01)
        assert not list(status.glob('done-*')), 'launcher waited for image work'
        assert {p.read_text().strip() for p in status.glob('started-*')} == {
            '0', '1', '2'}
        metadata = dict(line.split('\t', 1) for line in
                        (status / 'submission.meta').read_text().splitlines())
        assert metadata['array_job_id'] == ''
        assert metadata['pending'] == metadata['expected'] == '3'
    finally:
        release.touch()
