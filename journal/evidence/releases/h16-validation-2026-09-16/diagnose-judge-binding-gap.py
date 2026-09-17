"""Explain the one-row difference between deletion and retained-locator checks."""
import json
import signal
import sqlite3
import time
from pathlib import Path
import duckdb
from swingset.state.identity_references import ReferenceReader

signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('60 second bound')))
signal.alarm(60)
start=time.monotonic()
root=Path('/var/tmp/swingset-h16-proof-build')
out=Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-validation-2026-09-16/judge-binding-gap.json')
state=sqlite3.connect((root/'state.sqlite').as_uri()+'?mode=ro',uri=True)
state.execute('PRAGMA query_only=ON')
state.execute('BEGIN')
db=duckdb.connect()
db.execute("SET memory_limit='256MB'")
db.execute('SET threads=1')
cursor=db.execute(f"SELECT j.judge_id,j.event_id,j.name_raw,j.wsdc_id,j.source,j.snapshot_id FROM read_parquet('{root}/candidates/cand_7f8cf9bcbf7e4a60/data/judges/*.parquet',hive_partitioning=false) j LEFT JOIN read_parquet('{root}/candidates/cand_cbbeeadd90634cc9/data/judges/*.parquet',hive_partitioning=false) n USING(judge_id) WHERE n.judge_id IS NULL ORDER BY j.snapshot_id,j.judge_id")
columns=[c[0] for c in cursor.description]
reader=ReferenceReader(state)
previous=None
report={'read_only':True,'missing':0,'with_bindings':0,'without_bindings':[]}
for values in cursor.fetchall():
    row=dict(zip(columns,values))
    if previous != row['snapshot_id']:
        reader.rows.clear()
        previous=row['snapshot_id']
    report['missing']+=1
    if reader.for_record('judge',row): report['with_bindings']+=1
    else:
        row['snapshot_metadata']=state.execute('SELECT w.source_ref,s.url FROM snapshots s JOIN watches w USING(watch_id) WHERE snapshot_id=?',(row['snapshot_id'],)).fetchall()
        row['indexed_judge_names']=[k[1] for k in reader._index(row['snapshot_id']) if k[0]=='judge']
        report['without_bindings'].append(row)
report['elapsed_seconds']=time.monotonic()-start
report['connection_total_changes']=state.total_changes
out.open('x').write(json.dumps(report,indent=2,sort_keys=True)+'\n')
print(json.dumps(report),flush=True)
