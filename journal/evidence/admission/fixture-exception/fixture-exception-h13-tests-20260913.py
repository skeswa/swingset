import importlib.util
import sqlite3
from pathlib import Path
import pytest
import httpx
from test_fixture_exception import setup as setup, REPO, MANIFEST, HOST, read_manifest, response
from swingset.config import Config,HostConfig
from swingset.state.controls import Selector,change_control,status
spec=importlib.util.spec_from_file_location('fixture_exception_h13','/tmp/fixture_exception_h13.py')
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
    modules={name:SimpleNamespace(__file__=str(source/path)) for name,path in {'swingset.state.db':'src/swingset/state/db.py','swingset.fetch.politeness':'src/swingset/fetch/politeness.py','swingset.state.controls':'src/swingset/state/controls.py','research.fixture_exception':'research/fixture_exception.py','research.fixture_transport':'research/fixture_transport.py'}.items()}
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
