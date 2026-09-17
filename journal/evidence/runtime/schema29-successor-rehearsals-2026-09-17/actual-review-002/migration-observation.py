from pathlib import Path
import json,hashlib,sqlite3
from datetime import datetime,timezone
cp=Path('/var/lib/swingset/checkpoints/extension28-held-20260917-004')
scratch=Path('/var/tmp/swingset-schema29-migration-20260917-002')
sha=lambda p:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
m=json.loads((cp/'checkpoint.json').read_bytes())
r={'checked_at':datetime.now(timezone.utc).isoformat(),'checkpoint':str(cp),'destination':str(scratch),'checkpoint_manifest_sha256':sha(cp/'checkpoint.json'),'checkpoint_database':{'size':(cp/'state.sqlite').stat().st_size,'sha256':sha(cp/'state.sqlite')},'expected_checkpoint_database':m['files']['state.sqlite'],'destination_receipt_sha256':sha(scratch/'migration-receipt.json'),'sidecars':{},'network_requests':0,'database_changes':0}
for name,item in m['files'].items():
 if len(Path(name).parts)==1 and name!='state.sqlite':r['sidecars'][name]={'checkpoint_sha256':sha(cp/name),'destination_sha256':sha(scratch/name),'expected_sha256':item['sha256']}
for key,root in [('checkpoint_state',cp),('destination_state',scratch)]:
 c=sqlite3.connect((root/'state.sqlite').as_uri()+'?mode=ro',uri=True);r[key]={'schema_markers':[c.execute('PRAGMA user_version').fetchone()[0],c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]],'application_tables':c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0],'foreign_key_sample':c.execute('PRAGMA foreign_key_check').fetchmany(1),'restore_pending':(root/'RESTORE_PENDING').exists()}
 if key=='destination_state':r[key]['fence']=c.execute('SELECT * FROM history_dispatch_fence').fetchall()
 c.close()
print(json.dumps(r,indent=2))
