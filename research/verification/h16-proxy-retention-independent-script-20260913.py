import hashlib, importlib.util, json, sqlite3, time, traceback
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from swingset.clock import SystemClock
from swingset.state.db import Database
from swingset.state.work import WorkUnit
import swingset.project.map as mapping
map_path=Path('/Users/skeswa/repos/skeswa/swingset/src/swingset/project/map.py')
spec=importlib.util.spec_from_file_location('swingset.project.map', map_path)
spec.loader.exec_module(mapping)
from swingset.project.process import process_unit
from swingset.project.materialization import output_rows, helper_recipe
from swingset.state.derivations import capture, current, desired, selected_generation
source=Path('/var/lib/swingset/checkpoints/h15-before-h16-20260913')
copy=Path('/var/tmp/swingset-h16-proxy-retention-independent-v2')
assert not copy.exists(); copy.mkdir()
with closing(sqlite3.connect((source/'state.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True)) as saved:
 with closing(sqlite3.connect(copy/'state.sqlite')) as target: saved.backup(target)
class Rollback(Exception): pass
result={'checkpoint':str(source),'scratch':str(copy),'runtime_overlay':'project/map.py','map_sha256':hashlib.sha256(map_path.read_bytes()).hexdigest(),'original_unchanged':True,'scope':'diagnostic disposable clone; map committed only in clone; every proxy probe rolled back','proxies':[]}
clock=SystemClock(); started=time.monotonic()
with closing(sqlite3.connect(copy/'state.sqlite',isolation_level=None)) as conn:
 conn.row_factory=sqlite3.Row; conn.execute('PRAGMA foreign_keys=ON'); conn.execute('PRAGMA journal_mode=WAL')
 db=Database(copy,conn,None)
 pin=Path('/nix/store/dihx01r68nq8nqxzm1dlgrzl5qrjw583-source')
 bundle=SimpleNamespace(files={'overrides/'+p.name:p.read_bytes() for p in (pin/'overrides').glob('*.csv')})
 run=db.start_run(clock.now(),dry_run=True)
 unit=WorkUnit('project','map','all')
 with db.transaction(): mapping.project_map(conn,bundle,clock.now().isoformat(),run,19)
 first=list(output_rows(conn,unit)); generation=selected_generation(conn,unit)
 for ref in ['109','11','111','12','120','123','13','20','21','210','212','217','226','24','266','28']:
  proxy=WorkUnit('project','source_event','scoringdance:'+ref)
  item={'unit_id':proxy.unit_id}; tick=time.monotonic()
  try:
   with db.transaction():
    selection=capture(conn,proxy,now=clock.now(),recipe=helper_recipe(conn,bundle.files,'project'))
    item['changed']=process_unit(db,proxy,bundle,clock,run,selection=selection)
    item['default_recipe_current']=current(conn,proxy)
    item['explicit_recipe_current']=selected_generation(conn,proxy)==desired(conn,proxy,recipe=helper_recipe(conn,bundle.files,'project')).generation_id
    item['map_generation_unchanged']=selected_generation(conn,unit)==generation
    item['map_rows_unchanged']=list(output_rows(conn,unit))==first
    item['mapped']=conn.execute('SELECT event_id FROM source_event_map WHERE source_ref=?',(proxy.unit_id,)).fetchone() is not None
    item['dates']=list(conn.execute('SELECT start_date,end_date FROM source_events WHERE source_ref=?',(proxy.unit_id,)).fetchone())
    assert item['explicit_recipe_current'] and item['map_generation_unchanged'] and item['map_rows_unchanged']
    assert not item['mapped'] and item['dates']==[None,None]
    raise Rollback()
  except Rollback: item['probe_rolled_back']=True
  except Exception as error: item['error']={'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc()}
  item['elapsed_seconds']=time.monotonic()-tick; result['proxies'].append(item)
  print(json.dumps(item),flush=True)
 result['foreign_key_check']=[tuple(r) for r in conn.execute('PRAGMA foreign_key_check')]
result['elapsed_seconds']=time.monotonic()-started
result['all_passed']=len(result['proxies'])==16 and all(p.get('probe_rolled_back') for p in result['proxies']) and not result['foreign_key_check']
Path('/var/tmp/swingset-h16-proxy-retention-independent-v2.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps(result),flush=True)
