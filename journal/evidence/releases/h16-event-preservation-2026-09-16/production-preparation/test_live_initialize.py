import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path.cwd()))
from swingset.clock import FakeClock
from swingset.state.attempts import begin_attempt,recover_interrupted
from swingset.state.db import open_database
from swingset.state.work import WorkUnit,enqueue

@pytest.fixture
def driver(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('live_initializer','/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16/production-preparation/initialize.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,'STATE',tmp_path/'live')
    return module


def test_output_guard_refuses_evidence_root_symlinks_and_existing_receipts(driver):
    root=driver.STATE
    root.mkdir()
    with pytest.raises(ValueError):
        driver.output_path(root/'inputs'/'unsafe.json')
    witness=root/'body';witness.write_bytes(b'retained')
    ops=root/'operations';ops.mkdir()
    link=ops/'receipt.json';link.symlink_to(witness)
    with pytest.raises(ValueError):
        driver.output_path(link)
    assert witness.read_bytes()==b'retained'
    existing=ops/'existing.json';existing.write_bytes(b'prior receipt')
    with pytest.raises(ValueError):
        driver.output_path(existing)
    assert existing.read_bytes()==b'prior receipt'


def test_protected_hash_detects_source_cursor_budget_journal_mutations(driver):
    with open_database(driver.STATE) as db:
        conn=db.connection
        before=driver.protected(conn)
        for query in ("INSERT INTO cursors VALUES ('probe','1')", "INSERT INTO host_budget VALUES ('example.test','2026-09-13',1,0)", "UPDATE meta SET value='changed' WHERE key='identity_journal_digest'", "UPDATE revisions SET value=value+1 WHERE name='identity_decisions'"):
            conn.execute('SAVEPOINT probe')
            conn.execute(query)
            assert driver.protected(conn)!=before
            conn.execute('ROLLBACK TO probe');conn.execute('RELEASE probe')
            assert driver.protected(conn)==before


@pytest.mark.parametrize('query', [
    "UPDATE accepted_inputs SET digest='changed' WHERE consumer='pipeline'",
    "UPDATE meta SET value='changed' WHERE key='input_bundle_hash'",
])
def test_prepared_input_guard_detects_changed_rows_or_bundle_pointer(driver,query):
    with open_database(driver.STATE) as db:
        conn=db.connection
        conn.execute("INSERT INTO accepted_inputs VALUES ('pipeline','recipe/runtime','approved')")
        conn.execute("INSERT INTO meta(key,value) VALUES ('input_bundle_hash','approved') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        marker={'prepared_input_authority':driver.input_authority(conn)}
        driver.check_input_authority(conn,marker)
        conn.execute('SAVEPOINT probe')
        conn.execute(query)
        with pytest.raises(ValueError,match='accepted input authority'):
            driver.check_input_authority(conn,marker)
        conn.execute('ROLLBACK TO probe');conn.execute('RELEASE probe')
        driver.check_input_authority(conn,marker)


def test_interrupted_project_recovery_keeps_parse_tokens_and_60_second_deadline(driver):
    with open_database(driver.STATE) as db:
        clock=FakeClock();run=db.start_run(clock.now())
        parse=WorkUnit('parse','snapshot','retained')
        project=WorkUnit('project','calendar','retained')
        with db.transaction() as conn:
            enqueue(conn,[parse,project],enqueued_at=clock.now().isoformat())
        parse_before=driver.parse_tokens(db.connection)
        attempt=begin_attempt(db,project,now=clock.now(),run_id=run)
        driver.check_idle(db.connection,allow_abandoned=True)
        with pytest.raises(ValueError):
            driver.check_idle(db.connection,allow_abandoned=False)
        with db.transaction():
            assert recover_interrupted(db,now=clock.now())==1
        row=db.connection.execute('SELECT outcome,retry_at FROM work_attempts WHERE attempt_id=?',(attempt.attempt_id,)).fetchone()
        from datetime import datetime
        assert row[0]=='interrupted'
        assert (datetime.fromisoformat(row[1])-clock.now()).total_seconds()==60
        assert driver.parse_tokens(db.connection)==parse_before
        begin_attempt(db,parse,now=clock.now(),run_id=run)
        with pytest.raises(ValueError,match='non-derivation'):
            driver.check_idle(db.connection,allow_abandoned=True)


def test_durable_marker_and_receipts_never_overwrite_existing_evidence(driver):
    ops=driver.STATE/'operations';ops.mkdir(parents=True)
    path=driver.output_path(ops/'receipt.json')
    driver.new_json(path,{'before':1})
    with pytest.raises(FileExistsError):
        driver.new_json(path,{'wrong':1})
    assert json.loads(path.read_bytes())=={'before':1}
    driver.replace_json(path,{'after':1})
    assert json.loads(path.read_bytes())=={'after':1}
    assert not path.with_suffix('.json.tmp').exists()


def test_schema_precheck_never_migrates_an_old_database(driver):
    import sqlite3
    driver.STATE.mkdir()
    with sqlite3.connect(driver.STATE/'state.sqlite') as conn:
        conn.execute('PRAGMA user_version=13')
        conn.execute('CREATE TABLE witness(value TEXT)')
        conn.execute("INSERT INTO witness VALUES ('untouched')")
    with pytest.raises(ValueError,match='migration is forbidden'):
        driver.require_schema14()
    with sqlite3.connect(driver.STATE/'state.sqlite') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0]==13
        assert conn.execute('SELECT value FROM witness').fetchone()[0]=='untouched'
        conn.execute('PRAGMA user_version=14')
    driver.require_schema14()

@pytest.mark.parametrize('accepted_pin', ['older-reviewed-pin', None])
def test_gate_rejects_production_acceptance_from_another_pin(driver,tmp_path,monkeypatch,accepted_pin):
    from research import accept_h16, replay_derivations
    from swingset.state import db as state_db
    monkeypatch.setattr(state_db,'SCHEMA_VERSION',14)
    monkeypatch.setattr(replay_derivations,'verify_runtime',lambda *_:None)
    source=Path(accept_h16.__file__).resolve().parents[1]
    acceptance={'passed':True,'executed':True,'network_requests':0,'before':{},'after':{},'gate':{'source_receipt_sha256':accepted_pin}}
    receipt=tmp_path/'acceptance.json'
    receipt.write_text(json.dumps(acceptance))
    gate={'format':'h16-production-initialization-gate-v1','state':str(driver.STATE),'stages':['project','link'],'capture_accept_authorized':True,'driver_sha256':driver.sha(Path(driver.__file__)),'source':str(source),'source_receipt_sha256':'current-reviewed-pin','receipts':{'production_acceptance':receipt.name},'evidence_files':{receipt.name:driver.sha(receipt)}}
    path=tmp_path/'gate.json'
    path.write_text(json.dumps(gate))
    with pytest.raises(ValueError,match='another source pin'):
        driver.gate_inputs(path)
    assert not driver.STATE.exists()


@pytest.mark.parametrize('mismatch', ['bundle', 'replay_receipt', 'audit_manifest', 'audit_state', 'candidate_id'])
def test_gate_rejects_unrelated_replay_build_evidence(driver,tmp_path,monkeypatch,mismatch):
    from research import accept_h16, replay_derivations
    from swingset.state import db as state_db
    monkeypatch.setattr(state_db,'SCHEMA_VERSION',14)
    monkeypatch.setattr(replay_derivations,'verify_runtime',lambda *_:None)
    source=Path(accept_h16.__file__).resolve().parents[1]
    acceptance={'passed':True,'executed':True,'network_requests':0,'before':{},'after':{},'gate':{'source_receipt_sha256':'current-reviewed-pin'}}
    replay={'status':'current','unfinished_by_scope':{},'network_requests':0,'parse_executed':False,'source_receipt_sha256':'current-reviewed-pin','input_bundle_hash':'wrong-bundle' if mismatch=='bundle' else 'approved-bundle'}
    build={'replay_receipt_sha256':'another-replay-receipt','passed':True,'source':str(source),'semantic_publication_preflight':True,'network_requests':0,'published':False,'candidate':'/unused/candidates/rehearsed','candidate_id':'rehearsed','manifest_hash':'built-artifact'}
    audit={'passed':True,'checks':{'actual_check':True},'candidate':build['candidate'],'network_requests':0,'candidate_id':'rehearsed','manifest_hash':'different-artifact'}
    if mismatch in {'audit_state','candidate_id'}:
        scratch=tmp_path/'scratch';candidate=scratch/'candidates'/'rehearsed';candidate.mkdir(parents=True)
        build.update(state=str(scratch),candidate=str(candidate))
        audit.update(state=str(scratch) if mismatch=='candidate_id' else str(tmp_path/'other'),candidate=str(candidate),manifest_hash=build['manifest_hash'])
        if mismatch=='candidate_id':
            build['candidate_id']=audit['candidate_id']='same-wrong-id'
    records={'production_acceptance':acceptance,'scratch_replay':replay,'scratch_build':build,'scratch_audit':audit}
    receipts={}
    hashes={}
    for key,value in records.items():
        if key=='scratch_build' and mismatch in {'audit_manifest','audit_state','candidate_id'}:
            value['replay_receipt_sha256']=hashes[receipts['scratch_replay']]
        receipt=tmp_path/(key+'.json')
        receipt.write_text(json.dumps(value))
        receipts[key]=receipt.name
        hashes[receipt.name]=driver.sha(receipt)
    gate={'format':'h16-production-initialization-gate-v1','state':str(driver.STATE),'stages':['project','link'],'capture_accept_authorized':True,'driver_sha256':driver.sha(Path(driver.__file__)),'source':str(source),'source_receipt_sha256':'current-reviewed-pin','input_bundle_hash':'approved-bundle','receipts':receipts,'evidence_files':hashes}
    path=tmp_path/'gate.json'
    path.write_text(json.dumps(gate))
    message={'bundle':'another accepted input bundle','replay_receipt':'supplied current replay receipt','audit_manifest':'exact built candidate artifact','audit_state':'actual rehearsal paths','candidate_id':'actual rehearsal paths'}[mismatch]
    with pytest.raises(ValueError,match=message):
        driver.gate_inputs(path)
    assert not driver.STATE.exists()

@pytest.fixture
def continuation(driver,tmp_path,monkeypatch):
    from research.accept_h11 import query_digest
    from types import SimpleNamespace
    ops=driver.STATE/'operations';ops.mkdir(parents=True)
    gate_path=ops/'gate.json';gate_path.write_text('{}')
    gate={'input_bundle_hash':'approved','source_receipt_sha256':'reviewed-pin'}
    with open_database(driver.STATE) as db:
        conn=db.connection
        conn.execute("INSERT INTO meta(key,value) VALUES ('input_bundle_hash','approved') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        conn.execute("INSERT INTO accepted_inputs VALUES ('pipeline','recipe/runtime','approved')")
        marker={'format':driver.FORMAT,'state':str(driver.STATE),'gate_sha256':driver.sha(gate_path),
            'driver_sha256':driver.sha(Path(driver.__file__)),'input_bundle_hash':'approved',
            'preflight_baseline':{'commit':'confirmed'},'protected':driver.protected(conn),
            'prepared_input_authority':driver.input_authority(conn),'controls':driver.controls(conn),
            'extract_cache_invalidated':False,
            'watch_extract_versions':query_digest(conn,'SELECT watch_id,extract_version FROM watches ORDER BY watch_id')}
    previous=ops/'marker.json';driver.new_json(previous,marker)
    monkeypatch.setattr(driver,'capture_bundle',lambda *_:SimpleNamespace(digest='approved'))
    monkeypatch.setattr(driver,'hold_and_baseline',lambda *_:{'baseline':{'commit':'confirmed'}})
    return SimpleNamespace(gate=gate,gate_path=gate_path,previous=previous,marker=marker,
        next=ops/'continued.json',review=ops/'review.json')


def write_continuation_review(driver,c):
    with driver.closing(driver.readonly()) as conn:
        reviewed_controls=driver.controls(conn)
    review={'format':'h16-initialization-continuation-review-v1','state':str(driver.STATE),
        'previous_marker':str(c.previous),'previous_marker_sha256':driver.sha(c.previous),
        'gate_sha256':driver.sha(c.gate_path),'driver_sha256':driver.sha(Path(driver.__file__)),
        'source_receipt_sha256':c.gate['source_receipt_sha256'],'input_bundle_hash':'approved',
        'coordinator_reviewed':True,'reviewed_at':'2026-09-13T18:00:00+00:00',
        'reviewed_by':'operations coordinator','controls':reviewed_controls}
    driver.new_json(c.review,review)
    return review


def invoke_continuation(driver,c):
    return driver.continue_initialization(c.gate,Path.cwd(),c.gate_path,c.previous,c.next,c.review)


def test_actual_pause_resume_can_continue_without_reacceptance_or_original_checkpoint(driver,continuation,monkeypatch):
    from swingset.state.controls import Selector,change_control
    from research import accept_h16
    from swingset.state import inputs
    c=continuation
    old_bytes=c.previous.read_bytes()
    clock=FakeClock()
    for paused in (True,False):
        change_control(driver.STATE,selector=Selector('all','all'),paused=paused,actor='coordinator',reason='reviewed operational stop',now=clock.now())
    with open_database(driver.STATE) as db:
        # Legitimate resumed outputs differ from the original checkpoint; they
        # must not require reverting to the initial all-table equality check.
        db.start_run(clock.now())
        control_before=driver.controls(db.connection)
        assert control_before!=c.marker['controls']
    monkeypatch.setattr(accept_h16,'preflight',lambda *_:pytest.fail('original checkpoint preflight cannot apply after derived writes'))
    monkeypatch.setattr(inputs,'accept',lambda *_:pytest.fail('continuation must not reaccept inputs'))
    write_continuation_review(driver,c)
    result=invoke_continuation(driver,c)
    assert result['status']=='continued' and result['input_acceptance_executed'] is False
    continued=driver.read_marker(c.next,c.gate,c.gate_path)
    assert continued['controls']==control_before
    assert continued['protected']==c.marker['protected']
    assert c.previous.read_bytes()==old_bytes
    with driver.closing(driver.readonly()) as conn:
        assert driver.controls(conn)==control_before
        driver.check_input_authority(conn,continued)
        assert driver.protected(conn)==continued['protected']


@pytest.mark.parametrize('mutation',['protected','input','marker','controls','baseline','cache','review'])
def test_continuation_rejects_changed_authority_without_new_marker(driver,continuation,monkeypatch,mutation):
    c=continuation
    write_continuation_review(driver,c)
    if mutation=='marker':
        c.previous.write_text(c.previous.read_text()+' ')
    elif mutation=='baseline':
        monkeypatch.setattr(driver,'hold_and_baseline',lambda *_:{'baseline':{'commit':'other'}})
    elif mutation=='cache':
        monkeypatch.setattr(driver,'check_cache',lambda *_:(_ for _ in ()).throw(ValueError('cache advanced')))
    elif mutation=='review':
        review=json.loads(c.review.read_bytes());review['coordinator_reviewed']=False;c.review.write_text(json.dumps(review))
    else:
        with open_database(driver.STATE) as db:
            query={'protected':"UPDATE revisions SET value=value+1 WHERE name='identity_decisions'",
                'input':"UPDATE accepted_inputs SET digest='changed'",
                'controls':"UPDATE control_state SET revision=revision+1"}[mutation]
            db.connection.execute(query)
    with pytest.raises(ValueError):
        invoke_continuation(driver,c)
    assert not c.next.exists()


def test_continuation_chain_rejects_rewritten_predecessor(driver,continuation):
    c=continuation
    write_continuation_review(driver,c)
    invoke_continuation(driver,c)
    assert driver.read_marker(c.next,c.gate,c.gate_path)['controls']==c.marker['controls']
    c.previous.write_text(c.previous.read_text()+' ')
    with pytest.raises(ValueError,match='predecessor'):
        driver.read_marker(c.next,c.gate,c.gate_path)
