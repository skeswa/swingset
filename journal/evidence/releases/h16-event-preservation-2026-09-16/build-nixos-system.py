"""Build the frozen NixOS artifact offline; never activate or change profiles."""
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

SOURCE=Path('/nix/store/z689qy41inndill3d92ym8im852x3649-source')
SOURCE_HASH='0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb'
EVIDENCE=Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16')
OUTPUT=EVIDENCE/'nix-build.json'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def links(): return {p:str(Path(p).resolve()) for p in ('/run/current-system','/nix/var/nix/profiles/system')}
def require(value,message):
    if not value: raise ValueError(message)

require(not OUTPUT.exists() and not (EVIDENCE/'nix-build.log').exists(),'Build evidence must be new')
require(sha(SOURCE/'h16-source.json')==SOURCE_HASH,'Frozen receipt mismatch')
argv=['nix','--extra-experimental-features','nix-command flakes','build','--offline','--no-update-lock-file','--no-link','--print-out-paths',f'path:{SOURCE}#nixosConfigurations.orb.config.system.build.toplevel']
report={'format':'h16-event-preservation-nixos-build-v1','at':datetime.now(UTC).isoformat(),'source':str(SOURCE),'source_receipt_sha256':SOURCE_HASH,'argv':argv,'active_before':links(),'activated':False,'passed':False,'script_sha256':sha(Path(__file__))}
start=time.monotonic()
try:
    result=subprocess.run(argv,capture_output=True,text=True,timeout=1200)
    (EVIDENCE/'nix-build.log').open('x').write(result.stdout+result.stderr)
    report['exit_code']=result.returncode
    require(result.returncode==0,'Offline Nix build failed; retained log')
    paths=[line for line in result.stdout.splitlines() if line.startswith('/nix/store/')]
    require(len(paths)==1,'Expected one immutable system result')
    report['system']=paths[0]
    require((Path(paths[0])/'etc/systemd/system/swingset-cycle.service').is_file(),'System missing cycle unit')
    report['active_after']=links()
    require(report['active_before']==report['active_after'],'Active/persistent system changed')
    require(sha(SOURCE/'h16-source.json')==SOURCE_HASH,'Frozen receipt changed')
    report['passed']=True
except BaseException as error:
    report['error']={'type':type(error).__name__,'message':str(error)}
    raise
finally:
    report['elapsed_seconds']=time.monotonic()-start
    OUTPUT.open('x').write(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report),flush=True)
