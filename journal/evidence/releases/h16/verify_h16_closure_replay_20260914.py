"""Read-only final proof check after the exact frozen scratch worker exits."""
import fcntl,hashlib,json,sqlite3,time
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from swingset.state import db as db_module,derivations

scratch=Path('/var/tmp/swingset-h16-closure-replay')
source=Path('/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source')
expected_source='f8f24c2d8b839bbfcd25bf87e551e9f2777d1236f89ed84da16c3c07d4d75b9f'
expected_marker='d3c8385032c6ba66baea1afffe53bec5ad5bd1dacb269a5b56f8a3c2eeeb0377'
expected_bundle='558bb5f4e88b47fe80a691254d3b21ac9e491296e80bb18f4599b64a88bfe9c0'
started=time.monotonic()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
if not Path(derivations.__file__).resolve().is_relative_to(source/'src') or db_module.SCHEMA_VERSION!=14:raise ValueError('wrong loaded source')
if sha(source/'h16-source.json')!=expected_source or sha(scratch/'offline-derivation-scratch.json')!=expected_marker:raise ValueError('source or marker changed')
with (scratch/'state.lock').open('rb') as lock:
 fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
 with closing(sqlite3.connect((scratch/'state.sqlite').as_uri()+'?mode=ro',uri=True)) as conn:
  conn.row_factory=sqlite3.Row;conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
  conn.set_progress_handler(lambda:int(time.monotonic()-started>180),10000)
  bundle=conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0]
  if bundle!=expected_bundle:raise ValueError('input bundle differs')
  unfinished=dict(Counter(unit.stage+'/'+unit.unit_kind for stage in ('project','link') for unit in derivations.pending_units(conn,stage)))
  attempts={row[0]:row[1] for row in conn.execute('SELECT outcome,count(*) FROM work_attempts GROUP BY outcome')}
  admissions={row[0]:row[1] for row in conn.execute('SELECT state,count(*) FROM execution_admissions GROUP BY state')}
  materialized={a+'/'+b:n for a,b,n in conn.execute('SELECT stage,unit_kind,count(*) FROM derivation_scopes WHERE materialized_generation_id IS NOT NULL GROUP BY stage,unit_kind')}
  generations=conn.execute('SELECT count(*) FROM derivation_generations').fetchone()[0]
  unfinished_runs=[tuple(row) for row in conn.execute('SELECT run_id,started_at,finished_at FROM runs WHERE finished_at IS NULL ORDER BY run_id')]
  checkpoint=Path(json.loads((scratch/'offline-derivation-scratch.json').read_bytes())['checkpoint'])
  with closing(sqlite3.connect((checkpoint/'state.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True)) as old:
   inherited_runs=[tuple(row) for row in old.execute('SELECT run_id,started_at,finished_at FROM runs WHERE finished_at IS NULL ORDER BY run_id')]
  foreign_keys=list(conn.execute('PRAGMA foreign_key_check'))
  quick_check=[row[0] for row in conn.execute('PRAGMA quick_check')]
  receipt={'format':'h16-closure-replay-final-verification-v1','verified_at':datetime.now(timezone.utc).isoformat(),'source':str(source),'source_receipt_sha256':expected_source,'scratch':str(scratch),'marker_sha256':expected_marker,'input_bundle_hash':bundle,'schema':14,'unfinished_by_scope':unfinished,'attempt_outcomes':attempts,'admission_states':admissions,'materialized_by_scope':materialized,'generation_count':generations,'foreign_key_violations':len(foreign_keys),'quick_check':quick_check,'inherited_unfinished_runs':unfinished_runs,'inherited_unfinished_runs_unchanged':unfinished_runs==inherited_runs,'lock_exclusive_acquired':True,'network_requests':0,'parse_executed':False,'published':False}
  receipt['passed']=not unfinished and not foreign_keys and quick_check==['ok'] and all(k=='succeeded' for k in attempts) and all(k=='settled' for k in admissions) and unfinished_runs==inherited_runs and generations==34986 and sum(materialized.values())==34986
  receipt['elapsed_seconds']=time.monotonic()-started
  print(json.dumps(receipt,sort_keys=True))
  if not receipt['passed']:raise SystemExit(1)
