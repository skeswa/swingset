from pathlib import Path
import json,hashlib,os,stat,subprocess
from datetime import datetime,timezone
cp=Path('/var/lib/swingset/checkpoints/extension28-held-20260917-004')
sha=lambda p:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
expected={'state.sqlite-wal':(0,'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),'state.sqlite-shm':(32768,'fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb')}
r={'started_at':datetime.now(timezone.utc).isoformat(),'checkpoint':str(cp),'removed':[],'open_fds':[],'units':{},'passed':False}
for unit in ['swingset-schema29-migration-20260917-002','swingset-schema29-restore-20260917-002','swingset-schema29-input-prepare-20260917-002']:
 value=subprocess.check_output(['systemctl','show',unit,'--property=ActiveState','--value'],text=True).strip();assert value in ['inactive','failed'];r['units'][unit]=value
paths={str(cp/name) for name in ['state.sqlite',*expected]}
for directory in Path('/proc').glob('[0-9]*/fd'):
 for fd in directory.iterdir():
  try:target=os.readlink(fd)
  except FileNotFoundError:continue
  if target in paths:r['open_fds'].append({'fd':str(fd),'target':target})
assert not r['open_fds'],r['open_fds']
assert sha(cp/'checkpoint.json')=='6ac7bb2d291136ef502abcb33f8762bf4449fd2fdc0160fb995f2aba0921f9d9'
m=json.loads((cp/'checkpoint.json').read_bytes())
for name,(size,digest) in expected.items():
 file=cp/name;st=file.lstat();assert stat.S_ISREG(st.st_mode) and st.st_nlink==1 and st.st_size==size and sha(file)==digest
 assert name not in m['files'];r['removed'].append({'name':name,'size':size,'sha256':digest,'mtime_ns':st.st_mtime_ns,'uid':st.st_uid})
for name in expected:(cp/name).unlink()
actual={str(f.relative_to(cp)) for f in cp.rglob('*') if f.is_file() and f.name!='checkpoint.json'}
assert actual==set(m['files']),(len(actual),len(m['files']))
for name,item in m['files'].items():
 file=cp/name;assert file.stat().st_size==item['size'] and sha(file)==item['sha256'],name
r.update(passed=True,verified_files=len(m['files']),verified_bytes=sum(v['size'] for v in m['files'].values()),manifest_sha256=sha(cp/'checkpoint.json'),database_bytes_changed=False,finished_at=datetime.now(timezone.utc).isoformat())
print(json.dumps(r,indent=2))
