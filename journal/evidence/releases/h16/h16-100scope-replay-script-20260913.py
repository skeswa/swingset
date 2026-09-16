import json,time
from collections import Counter
from pathlib import Path
from swingset.clock import SystemClock
from swingset.fetch.archive import Archive
from swingset.schedule.cycle import versions
from swingset.schedule.derive import derive_one
from swingset.schedule.fairness import next_offline,record_offline_service
from swingset.state.db import open_database
from swingset.state.inputs import capture,accept
state=Path('/var/tmp/swingset-h15-offline-replay')
source=Path('/var/tmp/swingset-h16-100scope-source')
pin=Path('/nix/store/694311m4fcif32w774bs8vpkc4iv6d9a-source')
output=Path('/var/tmp/swingset-h16-100scope.json')
clock=SystemClock()
def sizes():
 return {p.name:p.stat().st_size for p in state.glob('state.sqlite*') if p.is_file()}
def stored(conn):
 return {'generations':conn.execute('SELECT count(*) FROM derivation_generations').fetchone()[0], 'output_rows':conn.execute('SELECT count(*) FROM derivation_rows').fetchone()[0], 'payload_bytes':conn.execute('SELECT coalesce(sum(length(cast(payload_json as blob))),0) FROM derivation_rows').fetchone()[0], 'dependency_sets':conn.execute('SELECT count(*) FROM derivation_dependency_sets').fetchone()[0], 'dependency_bytes':conn.execute('SELECT coalesce(sum(length(cast(manifest_json as blob))),0) FROM derivation_dependency_sets').fetchone()[0]}
result={'purpose':'100 actual offline derivations with isolated frozen H16 runtime','source':str(source),'source_digest':json.loads((source/'SOURCE.json').read_text())['digest'],'state':str(state),'network_requests':0,'published':False,'before_file_bytes':sizes(),'started_at':clock.now().isoformat()}
counts=Counter();records=[]
with open_database(state) as db:
 result['before_storage']=stored(db.connection)
 begin=time.monotonic()
 bundle=capture(pin/'config',Path('/Users/skeswa/repos/skeswa/swingset/overrides'),state,versions())
 result['accepted_changed_inputs']=sorted(accept(db,bundle,clock))
 result['input_digest']=bundle.digest
 result['capture_accept_seconds']=time.monotonic()-begin
 run=db.start_run(clock.now(),dry_run=True)
 archive=Archive(state)
 began=time.monotonic()
 while len(records)<100 and time.monotonic()-began<600:
  t=time.monotonic()
  unit=next_offline(db.connection,now=clock.now(),allowed=lambda u:u.stage in {'project','link'})
  selected=time.monotonic()
  if unit is None:
   result['no_eligible_derivation']=True
   break
  outcome=derive_one(db,archive,unit,bundle,clock,run)
  derived=time.monotonic()
  with db.transaction() as conn:
   record_offline_service(conn,unit,now=clock.now())
  record={'stage':unit.stage,'kind':unit.unit_kind,'id':unit.unit_id,'selection_seconds':selected-t,'derive_seconds':derived-selected,'failed':outcome.failed,'reason':outcome.reason}
  records.append(record);counts[unit.stage+'/'+unit.unit_kind]+=1
  if len(records)%20==0:
   print(json.dumps({'progress':len(records),'elapsed_seconds':time.monotonic()-began,'latest':record}),flush=True)
 result['replay_seconds']=time.monotonic()-began
 result['records']=records
 result['counts']=dict(counts)
 result['after_storage']=stored(db.connection)
 result['finished_at']=clock.now().isoformat()
 result['selection_seconds']=sum(r['selection_seconds'] for r in records)
 result['derive_seconds']=sum(r['derive_seconds'] for r in records)
 result['failures']=[r for r in records if r['failed'] or r['reason']]
 result['after_open_file_bytes']=sizes()
 with db.transaction() as conn:
  conn.execute('UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?',(clock.now().isoformat(),json.dumps({'benchmark':str(output),'counts':dict(counts)}),run))
result['after_closed_file_bytes']=sizes()
output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps({key:value for key,value in result.items() if key!='records'},indent=2),flush=True)
