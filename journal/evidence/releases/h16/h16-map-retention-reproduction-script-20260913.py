import importlib.util,json,sqlite3,time,traceback
from pathlib import Path
from contextlib import closing
from swingset.state.db import Database
from swingset.state.work import WorkUnit
from types import SimpleNamespace
from swingset.clock import SystemClock
import swingset.project.map as mapping
spec=importlib.util.spec_from_file_location('swingset.project.map','/Users/skeswa/repos/skeswa/swingset/src/swingset/project/map.py')
spec.loader.exec_module(mapping)
from swingset.project.materialization import output_rows
from swingset.project.process import process_unit
source=Path('/var/lib/swingset/checkpoints/h15-before-h16-20260913')
copy=Path('/var/tmp/swingset-h16-map-fixed-reproduction')
copy=Path('/var/tmp/swingset-h16-map-final-reproduction')
assert not copy.exists(); copy.mkdir()
with closing(sqlite3.connect((source/'state.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True)) as saved:
 with closing(sqlite3.connect(copy/'state.sqlite')) as target: saved.backup(target)
print('copied',flush=True)

class Rollback(Exception): pass
results={"checkpoint":str(source),"scratch":str(copy),"rollback_only":True,"runtime_overlay":"project/map.py"}
clock=SystemClock()
with closing(sqlite3.connect(copy/'state.sqlite',isolation_level=None)) as conn:
 conn.row_factory=sqlite3.Row; conn.execute('PRAGMA foreign_keys=ON'); conn.execute('PRAGMA journal_mode=WAL')
 db=Database(copy,conn,None)
 pin=Path('/nix/store/dihx01r68nq8nqxzm1dlgrzl5qrjw583-source')
 bundle=SimpleNamespace(files={'overrides/'+p.name:p.read_bytes() for p in (pin/'overrides').glob('*.csv')})
 eventids=['2023-10-augsburg-westie-station','2024-07-saunaswing','2024-09-mooseland-swing','2024-09-retaliation-swing','2025-12-new-year-s-swing-fling']
 before={e:dict(conn.execute('SELECT * FROM events WHERE event_id=?',(e,)).fetchone()) for e in eventids}
 run=db.start_run(clock.now(),dry_run=True)
 started=time.monotonic()
 try:
  with db.transaction():
   results['first_map_changed']=mapping.project_map(conn,bundle,clock.now().isoformat(),run,19)
   results['event_changes']={e:{k:[v,dict(conn.execute('SELECT * FROM events WHERE event_id=?',(e,)).fetchone())[k]] for k,v in before[e].items() if dict(conn.execute('SELECT * FROM events WHERE event_id=?',(e,)).fetchone())[k]!=v} for e in eventids}
   print(json.dumps(results),flush=True)
   unit=WorkUnit('project','map','all'); first=list(output_rows(conn,unit))
   results['second_map_changed']=mapping.project_map(conn,bundle,clock.now().isoformat(),run,19)
   results['identical_map_output']=first==list(output_rows(conn,unit))
   results['events']=[]
   for e in eventids:
    changed=process_unit(db,WorkUnit('project','event',e),bundle,clock,run)
    results['events'].append({'event_id':e,'changed':changed,'contests':conn.execute('SELECT count(*) FROM contests WHERE event_id=?',(e,)).fetchone()[0]})
   results['foreign_key_check']=[tuple(r) for r in conn.execute('PRAGMA foreign_key_check')]
   raise Rollback()
 except Rollback: pass
 except Exception as error:
  results['error']={'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc()}
 results['elapsed_seconds']=time.monotonic()-started
Path('/var/tmp/swingset-h16-map-final-reproduction.json').write_text(json.dumps(results,indent=2,sort_keys=True)+'\n')
print(json.dumps(results),flush=True)
