import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('supervisor', '/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16/production-preparation/supervise-initialization.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


@pytest.fixture
def args(tmp_path):
    return SimpleNamespace(gate=tmp_path/'gate.json', gate_sha256='1'*64,
        marker=tmp_path/'marker.json', marker_sha256='2'*64,
        driver=tmp_path/'driver.py', driver_sha256=m.DRIVER_SHA,
        session='reviewed-001', max_invocations=12)


@pytest.fixture
def authority():
    return {'hold': {'services': 'inactive'}, 'baseline': {'commit': 'published'}}


def receipt(args, authority, status='bounded_stop', n=2):
    result = {'format': 'h16-production-initialization-receipt-v1', 'mode': 'run',
        'gate_sha256': args.gate_sha256, 'marker_sha256': args.marker_sha256,
        'driver_sha256': args.driver_sha256, 'source_receipt_sha256': m.SOURCE_RECEIPT,
        'status': status, 'attempted': n, 'completed': n, 'outcomes': {'succeeded': n},
        'network_requests': 0, 'parse_executed': False, 'build_executed': False,
        'published': False, 'finished_at': '2026-09-14T00:00:00+00:00',
        'initial_hold': authority, 'final_hold': authority,
        **{key: True for key in m.PRESERVATION}}
    if status == 'current':
        result['unfinished_by_scope'] = {}
    return result


class Fake:
    def __init__(self, args, authority, results, *, change_at=None, exit_code=0):
        self.args, self.initial, self.results = args, authority, iter(results)
        self.change_at, self.exit_code = change_at, exit_code
        self.calls = 0
        self.launched, self.stopped, self.waits = [], [], []
        self.reads = 0

    def authority(self):
        self.calls += 1
        if self.change_at and self.calls >= self.change_at:
            raise ValueError('operator controls changed')
        return self.initial

    def launch(self, cmd, log):
        self.launched.append(cmd)
        value = next(self.results)
        m.write_new(Path(cmd[cmd.index('--output')+1]), value)
        return object()

    def wait(self, process, seconds):
        self.waits.append(seconds)
        # One live monitor boundary then completion, including stop path.
        self.reads += 1
        return None if self.reads % 2 else self.exit_code

    def stop(self, unit):
        self.stopped.append(unit)

    def settled(self, unit):
        return {'unit': {'ActiveState': 'inactive', 'MainPID': '0'}, 'writer_lock_released': True}


def test_two_clean_batches_then_explicit_current(args, authority, tmp_path):
    fake = Fake(args, authority, [receipt(args, authority), receipt(args, authority, 'current', 0)])
    assert m.supervise(args, fake, tmp_path) == 'current'
    final = json.loads((tmp_path/'final.json').read_text())
    assert final['current'] is True and final['completed'] == 2 and final['invocations'] == 2
    assert len(fake.launched) == 2 and fake.waits == [20]*4 and not fake.stopped
    assert len(list(tmp_path.glob('batch-*-start.json'))) == 2
    assert len(list(tmp_path.glob('batch-*-final.json'))) == 2
    assert all((p.stat().st_mode & 0o077) == 0 for p in tmp_path.glob('*.json'))
    command = fake.launched[0]
    assert command[0:2] == ['systemd-run', '--wait']
    for value in ('--property=User=swingset', '--property=Group=swingset', '--property=RuntimeMaxSec=1500'):
        assert value in command
    assert command[-2:] == ['--max-seconds', '900']
    assert command[command.index(str(args.driver))+1] == 'run'
    assert not any(value in command for value in ('prepare', 'continue', '--continuation-review'))
    assert '--setenv=PYTHONPATH='+str(m.SOURCE/'src')+':'+str(m.SOURCE) in command
    assert '--setenv=SWINGSET_REVISION=uncommitted:'+m.SOURCE.name in command
    assert '--setenv=PYTHONDONTWRITEBYTECODE=1' in command


@pytest.mark.parametrize('key,value', [
    ('status','no_runnable_progress'), ('status','operator_control_changed'), ('status','interrupted'),
    ('attempted',3), ('completed',0), ('network_requests',1), ('network_requests',False),
    ('parse_executed',True), ('build_executed',True), ('published',True),
    ('controls_unchanged',False), ('protected_unchanged',None), ('parse_tokens_unchanged',False),
    ('input_authority_unchanged',False), ('gate_sha256','bad'), ('marker_sha256','bad'),
    ('source_receipt_sha256','bad'), ('driver_sha256','bad'), ('mode','prepare'),
    ('outcomes',{'inputs_changed':2}), ('outcomes',{'succeeded':1}), ('outcomes',{'succeeded':True}),
    ('final_hold',{'changed':True}), ('initial_hold',{}), ('finished_at',None), ('error',{'type':'Error'}),
])
def test_bad_receipt_stops_without_next_batch(args, authority, tmp_path, key, value):
    bad = receipt(args, authority)
    bad[key] = value
    fake = Fake(args, authority, [bad])
    with pytest.raises(ValueError):
        m.supervise(args, fake, tmp_path)
    assert len(fake.launched) == 1
    assert json.loads((tmp_path/'final.json').read_text())['current'] is False
    assert (tmp_path/'batch-001-final.json').is_file()


@pytest.mark.parametrize('scope', [None, {'link/event':1}])
def test_current_requires_explicit_empty_unfinished(args, authority, scope):
    result = receipt(args, authority, 'current')
    if scope is None:
        result.pop('unfinished_by_scope')
    else:
        result['unfinished_by_scope'] = scope
    with pytest.raises(ValueError, match='explicit empty'):
        m.validate_receipt(result, args, authority, 0)


def test_actual_success_label_and_ledger_success_label(args, authority):
    result = receipt(args, authority)
    for label in ('succeeded', 'output_committed'):
        result['outcomes'] = {label:2}
        assert m.validate_receipt(result, args, authority, 0) == 'bounded_stop'


def test_nonzero_exit_refuses_even_success_receipt(args, authority, tmp_path):
    fake = Fake(args, authority, [receipt(args, authority)], exit_code=1)
    with pytest.raises(ValueError, match='exited unsuccessfully'):
        m.supervise(args, fake, tmp_path)
    assert len(fake.launched) == 1


def test_change_during_active_unit_stops_only_own_unit(args, authority, tmp_path):
    fake = Fake(args, authority, [receipt(args, authority)], change_at=3)
    with pytest.raises(ValueError, match='controls changed'):
        m.supervise(args, fake, tmp_path)
    assert fake.stopped == ['swingset-h16-init-reviewed-001-001.service']
    assert len(fake.launched) == 1
    final = json.loads((tmp_path/'final.json').read_text())
    assert not final['current'] and final['batches'][0]['settled']['writer_lock_released']


def test_cap_is_not_current(args, authority, tmp_path):
    fake = Fake(args, authority, [receipt(args, authority)]*12)
    assert m.supervise(args, fake, tmp_path) == 'invocation_cap'
    final = json.loads((tmp_path/'final.json').read_text())
    assert final['completed'] == 24 and final['invocations'] == 12 and not final['current']


def test_new_receipts_never_overwrite(args, authority, tmp_path):
    p = tmp_path/'preflight.json'
    p.write_text('old evidence')
    fake = Fake(args, authority, [])
    with pytest.raises(FileExistsError):
        m.supervise(args, fake, tmp_path)
    assert p.read_text() == 'old evidence' and not fake.launched


def test_reused_initializer_output_never_launches(args, authority, tmp_path):
    (tmp_path/'initializer-001.json').write_text('old evidence')
    fake = Fake(args, authority, [])
    with pytest.raises(ValueError, match='must be new'):
        m.supervise(args, fake, tmp_path)
    assert not fake.launched and (tmp_path/'initializer-001.json').read_text() == 'old evidence'


def test_changed_hash_symlink_and_directory_rejected(tmp_path):
    p = tmp_path/'reviewed.json'
    p.write_text('{}')
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    assert m.checked(p, digest) == b'{}'
    p.write_text('{"changed":true}')
    with pytest.raises(ValueError, match='changed'):
        m.checked(p, digest)
    link = tmp_path/'alias.json'
    link.symlink_to(p)
    with pytest.raises(ValueError, match='nonsymlink'):
        m.checked(link, digest)
    with pytest.raises((ValueError, IsADirectoryError)):
        m.regular(tmp_path)


def test_gate_marker_exact_pins_and_tampering(args, monkeypatch):
    args.driver.write_text('# unchanged initializer surrogate')
    args.driver_sha256 = hashlib.sha256(args.driver.read_bytes()).hexdigest()
    monkeypatch.setattr(m, 'DRIVER_SHA', args.driver_sha256)
    fields = {'state':str(m.STATE),'source':str(m.SOURCE),'source_receipt_sha256':m.SOURCE_RECEIPT,
              'input_bundle_hash':m.BUNDLE,'driver_sha256':args.driver_sha256}
    gate = dict(fields, format='h16-production-initialization-gate-v1', stages=['project','link'], capture_accept_authorized=True)
    args.gate.write_text(json.dumps(gate))
    args.gate_sha256 = hashlib.sha256(args.gate.read_bytes()).hexdigest()
    marker = dict(fields, format='h16-production-initialization-v1', gate_sha256=args.gate_sha256,
                  controls={'pause':'hash'}, prepared_input_authority={'accepted':'hash'})
    args.marker.write_text(json.dumps(marker))
    args.marker_sha256 = hashlib.sha256(args.marker.read_bytes()).hexdigest()
    assert m.review(args) == (gate, marker)
    marker['input_bundle_hash'] = 'wrong'
    args.marker.write_text(json.dumps(marker))
    args.marker_sha256 = hashlib.sha256(args.marker.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='authority mismatch'):
        m.review(args)


def test_dead_launcher_stops_its_still_active_service(args, authority, tmp_path):
    fake = Fake(args, authority, [receipt(args, authority)], exit_code=-9)
    real_settled = fake.settled
    def settled(unit):
        if not fake.stopped:
            raise ValueError('initializer unit is not inactive')
        return real_settled(unit)
    fake.settled = settled
    with pytest.raises(ValueError, match='not inactive'):
        m.supervise(args, fake, tmp_path)
    assert fake.stopped == ['swingset-h16-init-reviewed-001-001.service']
    assert json.loads((tmp_path/'batch-001-final.json').read_text())['settled']['writer_lock_released']


def test_settled_checks_actual_lock_and_process_state(args, tmp_path, monkeypatch):
    import fcntl
    import subprocess
    monkeypatch.setattr(m, 'STATE', tmp_path)
    lockfile = tmp_path/'state.lock'
    lockfile.touch()
    calls = []
    def command(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout='LoadState=loaded\nActiveState=inactive\nMainPID=0\n')
    monkeypatch.setattr(subprocess, 'run', command)
    backend = object.__new__(m.Backend)
    with lockfile.open('r+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            backend.settled('only-own.service')
    assert backend.settled('only-own.service')['writer_lock_released']
    assert all(cmd[0:3] == ['systemctl','show','only-own.service'] for cmd in calls)


def test_systemd_launch_redirects_private_exclusive_log(args, tmp_path, monkeypatch):
    calls = []
    def popen(cmd, **kwargs):
        import os
        calls.append((cmd, os.fstat(kwargs['stdout']).st_mode & 0o777))
        return 'process'
    monkeypatch.setattr(m.subprocess, 'Popen', popen)
    backend = object.__new__(m.Backend)
    log = tmp_path/'unit.log'
    assert backend.launch(['fixed-command'], log) == 'process'
    assert calls == [(['fixed-command'],0o600)]
    with pytest.raises(FileExistsError):
        backend.launch(['fixed-command'], log)


@pytest.mark.parametrize('interruption', [KeyboardInterrupt('SIGTERM during launch'), OSError('launcher could not start')])
def test_launch_failure_always_stops_known_unit_and_preserves_batch(args, authority, tmp_path, interruption):
    class LaunchFailure(Fake):
        active = False
        def launch(self, cmd, log):
            self.launched.append(cmd)
            # The interruption is after systemd has started a unit but before Popen returns.
            self.active = isinstance(interruption, KeyboardInterrupt)
            raise interruption
        def stop(self, unit):
            self.stopped.append(unit)
            self.active = False
        def wait(self, process, seconds):
            pytest.fail('no process object was returned; never wait on None')
        def settled(self, unit):
            assert self.stopped, 'known unit must be stopped even when no process was returned'
            assert not self.active
            return {'unit': {'LoadState': 'not-found' if isinstance(interruption, OSError) else 'loaded',
                             'ActiveState':'inactive', 'MainPID':'0'}, 'writer_lock_released':True}
    fake = LaunchFailure(args, authority, [])
    with pytest.raises(type(interruption), match=str(interruption)):
        m.supervise(args, fake, tmp_path)
    own_unit = 'swingset-h16-init-reviewed-001-001.service'
    assert fake.stopped == [own_unit] and len(fake.launched) == 1
    batch = json.loads((tmp_path/'batch-001-final.json').read_text())
    final = json.loads((tmp_path/'final.json').read_text())
    assert batch['unit'] == own_unit and batch['error']['type'] == type(interruption).__name__
    assert batch['stop_exit_code'] is None and batch['settled']['writer_lock_released']
    assert final['current'] is False and final['status'] == 'stopped' and final['invocations'] == 1
    assert (tmp_path/'batch-001-start.json').is_file() and (tmp_path/'stop.json').is_file()


def test_launch_error_stop_accepts_only_confirmed_absent_unit(monkeypatch):
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == 'stop':
            return SimpleNamespace(returncode=5)
        return SimpleNamespace(stdout='LoadState=not-found\nActiveState=inactive\nMainPID=0\n')
    monkeypatch.setattr(m.subprocess, 'run', run)
    backend = object.__new__(m.Backend)
    # Avoid any real state access: settled unit check is separately covered with real temporary flock.
    backend.settled = lambda unit: calls.append(['confirmed-absent',unit])
    backend.stop('only-own.service')
    assert calls == [['systemctl','stop','--no-block','only-own.service'], ['confirmed-absent','only-own.service']]
