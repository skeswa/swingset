import fcntl,hashlib,json,os,signal,sqlite3,subprocess,time
from collections import Counter
from datetime import UTC,datetime
from pathlib import Path
from contextlib import closing
from swingset.build import generations
from swingset.state import derivations,db
SOURCE=Path('/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source')
STATE=Path('/var/tmp/swingset-h16-closure-build')
CANDIDATE=STATE/'candidates/cand_95d350f1e29a4e19'
OUTPUT=Path('/var/tmp/h16-closure-build-failure-verification-20260915.json')
RECEIPT=Path('/var/tmp/swingset-h16-closure-build.json')
SOURCE_SHA='f8f24c2d8b839bbfcd25bf87e551e9f2777d1236f89ed84da16c3c07d4d75b9f'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def statdb():
 return {p.name:{'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in (STATE/'state.sqlite',STATE/'state.sqlite-wal') if p.exists()}
def unit():
 s=subprocess.run(['systemctl','show','swingset-h16-closure-build.service','--property=LoadState,ActiveState,SubState,MainPID,Result,ExecMainStatus'],capture_output=True,text=True,check=True).stdout
 return dict(line.split('=',1) for line in s.splitlines())
assert Path(db.__file__).resolve()==SOURCE/'src/swingset/state/db.py'
assert Path(generations.__file__).resolve()==SOURCE/'src/swingset/build/generations.py'
assert db.SCHEMA_VERSION==14 and sha(SOURCE/'h16-source.json')==SOURCE_SHA
assert not OUTPUT.exists()
receipt=json.loads(RECEIPT.read_bytes());built=json.loads((CANDIDATE/'BUILT').read_bytes());manifest=json.loads((CANDIDATE/'_meta/manifest.json').read_bytes())
report={'format':'h16-build-failure-independent-verification-v1','verified_at':datetime.now(UTC).isoformat(),'state':str(STATE),'source':str(SOURCE),'source_receipt_sha256':SOURCE_SHA,'candidate':str(CANDIDATE),'candidate_id':built['candidate_id'],'manifest_sha256':sha(CANDIDATE/'_meta/manifest.json'),'built_manifest_hash':built['manifest_hash'],'content_hash':built['content_hash'],'input_bundle_hash':manifest['release_policy']['input_bundle_hash'],'failed_build_receipt_sha256':sha(RECEIPT),'failed_build_receipt':receipt,'unit_before':unit(),'published_marker_exists':(CANDIDATE/'PUBLISHED').exists(),'rejected_marker_exists':(CANDIDATE/'REJECTED').exists(),'database_before':statdb(),'acceptance_audit_executed':False,'network_requests':0,'production_changes':False}
with (STATE/'state.lock').open('rb') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 report['exclusive_writer_lock_acquired']=True
 with closing(sqlite3.connect((STATE/'state.sqlite').as_uri()+'?mode=ro',uri=True)) as conn:
  conn.row_factory=sqlite3.Row;conn.execute('BEGIN')
  report['schema_version']=conn.execute('PRAGMA user_version').fetchone()[0]
  report['durable_candidate_completion']=generations.completed(conn,built['candidate_id'],built['manifest_hash'])
  report['build_generation_count']=conn.execute("SELECT COUNT(*) FROM derivation_generations WHERE stage='build'").fetchone()[0]
  report['candidate_artifact_row_count']=conn.execute("SELECT COUNT(*) FROM derivation_rows WHERE table_name='artifact' AND record_key=?",(built['candidate_id'],)).fetchone()[0]
  report['materialized_scope_counts']={row[0]:row[1] for row in conn.execute("SELECT stage,count(*) FROM derivation_scopes WHERE stage IN ('project','link') AND materialized_generation_id IS NOT NULL GROUP BY stage")}
  report['null_project_link_pointers']=conn.execute("SELECT COUNT(*) FROM derivation_scopes WHERE stage IN ('project','link') AND materialized_generation_id IS NULL").fetchone()[0]
  report['running_attempts']=conn.execute("SELECT COUNT(*) FROM work_attempts WHERE outcome='running'").fetchone()[0]
  report['unsettled_admissions']=conn.execute("SELECT COUNT(*) FROM execution_admissions WHERE state!='settled'").fetchone()[0]
  report['accepted_bundle']=conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0]
  started=time.monotonic();deadline=started+15
  def limit(*_):raise TimeoutError('read-only currentness assessment exceeded15seconds')
  old=signal.signal(signal.SIGALRM,limit);signal.setitimer(signal.ITIMER_REAL,15)
  conn.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
  try:
   pending=Counter(item.stage+'/'+item.unit_kind for stage in ('project','link') for item in derivations.pending_units(conn,stage))
   report['unfinished_project_link']=dict(pending);report['currentness_assessed']=True
  except (TimeoutError,sqlite3.OperationalError) as error:
   report['currentness_assessed']=False;report['currentness_limit_reason']=str(error)
  finally:
   conn.set_progress_handler(None,0);signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old)
   report['currentness_seconds']=time.monotonic()-started
  report['connection_total_changes']=conn.total_changes
 fcntl.flock(lock,fcntl.LOCK_UN)
report['writer_lock_released']=True;report['unit_after']=unit();report['database_after']=statdb()
report['checks']={
 'reported_failed_deadline':receipt['passed'] is False and receipt['error']['type']=='WriteDeadlineExceeded',
 'exact_candidate_manifest':built['candidate_id']==CANDIDATE.name and built['manifest_hash']==report['manifest_sha256'],
 'correct_source_bundle':receipt['source']==str(SOURCE) and report['accepted_bundle']==report['input_bundle_hash']=='558bb5f4e88b47fe80a691254d3b21ac9e491296e80bb18f4599b64a88bfe9c0',
 'no_completed_build':report['durable_candidate_completion'] is False and report['build_generation_count']==report['candidate_artifact_row_count']==0,
 'not_published':not report['published_marker_exists'] and receipt['published'] is False,
 'worker_stopped':report['unit_after']['MainPID']=='0' and report['unit_after']['ActiveState'] in {'inactive','failed'},
 'writer_lock_free':report['exclusive_writer_lock_acquired'] and report['writer_lock_released'],
 'readonly_database_unchanged':report['connection_total_changes']==0 and report['database_before']==report['database_after'],
 'all_project_link_current':report.get('currentness_assessed') is True and report.get('unfinished_project_link')=={} and report['materialized_scope_counts']=={'project':32445,'link':2541} and report['null_project_link_pointers']==0,
}
report['failure_verified']=all(report['checks'].values())
report['limitations']=['Failure-state verification only: candidate file closure, public data correctness, publication eligibility, and performance acceptance were not audited.','No retries, run cleanup, builds, or acceptance performed.']
fd=os.open(OUTPUT,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'w') as out:json.dump(report,out,indent=2,sort_keys=True);out.write('\n');out.flush();os.fsync(out.fileno())
print(json.dumps({'output':str(OUTPUT),'sha256':sha(OUTPUT),'checks':report['checks'],'currentness_seconds':report['currentness_seconds'],'unsettled_admissions':report['unsettled_admissions']},indent=2))
