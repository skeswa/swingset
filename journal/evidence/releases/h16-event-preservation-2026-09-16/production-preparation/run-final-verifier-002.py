import hashlib,json,os,subprocess
from pathlib import Path
from datetime import UTC,datetime
p=Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16/production-preparation/final-verifier-002-command.json')
data=p.read_bytes();assert hashlib.sha256(data).hexdigest()=='f2dac4be121da32f40e5ae1e3cc67ae3463cb1d351c007c956263f425a2be42d'
c=json.loads(data);v=Path(c['argv'][1]);assert not v.is_symlink() and hashlib.sha256(v.read_bytes()).hexdigest()==c['verifier_sha256']
out=Path('/var/tmp/h16-event-preservation-final-verification-002-20260916');out.mkdir(mode=0o700)
with (out/'command.json').open('x') as f:json.dump(c,f,indent=2)
start=datetime.now(UTC).isoformat()
with (out/'run.log').open('xb') as f:
 r=subprocess.run(c['argv'],env=dict(os.environ,**c['env']),stdout=f,stderr=subprocess.STDOUT,timeout=660)
receipt=Path(c['argv'][c['argv'].index('--output')+1]);data=receipt.read_bytes() if receipt.exists() else None
if data is not None:
 with (out/'initialization-verification-002.json').open('xb') as f:f.write(data)
result={'started_at':start,'finished_at':datetime.now(UTC).isoformat(),'direct_exit_code':r.returncode,'receipt_sha256':hashlib.sha256(data).hexdigest() if data else None}
with (out/'execution.json').open('x') as f:json.dump(result,f,indent=2)
print(json.dumps(result),flush=True)
raise SystemExit(r.returncode)
