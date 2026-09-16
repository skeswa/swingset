"""Coordinator-reviewed finite phase-one resume. No migration or input acceptance.

Default preflight; requires a separately hashed gate populated from actual evidence.
Execution is deliberately not wired to a timer. All ordinary request/admission
controls remain in effect. Use an external hard timeout for blocking HTTP calls.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

TARGET_IDS = frozenset('a9a1981e91474f1967356d61 ea4e68c086533dd6581c3015 24eaae0d5fea3221affb5a0a 828e41e7f8f4bfd34d171092 3c5a6116618208e4556dd9e7 e7dcb140bcbd077c67c0d425 f140e9fd5864d1c777006540 763f8950892b9e719910fcd3 f0dcc51ff963f79c44d90734 fa9d6701e2418b435644b296 9fad90785a2f773113d0c84d 032270847f79d7aebca98f65 edb1d1478b3fe19a626a30d7 017e59a25155c25983b14000 2b8b7d5b424f394e25c2b7bb 9592958a3cd694d86e677fc5 2dd8b6dc4d2d264495a33f0f'.split())

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def rows_hash(conn, table):
    rows=sorted((tuple(r) for r in conn.execute('SELECT * FROM '+table)),key=repr)
    return hashlib.sha256(json.dumps(rows,separators=(',',':'),default=str).encode()).hexdigest()

def inputs(conn):
    return {'accepted_inputs':rows_hash(conn,'accepted_inputs'), 'accepted_meta':list(map(tuple,conn.execute("SELECT key,value FROM meta WHERE key LIKE 'accepted_%' OR key='input_bundle_hash' ORDER BY key")))}

def check_authority(conn,state,gate):
    if json.loads(json.dumps(inputs(conn))) != gate['inputs']:
        raise ValueError('accepted input authority changed')
    baseline=(state/'baseline').resolve(strict=True)
    if str(baseline)!=gate['baseline_path'] or sha(baseline/'PUBLISHED')!=gate['published_sha256'] or sha(baseline/'_meta/manifest.json')!=gate['manifest_sha256']:
        raise ValueError('public baseline changed')
    if (state/'RESTORE_PENDING').exists(): raise ValueError('restore pending')

def check_targets(conn,state,gate,load_catalog,get_page_kind):
    path=state/'phase1-catalog.json'
    if sha(path)!=gate['catalog_sha256'] or sha(state/'phase1-ledger.json')!=gate['ledger_sha256']:
        raise ValueError('catalog or ledger changed; review a fresh continuation gate')
    targets=load_catalog(path)
    ledger=json.loads((state/'phase1-ledger.json').read_bytes())['targets']
    pending={t.target_id for t in targets if ledger.get(t.target_id,{}).get('status','pending')=='pending'}
    remaining=set(gate['remaining_target_ids'])
    if not remaining<=TARGET_IDS or pending!=remaining:
        raise ValueError('pending catalog is not the exact reviewed remainder of 17 targets')
    original={t.target_id:t for t in targets if t.target_id in TARGET_IDS}
    if set(original)!=TARGET_IDS: raise ValueError('original target identities missing')
    identities={key:{'source':t.source,'parser':t.parser,'url':t.url,'timestamp':t.timestamp} for key,t in original.items()}
    if identities!=gate['original_targets']: raise ValueError('original target identities changed')
    statuses={key:ledger.get(key,{}).get('status','pending') for key in TARGET_IDS}
    if statuses!=gate['target_statuses']: raise ValueError('reviewed target statuses changed')
    for t in targets:
        status=ledger.get(t.target_id,{})
        if t.target_id in TARGET_IDS:
            if t.parser!='wsdc_calendar.events' or t.source!='wsdc_calendar' or not t.archive_url:
                raise ValueError('pending target purpose changed')
        if t.target_id not in remaining and status.get('status') not in {'duplicate','finding','parsed','empty'}:
            raise ValueError('unexpected intake work')
        elif status.get('status') in {'parsed','empty'} and str(status.get('parser_version'))!=str(get_page_kind(t.parser).PARSER_VERSION):
            raise ValueError('existing interpretation needs offline replay before this finite intake')
    return {t.target_id:t for t in targets if t.target_id in remaining}

def debit_gate(conn,state,gate,targets,watch_id,host,*,now,elapsed):
    check_authority(conn,state,gate)
    if now.date().isoformat()!=gate['utc_day'] or elapsed>=gate['wall_seconds']:
        return 'finite phase1 execution window ended'
    row=conn.execute('SELECT source,parser,url,archive_url FROM watches WHERE watch_id=?',(watch_id,)).fetchone()
    if host!='web.archive.org' or row is None or not any(tuple(row)==(t.source,t.parser,t.url,t.archive_url) for t in targets.values()):
        return 'outside exact reviewed phase1 targets'
    return None

def scoped_client(base,state,gate,targets,*,monotonic=time.monotonic):
    from swingset.fetch.politeness import Paused
    started=monotonic()
    class ScopedClient(base):
        active_watch=None
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            acquire=self.gate.acquire
            def bounded(host,**kw):
                # Existing issue() calls this inside its admission transaction.
                if reason:=debit_gate(self.connection,state,gate,targets,self.active_watch,host,now=self.clock.now(),elapsed=monotonic()-started):
                    return Paused(reason)
                return acquire(host,**kw)
            self.gate.acquire=bounded
        def fetch(self,watch_id,*args,**kwargs):
            self.active_watch=watch_id
            return super().fetch(watch_id,*args,**kwargs)
    return ScopedClient

def budget(conn,day):
    row=conn.execute("SELECT requests,bytes FROM host_budget WHERE host='web.archive.org' AND day=?",(day,)).fetchone()
    return tuple(row) if row else (0,0)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gate',type=Path,required=True);p.add_argument('--gate-sha256',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true')
    a=p.parse_args()
    if sha(a.gate)!=a.gate_sha256: raise ValueError('gate hash differs')
    g=json.loads(a.gate.read_bytes())
    if sha(Path(__file__).resolve())!=g.get('driver_sha256'): raise ValueError('copied reviewed driver differs')
    if g.get('format')!='phase1-17-resume-v1' or g.get('coordinator_reviewed') is not True:
        raise ValueError('reviewed coordinator gate required; not owner year acceptance')
    state=Path(g['state']).resolve(strict=True);source=Path(g['source']).resolve(strict=True)
    if 'checkpoints' in state.parts or not str(source).startswith('/nix/store/'):
        raise ValueError('live state and immutable deployed runtime required')
    output=a.output.absolute()
    if output.exists() or output.is_symlink() or output.parent.resolve()!=output.parent or output.is_relative_to(source) or not output.is_relative_to(state/'operations'):
        raise ValueError('new receipt must be outside evidence/source, in real operations directory')
    final=output.with_suffix(output.suffix+'.finished.json')
    if final.exists() or final.is_symlink(): raise ValueError('completion receipt already exists')
    if Path(g['source_receipt']).is_absolute() or '..' in Path(g['source_receipt']).parts: raise ValueError('invalid source receipt path')
    if sha(source/g['source_receipt'])!=g['source_receipt_sha256']: raise ValueError('source receipt differs')
    inventory=json.loads((source/g['source_receipt']).read_bytes())['files']
    for name,expected in inventory.items():
        if Path(name).is_absolute() or '..' in Path(name).parts: raise ValueError('invalid source inventory path')
        if sha(source/name)!=(expected['sha256'] if isinstance(expected,dict) else expected): raise ValueError('source file differs')
    import swingset.state.db as db_module
    from swingset.clock import SystemClock
    from swingset.config import load_config
    from swingset.history import intake
    from swingset.history.catalog import load_catalog
    from swingset.sources import get_page_kind
    if Path(db_module.__file__).resolve()!=source/'src/swingset/state/db.py': raise ValueError('wrong imported runtime')
    # Own the ordinary writer lock, but never invoke the migrating opener.
    with (state/'state.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        conn=sqlite3.connect(f'file:{state}/state.sqlite?mode={"rw" if a.execute else "ro"}',uri=True,isolation_level=None)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        if a.execute: conn.execute('PRAGMA synchronous=FULL')
        db=db_module.Database(state,conn,None)
        try:
            schema=int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
            if schema!=g['schema'] or schema!=db_module.SCHEMA_VERSION: raise ValueError('exact deployed schema required; no migration')
            check_authority(conn,state,g)
            targets=check_targets(conn,state,g,load_catalog,get_page_kind)
            now=datetime.now(UTC);day=g['utc_day']
            if now.date().isoformat()!=day or day<'2026-09-14': raise ValueError('not the reviewed next UTC quota window')
            seconds=g['wall_seconds'];cap=g['max_additional_requests']
            if not 0<seconds<=600 or not max(1,len(targets))<=cap<=48: raise ValueError('invalid finite limits')
            if (now.replace(hour=23,minute=59,second=59)-now).total_seconds()<seconds+60: raise ValueError('too close to UTC rollover')
            before=budget(conn,day)
            config=intake.intake_config(load_config(source/'config'))
            policy=config.host('web.archive.org')
            if before[0]>=min(200,policy.daily_request_budget): raise ValueError('Archive quota exhausted')
            config=replace(config,hosts={**config.hosts,'web.archive.org':replace(policy,daily_request_budget=min(200,policy.daily_request_budget,before[0]+cap))})
            report={'gate_sha256':a.gate_sha256,'execute':a.execute,'started_at':now.isoformat(),'schema':schema,'targets':sorted(targets),'budget_before':before,'request_cap':cap,'wall_seconds':seconds,'deadline':'cooperative; external hard timeout required','source':str(source)}
            # Durable initial receipt before the first possible source request.
            fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as f: json.dump(report,f,sort_keys=True);f.flush();os.fsync(f.fileno())
            if a.execute and targets:
                original=intake.FetchClient
                intake.FetchClient=scoped_client(original,state,g,targets)
                try:
                    ledger=intake.run_intake(db,state/'phase1-catalog.json',config=config,clock=SystemClock(),max_targets=len(targets),wall_seconds=seconds,retry_failures=False)
                    report['statuses']={k:ledger['targets'][k]['status'] for k in sorted(targets)}
                finally:
                    intake.FetchClient=original
                    report['budget_after']=budget(conn,day)
                    report['charged_attempts']=report['budget_after'][0]-before[0]
                    report['finished_at']=datetime.now(UTC).isoformat()
                    final=output.with_suffix(output.suffix+'.finished.json')
                    fd=os.open(final,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                    with os.fdopen(fd,'w') as f: json.dump(report,f,sort_keys=True);f.flush();os.fsync(f.fileno())
        finally: db.close()

if __name__=='__main__': main()
