import importlib.util
import sqlite3
from pathlib import Path
import pytest
import httpx
from test_fixture_exception import setup as setup, REPO, MANIFEST, HOST, read_manifest, response
from swingset.config import Config,HostConfig
from swingset.state.controls import Selector,change_control,status
spec=importlib.util.spec_from_file_location('fixture_exception_h13',str(Path(__file__).resolve().parents[1]/'fixture-exception-h13-002.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def make(setup,conn,handler):
    state,out,clock,record=setup
    return m.ControlledFixtureRunner(conn,Config({HOST:HostConfig()},{}),clock,state=state,output=out,manifest=read_manifest(MANIFEST,REPO),authorization=record,transport=httpx.MockTransport(handler))

@pytest.mark.parametrize('kind',['source_event_mapping','round_observations'])
def test_kind_pauses_apply_to_exact_fixture_purpose(setup,kind):
    state,out,clock,_=setup
    change_control(state,selector=Selector('kind',kind),paused=True,actor='test',reason='hold fixture kind',now=clock.now())
    calls=[]
    def handler(request):calls.append(str(request.url));return response()
    with m.accounting_connection(state) as conn:
        runner=make(setup,conn,handler)
        result=runner.run()
        assert result['status']=='stopped_incomplete'
        assert len(calls)==(0 if kind=='source_event_mapping' else 2)
        assert conn.execute("SELECT COUNT(*) FROM execution_admissions WHERE state='active'").fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM watches').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM source_generations').fetchone()[0]==0
        assert not runner.gate.inflight

def test_pause_inside_http_drains_retained_body_before_settlement(setup):
    state,out,clock,_=setup
    calls=[];during=[]
    def handler(request):
        calls.append(str(request.url))
        change_control(state,selector=Selector('source','steprightsolutions'),paused=True,actor='test',reason='pause inflight',now=clock.now())
        with sqlite3.connect(state/'state.sqlite') as observer:
            observer.row_factory=sqlite3.Row
            during.append(status(observer,now=clock.now()))
        return response(body=b'<html>retained before drain settles</html>')
    with m.accounting_connection(state) as conn:
        runner=make(setup,conn,handler);result=runner.run()
        assert len(calls)==1 and result['status']=='stopped_incomplete'
        assert len(result['targets'])==1
        retained=out/'bodies'/result['targets'][0]['body_sha256']
        assert retained.read_bytes()==b'<html>retained before drain settles</html>'
        assert during[0]['state']=='pausing'
        assert status(conn,now=clock.now())['state']=='paused'
        assert conn.execute("SELECT COUNT(*) FROM execution_admissions WHERE state!='settled'").fetchone()[0]==0
        assert not runner.gate.inflight

def test_authorizer_allows_lifecycle_but_forbids_domain_writes(setup):
    state,out,clock,_=setup
    with m.accounting_connection(state) as conn:
        with pytest.raises(sqlite3.DatabaseError):conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('forbidden','now',1)")
        with pytest.raises(sqlite3.DatabaseError):conn.execute('DELETE FROM history_acceptance')
        with pytest.raises(sqlite3.DatabaseError):conn.execute('CREATE TABLE forbidden(id)')

def test_existing_exact_full_recipe_and_shared_ceiling_remain(setup):
    state,out,clock,_=setup
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return response(body=b'0' if request.url.path=='/cdx/search/cdx' else b'<html>fixture</html>')
    with m.accounting_connection(state) as conn:
        runner=make(setup,conn,handler);result=runner.run()
        assert result['status']=='captured_pending_independent_review'
        assert len(result['targets'])==5 and len(calls)==6
        assert set(calls)=={t['replay_url'] for t in runner.manifest['targets']}|{runner.manifest['metadata_queries'][0]['probe_url']}
        assert conn.execute('SELECT SUM(requests) FROM host_budget').fetchone()[0]==6
        assert conn.execute("SELECT COUNT(*) FROM execution_admissions WHERE state!='settled'").fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM source_generations').fetchone()[0]==0

def test_pre_io_reservation_failure_retains_debit_and_settles_claim(setup,monkeypatch):
    state,out,clock,_=setup
    with m.accounting_connection(state) as conn:
        runner=make(setup,conn,lambda request:(_ for _ in ()).throw(AssertionError('no HTTP')))
        monkeypatch.setattr(runner,'_reservation',lambda day:(_ for _ in ()).throw(OSError('fixture disk unavailable')))
        result=runner.run()
        assert result['status']=='stopped_incomplete'
        assert conn.execute('SELECT SUM(requests) FROM host_budget').fetchone()[0]==1
        assert conn.execute("SELECT COUNT(*) FROM execution_admissions WHERE state!='settled'").fetchone()[0]==0
        assert not runner.gate.inflight

def test_real_short_wall_limit_preserves_h13_settlement(setup):
    import signal
    state,out,clock,_=setup
    def handler(request):return response(body=b'0' if request.url.path=='/cdx/search/cdx' else b'<html>fixture</html>')
    with m.accounting_connection(state) as conn:
        runner=make(setup,conn,handler)
        with m.existing.wall_limit(5):
            installed=signal.getsignal(signal.SIGALRM)
            result=runner.run()
            assert signal.getsignal(signal.SIGALRM) is installed
            assert 0<signal.getitimer(signal.ITIMER_REAL)[0]<=5
        assert result['status']=='captured_pending_independent_review'
        assert conn.execute("SELECT COUNT(*) FROM execution_admissions WHERE state!='settled'").fetchone()[0]==0

def test_expired_short_timer_rolls_back_and_restores_sqlite_handlers(tmp_path):
    import signal,time
    conn=sqlite3.connect(':memory:',isolation_level=None)
    conn.execute('CREATE TABLE example(value)')
    db=m.FixtureDatabase(tmp_path,conn,None)
    busy=conn.execute('PRAGMA busy_timeout').fetchone()[0]
    previous=signal.getsignal(signal.SIGALRM)
    try:
        with m.existing.wall_limit(.03):
            installed=signal.getsignal(signal.SIGALRM)
            with pytest.raises(m.existing.FixtureStopped,match='elapsed-time'):
                with db.transaction() as tx:
                    tx.execute('INSERT INTO example VALUES (1)')
                    time.sleep(.08)
            assert signal.getsignal(signal.SIGALRM) is installed
            assert conn.execute('SELECT COUNT(*) FROM example').fetchone()[0]==0
            assert conn.execute('PRAGMA busy_timeout').fetchone()[0]==busy
            # A stale expired progress handler would interrupt this query.
            assert conn.execute('WITH RECURSIVE n(i) AS (VALUES(1) UNION ALL SELECT i+1 FROM n WHERE i<5000) SELECT SUM(i) FROM n').fetchone()[0]==12502500
        assert signal.getsignal(signal.SIGALRM) is previous
    finally:db.close()

def test_short_timer_keeps_nested_savepoint_and_read_only_contract(tmp_path):
    conn=sqlite3.connect(':memory:',isolation_level=None)
    conn.execute('CREATE TABLE example(value)')
    db=m.FixtureDatabase(tmp_path,conn,None)
    try:
        with m.existing.wall_limit(5):
            with db.transaction() as tx:
                tx.execute('INSERT INTO example VALUES (1)')
                with pytest.raises(ValueError):
                    with db.transaction() as nested:
                        nested.execute('INSERT INTO example VALUES (2)');raise ValueError('rollback child')
            assert conn.execute('SELECT value FROM example').fetchall()==[(1,)]
            with pytest.raises(sqlite3.OperationalError):
                with db.transaction(immediate=False) as tx:tx.execute('INSERT INTO example VALUES (3)')
    finally:db.close()

def test_transient_settlement_failure_preserves_action_for_outer_cleanup(setup,monkeypatch):
    state,out,clock,_=setup
    original=m.settle;failed=[]
    def once(conn,action,**kwargs):
        if action.startswith('fixture_request_') and not failed:
            failed.append(action);raise RuntimeError('temporary settlement failure')
        return original(conn,action,**kwargs)
    monkeypatch.setattr(m,'settle',once)
    with m.accounting_connection(state) as conn:
        runner=make(setup,conn,lambda request:response(body=b'<html>retained</html>'))
        with pytest.raises(RuntimeError,match='temporary settlement'):runner.run()
        assert failed and runner.gate.action is None and not runner.gate.inflight
        assert conn.execute("SELECT COUNT(*) FROM execution_admissions WHERE state!='settled'").fetchone()[0]==0
        # Failed refund transaction rolled back; retain conservative reservation.
        assert conn.execute('SELECT SUM(bytes) FROM host_budget').fetchone()[0]==8*1024*1024
        assert list((out/'bodies').iterdir())

def test_request_day_remains_grant_day_across_midnight(setup,monkeypatch):
    from datetime import timedelta
    from swingset.config import Config,HostConfig
    state,out,clock,record=setup
    clock.current=clock.now().replace(hour=23,minute=59,second=59)
    record['approved_at']=clock.now().isoformat();record['execution_window']={'starts_at':clock.now().isoformat(),'expires_at':(clock.now()+timedelta(hours=1)).isoformat()}
    with m.accounting_connection(state) as conn:
        runner=m.ControlledFixtureRunner(conn,Config({HOST:HostConfig(daily_byte_budget=32*1024*1024)},{}),clock,state=state,output=out,manifest=read_manifest(MANIFEST,REPO),authorization=record)
        target=runner.manifest['targets'][0];runner.source=target['source']
        original=runner._reservation
        def rollover(day):
            clock.current+=timedelta(seconds=2)
            return original(day)
        monkeypatch.setattr(runner,'_reservation',rollover)
        out.mkdir();runner._save()
        with httpx.Client(transport=httpx.MockTransport(lambda request:response(body=b'body'))) as client:
            runner._request(client,target['replay_url'],target['id'])
        rows=list(map(tuple,conn.execute('SELECT day,requests,bytes FROM host_budget')))
        assert rows==[('2026-01-01',1,4)]
        assert runner.receipt['requests'][0]['request_day']=='2026-01-01'
        assert runner.gate.action is None and not runner.gate.inflight

def test_runtime_and_repo_must_match_exact_source(tmp_path):
    from types import SimpleNamespace
    source=tmp_path/'source';source.mkdir()
    modules={name:SimpleNamespace(__file__=str(source/path)) for name,path in {'swingset.state.db':'src/swingset/state/db.py','swingset.fetch.politeness':'src/swingset/fetch/politeness.py','swingset.state.controls':'src/swingset/state/controls.py','fixture_helpers.fixture_exception':'ignored','fixture_helpers.fixture_transport':'ignored'}.items()}
    for name in ('fixture_helpers.fixture_exception','fixture_helpers.fixture_transport'):
        modules[name]=SimpleNamespace(__file__=str(m.HELPER_ROOT/'fixture_helpers'/(name.split('.')[-1]+'.py')))
    m.verify_imports(source,source,modules=modules)
    other=tmp_path/'other';other.mkdir()
    with pytest.raises(m.existing.FixtureStopped,match='--repo'):m.verify_imports(source,other,modules=modules)
    for name in tuple(modules):
        modified=dict(modules);modified[name]=SimpleNamespace(__file__=str(other/'mixed.py'))
        with pytest.raises(m.existing.FixtureStopped,match='mixed imported'):m.verify_imports(source,source,modules=modified)

@pytest.mark.parametrize('option',['repo','state'])
@pytest.mark.parametrize('equals',[False,True])
def test_repeated_effective_repo_or_state_rejected(option,equals):
    argv=['--manifest','m','--state','original-state','--quarantine','q','--repo','original-repo']
    argv+=['--'+option+'=other'] if equals else ['--'+option,'other']
    with pytest.raises(m.existing.FixtureStopped,match='repeated option'):
        m.parse_arguments(argv)

def test_equals_spelling_is_canonicalized_for_original_parser():
    args=m.parse_arguments(['--manifest=m','--state=s','--quarantine=q','--repo=p','--authorization=a','--execution-gate=g','--execution-gate-sha256=h','--execute'])
    assert m.original_arguments(args)[1:]==['--manifest','m','--state','s','--quarantine','q','--authorization','a','--repo','p','--execute']

def test_actual_selected_state_checked_before_open(tmp_path):
    first=tmp_path/'first';first.mkdir();second=tmp_path/'second';second.mkdir()
    assert m.verify_state(first,{'state':str(first)})==first
    with pytest.raises(m.existing.FixtureStopped,match='execution state differs'):
        m.verify_state(second,{'state':str(first)})

FROZEN = Path('/Users/skeswa/.cache/swingset-h16-event-preservation-tests-20260916')

def test_actual_frozen_receipt_and_apis_are_used():
    import json
    from swingset.state import db
    assert db.SCHEMA_VERSION == 14
    assert Path(db.__file__).resolve().is_relative_to(FROZEN)
    assert m.digest(FROZEN/'h16-source.json') == m.FROZEN_RECEIPT_SHA
    for name, expected in json.loads((FROZEN/'h16-source.json').read_bytes())['files'].items():
        assert m.digest(FROZEN/name) == (expected['sha256'] if isinstance(expected,dict) else expected)
    m.verify_imports(FROZEN,FROZEN)

@pytest.mark.parametrize('damage',['manifest','helper','extra_code','bytecode','extension','data','symlink'])
def test_helper_closure_rejects_changes_before_import(tmp_path,damage):
    import shutil
    target=tmp_path/'helpers';shutil.copytree(m.HELPER_ROOT,target)
    if damage=='manifest':(target/'closure.json').write_text('{}')
    elif damage=='helper':(target/'fixture_helpers/fixture_transport.py').write_text('raise AssertionError("never load")')
    elif damage=='extra_code':(target/'fixture_helpers/extra.py').write_text('raise AssertionError("never load")')
    elif damage=='bytecode':
        (target/'fixture_helpers/__pycache__').mkdir()
        (target/'fixture_helpers/__pycache__/fixture_exception.cpython-313.pyc').write_bytes(b'not allowed')
    elif damage=='extension':(target/'fixture_helpers/fixture_exception.so').write_bytes(b'not allowed')
    elif damage=='data':(target/'undeclared.data').write_bytes(b'not allowed')
    else:
        victim=target/'fixture_helpers/__init__.py'
        outside=tmp_path/'init.py';outside.write_bytes(victim.read_bytes());victim.unlink();victim.symlink_to(outside)
    with pytest.raises(RuntimeError,match='helper'):
        m.verify_helpers(target)

def coordinator_gate():
    return {'format':'fixture-h13-coordinator-v2','driver_sha256':m.digest(Path(m.__file__)),
            'source':str(FROZEN),'source_receipt':'h16-source.json','source_receipt_sha256':m.FROZEN_RECEIPT_SHA,
            'helper_root':str(m.HELPER_ROOT),'helper_manifest_sha256':m.HELPER_MANIFEST_SHA}

@pytest.mark.parametrize('field',['driver_sha256','source_receipt_sha256','helper_manifest_sha256','helper_root','format'])
def test_coordinator_rejects_wrong_pins(tmp_path,monkeypatch,field):
    import json
    monkeypatch.setattr(m,'FROZEN_SOURCE',FROZEN)
    gate=coordinator_gate();gate[field]='wrong'
    path=tmp_path/'gate.json';path.write_text(json.dumps(gate))
    with pytest.raises(m.existing.FixtureStopped):m.execution_gate(path,m.digest(path),FROZEN)

def test_coordinator_verifies_exact_frozen_and_helper_closures(tmp_path,monkeypatch):
    import json
    monkeypatch.setattr(m,'FROZEN_SOURCE',FROZEN)
    gate=coordinator_gate();path=tmp_path/'gate.json';path.write_text(json.dumps(gate))
    assert m.execution_gate(path,m.digest(path),FROZEN)==gate
    with pytest.raises(m.existing.FixtureStopped,match='gate hash'):
        m.execution_gate(path,'0'*64,FROZEN)

@pytest.mark.parametrize('schema',[14,23])
def test_exact_schema_and_publication_gate_before_work(setup,schema):
    from swingset.state.db import open_database
    state,_,_,_=setup
    baseline=state/'baseline';(baseline/'_meta').mkdir(parents=True)
    (baseline/'PUBLISHED').write_text('reviewed-publication')
    (baseline/'_meta/manifest.json').write_text('{}')
    gate={'schema':14,'baseline_path':str(baseline.resolve()),'published_sha256':m.digest(baseline/'PUBLISHED'),
          'manifest_sha256':m.digest(baseline/'_meta/manifest.json')}
    with open_database(state) as db:
        db.connection.execute("UPDATE meta SET value=? WHERE key='schema_version'",(str(schema),))
        if schema==14:m.verify_database(db.connection,state,gate)
        else:
            with pytest.raises(m.existing.FixtureStopped,match='schema'):m.verify_database(db.connection,state,gate)
        assert db.connection.execute('SELECT COUNT(*) FROM execution_admissions').fetchone()[0]==0
        assert db.connection.execute('SELECT COUNT(*) FROM host_budget').fetchone()[0]==0

def test_removed_scheduled_hold_stops_before_first_body(setup):
    state,output,_,_=setup
    (state/'operator-hold').unlink()
    calls=[]
    with m.accounting_connection(state) as conn:
        result=make(setup,conn,lambda request:calls.append(request)).run()
        assert result['status']=='stopped_incomplete' and not calls
        assert 'hold' in result['stop_reason']
        assert conn.execute('SELECT COUNT(*) FROM host_budget').fetchone()[0]==0

def test_removed_hold_after_response_blocks_next_request_preserves_body(setup):
    state,output,_,_=setup
    calls=[]
    def handler(request):
        calls.append(request);(state/'operator-hold').unlink();return response(body=b'held result')
    with m.accounting_connection(state) as conn:
        result=make(setup,conn,handler).run()
        assert len(calls)==1 and len(result['targets'])==1 and result['status']=='stopped_incomplete'
        assert (output/'bodies'/result['targets'][0]['body_sha256']).read_bytes()==b'held result'
        assert conn.execute("SELECT COUNT(*) FROM execution_admissions WHERE state!='settled'").fetchone()[0]==0

def test_absent_approval_never_opens_accounting_or_constructs_http(setup,monkeypatch):
    import sys
    state,output,_,_=setup
    gate={**coordinator_gate(),'state':str(state.resolve())}
    monkeypatch.setattr(m,'execution_gate',lambda *args:gate)
    monkeypatch.setattr(m,'accounting_connection',lambda *args:(_ for _ in ()).throw(AssertionError('no state opening')))
    monkeypatch.setattr(httpx,'Client',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('no client')))
    monkeypatch.setattr(sys,'argv',[str(m.__file__),'--manifest',str(MANIFEST),'--state',str(state),'--quarantine',str(output),
                                   '--repo',str(FROZEN),'--execution-gate','unused','--execution-gate-sha256','unused','--execute'])
    import os
    old=os.umask(0o077)
    try:
        with pytest.raises(m.existing.FixtureStopped,match='authorization'):m.main()
    finally:os.umask(old)
    assert not output.exists() and (state/'operator-hold').exists()
