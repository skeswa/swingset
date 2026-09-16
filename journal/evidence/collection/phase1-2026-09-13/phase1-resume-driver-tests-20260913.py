import importlib.util
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace
import pytest
spec=importlib.util.spec_from_file_location('resume_phase1','/tmp/resume_phase1.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

@pytest.fixture
def fixture(tmp_path):
    conn=sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE accepted_inputs(name,value); CREATE TABLE meta(key,value); CREATE TABLE watches(watch_id,source,parser,url,archive_url);')
    baseline=tmp_path/'candidate';(baseline/'_meta').mkdir(parents=True)
    (baseline/'PUBLISHED').write_text('{}');(baseline/'_meta/manifest.json').write_text('{}')
    (tmp_path/'baseline').symlink_to(baseline)
    gate={'inputs':m.inputs(conn),'baseline_path':str(baseline),'published_sha256':m.sha(baseline/'PUBLISHED'),'manifest_sha256':m.sha(baseline/'_meta/manifest.json'),'utc_day':'2026-09-14','wall_seconds':600}
    t=SimpleNamespace(source='wsdc_calendar',parser='wsdc_calendar.events',url='https://www.worldsdc.com/events/',archive_url='https://web.archive.org/web/20250226151724id_/https://www.worldsdc.com/events/')
    conn.execute('INSERT INTO watches VALUES (?,?,?,?,?)',('w',t.source,t.parser,t.url,t.archive_url))
    yield conn,tmp_path,gate,{'id':t}
    conn.close()

def check(f,**kwargs):
    conn,state,g,targets=f
    return m.debit_gate(conn,state,g,targets,'w',kwargs.pop('host','web.archive.org'),now=kwargs.pop('now',datetime(2026,9,14,tzinfo=UTC)),elapsed=kwargs.pop('elapsed',0))

def test_exact_watch_allowed_and_origin_host_denied(fixture):
    assert check(fixture) is None
    assert 'outside exact' in check(fixture,host='www.worldsdc.com')

def test_capture_mutation_before_debit_denied(fixture):
    fixture[0].execute("UPDATE watches SET archive_url='other'")
    assert 'outside exact' in check(fixture)

def test_inputs_mutated_before_debit_denied(fixture):
    fixture[0].execute("INSERT INTO accepted_inputs VALUES ('changed','authority')")
    with pytest.raises(ValueError,match='input authority'): check(fixture)

def test_publication_changed_before_debit_denied(fixture):
    (fixture[1]/'candidate/PUBLISHED').write_text('{"commit":"new"}')
    with pytest.raises(ValueError,match='baseline'): check(fixture)

@pytest.mark.parametrize('kwargs',[{'elapsed':600},{'now':datetime(2026,9,15,tzinfo=UTC)}])
def test_deadline_and_utc_rollover_deny_debit(fixture,kwargs):
    assert 'window ended' in check(fixture,**kwargs)

def test_exact_seventeen_pending_required(fixture):
    conn,state,g,_=fixture
    (state/'phase1-catalog.json').write_text('{}')
    (state/'phase1-ledger.json').write_text(json.dumps({'targets':{}}))
    g.update(remaining_target_ids=sorted(m.TARGET_IDS),catalog_sha256=m.sha(state/'phase1-catalog.json'),ledger_sha256=m.sha(state/'phase1-ledger.json'))
    with pytest.raises(ValueError,match='exact reviewed remainder'):
        m.check_targets(conn,state,g,lambda path:(),lambda name:None)


def test_input_bundle_pointer_change_denies_next_debit(fixture):
    conn,state,g,_=fixture
    conn.execute("INSERT INTO meta VALUES ('input_bundle_hash','original')")
    g['inputs']=json.loads(json.dumps(m.inputs(conn)))
    assert check(fixture) is None
    conn.execute("UPDATE meta SET value='replacement' WHERE key='input_bundle_hash'")
    with pytest.raises(ValueError,match='input authority'): check(fixture)

def test_partial_success_continues_only_reviewed_remainder(fixture):
    from pathlib import Path
    import csv
    from swingset.history.catalog import Target
    conn,state,g,_=fixture
    rows=list(csv.DictReader(Path('research/workflow-output/v2-phase1/targets.csv').open()))
    targets=tuple(Target(r['source'],r['url'],r['parser'],r['timestamp']) for r in rows if r['target_id'] in m.TARGET_IDS)
    assert {t.target_id for t in targets}==m.TARGET_IDS
    identities={t.target_id:{'source':t.source,'parser':t.parser,'url':t.url,'timestamp':t.timestamp} for t in targets}
    (state/'phase1-catalog.json').write_text('{}')
    ledger={t.target_id:{'status':'pending'} for t in targets}
    def reviewed():
        (state/'phase1-ledger.json').write_text(json.dumps({'targets':ledger}))
        g.update(catalog_sha256=m.sha(state/'phase1-catalog.json'),ledger_sha256=m.sha(state/'phase1-ledger.json'),remaining_target_ids=[k for k,v in ledger.items() if v['status']=='pending'],target_statuses={k:v['status'] for k,v in ledger.items()},original_targets=identities)
    reviewed()
    check=lambda:m.check_targets(conn,state,g,lambda p:targets,lambda parser:SimpleNamespace(PARSER_VERSION=7))
    assert len(check())==17
    for target in targets[:3]: ledger[target.target_id]={'status':'parsed','parser_version':'7'}
    (state/'phase1-ledger.json').write_text(json.dumps({'targets':ledger}))
    with pytest.raises(ValueError,match='ledger changed'): check()
    reviewed()
    assert set(check())==m.TARGET_IDS-{t.target_id for t in targets[:3]}
    assert len(check())==14
    ledger[targets[0].target_id]['parser_version']='6';reviewed()
    with pytest.raises(ValueError,match='offline replay'): check()

@pytest.mark.parametrize('pause_during_wait',[False,True])
def test_real_intake_scope_caps_retries_and_rechecks_wait(tmp_path,monkeypatch,pause_during_wait):
    from pathlib import Path
    from datetime import timedelta
    import httpx
    from swingset.clock import FakeClock
    from swingset.config import Config,HostConfig
    from swingset.history import intake
    from swingset.history.catalog import Target,save_catalog
    from swingset.state.controls import Selector,change_control
    from swingset.state.db import open_database
    body=Path('src/swingset/sources/wsdc_calendar/fixtures/calendar-20210415.html').read_bytes()
    state=tmp_path/'state';state.mkdir()
    candidate=state/'candidate';(candidate/'_meta').mkdir(parents=True)
    (candidate/'PUBLISHED').write_text('{}');(candidate/'_meta/manifest.json').write_text('{}')
    (state/'baseline').symlink_to(candidate)
    targets=tuple(Target('wsdc_calendar','https://www.worldsdc.com/events/','wsdc_calendar.events',stamp) for stamp in ('20250226151724','20250512152735'))
    catalog=state/'phase1-catalog.json';save_catalog(catalog,targets)
    clock=FakeClock(datetime(2026,9,14,tzinfo=UTC));start=clock.now()
    requests=[];archive_attempts=[];checked=[]
    original_debit=m.debit_gate
    def checked_debit(conn,*args,**kwargs):
        assert conn.in_transaction
        checked.append(True)
        return original_debit(conn,*args,**kwargs)
    monkeypatch.setattr(m,'debit_gate',checked_debit)
    with open_database(state) as db:
        gate={'inputs':json.loads(json.dumps(m.inputs(db.connection))),'baseline_path':str(candidate),'published_sha256':m.sha(candidate/'PUBLISHED'),'manifest_sha256':m.sha(candidate/'_meta/manifest.json'),'utc_day':'2026-09-14','wall_seconds':600}
        original=intake.FetchClient
        scoped=m.scoped_client(original,state,gate,{t.target_id:t for t in targets},monotonic=lambda:(clock.now()-start).total_seconds())
        instances=[]
        class Captured(scoped):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs);instances.append(self)
        monkeypatch.setattr(intake,'FetchClient',Captured)
        slept=clock.sleep
        paused=[]
        def sleep(seconds):
            if pause_during_wait and not paused:
                change_control(state,selector=Selector('source','wsdc_calendar'),paused=True,actor='offline-test',reason='pause during polite wait',now=clock.now())
                paused.append(True)
            slept(seconds)
        clock.sleep=sleep
        def respond(request):
            requests.append((clock.now(),str(request.url)))
            assert request.url.host=='web.archive.org'
            if request.url.path=='/robots.txt': return httpx.Response(404)
            archive_attempts.append(str(request.url))
            if len(archive_attempts)==1:return httpx.Response(500)
            return httpx.Response(200,content=body)
        db.connection.execute("INSERT INTO admission_policies(page_kind,contract_version,mode,policy_revision) VALUES ('wsdc_calendar.events','unassessed','shadow','shadow:unassessed')")
        before={name:list(map(tuple,db.connection.execute('SELECT * FROM '+name))) for name in ('history_acceptance','admission_policies','accepted_inputs')}
        ledger=intake.run_intake(db,catalog,config=Config({'web.archive.org':HostConfig(min_gap_seconds=10,daily_request_budget=3)},{}),clock=clock,max_targets=2,wall_seconds=600,transport=httpx.MockTransport(respond))
        charged=m.budget(db.connection,'2026-09-14')[0]
        assert checked
        assert charged==len(requests)==(1 if pause_during_wait else 3)
        assert all(b[0]-a[0]>=timedelta(seconds=10) for a,b in zip(requests,requests[1:]))
        assert all(not c.gate.inflight for c in instances)
        if pause_during_wait:
            assert not archive_attempts
            assert ledger['targets'][targets[0].target_id]['status']=='pending'
            assert db.connection.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]==0
        else:
            assert len(archive_attempts)==2 and all(targets[0].timestamp in url for url in archive_attempts)
            assert ledger['targets'][targets[0].target_id]['status']=='parsed'
            assert ledger['targets'][targets[1].target_id]['status']=='pending'
            assert db.connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0]>0
        for name,rows in before.items(): assert list(map(tuple,db.connection.execute('SELECT * FROM '+name)))==rows
        assert db.connection.execute("SELECT COUNT(*) FROM watches WHERE kind!='index'").fetchone()[0]==0
