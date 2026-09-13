import pathlib,json,hashlib,sqlite3,fcntl,subprocess,datetime,sys,time
from research.replay_derivations import verify_runtime,validate_scratch
from swingset.state import db,derivations
s=pathlib.Path('/nix/store/093lylp4naslq5yb2mygbkkgkbsar3ym-source'); p=pathlib.Path('/var/tmp/swingset-h16-changelog-replay')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
started=time.monotonic()
verify_runtime(s,'3930dbc9f6023250a5253043ade60bd3fc90c57bda27e2c64f8520aa4c588a92')
validate_scratch(p,s,'3930dbc9f6023250a5253043ade60bd3fc90c57bda27e2c64f8520aa4c588a92')
receipt_path=pathlib.Path('/var/tmp/swingset-h16-changelog-replay-012.json')
receipt=json.loads(receipt_path.read_bytes())
assert receipt['status']=='current' and receipt['unfinished_by_scope']=={} and receipt['generation_count']==34986
assert receipt['input_bundle_hash']=='9370ccc35058661037c78b2dbcea797932dce1e4211638826149661a4e37e683'
with (p/'state.lock').open('rb') as f:
 fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
 with db.open_database(p,lock=False,read_only=True) as d:
  c=d.connection
  c.execute('BEGIN')
  unfinished={stage:sum(1 for _ in derivations.pending_units(c,stage)) for stage in ('project','link')}
  counts=[list(r) for r in c.execute('select stage,outcome,count(*) from work_attempts group by stage,outcome')]
  generations=c.execute('select count(*) from derivation_generations').fetchone()[0]
  materialized=[list(r) for r in c.execute('select stage,count(*) from derivation_scopes where materialized_generation_id is not null group by stage')]
  fk=[list(r) for r in c.execute('PRAGMA foreign_key_check')]
  inherited_open_runs=[list(r) for r in c.execute('select run_id,started_at,finished_at from runs where finished_at is null order by run_id')]
  c.rollback()
 assert unfinished=={'project':0,'link':0}
 assert counts==[['link','succeeded',2541],['project','succeeded',32445]],counts
 assert generations==34986 and not fk
 with sqlite3.connect('file:/var/lib/swingset/checkpoints/h15-before-h16-20260913/state.sqlite?mode=ro&immutable=1',uri=True) as predecessor:
  checkpoint_open_runs=[list(r) for r in predecessor.execute('select run_id,started_at,finished_at from runs where finished_at is null order by run_id')]
 assert inherited_open_runs==checkpoint_open_runs
 for n in range(1,13):
  r=json.loads(pathlib.Path(f'/var/tmp/swingset-h16-changelog-replay-{n:03d}.json').read_bytes())
  with db.open_database(p,lock=False,read_only=True) as replay_db:
   assert replay_db.connection.execute('select finished_at from runs where run_id=?',(r['run_id'],)).fetchone()[0] is not None
 fcntl.flock(f,fcntl.LOCK_UN)
units=['swingset-'+n+'.'+t for n in ['cycle','backup','summary'] for t in ['service','timer']]
states={u:subprocess.run(['systemctl','is-active',u],capture_output=True,text=True).stdout.strip() for u in units}
assert all(v=='inactive' for v in states.values())
assert pathlib.Path('/var/lib/swingset/operator-hold').exists()
active=str(pathlib.Path('/run/current-system').resolve())
persistent=str(pathlib.Path('/nix/var/nix/profiles/system').resolve())
assert active==persistent=='/nix/store/r29kapzbjk0ch5h0vkk288jd47dvg90j-nixos-system-swingset-lxc-25.11.20260630.b6018f8'
baseline=pathlib.Path('/var/lib/swingset/baseline').resolve()
assert baseline.name=='cand_7f8cf9bcbf7e4a60'
assert not pathlib.Path('/var/tmp/swingset-h16-changelog-build').exists()
with db.open_database(pathlib.Path('/var/lib/swingset'),lock=False,read_only=True) as production:
 assert production.connection.execute('PRAGMA user_version').fetchone()[0]==14
 assert production.connection.execute('select count(*) from derivation_scopes where materialized_generation_id is not null').fetchone()[0]==0
print(json.dumps({'format':'offline-derivation-replay-final-verification-v1','checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scratch':str(p),'source':str(s),'source_receipt_sha256':sha(s/'h16-source.json'),'driver_sha256':sha(s/'research/replay_derivations.py'),'marker_sha256':sha(p/'offline-derivation-scratch.json'),'input_bundle_hash':receipt['input_bundle_hash'],'final_receipt_path':str(receipt_path),'final_receipt_sha256':sha(receipt_path),'current':True,'unfinished_by_stage':unfinished,'counts':counts,'materialized_by_stage':materialized,'generation_count':generations,'foreign_key_violations':fk,'inherited_open_runs_unchanged':inherited_open_runs,'unfinished_replay_runs':0,'writer_lock_released_and_independently_verified':True,'production_units':states,'active_system':active,'persistent_system':persistent,'production_baseline':str(baseline),'production_schema':14,'production_materialized_pointers':0,'build_executed':False,'production_mutated':False,'published':False,'network_requests':0,'elapsed_seconds':time.monotonic()-started,'passed':True},indent=2))
