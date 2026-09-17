"""Mocked switch-driver boundaries; Nix/systemd never execute."""
import hashlib
import json
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

DRIVER = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16/production-preparation/activate-system.py')
SOURCE = '/nix/store/z689qy41inndill3d92ym8im852x3649-source'
OLD = '/nix/store/r29kapzbjk0ch5h0vkk288jd47dvg90j-nixos-system-swingset-lxc-25.11.20260630.b6018f8'
NEW = '/nix/store/sx7lpr80cx0n9vzsi3cwz9nxawqc4p1i-nixos-system-swingset-lxc-25.11.20260630.b6018f8'
OPS = '/var/lib/swingset/operations/h16-event-preservation-release-20260916'
SOURCE_SHA = '0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb'


@pytest.mark.parametrize('scenario', ['success', 'activation_failure', 'stop_failure', 'gate_mismatch'])
def test_switch_boundary(tmp_path, monkeypatch, scenario):
    assert hashlib.sha256(DRIVER.read_bytes()).hexdigest() == 'db4cd1a26e3f1c593570316759b779e44f98cf1d691b1b32872a35416e81cf2e'
    pointers = {'/run/current-system': OLD, '/nix/var/nix/profiles/system': OLD}
    calls = []
    gate = b'{"actual":"reviewed gate"}'
    build = dict(passed=True, semantic_publication_preflight=True, source=SOURCE,
                 state='/var/tmp/mock-state', candidate='/var/tmp/mock-state/candidates/candidate',
                 candidate_id='candidate', manifest_hash='manifest')
    audit = {**build, 'checks': {'independent': True}}
    preflight = {'preflight_passed': True, 'gate_sha256': hashlib.sha256(gate).hexdigest(),
                 'gate': {'source_receipt_sha256': SOURCE_SHA}}
    if scenario == 'gate_mismatch':
        preflight['gate_sha256'] = 'different'
    files = {OPS+'/gate.json': gate, SOURCE+'/h16-source.json': b'synthetic pinned source receipt',
             **{OPS+'/'+name: json.dumps(value).encode() for name,value in
                [('preflight.json',preflight),('scratch-build.json',build),('scratch-audit.json',audit)]}}
    real_read, real_text, real_open = Path.read_bytes, Path.read_text, Path.open
    real_exists, real_is_file, real_resolve = Path.exists, Path.is_file, Path.resolve
    real_sha = hashlib.sha256
    def read(path):
        return files[str(path)] if str(path) in files else real_read(path)
    def read_text(path, *args, **kwargs):
        if str(path) == NEW+'/etc/systemd/system/swingset-cycle.service':
            return 'ExecStart='+SOURCE+'/service'
        return real_text(path, *args, **kwargs)
    def opened(path, *args, **kwargs):
        if str(path).startswith(OPS+'/'):
            return real_open(tmp_path/path.name, *args, **kwargs)
        return real_open(path, *args, **kwargs)
    def exists(path):
        if str(path).startswith(OPS+'/'):
            return real_exists(tmp_path/path.name)
        return real_exists(path)
    def sha(value=b'', *args, **kwargs):
        if value == b'synthetic pinned source receipt':
            return SimpleNamespace(hexdigest=lambda: SOURCE_SHA)
        return real_sha(value, *args, **kwargs)
    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ['systemctl', 'show']:
            return SimpleNamespace(stdout='inactive\n', returncode=0)
        if command[0] == 'nix-env':
            pointers['/nix/var/nix/profiles/system'] = NEW
        elif command == [NEW+'/bin/switch-to-configuration', 'switch']:
            pointers['/run/current-system'] = NEW
            if scenario == 'activation_failure':
                raise subprocess.CalledProcessError(1, command)
        elif command[:2] == ['systemctl', 'stop']:
            assert len(command[2:]) == 6
            if scenario == 'stop_failure':
                raise subprocess.CalledProcessError(1, command)
        else:
            raise AssertionError('unmocked command: '+repr(command))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(Path, 'read_bytes', read)
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setattr(Path, 'exists', exists)
    monkeypatch.setattr(Path, 'is_file', lambda path: True if str(path)=='/var/lib/swingset/operator-hold' else real_is_file(path))
    monkeypatch.setattr(Path, 'resolve', lambda path, *a, **k: Path(pointers[str(path)]) if str(path) in pointers else real_resolve(path,*a,**k))
    monkeypatch.setattr(hashlib, 'sha256', sha)
    monkeypatch.setattr(subprocess, 'run', run)
    if scenario == 'gate_mismatch':
        with pytest.raises(AssertionError):
            runpy.run_path(str(DRIVER), run_name='__main__')
        assert all(command[:2] == ['systemctl','show'] for command in calls)
        assert not list(tmp_path.iterdir())
        return
    if scenario == 'success':
        runpy.run_path(str(DRIVER), run_name='__main__')
    else:
        with pytest.raises(subprocess.CalledProcessError):
            runpy.run_path(str(DRIVER), run_name='__main__')
    intent = json.loads((tmp_path/'deployment-intent.json').read_bytes())
    receipt = json.loads((tmp_path/'deployment.json').read_bytes())
    assert intent['passed'] is False and receipt['finished_at']
    assert receipt['passed'] is (scenario=='success')
    assert receipt['activation_commands_succeeded'] is (scenario!='activation_failure')
    assert any(command[:2] == ['systemctl','stop'] for command in calls)
    if scenario == 'activation_failure':
        assert pointers['/run/current-system'] == pointers['/nix/var/nix/profiles/system'] == NEW
        assert receipt['activation_error'] == 'CalledProcessError'
    if scenario == 'stop_failure':
        assert receipt['hold_or_verification_error'] == 'CalledProcessError'
    assert (tmp_path/'deployment.json').stat().st_mode & 0o777 == 0o600
