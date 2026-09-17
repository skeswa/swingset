from pathlib import Path
import json,hashlib,sqlite3,subprocess
from datetime import datetime,timezone
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
s=Path('/var/tmp/swingset-schema29-restore-20260917-002/restored-state');cp=Path('/var/lib/swingset/checkpoints/extension28-held-20260917-004');m=json.loads((cp/'checkpoint.json').read_bytes())
r={'checked_at':datetime.now(timezone.utc).isoformat(),'scratch':str(s),'checkpoint_manifest_sha256':sha(cp/'checkpoint.json'),'receipt_sha256':sha(s.parent/'restore-receipt.json'),'scratch_sidecars':{},'production':{'systems':{x:str(Path(x).resolve()) for x in ['/run/current-system','/nix/var/nix/profiles/system']},'hold_sha256':sha(Path('/var/lib/swingset/operator-hold')),'units':{}},'network_requests':0,'database_changes':0}
for kind in ['cycle','backup','summary']:
 for suffix in ['service','timer']:
  u=f'swingset-{kind}.{suffix}';r['production']['units'][u]=subprocess.check_output(['systemctl','show',u,'--property=ActiveState','--value'],text=True).strip()
for name,item in m['files'].items():
 if len(Path(name).parts)==1 and name!='state.sqlite':r['scratch_sidecars'][name]={'sha256':sha(s/name),'expected_sha256':item['sha256']}
for k,path in [('scratch_state',s/'state.sqlite'),('production_state',Path('/var/lib/swingset/state.sqlite'))]:
 c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);r[k]={'schema_markers':[c.execute('PRAGMA user_version').fetchone()[0],c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]],'restore_pending':(path.parent/'RESTORE_PENDING').exists(),'unsettled_admissions':c.execute("SELECT count(*) FROM execution_admissions WHERE state!='settled'").fetchone()[0]};c.close()
print(json.dumps(r,indent=2))
