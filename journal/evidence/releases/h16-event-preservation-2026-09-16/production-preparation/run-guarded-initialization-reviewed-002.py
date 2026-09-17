"""Execute the authorized reviewed-002 guarded session; preserve VM-local evidence."""
import hashlib,json,os,subprocess
from datetime import UTC,datetime
from pathlib import Path
OPS=Path('/var/lib/swingset/operations/h16-event-preservation-release-20260916')
SOURCE=Path('/nix/store/z689qy41inndill3d92ym8im852x3649-source')
CAPTURE=Path('/var/tmp/h16-event-preservation-initialization-reviewed-002-20260916')
pins={'monitoring/guard-initialization-003.py':'b39cedadd065dad76eb5a47f0c5de3dd39a737895a7198bb7a48a75f5ea1623b','supervise-initialization-002.py':'7d2746bd079a0e0fb608ae666f4cec2146ab23561c46e2d808f0f2ba2216324f','initialization-anchor-003.json':'2bff03bce51be83ed62315274adbc07d0a5396d7b10e74a214bf9b0fb0421654'}
for name,digest in pins.items():
 path=OPS/name
 assert not path.is_symlink() and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==digest
assert json.loads((OPS/'initialization-anchor-003.json').read_bytes())['passed'] is True
CAPTURE.mkdir(mode=0o700)
env={'PYTHONPATH':str(SOURCE/'src')+':'+str(SOURCE),'SWINGSET_REVISION':'uncommitted:'+SOURCE.name,'PYTHONDONTWRITEBYTECODE':'1','LD_LIBRARY_PATH':'/nix/store/x03dxqva88ax4w45hyms977zv3f8a8i9-gcc-14.3.0-lib/lib:/nix/store/5zq11bibj72nvrhlx9fm0xl0xhxd6388-zlib-1.3.2/lib'}
argv=['/var/lib/swingset/venv/bin/python',str(OPS/'monitoring/guard-initialization-003.py'),'--supervisor',str(OPS/'supervise-initialization-002.py'),'--gate',str(OPS/'initialization-gate-002.json'),'--gate-sha256','00da07082f1cea7a20a6d3e04849e7e2bac8aa4eae8b2ad8d344da9951cc5af6','--marker',str(OPS/'initialization-marker-002.json'),'--marker-sha256','dad425983041d0a27ab2eae1127db1fc9aeec4a401f50d678d6fe87e176baece','--driver',str(OPS/'initialize-002.py'),'--driver-sha256','c3259a023436e2cc3ee56e1ad83f3170b455f42187ff71421eeac25387bf96d7','--session','reviewed-002','--max-invocations','12']
intent={'started_at':datetime.now(UTC).isoformat(),'argv':argv,'env':env,'verified_sha256':pins}
with (CAPTURE/'intent.json').open('x') as f:
 json.dump(intent,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
print(json.dumps({'started':True,'capture':str(CAPTURE)}),flush=True)
with (CAPTURE/'run.log').open('xb') as f:
 result=subprocess.run(argv,env=dict(os.environ,**env),stdout=f,stderr=subprocess.STDOUT)
receipt={'finished_at':datetime.now(UTC).isoformat(),'direct_exit_code':result.returncode}
with (CAPTURE/'execution.json').open('x') as f:
 json.dump(receipt,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
print(json.dumps(receipt),flush=True)
raise SystemExit(result.returncode)
