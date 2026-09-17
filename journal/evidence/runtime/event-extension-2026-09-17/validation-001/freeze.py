import hashlib,json,shutil,subprocess
from datetime import UTC,datetime
from pathlib import Path
root=Path.cwd().resolve()
out=Path('/tmp/swingset-extension-freeze-20260917-001')
if out.exists():raise ValueError('single-use destination exists')
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
paths=[Path(p.decode()) for p in subprocess.check_output(['jj','file','list','-T','path ++ "\\0"']).split(b'\0') if p]
if any((root/p).is_symlink() or not (root/p).is_file() for p in paths):raise ValueError('only regular tracked files')
before={p.as_posix():sha(root/p) for p in paths}
out.mkdir()
for p in paths:
    (out/p).parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(root/p,out/p)
after={p.as_posix():sha(root/p) for p in paths}
actual={p.as_posix():sha(out/p) for p in paths}
if not before==after==actual:raise ValueError('source changed while freezing')
receipt={'format':'event-extension-source-v1','created_at':datetime.now(UTC).isoformat(),'parent':subprocess.check_output(['jj','log','-r','@-','--no-graph','-T','commit_id']).decode(),'schema':27,'files':actual,'deployed':False,'published':False}
(out/'extension-source.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
print(json.dumps({'source':str(out),'file_count':len(actual),'receipt_sha256':sha(out/'extension-source.json'),'bytes':sum((out/p).stat().st_size for p in paths)},indent=2))
