"""Stage reviewed private operation files; no database, activation, or release calls."""
import hashlib,json,os,pwd
from pathlib import Path
from datetime import datetime,timezone
ops=Path('/var/lib/swingset/operations/h16-closure-release-20260914')
assert not ops.exists() and not ops.is_symlink()
owner=pwd.getpwnam('swingset')
prepared=Path('/mnt/mac/tmp/h16-closure-operation-preparation-20260914')
gate=json.loads((prepared/'gate.json').read_bytes())
files={
 'gate.json':(prepared/'gate.json','2ed8c721a07efa12cca325e8f3a048417f82b5c72f4197370fe8bfd52d731d7a'),
 'accept_h16.py':(prepared/'accept_h16.py','8ed4b2384fb946100934a14b57025b6cc6272fb7927632eaede5c19eb2519f50'),
 'initialize.py':(Path('/mnt/mac/tmp/h16-live-initialize.py'),'b54c02a15c3d86a603bef4d6926e66bde4cf1ef9d23f273aa5db771d3a99d233'),
 'release.py':(Path('/mnt/mac/tmp/h16-closure-production-release.py'),'9da822db06cd1fcb4d0119706b7c91bc6e128e1447414b817f6b91ff703a51ca'),
 'switch.py':(Path('/mnt/mac/tmp/h16-closure-switch-20260914.py'),'94ceacce56d3f24d2fadbc0a3d9d59cee0acd6fd32537e61e113441aeada7985'),
 'supervise-initialization.py':(Path('/mnt/mac/tmp/h16-initializer-supervisor-v2.py'),'2f6c6f88604db55342bc9d3adbfee9b789143846815cc6380768d26ebb952cae'),
 'audit.py':(Path('/var/tmp/h16-changelog-audit-driver.py'),'9de88bd2b5d988a00ee0529504e301b23c93eaf5018485a3f6f4db0deb2deebf')}
files.update({name:(prepared/name,sha) for name,sha in gate['evidence_files'].items()})
content={}
for name,(path,expected) in files.items():
 assert path.is_file() and not path.is_symlink()
 raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==expected
 content[name]=raw
assert all(not p.is_symlink() for p in (ops.parent,*ops.parent.parents))
ops.mkdir(mode=0o700);os.chown(ops,owner.pw_uid,owner.pw_gid)
for name,raw in content.items():
 path=ops/name
 with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
 path.chmod(0o600);os.chown(path,owner.pw_uid,owner.pw_gid)
print(json.dumps({'format':'h16-private-operation-staging-v1','staged_at':datetime.now(timezone.utc).isoformat(),'operation_directory':str(ops),'files':{name:sha for name,(_,sha) in files.items()},'staged_only':True,'database_changed':False,'activated':False,'published':False},sort_keys=True,indent=2))
