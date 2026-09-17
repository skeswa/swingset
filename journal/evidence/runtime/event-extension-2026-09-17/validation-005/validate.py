from pathlib import Path
from datetime import datetime, timezone
import subprocess,os,hashlib,json
s=Path('/private/tmp/swingset-extension-freeze-20260917-005');o=Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/runtime/event-extension-2026-09-17/validation-005');v=Path('/Users/skeswa/repos/skeswa/swingset/.venv/bin');r=json.loads((s/'extension-source.json').read_bytes());expected='9255e8641a24c21c0942512ec251b32dd294bc6f2a537868a0012cafe331dc99'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def verify():
 assert sha(s/'extension-source.json')==expected
 actual={str(p.relative_to(s)) for p in s.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p!=s/'extension-source.json'}
 assert actual==set(r['files'])
 for n,h in r['files'].items():assert sha(s/n)==h,n
verify();env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(s/'src')+':'+str(s));env['PATH']=str(v)+':'+env['PATH']
report=dict(format='event-extension-validation-v1',started_at=datetime.now(timezone.utc).isoformat(),source=str(s),source_receipt_sha256=expected,schema=29,source_verified_before=True,checks=[],passed=False,deployed=False,published=False)
for name,cmd in [('pytest',[str(v/'python'),'-m','pytest','-q','-p','no:cacheprovider']),('ruff',[str(v/'ruff'),'check','--no-cache','.']),('mypy',[str(v/'mypy'),'--cache-dir=/tmp/swingset-extension-mypy-cache-20260917-005','src/swingset'])]:
 print('Starting '+name,flush=True)
 with (o/(name+'.log')).open('x') as stream: result=subprocess.run(cmd,cwd=s,env=env,stdout=stream,stderr=subprocess.STDOUT)
 report['checks'].append(dict(name=name,command=cmd,exit_code=result.returncode,log_sha256=sha(o/(name+'.log'))));print(name+' exit '+str(result.returncode),flush=True)
verify();report.update(source_verified_after=True,finished_at=datetime.now(timezone.utc).isoformat(),passed=all(x['exit_code']==0 for x in report['checks']))
(o/'validation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
