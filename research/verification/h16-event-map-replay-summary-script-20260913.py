import json,sqlite3,statistics,hashlib,datetime
from pathlib import Path
from contextlib import closing
scratch=Path('/var/tmp/swingset-h16-event-map-replay')
receipts=[]
for i in range(1,12):
 path=Path(f'/var/tmp/swingset-h16-event-map-replay-{i:03d}.json')
 body=path.read_bytes(); row=json.loads(body)
 receipts.append({'path':str(path),'sha256':hashlib.sha256(body).hexdigest(),**row})
assert receipts[-1]['status']=='current' and receipts[-1]['unfinished_by_scope']=={}
with closing(sqlite3.connect((scratch/'state.sqlite').as_uri()+'?mode=ro',uri=True)) as conn:
 conn.row_factory=sqlite3.Row
 outcomes=[dict(r) for r in conn.execute('SELECT run_id,stage,unit_kind,outcome,count(*) AS count FROM work_attempts GROUP BY run_id,stage,unit_kind,outcome ORDER BY run_id,stage,unit_kind,outcome')]
 failures=[dict(r) for r in conn.execute("SELECT attempt_id,stage,unit_kind,unit_id,outcome,reason_code FROM work_attempts WHERE outcome!='succeeded'")]
 links=conn.execute("SELECT unit_id,started_at,finished_at FROM work_attempts WHERE stage='link' AND outcome='succeeded'").fetchall()
 times=[(datetime.datetime.fromisoformat(r['finished_at'])-datetime.datetime.fromisoformat(r['started_at'])).total_seconds() for r in links]
 eventids=['2023-10-augsburg-westie-station','2024-07-saunaswing','2024-09-mooseland-swing','2024-09-retaliation-swing','2025-12-new-year-s-swing-fling']
 proxyids=['scoringdance:'+v for v in ['109','11','111','12','120','123','13','20','21','210','212','217','226','24','266','28']]
 cases=[dict(r) for r in conn.execute("SELECT stage,unit_kind,unit_id,outcome,reason_code FROM work_attempts WHERE stage='project' AND unit_kind IN ('event','source_event') AND unit_id IN ("+','.join('?' for _ in eventids+proxyids)+') ORDER BY unit_kind,unit_id',eventids+proxyids)]
 result={'format':'offline-derivation-replay-summary-v1','scratch':str(scratch),'source':'/nix/store/kshnpz73b37h89zkmj03f4b3srd5ldaf-source','source_receipt_sha256':receipts[-1]['source_receipt_sha256'],'input_bundle_hash':receipts[-1]['input_bundle_hash'],'final_receipt_sha256':receipts[-1]['sha256'],'status':'current','unfinished_by_scope':{},'generation_count':receipts[-1]['generation_count'],'total_attempted':sum(r['attempted'] for r in receipts),'total_committed':sum(r['completed'] for r in receipts),'total_invocation_elapsed_seconds':sum(r['elapsed_seconds'] for r in receipts),'total_selection_seconds':sum(r['selection_seconds'] for r in receipts),'total_worker_seconds':sum(r['worker_seconds'] for r in receipts),'failed_or_running_attempts':failures,'outcomes_by_run':outcomes,'link_worker_durations_seconds':{'count':len(times),'median':statistics.median(times),'maximum':max(times),'total':sum(times)},'prior_failed_cases':cases,'foreign_key_check':[tuple(r) for r in conn.execute('PRAGMA foreign_key_check')],'database_bytes':(scratch/'state.sqlite').stat().st_size,'initial_database_bytes':2646597632,'network_requests':0,'parse_executed':False,'built':False,'published':False,'writer_lock_release_independently_verified':True,'receipts':receipts}
result['accounting_checks']={'no_failed_or_running':not failures,'foreign_keys_clean':not result['foreign_key_check'],'prior_case_count':len(cases),'all_prior_cases_succeeded':all(r['outcome']=='succeeded' for r in cases)}
assert not failures and not result['foreign_key_check'] and len(cases)==21 and all(r['outcome']=='succeeded' for r in cases)
print(json.dumps(result,indent=2,sort_keys=True))
