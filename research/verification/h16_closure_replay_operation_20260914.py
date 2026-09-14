import hashlib,json,subprocess,sys
from pathlib import Path
mode,number=sys.argv[1],int(sys.argv[2]);suffix=f'{number:03d}'
pin='/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source'
unit=f'swingset-h16-closure-replay-{suffix}'
base=['orb','-m','swingset','-u','root']
if mode=='retain':
 remote=f'/var/tmp/swingset-h16-closure-replay-{suffix}.json'
 data=subprocess.check_output(base+['cat',remote]); receipt=json.loads(data)
 if receipt['status'] not in ('bounded_stop','current','no_runnable_progress','interrupted'):raise ValueError('not a final receipt')
 target=Path(f'research/verification/h16-closure-replay-{suffix}-20260914.json')
 with target.open('xb') as out:out.write(data)
 journal=subprocess.check_output(base+['journalctl','-u',unit+'.service','--no-pager','-o','short-iso'])
 with target.with_suffix('.log').open('xb') as out:out.write(journal)
 print(json.dumps({'path':str(target),'sha256':hashlib.sha256(data).hexdigest(),'status':receipt['status'],'generation_count':receipt['generation_count']}))
elif mode=='launch':
 cmd=base+['systemd-run','--unit='+unit,'--property=User=swingset','--property=Group=swingset','--property=RuntimeMaxSec=900','--property=MemoryAccounting=yes','--property=RemainAfterExit=yes','--setenv=PYTHONDONTWRITEBYTECODE=1','--setenv=PYTHONPATH='+pin+'/src:'+pin,'--setenv=SWINGSET_REVISION=uncommitted:2d5q3lgljfkmm78hf44g954lzwx79yhv-source','--setenv=LD_LIBRARY_PATH=/nix/store/x03dxqva88ax4w45hyms977zv3f8a8i9-gcc-14.3.0-lib/lib:/nix/store/5zq11bibj72nvrhlx9fm0xl0xhxd6388-zlib-1.3.2/lib','/var/lib/swingset/venv/bin/python',pin+'/research/replay_derivations.py','--scratch','/var/tmp/swingset-h16-closure-replay','--source',pin,'--source-receipt-sha256','f8f24c2d8b839bbfcd25bf87e551e9f2777d1236f89ed84da16c3c07d4d75b9f','--config',pin+'/config','--overrides',pin+'/overrides','--output',f'/var/tmp/swingset-h16-closure-replay-{suffix}.json','--max-seconds','600']
 subprocess.run(cmd,check=True)
else:raise ValueError(mode)
