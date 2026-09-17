"""Authorized two-file metadata repair; preserve immutable proof bytes."""
import fcntl
import hashlib
import json
import os
import stat
import subprocess
from datetime import UTC,datetime
from pathlib import Path
OPS=Path('/var/lib/swingset/operations/h16-event-preservation-release-20260916')
OUT=Path('/var/tmp/h16-proof-read-access-repair-20260916.json')
TARGETS={'initialization-verification-002.json':(8229891,'dfd48d5550118410ecaab2ebd0e40ae132ab9b789ed948ea1474b9b7d7e33157'),'initialization-anchor-003.json':(8193946,'2bff03bce51be83ed62315274adbc07d0a5396d7b10e74a214bf9b0fb0421654')}
def record(st):return {'inode':st.st_ino,'device':st.st_dev,'uid':st.st_uid,'gid':st.st_gid,'mode':oct(stat.S_IMODE(st.st_mode)),'size':st.st_size,'mtime_ns':st.st_mtime_ns}
def digest(fd):
    os.lseek(fd,0,0);h=hashlib.sha256()
    while chunk:=os.read(fd,65536):h.update(chunk)
    return h.hexdigest()
assert not OUT.exists()
assert Path('/var/lib/swingset/operator-hold').is_file()
for name in ('cycle','backup','summary'):
 for kind in ('service','timer'):
  r=subprocess.run(['systemctl','show',f'swingset-{name}.{kind}','--property=ActiveState','--value'],capture_output=True,text=True,check=True)
  assert r.stdout.strip()=='inactive'
report={'started_at':datetime.now(UTC).isoformat(),'database_writes':False,'files':[],'passed':False}
lock=os.open('/var/lib/swingset/state.lock',os.O_RDONLY|os.O_NOFOLLOW)
try:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 for name,(inode,expected) in TARGETS.items():
  path=OPS/name;fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
  try:
   before=os.fstat(fd);entry=path.lstat()
   assert stat.S_ISREG(before.st_mode) and stat.S_ISREG(entry.st_mode)
   assert (before.st_ino,before.st_uid,before.st_gid,stat.S_IMODE(before.st_mode))==(inode,0,0,0o600)
   assert (entry.st_dev,entry.st_ino)==(before.st_dev,before.st_ino)
   assert digest(fd)==expected
   os.fchown(fd,0,995);os.fchmod(fd,0o640);os.fsync(fd)
   after=os.fstat(fd);entry=path.lstat()
   assert (after.st_ino,after.st_uid,after.st_gid,stat.S_IMODE(after.st_mode))==(inode,0,995,0o640)
   assert (entry.st_dev,entry.st_ino)==(after.st_dev,after.st_ino)==(before.st_dev,before.st_ino)
   assert before.st_size==after.st_size and before.st_mtime_ns==after.st_mtime_ns and digest(fd)==expected
   report['files'].append({'path':str(path),'before':record(before),'after':record(after),'sha256':expected})
  finally:os.close(fd)
 code="""import pathlib,json,hashlib,os
p=pathlib.Path('/var/lib/swingset/operations/h16-event-preservation-release-20260916/production-build-gate-001.json')
g=json.loads(p.read_bytes());r=[]
for item in g.values():
 if isinstance(item,dict) and 'path' in item:
  actual=hashlib.sha256(pathlib.Path(item['path']).read_bytes()).hexdigest();assert actual==item['sha256'];r.append(item['path'])
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'all_refs_readable_and_exact':True,'refs':r}))
"""
 result=subprocess.run(['/var/lib/swingset/venv/bin/python','-c',code],user=996,group=995,extra_groups=[],capture_output=True,text=True,check=True)
 report['service_probe']=json.loads(result.stdout);report['passed']=True
finally:os.close(lock)
report['writer_lock_released']=True;report['finished_at']=datetime.now(UTC).isoformat()
fd=os.open(OUT,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'w') as f:json.dump(report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
print(json.dumps(report))
