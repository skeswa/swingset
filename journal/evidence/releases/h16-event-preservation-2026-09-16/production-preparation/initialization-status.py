"""Small read-only progress sample; does not assert completion or hold a writer lock."""
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
p=Path('/var/lib/swingset/operations/h16-event-preservation-release-20260916/supervision-reviewed-002')
with closing(sqlite3.connect('file:/var/lib/swingset/state.sqlite?mode=ro',uri=True)) as conn:
    conn.execute('PRAGMA query_only=ON')
    rows=conn.execute('SELECT stage,outcome,count(*) FROM work_attempts GROUP BY stage,outcome').fetchall()
f=sorted(p.glob('*.resources.jsonl'))[-1]
with f.open('rb') as stream:
    stream.seek(0,2);stream.seek(max(0,stream.tell()-16384));last=json.loads(stream.read().splitlines()[-1])
print(json.dumps({'at':datetime.now(UTC).isoformat(),'attempts':rows,'unit':last.get('unit'),'state':last.get('ActiveState'),'anonymous_bytes':last.get('anonymous_bytes'),'elapsed_seconds':last.get('elapsed_seconds'),'final_exists':(p/'final.json').exists(),'closed_batches':len(list(p.glob('batch-*-final.json')))}))
