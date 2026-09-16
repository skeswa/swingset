"""H13 lifecycle adapter for the existing exact, still-unapproved fixture runner.

No new transport/allowlist, no parser activation. Default original dry-run is pure.
Execution requires --execution-gate plus the original actual owner authorization.
"""
from __future__ import annotations
import contextlib
import argparse
import fcntl
import hashlib
import json
import sqlite3
import signal
import time
import sys
from pathlib import Path
from uuid import uuid4

from research import fixture_exception as existing
from research.fixture_transport import FixtureRunner
from swingset.fetch.politeness import Gate, Grant, Paused
from swingset.state.controls import ActionScope, ControlPaused, admission, operation, settle
from swingset.state.db import Database

CONTROL_TABLES=frozenset({'execution_admissions','execution_dependencies','control_state','control_events','operator_pauses'})

def accounting_only(action,table,column,*extra):
    if action in {sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE} and table in CONTROL_TABLES:
        return sqlite3.SQLITE_OK
    return existing._accounting_only(action,table,column,*extra)

class FixtureDatabase(Database):
    @contextlib.contextmanager
    def transaction(self,*,immediate=True):
        delay,interval=signal.getitimer(signal.ITIMER_REAL)
        if self.connection.in_transaction or not immediate or not 0<delay<=45 or interval:
            with super().transaction(immediate=immediate) as conn:yield conn
            return
        # The existing CLI alarm already imposes a stricter bound than H13.
        # Preserve its handler and timer, rather than replacing either one.
        deadline=time.monotonic()+delay
        previous_busy=int(self.connection.execute('PRAGMA busy_timeout').fetchone()[0])
        self.connection.execute('PRAGMA busy_timeout='+str(min(previous_busy,max(1,int(delay*1000)))))
        self.connection.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
        try:
            self.connection.execute('BEGIN IMMEDIATE')
            yield self.connection
            if time.monotonic()>=deadline:raise existing.FixtureStopped('elapsed-time ceiling during bookkeeping')
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            self.connection.set_progress_handler(None,0)
            self.connection.execute('PRAGMA busy_timeout='+str(previous_busy))

class Deferred(Exception):
    def __init__(self,grant): self.grant=grant

class AdmittedGate(Gate):
    def __init__(self,runner):
        super().__init__(runner.conn,runner.config,runner.clock)
        self.runner=runner;self.action=None;self.request_day=None
    def acquire(self,host,**kwargs):
        action='fixture_request_'+uuid4().hex
        acquired=False
        try:
            with admission(self.runner.database,action_id=action,action_kind='request',scope=self.runner.scope(host),now=self.clock.now()):
                original_clock=self.clock
                at=original_clock.now()
                class DebitClock:
                    def now(self):return at
                self.clock=DebitClock()
                try:grant=super().acquire(host,**kwargs)
                finally:self.clock=original_clock
                acquired=isinstance(grant,Grant)
                if not acquired:raise Deferred(grant)
            self.action=action;self.request_day=at.date().isoformat()
            return grant
        except Deferred as exc:return exc.grant
        except ControlPaused:return Paused('operator')
        except BaseException:
            if acquired:self.discard(host)
            if self.connection.in_transaction:self.connection.rollback()
            if self.connection.execute('SELECT 1 FROM execution_admissions WHERE action_id=?',(action,)).fetchone():
                with self.runner.database.transaction() as conn:settle(conn,action,now=self.clock.now(),outcome='not_issued')
            raise
    def release(self,host,classification,**kwargs):
        try:
            with self.runner.database.transaction() as conn:
                kwargs['request_day']=self.request_day
                super().release(host,classification,**kwargs)
                if self.action:settle(conn,self.action,now=self.clock.now(),outcome=classification.outcome.value)
            self.action=None;self.request_day=None
        finally:
            self.discard(host)

class ControlledFixtureRunner(FixtureRunner):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.database=FixtureDatabase(self.state,self.conn,None)
        self.kind='source_event_mapping'
        self.gate=AdmittedGate(self)
    def _reservation(self,day):
        if self.gate.request_day is None:raise existing.FixtureStopped('missing durable debit day')
        return super()._reservation(self.gate.request_day)
    def _save(self):
        if self.gate.action and self.receipt['requests']:
            self.receipt['requests'][-1]['request_day']=self.gate.request_day
        super()._save()
    def scope(self,host):
        return ActionScope(sources=frozenset({self.source}),kinds=frozenset({self.kind}),host=host)
    def _exchange(self,client,url,purpose):
        try:return super()._exchange(client,url,purpose)
        finally:
            # Reservation/receipt failures can precede the older transport's
            # I/O finally block. Retain every debit and settle only this claim.
            if self.gate.action:
                action=self.gate.action
                self.gate.discard(existing.HOST)
                with self.database.transaction() as conn:
                    settle(conn,action,now=self.clock.now(),outcome='failed_before_retention')
                self.gate.action=None;self.gate.request_day=None
    def _request(self,client,initial,purpose):
        prior=self.kind
        if purpose!='robots':
            self.kind='round_observations' if purpose in {'srs-round507','srs-round508'} else 'source_event_mapping'
        try:
            with operation(self.database,action_id='fixture_fetch_'+uuid4().hex,action_kind='fetch',scope=self.scope(existing.HOST),clock=self.clock):
                return super()._request(client,initial,purpose)
        except ControlPaused as exc:
            raise existing.FixtureStopped('operator control pauses fixture purpose') from exc
        finally:self.kind=prior

@contextlib.contextmanager
def accounting_connection(state):
    if (state/'RESTORE_PENDING').exists():raise existing.FixtureStopped('restore verification pending')
    with (state/'state.lock').open('r+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        conn=sqlite3.connect((state/'state.sqlite').resolve().as_uri()+'?mode=rw',uri=True,isolation_level=None)
        try:
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA synchronous=FULL');conn.execute('PRAGMA foreign_keys=ON')
            for table in CONTROL_TABLES:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():raise existing.FixtureStopped('H13 control schema missing')
            conn.set_authorizer(accounting_only)
            yield conn
        finally:conn.close()

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()

def verify_imports(source,repo,*,modules=None):
    if Path(repo).resolve(strict=True)!=source:raise existing.FixtureStopped('--repo must equal exact reviewed source')
    selected=sys.modules if modules is None else modules
    for name,module in tuple(selected.items()):
        if name=='swingset' or name.startswith('swingset.'):
            expected=source/'src'/Path(*name.split('.'))
            location=getattr(module,'__file__',None)
            if location is None or Path(location).resolve() not in {expected.with_suffix('.py'),expected/'__init__.py'}:
                raise existing.FixtureStopped('mixed imported runtime: '+name)
        elif name in {'research.fixture_exception','research.fixture_transport'}:
            if Path(module.__file__).resolve()!=source/'research'/(name.split('.')[-1]+'.py'):
                raise existing.FixtureStopped('mixed imported fixture module: '+name)

def execution_gate(path,expected,repo):
    if digest(path)!=expected:raise existing.FixtureStopped('execution gate hash differs')
    gate=json.loads(path.read_bytes())
    if gate.get('format')!='fixture-h13-coordinator-v1' or gate.get('driver_sha256')!=digest(__file__):raise existing.FixtureStopped('reviewed copied driver differs')
    source=Path(gate['source']).resolve(strict=True)
    if not str(source).startswith('/nix/store/') or Path(existing.__file__).resolve()!=source/'research/fixture_exception.py':raise existing.FixtureStopped('exact frozen runtime required')
    verify_imports(source,repo)
    receipt=source/gate['source_receipt']
    if Path(gate['source_receipt']).is_absolute() or '..' in Path(gate['source_receipt']).parts or digest(receipt)!=gate['source_receipt_sha256']:raise existing.FixtureStopped('reviewed source receipt differs')
    for name,value in json.loads(receipt.read_bytes())['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or digest(source/name)!=(value['sha256'] if isinstance(value,dict) else value):raise existing.FixtureStopped('source inventory differs')
    return gate

def parse_arguments(argv):
    class Once(argparse.Action):
        def __call__(self,parser,namespace,values,option_string=None):
            provided=getattr(namespace,'_provided',set())
            if self.dest in provided:raise existing.FixtureStopped('repeated option: '+str(option_string))
            provided.add(self.dest);namespace._provided=provided
            setattr(namespace,self.dest,True if self.nargs==0 else values)
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    for name in ('manifest','state','quarantine'):
        parser.add_argument('--'+name,type=Path,required=True,action=Once)
    for name in ('authorization','repo','execution-gate'):
        parser.add_argument('--'+name,type=Path,action=Once)
    parser.add_argument('--execution-gate-sha256',action=Once)
    parser.add_argument('--execute',nargs=0,default=False,action=Once)
    args=parser.parse_args(argv)
    if args.execute and (args.repo is None or args.execution_gate is None or args.execution_gate_sha256 is None):
        raise existing.FixtureStopped('explicit pinned repo and hashed coordinator gate required')
    return args

def original_arguments(args):
    result=[sys.argv[0]]
    for name in ('manifest','state','quarantine','authorization','repo'):
        if (value:=getattr(args,name)) is not None:result+=['--'+name,str(value)]
    if args.execute:result.append('--execute')
    return result

def verify_state(selected,gate):
    state=selected.resolve(strict=True)
    if str(state)!=gate['state'] or 'checkpoints' in state.parts:
        raise existing.FixtureStopped('execution state differs')
    return state

def main():
    args=parse_arguments(sys.argv[1:])
    original_argv=sys.argv[:]
    if not args.execute:
        try:
            sys.argv=original_arguments(args)
            existing.main()
        finally:sys.argv=original_argv
        return
    # One effective parse; the original CLI receives only canonical unique options.
    repo=args.repo
    gate=execution_gate(args.execution_gate,args.execution_gate_sha256,repo)
    state=verify_state(args.state,gate)
    original_accounting=existing.accounting_connection
    @contextlib.contextmanager
    def verified_accounting(selected):
        selected=verify_state(selected,gate)
        with accounting_connection(selected) as conn:
            verify_imports(Path(gate['source']).resolve(),repo)
            baseline=(selected/'baseline').resolve(strict=True)
            if str(baseline)!=gate['baseline_path'] or digest(baseline/'PUBLISHED')!=gate['published_sha256'] or digest(baseline/'_meta/manifest.json')!=gate['manifest_sha256']:raise existing.FixtureStopped('verified publication baseline changed')
            import swingset.state.db as db_module
            schema=int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
            if schema!=gate['schema'] or schema!=db_module.SCHEMA_VERSION:raise existing.FixtureStopped('exact runtime schema required; migration forbidden')
            yield conn
    import research.fixture_transport as transport
    original_runner=transport.FixtureRunner
    existing.accounting_connection=verified_accounting;transport.FixtureRunner=ControlledFixtureRunner
    try:
        sys.argv=original_arguments(args)
        existing.main()
    finally:
        sys.argv=original_argv
        existing.accounting_connection=original_accounting;transport.FixtureRunner=original_runner

if __name__=='__main__':main()
