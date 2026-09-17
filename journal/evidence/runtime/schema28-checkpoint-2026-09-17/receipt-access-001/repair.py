import fcntl, grp, hashlib, json, os, stat, subprocess
from pathlib import Path
from datetime import datetime, timezone
from swingset.state.control_lock import control_lock
S=Path('/var/lib/swingset')
FILES={'preflight-receipt.json':'d3a12cb9dd9699eaac20d5a121b8fdc50f59087de74253dc626127fd13a1d084','migration-receipt.json':'9ed991d1389d36d36aa541f45fcd3bcdd2367b18206a429a357d62f079e8b636'}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
rows=[]
with (S/'state.lock').open('r+b') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 with control_lock(S,timeout=0):
  assert sha(S/'operator-hold')=='965468bb03ba65b6d1d4950ab8e85c09c8f2f2a323461b7a3f2b6177bba5c638'
  for k in ('cycle','backup','summary'):
   for suffix in ('service','timer'):
    assert subprocess.check_output(['systemctl','show',f'swingset-{k}.{suffix}','-p','ActiveState','--value'],text=True).strip()=='inactive'
  gid=grp.getgrnam('swingset').gr_gid
  for name,expected in FILES.items():
   p=S/'operations/v2-continuation-20260917/rollout-001'/name;t=p.lstat()
   assert stat.S_ISREG(t.st_mode) and t.st_uid==0 and stat.S_IMODE(t.st_mode)==0o600 and sha(p)==expected
  for name,expected in FILES.items():
   p=S/'operations/v2-continuation-20260917/rollout-001'/name;before=p.stat()
   os.chown(p,-1,gid);os.chmod(p,0o640);after=p.stat()
   assert sha(p)==expected and before.st_ino==after.st_ino and before.st_mtime_ns==after.st_mtime_ns
   rows.append(dict(path=str(p),sha256=expected,uid=after.st_uid,gid=after.st_gid,mode='0640',inode_and_mtime_preserved=True))
print(json.dumps(dict(recorded_at=datetime.now(timezone.utc).isoformat(),passed=True,files=rows,database_opened=False,hold_preserved=True),indent=2))
