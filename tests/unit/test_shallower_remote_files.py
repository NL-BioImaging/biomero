"""Execute shallower remote commands on a temporary POSIX filesystem, no HPC."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from biomero.remote_shallower import _prepare_manifest, _submit_once


pytestmark = pytest.mark.skipif(
    os.name == 'nt' or not shutil.which('flock'),
    reason='Generated remote commands require POSIX flock (Linux CI)')


class LocalRemoteClient:
    def __init__(self, environment=None):
        self.environment = dict(os.environ, **(environment or {}))
        self.uploads = []

    def run_commands(self, commands, **kwargs):
        result = subprocess.run(' && '.join(commands), shell=True,
                                executable='/bin/sh', capture_output=True,
                                text=True, env=self.environment, timeout=10)
        return SimpleNamespace(ok=result.returncode == 0, stdout=result.stdout,
                               stderr=result.stderr)

    def put(self, stream, path):
        self.uploads.append(path)
        Path(path).write_text(stream.read(), encoding='utf-8')


def test_manifest_is_published_once_and_not_rewritten(tmp_path):
    client = LocalRemoteClient()
    manifest = tmp_path / 'canonical.json'
    canonical = SimpleNamespace(to_dict=lambda: {'inputs': [1]})
    _prepare_manifest(client, str(manifest), canonical, submitted=False)
    original = manifest.stat()
    _prepare_manifest(client, str(manifest), canonical, submitted=True)
    assert len(client.uploads) == 1
    assert client.uploads[0] != str(manifest)
    assert manifest.stat().st_ino == original.st_ino
    assert manifest.stat().st_mtime_ns == original.st_mtime_ns
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('marker', ['shallow.intent', 'shallow.job',
                                   'recovery.intent', 'recovery.job'])
def test_missing_manifest_after_intent_is_never_recreated(tmp_path, marker):
    (tmp_path / marker).write_text('123')
    manifest = tmp_path / 'canonical.json'
    canonical = SimpleNamespace(to_dict=lambda: {'inputs': [1]})
    with pytest.raises(RuntimeError, match='manifest'):
        _prepare_manifest(LocalRemoteClient(), str(manifest), canonical,
                          submitted=False)
    assert not manifest.exists()
    assert not list(tmp_path.glob('*.tmp'))


def test_interrupted_upload_does_not_publish_partial_manifest(tmp_path):
    class InterruptedClient(LocalRemoteClient):
        def put(self, stream, path):
            Path(path).write_text('{"inputs":')
            raise OSError('upload interrupted')

    manifest = tmp_path / 'canonical.json'
    canonical = SimpleNamespace(to_dict=lambda: {'inputs': [1]})
    with pytest.raises(OSError, match='interrupted'):
        _prepare_manifest(InterruptedClient(), str(manifest), canonical,
                          submitted=False)
    assert not manifest.exists()
    assert not list(tmp_path.glob('*.tmp'))


def test_concurrent_publishers_cannot_overwrite_a_different_manifest(tmp_path):
    barrier = Barrier(2)

    class ConcurrentClient(LocalRemoteClient):
        def put(self, stream, path):
            super().put(stream, path)
            barrier.wait(timeout=5)

    manifest = tmp_path / 'canonical.json'

    def publish(value):
        try:
            _prepare_manifest(ConcurrentClient(), str(manifest),
                              SimpleNamespace(to_dict=lambda: {'value': value}),
                              submitted=False)
            return value
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, (1, 2)))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1
    assert json.loads(manifest.read_text()) == {'value': winners[0]}
    assert not list(tmp_path.glob('*.tmp'))


def test_recovery_reconciliation_uses_only_its_own_name(tmp_path):
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    sacct = fake_bin / 'sacct'
    sacct.write_text('#!/bin/sh\ncase "$*" in\n'
                     '*--name=biomero-recovery-task*) echo 124;;\n'
                     '*) echo 123;;\nesac\n')
    sacct.chmod(0o755)
    state = str(tmp_path / 'recovery')
    Path(state + '.intent').write_text('2026-01-01T00:00:00')
    client = LocalRemoteClient({'PATH': str(fake_bin) + ':' + os.environ['PATH']})
    assert _submit_once(client, 'exit 99', state,
                        job_name='biomero-recovery-task') == 124
    assert Path(state + '.job').read_text().strip() == '124'


def test_old_unresolved_recovery_intent_is_not_resubmitted(tmp_path):
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    sacct = fake_bin / 'sacct'
    sacct.write_text('#!/bin/sh\nexit 0\n')
    sacct.chmod(0o755)
    state = str(tmp_path / 'recovery')
    Path(state + '.intent').write_text('2026-01-01T00:00:00')
    client = LocalRemoteClient({'PATH': str(fake_bin) + ':' + os.environ['PATH']})
    marker = tmp_path / 'should-not-submit'
    with pytest.raises(RuntimeError, match='unresolved'):
        _submit_once(client, f'touch {marker}', state,
                     job_name='biomero-recovery-task')
    assert not Path(state + '.job').exists()
    assert not marker.exists()
