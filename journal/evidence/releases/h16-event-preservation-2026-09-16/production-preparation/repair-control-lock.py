"""Authorized metadata-only repair of the existing, independently identified lock."""
import fcntl
import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

STATE = Path('/var/lib/swingset')
OUT = Path('/var/tmp/h16-event-preservation-control-lock-repair-20260916.json')
DIAGNOSTIC = Path('/var/tmp/h16-event-preservation-initialization-prepare-002-20260916/failure-preservation.json')
assert not OUT.exists()
data = DIAGNOSTIC.read_bytes()
assert json.loads(data)['passed'] is True
assert json.loads(data)['marker_sha256'] == 'dad425983041d0a27ab2eae1127db1fc9aeec4a401f50d678d6fe87e176baece'
def record(st):
    return {'inode':st.st_ino,'device':st.st_dev,'uid':st.st_uid,'gid':st.st_gid,'mode':oct(stat.S_IMODE(st.st_mode)),'size':st.st_size,'mtime_ns':st.st_mtime_ns,'ctime_ns':st.st_ctime_ns}
report = {'started_at':datetime.now(UTC).isoformat(),'diagnostic_sha256':hashlib.sha256(data).hexdigest(),'database_writes':False,'lock_replaced':False,'passed':False}
state_fd = os.open(STATE/'state.lock',os.O_RDONLY|os.O_NOFOLLOW)
try:
    st = os.fstat(state_fd)
    assert stat.S_ISREG(st.st_mode) and (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode)) == (996,995,0o600)
    fcntl.flock(state_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    control_fd = os.open(STATE/'control.lock',os.O_RDWR|os.O_NOFOLLOW)
    try:
        fcntl.flock(control_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before = os.fstat(control_fd); path_before = (STATE/'control.lock').lstat()
        assert stat.S_ISREG(before.st_mode) and stat.S_ISREG(path_before.st_mode)
        assert (before.st_ino,before.st_uid,before.st_gid,stat.S_IMODE(before.st_mode),before.st_size) == (6400127,0,0,0o644,0)
        assert (before.st_dev,before.st_ino) == (path_before.st_dev,path_before.st_ino)
        report['before'] = record(before)
        os.fchown(control_fd,996,995)
        os.fchmod(control_fd,0o600)
        os.fsync(control_fd)
        after = os.fstat(control_fd); path_after = (STATE/'control.lock').lstat()
        assert stat.S_ISREG(after.st_mode) and stat.S_ISREG(path_after.st_mode)
        assert (after.st_ino,after.st_uid,after.st_gid,stat.S_IMODE(after.st_mode),after.st_size) == (6400127,996,995,0o600,0)
        assert (after.st_dev,after.st_ino) == (path_after.st_dev,path_after.st_ino) == (before.st_dev,before.st_ino)
        report['after'] = record(after)
        report['passed'] = True
    finally:
        os.close(control_fd)
finally:
    os.close(state_fd)
report['locks_released'] = True
report['finished_at'] = datetime.now(UTC).isoformat()
fd = os.open(OUT,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'w') as f:
    json.dump(report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
print(json.dumps(report))
