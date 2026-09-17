from pathlib import Path
import subprocess,tarfile,io,json,hashlib
src=Path('/private/tmp/swingset-extension-freeze-20260917-005');r=json.loads((src/'extension-source.json').read_bytes());assert hashlib.sha256((src/'extension-source.json').read_bytes()).hexdigest()=='9255e8641a24c21c0942512ec251b32dd294bc6f2a537868a0012cafe331dc99'
remote='/var/tmp/swingset-runtime-candidate005/source';subprocess.run(['orb','-m','swingset','-u','root','mkdir','-p',remote],check=True);b=io.BytesIO()
with tarfile.open(fileobj=b,mode='w') as t:
 for n in sorted([*r['files'],'extension-source.json']):
  f=src/n;data=f.read_bytes()
  if n in r['files']:assert hashlib.sha256(data).hexdigest()==r['files'][n],n
  m=tarfile.TarInfo(n);m.size=len(data);m.mode=0o755 if f.stat().st_mode&0o111 else 0o644;t.addfile(m,io.BytesIO(data))
subprocess.run(['orb','-m','swingset','-u','root','tar','-xf','-','-C',remote],input=b.getvalue(),check=True);pin=subprocess.check_output(['orb','-m','swingset','-u','root','nix-store','--add',remote],text=True).strip();print(pin,flush=True);Path('/tmp/swingset-runtime-candidate005-pin.txt').write_text(pin+'\n')
with Path('/tmp/swingset-runtime-candidate005-build.log').open('x') as log:
 result=subprocess.run(['orb','-m','swingset','-u','root','nix','build','--offline','--impure','--no-link','--print-out-paths',pin+'#nixosConfigurations.orb.config.system.build.toplevel'],stdout=log,stderr=subprocess.STDOUT)
print('build exit',result.returncode,flush=True)
