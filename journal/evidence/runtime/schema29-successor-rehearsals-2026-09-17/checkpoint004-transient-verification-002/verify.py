from pathlib import Path
import json,hashlib
from datetime import datetime,timezone
cp=Path('/var/lib/swingset/checkpoints/extension28-held-20260917-004')
sha=lambda p:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
started=datetime.now(timezone.utc).isoformat();m=json.loads((cp/'checkpoint.json').read_bytes())
actual={str(f.relative_to(cp)) for f in cp.rglob('*') if f.is_file() and f!=cp/'checkpoint.json'}
assert actual==set(m['files']),{'missing':sorted(set(m['files'])-actual),'extra':sorted(actual-set(m['files']))}
for name,item in m['files'].items():
 f=cp/name;assert f.stat().st_size==item['size'] and sha(f)==item['sha256'],name
assert sha(cp/'checkpoint.json')=='6ac7bb2d291136ef502abcb33f8762bf4449fd2fdc0160fb995f2aba0921f9d9'
print(json.dumps({'format':'checkpoint-transient-cleanup-verification-v1','passed':True,'started_at':started,'finished_at':datetime.now(timezone.utc).isoformat(),'checkpoint':str(cp),'manifest_sha256':sha(cp/'checkpoint.json'),'verified_files':len(m['files']),'verified_bytes':sum(v['size'] for v in m['files'].values()),'extra_files':[],'missing_files':[],'database_bytes_changed':False,'network_requests':0,'limitations':['The prior cleanup script removed the captured empty WAL and SHM, then its verification incorrectly excluded a nested retained checkpoint.json by basename; this separate read-only attempt verifies the complete relative-path inventory.']},indent=2))
