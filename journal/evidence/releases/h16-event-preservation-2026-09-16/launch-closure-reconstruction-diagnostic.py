"""Guard one explicitly non-acceptance reconstruction diagnostic; no release actions."""
import importlib.util
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE=Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16')
HELPER=EVIDENCE.parent/'h16-validation-2026-09-16/launch-proof-build-audit.py'
import hashlib
assert hashlib.sha256(HELPER.read_bytes()).hexdigest()=='3ed2731f20e67d0f45e2f0c1262dff0e3e2135597cf00eb570926201795af6da'
spec=importlib.util.spec_from_file_location('retained_launcher_helpers',HELPER)
h=importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
unit='swingset-h16-event-preservation-diagnostic.service'
monitor='swingset-h16-event-preservation-diagnostic-monitor.service'
worker=EVIDENCE/'reconstruct-closure-diagnostic.py'
resources=Path('/var/tmp/swingset-h16-event-preservation-diagnostic-resources.json')
output=Path('/var/tmp/swingset-h16-event-preservation-diagnostic.json')
monitor_driver=Path('/var/tmp/h16-changelog-build-monitor.py')
h.require(h.sha(monitor_driver)==h.DRIVERS[str(monitor_driver)],'Retained monitor differs')
h.require(h.sha(EVIDENCE/'closure_rows.diagnostic.py')=='982f8b4eb1bdd96ad8473882b312242ec4a45b3cf0c68bc4a0426de2683798e7','Override changed')
h.held()
for path in (resources,output,EVIDENCE/'diagnostic-orchestration.json',EVIDENCE/'diagnostic-launch.json'):
    h.require(not path.exists() and not path.is_symlink(),'Output must be new: '+str(path))
for name in (unit,monitor): h.require(h.status(name)['LoadState']=='not-found','Unit already exists')
argv=['systemd-run','--wait','--unit='+unit,'--property=Type=exec','--property=User=swingset','--property=Group=swingset','--property=RuntimeMaxSec=15min','--property=TimeoutStopSec=3min','--property=MemoryAccounting=yes','--property=UMask=0077','--setenv=PYTHONDONTWRITEBYTECODE=1',f'--setenv=PYTHONPATH={h.SOURCE}/src:{h.SOURCE}','--setenv=SWINGSET_REVISION=diagnostic-only:old-source-with-closure-override','--setenv=LD_LIBRARY_PATH='+h.LD,h.PYTHON,str(worker)]
monitor_argv=['systemd-run','--unit='+monitor,'--property=Type=exec','--property=RuntimeMaxSec=19min','--property=TimeoutStopSec=30','--property=UMask=0077','--setenv=PYTHONDONTWRITEBYTECODE=1',h.PYTHON,str(monitor_driver),'--unit',unit,'--output',str(resources),'--max-seconds','900']
report={'format':'h16-event-preservation-diagnostic-orchestration-v1','at':datetime.now(UTC).isoformat(),'diagnostic_only':True,'release_acceptance':False,'production_mutated':False,'passed':False,'worker_argv':argv,'monitor_argv':monitor_argv,'script_sha256':h.sha(Path(__file__)),'worker_sha256':h.sha(worker),'helper_sha256':h.sha(HELPER),'monitor_sha256':h.sha(monitor_driver),'override_sha256':h.sha(EVIDENCE/'closure_rows.diagnostic.py')}
h.save(EVIDENCE/'diagnostic-launch.json',report)
process=None
try:
    with (EVIDENCE/'diagnostic-systemd-wait.log').open('xb') as log:
        process=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT)
        started=time.monotonic()
        while True:
            initial=h.status(unit)
            if initial['ActiveState']=='active' and initial['MainPID']!='0': break
            h.require(process.poll() is None and time.monotonic()-started<30,'Worker failed before monitor start')
            time.sleep(.2)
        report['initial_unit']=initial
        h.run(monitor_argv)
        while process.poll() is None:
            h.held()
            h.require(time.monotonic()-started<1110,'Launcher deadline exceeded')
            print(json.dumps({'unit':unit,'elapsed_seconds':round(time.monotonic()-started),'diagnostic_only':True,'state':h.status(unit)}),flush=True)
            time.sleep(20)
        report['systemd_wait_exit_code']=process.returncode
        report['final_unit']=h.status(unit)
        deadline=time.monotonic()+35
        while not resources.exists() or not json.loads(resources.read_bytes()).get('finished_at'):
            h.require(time.monotonic()<deadline,'Monitor did not finish')
            time.sleep(1)
        resource=json.loads(resources.read_bytes())
        report['resource_sha256']=h.sha(resources)
        h.require(resource['terminated_reason'] is None,'Resource guard stopped diagnostic')
        h.require(any(s.get('MainPID')==initial['MainPID'] and s.get('ActiveState')=='active' for s in resource['samples']),'No running worker resource sample')
        h.require(process.returncode==0,'Worker failed; see diagnostic result')
        result=json.loads(output.read_bytes())
        report['result_sha256']=h.sha(output)
        h.require(result.get('passed') is True and result.get('diagnostic_only') is True and result.get('release_acceptance') is False,'Diagnostic outcome differs')
        h.require(h.sha(worker)==report['worker_sha256'],'Worker source changed during diagnostic')
        h.held()
        report['passed']=True
except BaseException as error:
    report['error']={'type':type(error).__name__,'message':str(error)}
    if process is not None and process.poll() is None:
        stopped=subprocess.run(['systemctl','stop',unit],capture_output=True,text=True,timeout=200)
        report['stop']={'returncode':stopped.returncode,'stderr':stopped.stderr}
    raise
finally:
    for source,name in ((output,'diagnostic-result.json'),(resources,'diagnostic-resources.json')):
        if source.exists(): (EVIDENCE/name).open('xb').write(source.read_bytes())
    for name in (unit,monitor):
        (EVIDENCE/(name+'.log')).open('x').write(h.run(['journalctl','-u',name,'--no-pager','-o','short-iso']))
    report['finished_at']=datetime.now(UTC).isoformat()
    h.save(EVIDENCE/'diagnostic-orchestration.json',report)
    print(json.dumps({'passed':report['passed'],'diagnostic_only':True,'release_acceptance':False,'error':report.get('error')}),flush=True)
