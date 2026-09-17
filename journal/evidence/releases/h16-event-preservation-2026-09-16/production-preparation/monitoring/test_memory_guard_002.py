"""Offline tests: run the frozen supervisor's real cleanup, never systemd or production."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('initialization_memory_guard', HERE / 'guard-initialization-002.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def setup(tmp_path, monkeypatch, states, *, anon=10, results=('current',)):
    supervisor = guard.load_supervisor(HERE.parent / 'supervise-initialization.py')
    supervisor.STATE = tmp_path
    (tmp_path / 'state.lock').touch()
    args = SimpleNamespace(driver=tmp_path/'driver.py', driver_sha256='d'*64,
        gate=tmp_path/'gate.json', gate_sha256='g'*64, marker=tmp_path/'marker.json',
        marker_sha256='m'*64, session='memory-test', max_invocations=12)
    authority = {'held': True}
    calls = {'launched': [], 'stopped': [], 'waits': [], 'states': iter(states), 'results': iter(results)}
    def launch(self, cmd, log):
        calls['launched'].append(cmd)
        status = next(calls['results'])
        completed = 0 if status == 'current' else 1
        receipt = {'format': 'h16-production-initialization-receipt-v1', 'mode':'run',
            'gate_sha256':args.gate_sha256, 'marker_sha256':args.marker_sha256,
            'driver_sha256':args.driver_sha256, 'source_receipt_sha256':supervisor.SOURCE_RECEIPT,
            'finished_at':'done', 'network_requests':0, 'parse_executed':False,
            'build_executed':False, 'published':False, 'initial_hold':authority,
            'final_hold':authority, 'attempted':completed, 'completed':completed,
            'outcomes':{'succeeded':completed}, 'status':status,
            **{key:True for key in supervisor.PRESERVATION}}
        if status == 'current':
            receipt['unfinished_by_scope'] = {}
        supervisor.write_new(Path(cmd[cmd.index('--output')+1]), receipt)
        return object()
    def wait(self, process, seconds):
        calls['waits'].append(seconds)
        return 0
    def systemctl(cmd, **kwargs):
        if cmd[1] == 'stop':
            calls['stopped'].append(cmd[-1])
            return SimpleNamespace(returncode=0)
        assert cmd[1] == 'show'
        return SimpleNamespace(stdout='LoadState=loaded\nActiveState=inactive\nMainPID=0\n')
    monkeypatch.setattr(supervisor.Backend, '__init__', lambda self, args: None)
    monkeypatch.setattr(supervisor.Backend, 'authority', lambda self: authority)
    monkeypatch.setattr(supervisor.Backend, 'launch', launch)
    monkeypatch.setattr(supervisor.Backend, 'wait', wait)
    monkeypatch.setattr(supervisor.subprocess, 'run', systemctl)
    monkeypatch.setattr(guard, 'no_other_workers', lambda: None)
    monkeypatch.setattr(guard, 'unit_state', lambda unit: next(calls['states']))
    monkeypatch.setattr(guard, 'anonymous_bytes', lambda group: anon)
    backend = guard.guarded_backend(supervisor)(args)
    return supervisor, args, backend, calls


LIVE = {'LoadState':'loaded', 'ActiveState':'active', 'SubState':'running', 'MainPID':'42', 'ControlGroup':'/unit'}
DEAD = {'LoadState':'loaded', 'ActiveState':'inactive', 'SubState':'dead', 'MainPID':'0', 'ControlGroup':''}
ABSENT = {'LoadState':'not-found', 'ActiveState':'inactive', 'MainPID':'0', 'ControlGroup':''}


@pytest.mark.parametrize('failure', ['high', 'missing', 'unreadable'])
def test_guard_uses_actual_supervisor_stop_and_writer_lock_no_next_batch(tmp_path, monkeypatch, failure):
    live = dict(LIVE, ControlGroup='' if failure == 'missing' else '/unit')
    supervisor, args, backend, calls = setup(tmp_path, monkeypatch, [live],
        anon=guard.ANON_LIMIT if failure == 'high' else 1)
    if failure == 'unreadable':
        def unreadable(group):
            raise PermissionError('cannot read actual cgroup')
        monkeypatch.setattr(guard, 'anonymous_bytes', unreadable)
    with pytest.raises((ValueError, PermissionError)):
        supervisor.supervise(args, backend, tmp_path)
    assert len(calls['launched']) == 1
    assert calls['stopped'] == ['swingset-h16-init-memory-test-001.service']
    final = json.loads((tmp_path/'final.json').read_text())
    assert final['status'] == 'stopped' and final['current'] is False
    assert final['batches'][0]['settled']['writer_lock_released'] is True
    samples = [json.loads(line) for line in (tmp_path/'initializer-001.resources.jsonl').read_text().splitlines()]
    assert samples[1]['guard_error'] and samples[-1]['settled']['writer_lock_released']
    assert (tmp_path/'initializer-001.resources.jsonl').stat().st_mode & 0o077 == 0


def test_dynamic_units_remain_serial_and_get_separate_private_samples(tmp_path, monkeypatch):
    supervisor, args, backend, calls = setup(tmp_path, monkeypatch, [LIVE, DEAD, LIVE, DEAD],
        results=('bounded_stop','current'))
    assert supervisor.supervise(args, backend, tmp_path) == 'current'
    assert len(calls['launched']) == 2 and not calls['stopped']
    assert all(value <= 5 for value in calls['waits'])
    files = sorted(tmp_path.glob('*.resources.jsonl'))
    assert len(files) == 2
    for index, path in enumerate(files, 1):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert all(row['unit'] == f'swingset-h16-init-memory-test-{index:03}.service' for row in rows)
        assert rows[-1]['settled']['writer_lock_released']
        assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('initial,final', [(ABSENT,DEAD), (LIVE,ABSENT),
    (dict(ABSENT, LoadState='loaded', ActiveState='activating'), DEAD),
    (LIVE, dict(DEAD, ActiveState='active', SubState='exited'))])
def test_normal_start_and_exit_without_cgroup_are_not_false_failures(tmp_path, monkeypatch, initial, final):
    supervisor, args, backend, calls = setup(tmp_path, monkeypatch, [initial, final])
    assert supervisor.supervise(args, backend, tmp_path) == 'current'
    assert not calls['stopped']


def test_start_transition_is_bounded_and_stops_exact_unit(tmp_path, monkeypatch):
    supervisor, args, backend, calls = setup(tmp_path, monkeypatch, [ABSENT])
    original = backend.launch
    def launch(cmd, log):
        process = original(cmd, log)
        backend.started -= 31
        return process
    backend.launch = launch
    with pytest.raises(ValueError, match='start transition'):
        supervisor.supervise(args, backend, tmp_path)
    assert len(calls['stopped']) == len(calls['launched']) == 1


def test_missing_live_cgroup_file_is_not_zero_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, 'CGROUP_ROOT', tmp_path)
    with pytest.raises(FileNotFoundError):
        guard.anonymous_bytes('/missing')
    (tmp_path/'actual').mkdir()
    (tmp_path/'actual/memory.stat').write_text(f'anon {guard.ANON_LIMIT}\nfile 99\n')
    assert guard.anonymous_bytes('/actual') == guard.ANON_LIMIT
    with pytest.raises(ValueError):
        guard.anonymous_bytes('/../escape')


def test_supervisor_hash_mismatch_prevents_import(tmp_path):
    changed = tmp_path/'supervisor.py'
    changed.write_text("raise AssertionError('must never execute')")
    with pytest.raises(ValueError, match='hash mismatch'):
        guard.load_supervisor(changed)


def test_existing_resource_log_is_not_overwritten_or_worker_launched(tmp_path, monkeypatch):
    supervisor, args, backend, calls = setup(tmp_path, monkeypatch, [])
    path = tmp_path/'initializer-001.resources.jsonl'
    path.write_text('retained evidence')
    with pytest.raises(FileExistsError):
        supervisor.supervise(args, backend, tmp_path)
    assert not calls['launched'] and path.read_text() == 'retained evidence'


def test_readable_sample_path_replacement_stops_before_continuation(tmp_path, monkeypatch):
    supervisor, args, backend, calls = setup(tmp_path, monkeypatch, [LIVE])
    original = backend.launch
    def launch(cmd, log):
        result = original(cmd, log)
        backend.sample_path.rename(backend.sample_path.with_suffix('.retained'))
        backend.sample_path.write_text('replacement')
        return result
    backend.launch = launch
    # First settled() detects log replacement and closes the old descriptor;
    # the supervisor retries settlement and still checks the actual lock.
    with pytest.raises(ValueError, match='replaced'):
        supervisor.supervise(args, backend, tmp_path)
    assert len(calls['launched']) == 1 and len(calls['stopped']) == 1
    assert json.loads((tmp_path/'final.json').read_text())['batches'][0]['settled']['writer_lock_released']


def test_overlap_preflight_rejects_any_active_h16_worker(monkeypatch):
    monkeypatch.setattr(guard.subprocess, 'run', lambda *args, **kwargs:
        SimpleNamespace(stdout='swingset-h16-other.service loaded active running\n'))
    with pytest.raises(ValueError, match='serial'):
        guard.no_other_workers()



def test_overlap_preflight_allows_exact_retained_replay_terminal_proof(monkeypatch):
    proof = json.loads((HERE.parent/'replay-terminal-resource-verification.json').read_text())
    assert len(proof['units']) == 13
    assert all(value['ActiveState'] == 'active' and value['SubState'] == 'exited'
        and value['MainPID'] == '0' and value['ControlGroup'] == '' for value in proof['units'].values())
    listing = ''.join(f'{unit} loaded active exited retained worker\n' for unit in proof['units'])
    monkeypatch.setattr(guard.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=listing))
    checked = []
    def state(unit):
        checked.append(unit)
        return proof['units'][unit]
    monkeypatch.setattr(guard, 'unit_state', state)
    guard.no_other_workers()
    assert checked == list(proof['units'])


@pytest.mark.parametrize('updates', [
    {'MainPID':'42'}, {'ControlGroup':'/still-charged'}, {'ActiveState':'activating'},
    {'ActiveState':'deactivating'}, {'Result':'exit-code', 'ExecMainStatus':'1'},
])
def test_retained_label_does_not_hide_a_live_or_unclean_unit(monkeypatch, updates):
    unit = 'swingset-h16-older.service'
    terminal = dict(DEAD, ActiveState='active', SubState='exited', Result='success', ExecMainStatus='0')
    terminal.update(updates)
    monkeypatch.setattr(guard.subprocess, 'run', lambda *args, **kwargs:
        SimpleNamespace(stdout=f'{unit} loaded active exited old\n'))
    monkeypatch.setattr(guard, 'unit_state', lambda name: terminal)
    with pytest.raises(ValueError, match='serial'):
        guard.no_other_workers()


def test_long_worker_collected_between_wait_and_sample_awaits_actual_process_result(tmp_path, monkeypatch):
    supervisor, args, backend, calls = setup(tmp_path, monkeypatch, [LIVE, ABSENT, ABSENT])
    def wait(self, process, seconds):
        calls['waits'].append(seconds)
        if len(calls['waits']) == 1:
            backend.started -= 40
            return None
        return 0
    monkeypatch.setattr(supervisor.Backend, 'wait', wait)
    assert supervisor.supervise(args, backend, tmp_path) == 'current'
    assert len(calls['waits']) == 2 and not calls['stopped']
    rows = [json.loads(line) for line in (tmp_path/'initializer-001.resources.jsonl').read_text().splitlines()]
    assert rows[2]['LoadState'] == 'not-found' and rows[2]['elapsed_seconds'] > 30
    assert rows[2]['observed_live_unit'] is True
    assert rows[-1]['settled']['writer_lock_released']


def test_live_observation_resets_for_each_worker(tmp_path, monkeypatch):
    supervisor, args, backend, calls = setup(tmp_path, monkeypatch, [LIVE, DEAD, ABSENT],
        results=('bounded_stop','current'))
    original = backend.launch
    def launch(cmd, log):
        result = original(cmd, log)
        if len(calls['launched']) == 2:
            backend.started -= 31
        return result
    backend.launch = launch
    with pytest.raises(ValueError, match='start transition'):
        supervisor.supervise(args, backend, tmp_path)
    assert len(calls['launched']) == 2
    assert calls['stopped'] == ['swingset-h16-init-memory-test-002.service']
