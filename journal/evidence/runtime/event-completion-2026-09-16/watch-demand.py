"""Bounded read-only watch counts; not admission, eligibility, or completion proof."""
import hashlib
import json
import signal
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

CHECKPOINT=Path('/var/lib/swingset/checkpoints/h15-before-h16-20260913')
MANIFEST_SHA='700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444'
DATABASE_SHA='d81b2e459471c25c430a57c2a4d096180f1094e57c1b8b1e896d66774f12e8dc'
ROOT=Path('/Users/skeswa/repos/skeswa/swingset')
OUTPUT=ROOT/'journal/evidence/runtime/event-completion-2026-09-16/watch-demand.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expired(*_):
    raise TimeoutError('Watch-demand observation exceeded20 seconds')


def main():
    from swingset.schedule.event_request_kind import SOURCE_EVENT_PARSERS, purpose
    assert not OUTPUT.exists()
    assert sha(CHECKPOINT/'checkpoint.json')==MANIFEST_SHA
    path=CHECKPOINT/'state.sqlite'
    before=(path.stat().st_size,path.stat().st_mtime_ns)
    signal.signal(signal.SIGALRM,expired)
    signal.alarm(20)
    start=time.monotonic()
    with sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        conn.set_progress_handler(lambda:int(time.monotonic()-start>15),10000)
        rows=conn.execute('SELECT w.source,w.parser,w.source_ref,w.state,w.url,w.archive_url,w.last_checked_at,w.paused_until, EXISTS(SELECT 1 FROM snapshots s WHERE s.watch_id=w.watch_id) AS retained FROM watches w WHERE parser IN (SELECT value FROM json_each(?)) ORDER BY w.source,w.parser,w.source_ref,w.watch_id',(json.dumps(sorted(SOURCE_EVENT_PARSERS)),)).fetchall()
        counts=Counter()
        events={}
        for row in rows:
            unattempted=row['last_checked_at'] is None and not row['retained']
            key=(row['source'],row['parser'],purpose(row['parser']),urlsplit(row['archive_url'] or row['url']).hostname,row['state'],bool(row['retained']),unattempted)
            counts[key]+=1
            if row['source_ref']:
                event=events.setdefault((row['source'],row['source_ref']),{'watches':0,'never_checked_or_retained':0,'result_watches':0,'unattempted_result_watches':0})
                event['watches']+=1
                event['never_checked_or_retained']+=int(unattempted)
                event['result_watches']+=int(purpose(row['parser'])=='result')
                event['unattempted_result_watches']+=int(purpose(row['parser'])=='result' and unattempted)
        assert conn.total_changes==0
    assert before==(path.stat().st_size,path.stat().st_mtime_ns)
    signal.alarm(0)
    report={'format':'event-watch-demand-observation-v1','at':datetime.now(UTC).isoformat(),'checkpoint':str(CHECKPOINT),'checkpoint_manifest_sha256':MANIFEST_SHA,'checkpoint_database_sha256_from_earlier_verified_preparation':DATABASE_SHA,'database_rehashed':False,'main_database_metadata_unchanged':True,'query_only':True,'immutable_open':True,'source_taxonomy_sha256':sha(ROOT/'src/swingset/schedule/event_request_kind.py'),'script_sha256':sha(Path(__file__)),'network_requests':0,'production_mutated':False,'elapsed_seconds':time.monotonic()-start,'watch_count':len(rows),'source_event_reference_count':len(events),'groups':[dict(zip(('source','parser','purpose','host','watch_state','has_snapshot','never_checked_or_retained'),key),watches=count) for key,count in sorted(counts.items())],'source_events':[{'source':source,'source_ref':ref,**value} for (source,ref),value in sorted(events.items())],'limits':['Watch rows can share one request; these are not distinct-request counts.','Source references are not canonical events.','No artifact, admission, current eligibility, historical year gate, or completion verification.','This checkpoint predates the new extension; no operating performance or service guarantees are inferred.','The database hash was verified by replay preparation earlier in this session; this bounded observation checks only unchanged metadata.']}
    with OUTPUT.open('x') as stream:stream.write(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'path':str(OUTPUT),'sha256':sha(OUTPUT),'watches':len(rows),'source_event_references':len(events),'seconds':report['elapsed_seconds']}))

if __name__=='__main__':main()
