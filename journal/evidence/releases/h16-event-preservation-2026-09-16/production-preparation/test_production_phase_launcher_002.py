import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC=importlib.util.spec_from_file_location('phase_launcher',Path(__file__).with_name('run-production-phase-002.py'))
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

def args(phase,**kwargs):
    return SimpleNamespace(phase=phase,label='reviewed-001',output=module.OPS/'fresh.json',gate=None,gate_sha256=None,candidate=None,baseline=None,resume=False,**kwargs)

@pytest.mark.parametrize('phase',['build','audit','publish'])
def test_service_bounds_and_frozen_environment(phase):
    value=args(phase)
    command=module.command(value,'new.service',Path('/var/tmp/new.json'))
    assert command[:2]==['systemd-run','--wait']
    for required in ('--property=User=swingset','--property=Group=swingset','--property=RuntimeMaxSec=3600','--property=TimeoutStopSec=180','--setenv=PYTHONDONTWRITEBYTECODE=1','--property=UMask=0077'):
        assert required in command
    assert ('--property=EnvironmentFile=/etc/swingset.env' in command)==(phase=='publish')
    assert '--setenv=PYTHONPATH='+str(module.SOURCE/'src')+':'+str(module.SOURCE) in command
    assert not any('HF_TOKEN' in word for word in command)

def test_audit_stays_outside_state_and_uses_exact_driver():
    value=args('audit');value.candidate=module.STATE/'candidates/new';value.baseline=module.STATE/'candidates/old'
    output=Path('/var/tmp/private-audit/audit.json')
    command=module.command(value,'new.service',output)
    assert str(module.AUDIT) in command
    assert command[command.index('--output')+1]==str(output)
    assert '--gate' not in command

def test_resume_keeps_same_publish_gate_and_fresh_output():
    value=args('publish');value.resume=True;value.gate=module.OPS/'immutable-gate.json'
    command=module.command(value,'new.service',value.output)
    assert command[-1]=='--resume'
    assert command[command.index('--gate')+1]==str(value.gate)
    assert command[command.index('--output')+1]==str(value.output)

@pytest.fixture
def private(monkeypatch,tmp_path):
    monkeypatch.setattr(module,'OPS',tmp_path)
    return tmp_path

@pytest.mark.parametrize('phase',['build','publish'])
def test_actual_gate_hash_and_phase_required(private,phase):
    value=args(phase);value.gate=private/'gate.json'
    value.gate.write_text(json.dumps({'mode':phase,'authorized':True,'source':str(module.SOURCE),'driver_sha256':module.RELEASE_SHA}))
    value.gate_sha256=module.sha(value.gate)
    module.validate_args(value)
    value.gate.write_text('{}')
    with pytest.raises(ValueError,match='gate changed'):module.validate_args(value)

@pytest.mark.parametrize('mutation',['existing','symlink','resume','gate_missing','mode'])
def test_reject_invalid_launch(private,mutation):
    value=args('build');value.gate=private/'gate.json'
    gate={'mode':'build','authorized':True,'source':str(module.SOURCE),'driver_sha256':module.RELEASE_SHA}
    if mutation=='mode':gate['mode']='publish'
    value.gate.write_text(json.dumps(gate));value.gate_sha256=module.sha(value.gate)
    if mutation=='existing':value.output.write_text('retained')
    elif mutation=='symlink':value.output.symlink_to(private/'absent')
    elif mutation=='resume':value.resume=True
    elif mutation=='gate_missing':value.gate_sha256=None
    with pytest.raises(ValueError):module.validate_args(value)

def test_save_never_overwrites(tmp_path):
    path=tmp_path/'receipt.json';module.save(path,{'passed':False})
    with pytest.raises(FileExistsError):module.save(path,{'passed':True})
    assert json.loads(path.read_text())=={'passed':False}

@pytest.mark.parametrize('pid,active,substate,group', [('1','active','running',''),('1','activating','start',''),('0','active','running','')])
def test_missing_live_cgroup_rejected(pid,active,substate,group):
    with pytest.raises(ValueError,match='cgroup'):
        module.memory_sample({'MainPID':pid,'ActiveState':active,'SubState':substate,'ControlGroup':group},SimpleNamespace())

@pytest.mark.parametrize('value',[6*1024**3,6*1024**3+1])
def test_memory_threshold_stops_at_limit(value):
    with pytest.raises(ValueError,match='reached'):
        module.memory_sample({'MainPID':'1','ControlGroup':'/test'},SimpleNamespace(anonymous_bytes=lambda _:value))

def test_clean_retained_exit_accepted_but_live_worker_rejected(monkeypatch):
    guard=module.load_guard(Path(__file__).parent/'monitoring/guard-initialization-003.py')
    monkeypatch.setattr(guard.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='swingset-h16-old.service loaded active exited old'))
    fields={'MainPID':'0','ControlGroup':'','ActiveState':'active','SubState':'exited','Result':'success','ExecMainStatus':'0'}
    monkeypatch.setattr(guard,'unit_state',lambda _:fields)
    guard.no_other_workers()
    fields['MainPID']='42'
    with pytest.raises(ValueError,match='live or unassessed'):guard.no_other_workers()

def test_guard_bytes_verified_before_execution(tmp_path):
    path=tmp_path/'guard.py';path.write_text('raise AssertionError("must not execute")')
    with pytest.raises(ValueError,match='guard changed'):module.load_guard(path)
