"""Serial, bounded launcher for separately reviewed build, audit, or publication."""
import argparse
import hashlib
import json
import os
import pwd
import re
import signal
import subprocess
import time
import types
from datetime import UTC, datetime
from pathlib import Path

OPS = Path('/var/lib/swingset/operations/h16-event-preservation-release-20260916')
SOURCE = Path('/nix/store/z689qy41inndill3d92ym8im852x3649-source')
STATE = Path('/var/lib/swingset')
RELEASE_SHA = 'f8cd9ec23953600d3c9445a765df97eb894fd5363cea3f1136446c67a4f902cc'
AUDIT = Path('/var/tmp/h16-changelog-audit-driver.py')
AUDIT_SHA = '9de88bd2b5d988a00ee0529504e301b23c93eaf5018485a3f6f4db0deb2deebf'
RUNTIME = 3600
STOP = 180
ENV = {'PYTHONPATH': str(SOURCE/'src')+':'+str(SOURCE), 'SWINGSET_REVISION': 'uncommitted:'+SOURCE.name, 'PYTHONDONTWRITEBYTECODE': '1', 'LD_LIBRARY_PATH': '/nix/store/x03dxqva88ax4w45hyms977zv3f8a8i9-gcc-14.3.0-lib/lib:/nix/store/5zq11bibj72nvrhlx9fm0xl0xhxd6388-zlib-1.3.2/lib'}

def require(value, message):
    if not value:
        raise ValueError(message)

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def safe_file(path, *, new=False):
    require(path.is_absolute() and '..' not in path.parts and not any(p.is_symlink() for p in (path,*path.parents)), 'unsafe file path')
    require(not path.exists() if new else path.is_file(), 'file existence differs')

def save(path, value):
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream,indent=2,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())

def status(unit):
    p = subprocess.run(['systemctl','show',unit,'--property=LoadState,ActiveState,SubState,MainPID,ControlGroup,Result,ExecMainStatus,MemoryCurrent,MemoryPeak'],capture_output=True,text=True,check=True,timeout=15)
    return dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)

def held():
    require((STATE/'operator-hold').is_file(),'operator hold disappeared')
    for name in ('cycle','backup','summary'):
        for kind in ('service','timer'):
            require(status(f'swingset-{name}.{kind}')['ActiveState']=='inactive','ordinary unit active')

def command(args, unit, output):
    if args.phase == 'audit':
        worker = ['/var/lib/swingset/venv/bin/python',str(AUDIT),'--state',str(STATE),'--candidate',str(args.candidate),'--baseline',str(args.baseline),'--output',str(output),'--temp-parent','/var/tmp']
    else:
        worker = ['/var/lib/swingset/venv/bin/python',str(OPS/'production-release.py'),args.phase,'--gate',str(args.gate),'--output',str(output)]
        if args.resume:
            worker.append('--resume')
    return ['systemd-run','--wait','--unit='+unit,'--property=Type=exec','--property=User=swingset','--property=Group=swingset','--property=UMask=0077','--property=WorkingDirectory='+str(SOURCE),'--property=RuntimeMaxSec='+str(RUNTIME),'--property=TimeoutStopSec='+str(STOP),'--property=MemoryAccounting=yes','--property=KillSignal=SIGTERM',*(['--property=EnvironmentFile=/etc/swingset.env'] if args.phase=='publish' else []),*['--setenv='+k+'='+v for k,v in ENV.items()],*worker]

def validate_args(args):
    require(re.fullmatch('[a-z0-9][a-z0-9-]{0,39}',args.label),'invalid exclusive label')
    require(args.output.parent==OPS,'receipt must be directly in private OPS')
    safe_file(args.output,new=True)
    require(not args.resume or args.phase=='publish','resume only for same-candidate publication')
    if args.phase=='audit':
        require(args.gate is None and args.gate_sha256 is None,'audit has no invented gate')
        require(args.candidate is not None and args.baseline is not None,'audit candidate and baseline required')
        for path in (args.candidate,args.baseline):
            require(path.parent==STATE/'candidates' and path.is_dir() and not any(p.is_symlink() for p in (path,*path.parents)), 'actual production candidate directories required')
    else:
        require(args.candidate is None and args.baseline is None,'candidate comes only from reviewed release gate')
        require(args.gate is not None and args.gate.parent==OPS and args.gate!=args.output,'private distinct gate required')
        require(args.gate_sha256 is not None and re.fullmatch('[0-9a-f]{64}',args.gate_sha256),'actual gate hash required')
        safe_file(args.gate)
        require(sha(args.gate)==args.gate_sha256,'reviewed gate changed')
        gate=json.loads(args.gate.read_bytes())
        require(gate.get('mode')==args.phase and gate.get('authorized') is True,'wrong phase authorization')
        require(gate.get('source')==str(SOURCE) and gate.get('driver_sha256')==RELEASE_SHA,'wrong frozen release authority')


GUARD_SHA = 'b39cedadd065dad76eb5a47f0c5de3dd39a737895a7198bb7a48a75f5ea1623b'

def load_guard(path):
    safe_file(path)
    data=path.read_bytes()
    require(hashlib.sha256(data).hexdigest()==GUARD_SHA,'reviewed serial/memory guard changed')
    module=types.ModuleType('reviewed_phase_guard');module.__file__=str(path)
    exec(compile(data,str(path),'exec'),module.__dict__)
    return module

def memory_sample(current,guard):
    pid=int(current.get('MainPID','0'));group=current.get('ControlGroup')
    require(not pid or group,'live worker lacks cgroup telemetry')
    if group:
        value=guard.anonymous_bytes(group)
        require(value<6*1024**3,'anonymous memory reached 6GiB')
        return value
    require(current.get('ActiveState')!='active' or current.get('SubState')=='exited','active worker lacks cgroup telemetry')
    return None

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=('build','audit','publish'))
    p.add_argument('--label',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--gate',type=Path);p.add_argument('--gate-sha256')
    p.add_argument('--candidate',type=Path);p.add_argument('--baseline',type=Path)
    p.add_argument('--resume',action='store_true')
    args=p.parse_args();require(os.geteuid()==0,'root launcher required; worker is service-owned')
    validate_args(args)
    driver=AUDIT if args.phase=='audit' else OPS/'production-release.py'
    safe_file(driver);require(sha(driver)==(AUDIT_SHA if args.phase=='audit' else RELEASE_SHA),'reviewed executable changed')
    # Capture and worker output stay outside the checkout; the audit must also stay outside state.
    capture=Path('/var/tmp/h16-production-'+args.phase+'-'+args.label)
    unit='swingset-h16-production-'+args.phase+'-'+args.label+'.service'
    require(status(unit)['LoadState']=='not-found','unit already exists')
    guard=load_guard(OPS/'monitoring/guard-initialization-003.py')
    guard.no_other_workers()
    held();capture.mkdir(mode=0o700)
    owner=pwd.getpwnam('swingset');os.chown(capture,owner.pw_uid,owner.pw_gid)
    output=capture/'audit.json' if args.phase=='audit' else args.output
    argv=command(args,unit,output)
    report={'phase':args.phase,'started_at':datetime.now(UTC).isoformat(),'argv':argv,'script_sha256':sha(Path(__file__)),'driver_sha256':sha(driver),'gate_sha256':args.gate_sha256,'guard_sha256':GUARD_SHA,'worker_runtime_seconds':RUNTIME,'monitor_deadline_seconds':RUNTIME+STOP+60,'passed':False}
    save(capture/'intent.json',report)
    def interrupted(*_):
        raise KeyboardInterrupt('phase launcher interrupted')
    signal.signal(signal.SIGTERM,interrupted)
    process=None;started=time.monotonic();peak=0;seen=False
    try:
        with (capture/'systemd-wait.log').open('xb') as log:
            process=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT)
            while process.poll() is None:
                current=status(unit);elapsed=time.monotonic()-started
                sample={'at':datetime.now(UTC).isoformat(),'elapsed_seconds':elapsed,**current}
                memory=memory_sample(current,guard)
                if memory is not None:
                    sample['anonymous_bytes']=memory;peak=max(peak,memory)
                    if current.get('MainPID','0')!='0':seen=True
                require(elapsed<=30 or process.poll() is not None or seen,'unit failed to start within30s')
                with (capture/'resources.jsonl').open('a') as stream:
                    stream.write(json.dumps(sample,sort_keys=True)+'\n');stream.flush();os.fsync(stream.fileno())
                held();require(elapsed<RUNTIME+STOP+60,'monitor deadline exceeded')
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:pass
            report['direct_exit_code']=process.returncode
            require(process.returncode==0,'phase worker failed')
        require(seen,'no live worker telemetry was observed')
        result=json.loads(output.read_bytes());require(result.get('passed') is True,'phase receipt failed')
        if args.phase=='audit':
            require(result.get('state')==str(STATE) and result.get('candidate')==str(args.candidate),'audit target differs')
            require(len(result.get('checks',{}))>=54 and all(v is True for v in result['checks'].values()),'audit checks incomplete')
            data=output.read_bytes();fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as stream:
                os.fchown(stream.fileno(),owner.pw_uid,owner.pw_gid);stream.write(data);stream.flush();os.fsync(stream.fileno())
            require(sha(args.output)==sha(output),'retained audit differs')
        elif args.phase=='publish':
            require(result.get('published') is True and result.get('publication_receipt',{}).get('commit'),'publication not acknowledged')
        else:
            require(result.get('status')=='built' and result.get('published') is False,'build result differs')
        held();report.update(passed=True,receipt_sha256=sha(args.output))
    except BaseException as error:
        report['error']={'type':type(error).__name__,'message':str(error)}
        stopped=subprocess.run(['systemctl','stop',unit],capture_output=True,text=True,timeout=STOP+20);report['stop_exit_code']=stopped.returncode
        if process is not None:
            try:report['direct_exit_code']=process.wait(timeout=STOP+30)
            except subprocess.TimeoutExpired:report['unsettled']=True
        raise
    finally:
        report.update(finished_at=datetime.now(UTC).isoformat(),peak_sampled_anonymous_bytes=peak,observed_live_unit=seen,final_unit=status(unit))
        journal=subprocess.run(['journalctl','-u',unit,'--no-pager','-o','short-iso'],capture_output=True,text=True,timeout=30)
        with (capture/'journal.log').open('x') as stream:stream.write(journal.stdout)
        if output.is_file() and output!=capture/'audit.json':
            with (capture/'receipt.json').open('xb') as stream:stream.write(output.read_bytes())
        save(capture/'execution.json',report)
    print(json.dumps({'passed':True,'capture':str(capture),'receipt_sha256':report['receipt_sha256']}),flush=True)

if __name__=='__main__':
    main()
