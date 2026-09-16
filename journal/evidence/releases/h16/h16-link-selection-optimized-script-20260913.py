import cProfile,io,json,pstats,sqlite3,time
from collections import Counter
from contextlib import closing
from pathlib import Path
import importlib.util,sys,hashlib
replacement=Path('/Users/skeswa/repos/skeswa/swingset/src/swingset/state/derivation_dependencies.py')
spec=importlib.util.spec_from_file_location('swingset.state.derivation_dependencies',replacement)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
from swingset.state import derivations
from swingset.state.work import WorkUnit
from swingset.state.derivations import canonical
source=Path('/nix/store/d8bwqgbqi94b19l0wfgqr7a2gir1xkf5-source')
state=Path('/var/tmp/swingset-h16-full-replay')
result={'source':str(source),'state':str(state),'read_only':True,'network_requests':0,'worker_executed':False,'overlay_file':str(replacement),'overlay_sha256':hashlib.sha256(replacement.read_bytes()).hexdigest()}
with closing(sqlite3.connect('file:'+str(state/'state.sqlite')+'?mode=ro',uri=True)) as conn:
 conn.row_factory=sqlite3.Row
 event=conn.execute('SELECT event_id,count(*) FROM entries GROUP BY event_id ORDER BY count(*) DESC,event_id LIMIT 1').fetchone()
 unit=WorkUnit('link','event',event[0]);result['scope']={'stage':unit.stage,'kind':unit.unit_kind,'id':unit.unit_id,'entries':event[1]}
 measurements=[]
 for name,call in [('ready',lambda:derivations.ready(conn,unit)),('desired',lambda:derivations.desired(conn,unit))]:
  profile=cProfile.Profile();started=time.monotonic();profile.enable()
  value=call()
  profile.disable();elapsed=time.monotonic()-started
  buffer=io.StringIO();pstats.Stats(profile,stream=buffer).sort_stats('cumulative').print_stats(28)
  measurements.append({'operation':name,'elapsed_seconds':elapsed,'profile':buffer.getvalue()})
  if name=='ready':result['ready']=value
  else:
   result.update(fingerprint=value.fingerprint,generation_id=value.generation_id,dependency_count=len(value.dependencies),dependency_manifest_bytes=len(canonical(value.dependencies).encode()),dependency_kinds=dict(Counter(v.get('kind') for v in value.dependencies)),dependency_sets={key:{'members':len(members),'bytes':len(canonical(members).encode()),'kinds':dict(Counter(v.get('kind') for v in members))} for key,members in value.dependency_sets.items()},recipe_bytes=len(canonical(value.recipe).encode()))
 result['measurements']=measurements
old=json.loads(Path('/var/tmp/swingset-h16-link-selection-profile.json').read_text())
assert all(result[key]==old[key] for key in ('scope','ready','fingerprint','generation_id','dependency_count','dependency_manifest_bytes','dependency_sets','recipe_bytes'))
result['exact_dependency_equivalence']=True
Path('/var/tmp/swingset-h16-link-selection-optimized.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps(result,indent=2,sort_keys=True),flush=True)
