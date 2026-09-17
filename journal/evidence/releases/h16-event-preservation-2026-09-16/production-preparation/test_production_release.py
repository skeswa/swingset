import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path.cwd()/'tests/publish'))
from test_safety import release as release

@pytest.fixture
def driver():
    spec=importlib.util.spec_from_file_location('production_release','/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16/production-preparation/production-release.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def gate(driver):
    return {'format':driver.FORMAT+'-gate','mode':'build','authorized':True,
            'state':str(driver.STATE),'source':str(driver.SOURCE),
            'source_receipt_sha256':driver.SOURCE_RECEIPT,'input_bundle_hash':driver.BUNDLE,
            'driver_sha256':driver.sha(Path(driver.__file__)),'reviewed_by':'offline reviewer',
            'reviewed_at':'2026-09-13T00:00:00+00:00'}


@pytest.mark.parametrize('key,value', [('mode','publish'),('authorized',False),('state','/tmp/scratch'),
 ('source','/worktree'),('source_receipt_sha256','another'),('input_bundle_hash','other'),
 ('driver_sha256','changed'),('reviewed_by',''),('reviewed_at','2026-09-13T00:00:00')])
def test_review_gate_rejects_changed_authority(driver,key,value):
    good=gate(driver);driver.gate_header(good,'build');good[key]=value
    with pytest.raises(ValueError):driver.gate_header(good,'build')


def test_hashed_receipt_and_symlink_cannot_replace_reviewed_bytes(driver,tmp_path):
    file=tmp_path/'receipt.json';file.write_text('{}');review={'path':file.name,'sha256':driver.sha(file)}
    assert driver.ref(tmp_path/'gate.json',review)==file
    file.write_text('{"changed":true}')
    with pytest.raises(ValueError):driver.ref(tmp_path/'gate.json',review)
    link=tmp_path/'link';link.symlink_to(file)
    with pytest.raises(ValueError):driver.ref(tmp_path/'gate.json',{'path':'link','sha256':driver.sha(file)})


def current(driver,tmp_path):
    paths=[tmp_path/name for name in ('gate','marker','initializer')]
    for p in paths:p.write_text(p.name)
    data={'format':'h16-production-initialization-receipt-v1','mode':'run','status':'current',
          'gate_sha256':driver.sha(paths[0]),'marker_sha256':driver.sha(paths[1]),
          'driver_sha256':driver.sha(paths[2]),'source_receipt_sha256':driver.SOURCE_RECEIPT,
          'network_requests':0,'parse_executed':False,'build_executed':False,'published':False,
          'protected_unchanged':True,'parse_tokens_unchanged':True,'controls_unchanged':True,
          'input_authority_unchanged':True,'unfinished_by_scope':{},'finished_at':'done'}
    return data,dict(gate_path=paths[0],marker_path=paths[1],initializer=paths[2])


@pytest.mark.parametrize('key,value',[('status','bounded_stop'),('unfinished_by_scope',{'link/event':1}),
 ('marker_sha256','other'),('input_authority_unchanged',False),('controls_unchanged',False),
 ('finished_at',None),('network_requests',1),('parse_executed',True)])
def test_initialized_receipt_requires_actual_current_same_chain(driver,tmp_path,key,value):
    good,args=current(driver,tmp_path);driver.initialization_receipt(good,**args);good[key]=value
    with pytest.raises(ValueError):driver.initialization_receipt(good,**args)


def audit_fixture(driver,tmp_path):
    build={'candidate':'/var/lib/swingset/candidates/cand_reviewed','candidate_id':'cand_reviewed',
           'manifest_hash':'exact','expected_parent':'parent'}
    report={**build,'state':str(driver.STATE),'baseline_commit':'parent','network_requests':0,'passed':True,
            'checks':{key:True for key in driver.AUDIT_CHECKS | {'row_count_'+name for name in __import__('swingset.build.schema',fromlist=['SCHEMAS']).SCHEMAS}}}
    script=tmp_path/'audit.py';script.write_text('reviewed audit')
    path=tmp_path/'audit.json';path.write_text(json.dumps(report))
    refs={'audit':{'path':path.name,'sha256':driver.sha(path)},'audit_driver':{'path':script.name,'sha256':driver.sha(script)}}
    return build,report,path,refs


@pytest.mark.parametrize('change',['candidate','state','manifest','missing_default_check','missing_named_null_check','missing_table_count','failed_judges'])
def test_publish_audit_binds_production_artifact_and_substantive_controls(driver,tmp_path,change):
    build,report,path,refs=audit_fixture(driver,tmp_path);driver.validate_audit(tmp_path/'gate',refs,build)
    if change=='candidate':report['candidate']='/tmp/other/cand_reviewed'
    if change=='state':report['state']='/tmp/scratch'
    if change=='manifest':report['manifest_hash']='other'
    if change=='missing_default_check':del report['checks']['entries_no_new_or_replaced_default_ids']
    if change=='missing_named_null_check':del report['checks']['named_null_id_judges_preserved_where_source_persists']
    if change=='missing_table_count':del report['checks']['row_count_entries']
    if change=='failed_judges':report['checks']['all_baseline_named_judges_preserved']=False
    path.write_text(json.dumps(report));refs['audit']['sha256']=driver.sha(path)
    with pytest.raises(ValueError):driver.validate_audit(tmp_path/'gate',refs,build)


def test_resume_cannot_reconcile_another_candidate(driver,tmp_path):
    chosen=tmp_path/'cand_reviewed';other=tmp_path/'cand_other'
    driver.check_pending([chosen],chosen,True)
    for pending,resume in (([chosen],False),([other],True),([chosen,other],True)):
        with pytest.raises(ValueError):driver.check_pending(pending,chosen,resume)


def test_execution_guard_allows_only_reviewed_publication_recovery(driver,tmp_path):
    db=sqlite3.connect(':memory:');db.executescript('CREATE TABLE work_attempts(outcome TEXT); CREATE TABLE execution_admissions(action_kind TEXT,candidate_id TEXT,state TEXT);')
    chosen=tmp_path/'cand_reviewed'
    db.execute("INSERT INTO execution_admissions VALUES('publication','cand_reviewed','active')")
    driver.check_execution(db,chosen,True)
    with pytest.raises(ValueError):driver.check_execution(db,chosen,False)
    db.execute("UPDATE execution_admissions SET action_kind='build'")
    with pytest.raises(ValueError):driver.check_execution(db,chosen,True)
    db.execute('DELETE FROM execution_admissions');db.execute("INSERT INTO work_attempts VALUES('running')")
    with pytest.raises(ValueError):driver.check_execution(db,chosen,True)
    db.close()


def publish_setup(driver,release,monkeypatch):
    database,_,_,_,hub,candidate=release
    monkeypatch.setattr(driver,'STATE',database.state_dir)
    manifest=json.loads((candidate.path/'_meta/manifest.json').read_text())
    built=json.loads((candidate.path/'BUILT').read_text())
    # Operational fixed production pin/parent guards have separate tests; this
    # real schema fixture begins at the empty remote parent 'base'.
    monkeypatch.setattr(driver,'candidate_files',lambda *_:(built,manifest))
    import swingset.publish.huggingface as remote
    monkeypatch.setattr(remote,'HuggingFaceHub',lambda *a,**kw:hub)
    monkeypatch.setenv('HF_TOKEN','offline-only')
    return database,hub,candidate,{'expected_parent':built['expected_parent']}


def test_real_lost_response_resume_verifies_without_second_commit(driver,release,monkeypatch):
    database,hub,candidate,review=publish_setup(driver,release,monkeypatch)
    hub.lose_response=True
    with pytest.raises(ConnectionError):driver.publish_reviewed(database,candidate.path,review,resume=False,promoted=False,report={})
    assert hub.calls==1 and (candidate.path/'PUBLISHING').exists()
    report={};driver.publish_reviewed(database,candidate.path,review,resume=True,promoted=False,report=report)
    assert report['status']=='recovered' and report['published'] and hub.calls==1
    assert (database.state_dir/'baseline').resolve()==candidate.path


def test_real_publication_pause_preserved_and_no_commit(driver,release,monkeypatch):
    from swingset.state.controls import Selector,change_control
    from datetime import UTC,datetime
    database,hub,candidate,review=publish_setup(driver,release,monkeypatch)
    change_control(database.state_dir,selector=Selector('all','all'),paused=True,actor='review',reason='hold',now=datetime.now(UTC),timeout=1)
    report={};driver.publish_reviewed(database,candidate.path,review,resume=False,promoted=False,report=report)
    assert report['status']=='held' and not report['published'] and hub.calls==0
    assert database.connection.execute('SELECT count(*) FROM operator_pauses').fetchone()[0]==1


def test_changed_remote_parent_stops_before_commit(driver,release,monkeypatch):
    database,hub,candidate,review=publish_setup(driver,release,monkeypatch);hub.current='unrelated'
    with pytest.raises(ValueError,match='remote parent'):driver.publish_reviewed(database,candidate.path,review,resume=False,promoted=False,report={})
    assert hub.calls==0 and not (candidate.path/'PUBLISHING').exists()


def test_material_correction_after_build_stops_before_remote_read(driver,release,monkeypatch):
    from test_safety import changed_journal
    database,config,overrides,clock,_,_=release
    database,hub,candidate,review=publish_setup(driver,release,monkeypatch)
    changed_journal(database,config,overrides,clock)
    observed=[]
    monkeypatch.setattr(hub,'head',lambda:observed.append('head'))
    with pytest.raises(Exception):driver.publish_reviewed(database,candidate.path,review,resume=False,promoted=False,report={})
    assert observed==[] and hub.calls==0


def test_successful_build_receipt_cannot_be_detached_from_reviewed_gate(driver,tmp_path,monkeypatch):
    paths={}
    for key in ('initializer_driver','initialization_gate','initialization_marker','initialization_receipt'):
        p=tmp_path/key;p.write_text(key);paths[key]={'path':str(p),'sha256':driver.sha(p)}
    bg={**gate(driver),**paths};bgpath=tmp_path/'build-gate.json';bgpath.write_text(json.dumps(bg))
    build={'format':driver.FORMAT,'mode':'build','state':str(driver.STATE),'source':str(driver.SOURCE),
           'source_receipt_sha256':driver.SOURCE_RECEIPT,'input_bundle_hash':driver.BUNDLE,
           'gate_sha256':driver.sha(bgpath),'driver_sha256':driver.sha(Path(driver.__file__)),
           'status':'built','passed':True,'published':False,'network_requests':0,
           'protected_unchanged':True,'controls_unchanged':True,'input_authority_unchanged':True,
           'parse_tokens_unchanged':True,'revisions_unchanged':True,'semantic_publication_preflight':True,
           'finished_at':'done'}
    br=tmp_path/'build.json';br.write_text(json.dumps(build))
    pg={**paths,'build_gate':{'path':str(bgpath),'sha256':driver.sha(bgpath)},'build_receipt':{'path':str(br),'sha256':driver.sha(br)}}
    monkeypatch.setattr(driver,'validate_audit',lambda *args:None)
    assert driver.reviewed_build(tmp_path/'pub-gate',pg)==build
    build['gate_sha256']='different reviewed gate';br.write_text(json.dumps(build));pg['build_receipt']['sha256']=driver.sha(br)
    with pytest.raises(ValueError,match='production build receipt'):driver.reviewed_build(tmp_path/'pub-gate',pg)


def test_build_rechecks_actual_unmaterialized_scope_after_queue_loss(driver,release,monkeypatch):
    from swingset.state import derivations
    from swingset.state.work import WorkUnit
    from types import SimpleNamespace
    database,_,_,clock,_,_=release
    with database.transaction() as conn:
        derivations.capture(conn,WorkUnit('project','dancer','lost-queue'),now=clock.now())
        conn.execute('DELETE FROM pending_work')
    observed=[]
    helper=SimpleNamespace(capture_bundle=lambda *args:observed.append('capture'))
    with pytest.raises(ValueError,match='derivations are not current'):
        driver.build(database,helper,{}, {}, {})
    assert observed==[]


def test_unchanged_unpublished_artifact_is_not_publication_success(driver,tmp_path,monkeypatch):
    monkeypatch.setattr(driver,'STATE',tmp_path)
    candidate=tmp_path/'candidates/cand_reviewed';candidate.mkdir(parents=True)
    assert not driver.publication_complete({'status':'unchanged','published':False},candidate)
    old=tmp_path/'candidates/cand_old';old.mkdir();(tmp_path/'baseline').symlink_to(old)
    assert not driver.publication_complete({'status':'unchanged','published':True},candidate)
    (tmp_path/'baseline').unlink();(tmp_path/'baseline').symlink_to(candidate)
    (candidate/'_meta').mkdir();manifest=candidate/'_meta/manifest.json';manifest.write_text('{}')
    (candidate/'PUBLISHED').write_text(json.dumps({'commit':'acknowledged'}))
    report={'published':True,'manifest_hash':driver.sha(manifest)}
    assert driver.publication_complete(report,candidate)
    assert report['final_baseline']['commit']=='acknowledged'
