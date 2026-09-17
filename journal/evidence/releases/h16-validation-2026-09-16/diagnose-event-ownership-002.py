"""Read-only event ownership/support diagnostic; no replay or reconstruction writes."""
import hashlib
import json
import signal
import sqlite3
import time
from collections import Counter
from pathlib import Path
import duckdb
import pyarrow.parquet as pq
from swingset.build.closure import hydrate
from swingset.build.closure_rows import _legacy_matches, _normalize
from swingset.build.closure_manifest import canonical
from swingset.build.closure_support import interpretation_lookup

BASE = Path('/var/tmp/swingset-h16-proof-build')
OLD = BASE / 'candidates/cand_7f8cf9bcbf7e4a60'
NEW = BASE / 'candidates/cand_cbbeeadd90634cc9'
OUT = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-validation-2026-09-16/event-ownership-diagnostic-002.json')
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('180 second bound')))
signal.alarm(180)
start=time.monotonic()
report={'read_only':True,'passed':False}
try:
    db=sqlite3.connect((BASE/'state.sqlite').as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    public=json.loads((NEW/'_meta/manifest.json').read_text())['release_policy']['closure']
    private=hydrate(db,public)
    support=interpretation_lookup(private)
    memory=sqlite3.connect(':memory:')
    memory.execute('CREATE TABLE baseline(table_name,record_key,payload,PRIMARY KEY(table_name,record_key))')
    duck=duckdb.connect()
    duck.execute("SET memory_limit='512MB'")
    duck.execute('SET threads=1')
    old={r['event_id']: _normalize(r) for f in (OLD/'data/events').glob('*.parquet') for r in pq.read_table(f).to_pylist()}
    memory.executemany('INSERT INTO baseline VALUES (?,?,?)',(('events',canonical([k]),canonical(v)) for k,v in old.items()))
    present={r[0] for r in duck.execute(f"SELECT event_id FROM read_parquet('{NEW}/data/events/*.parquet',hive_partitioning=false)").fetchall()}
    target='2020-01-municorn-swing'
    report['target_selected_event_rows']=[]
    rejected=[]
    selectedevents=set()
    report['target_scope_ownership']=[dict(r) for r in db.execute('SELECT * FROM canonical_scope_rows WHERE table_name=\'events\' AND record_key=?',(canonical([target]),))]
    for generation in private['selected']:
        if generation['stage']!='project' or generation['unit_kind'] in ('dancer','source_index','source_event'):
            continue
        for row in db.execute("SELECT record_key,payload_json FROM derivation_rows WHERE generation_id=? AND table_name='events'",(generation['generation_id'],)):
            key,raw=row
            payload=json.loads(raw)
            event=payload['event_id']
            selectedevents.add(event)
            snapshot=payload.get('snapshot_id')
            state=support.get((str(snapshot),str(payload.get('parser_version',''))),{'state':'legacy_unassessed'})['state'] if snapshot else 'not_required'
            matches=_legacy_matches(memory,'events',key,payload) if snapshot and state!='accepted' else None
            fields={k:{'selected':v,'baseline':old.get(event,{}).get(k)} for k,v in payload.items() if v != old.get(event,{}).get(k)}
            info={'generation':generation,'payload':payload,'support_state':state,'legacy_matches':matches,'baseline_differences':fields,'in_public':event in present}
            if event==target: report['target_selected_event_rows'].append(info)
            if snapshot and state!='accepted' and not matches: rejected.append(info)
    report['selected_event_count']=len(selectedevents)
    report['public_event_count']=len(present)
    report['rejected_event_row_count']=len(rejected)
    report['rejected_unique_events']=len({x['payload']['event_id'] for x in rejected})
    report['rejection_by_unit_kind']=dict(Counter(x['generation']['unit_kind'] for x in rejected))
    report['rejection_fields']=dict(Counter(k for x in rejected for k in x['baseline_differences']))
    report['rejected_examples']=rejected[:8]
    report['rejected_events_in_public']=[x['payload']['event_id'] for x in rejected if x['in_public']][:20]
    missing = set(old)-present
    rejectedids={x['payload']['event_id'] for x in rejected}
    report['missing_baseline_events']=len(missing)
    report['missing_baseline_events_without_rejected_event']=sorted(missing-rejectedids)[:30]
    report['judge_loss_distribution']=duck.execute(f"SELECT count(*),count(*) FILTER(WHERE j.wsdc_id IS NULL),count(DISTINCT j.event_id) FROM read_parquet('{OLD}/data/judges/*.parquet',hive_partitioning=false) j LEFT JOIN read_parquet('{NEW}/data/judges/*.parquet',hive_partitioning=false) n USING(judge_id) WHERE n.judge_id IS NULL").fetchall()
    report['missing_judge_events_without_event_rejection']=[]
    for event,n in duck.execute(f"SELECT j.event_id,count(*) FROM read_parquet('{OLD}/data/judges/*.parquet',hive_partitioning=false) j LEFT JOIN read_parquet('{NEW}/data/judges/*.parquet',hive_partitioning=false) n USING(judge_id) WHERE n.judge_id IS NULL GROUP BY j.event_id").fetchall():
        if event not in rejectedids: report['missing_judge_events_without_event_rejection'].append([event,n])
    report['state_registry_nulls']=db.execute("SELECT count(*) FROM registry_placements WHERE event_month>='2010-01' AND event_id IS NULL").fetchone()[0]
    report['state_target_registry']= [dict(r) for r in db.execute("SELECT event_id,series_id,event_month,count(*) n FROM registry_placements WHERE series_id='wsdc-291' AND substr(event_month,1,7)='2020-01' GROUP BY 1,2,3")]
    report['passed']=True
    report['connection_total_changes']=db.total_changes
except BaseException as error:
    report['error']={'type':type(error).__name__,'message':str(error)}
    raise
finally:
    signal.alarm(0)
    report['elapsed_seconds']=time.monotonic()-start
    OUT.write_text(json.dumps(report,indent=2,sort_keys=True,default=str)+'\n') if not OUT.exists() else (_ for _ in ()).throw(FileExistsError(OUT))
    print(json.dumps({'output':str(OUT),'passed':report['passed'],'error':report.get('error')}),flush=True)
