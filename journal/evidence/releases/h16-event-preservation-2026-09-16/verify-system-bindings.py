"""Independent frozen file and Nix service binding check; never activate."""
import hashlib
import json
import re
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

SOURCE=Path('/nix/store/z689qy41inndill3d92ym8im852x3649-source')
SOURCE_HASH='0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb'
SYSTEM=Path('/nix/store/sx7lpr80cx0n9vzsi3cwz9nxawqc4p1i-nixos-system-swingset-lxc-25.11.20260630.b6018f8')
CHECKPOINT=Path('/var/lib/swingset/checkpoints/h15-before-h16-20260913')
EVIDENCE=Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16')
OUTPUT=EVIDENCE/'system-binding-verification.json'

def sha(p):
    with p.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()

def check(name,value):
    report['checks'][name]=bool(value)
    if not value: raise ValueError(name)

report={'at':datetime.now(UTC).isoformat(),'source':str(SOURCE),'source_receipt_sha256':SOURCE_HASH,'system':str(SYSTEM),'checks':{},'units':{},'passed':False,'activated':False,'production_mutated':False,'script_sha256':sha(Path(__file__))}
try:
    check('receipt_matches_pin',sha(SOURCE/'h16-source.json')==SOURCE_HASH)
    receipt=json.loads((SOURCE/'h16-source.json').read_bytes())
    actual={p.relative_to(SOURCE).as_posix():sha(p) for p in SOURCE.rglob('*') if p.is_file() and p!=SOURCE/'h16-source.json'}
    check('every_frozen_file_matches_receipt',actual==receipt['files'])
    report['source_file_count']=len(actual)+1
    wrappers=set()
    for name in ('cycle','backup','summary'):
        unit=(SYSTEM/f'etc/systemd/system/swingset-{name}.service').read_text()
        report['units'][name]=unit
        check(name+'_working_directory',f'WorkingDirectory={SOURCE}\n' in unit)
        check(name+'_sync',f'--project {SOURCE} --frozen --no-dev' in unit)
        check(name+'_config',f'--config {SOURCE}/config ' in unit)
        check(name+'_operator_hold','ConditionPathExists=!/var/lib/swingset/operator-hold\n' in unit)
        check(name+'_user','User=swingset\n' in unit)
        match=re.search(r'^ExecStart=(\S+) ',unit,re.M)
        check(name+'_wrapper',match is not None)
        wrappers.add(match.group(1))
    check('shared_wrapper',len(wrappers)==1)
    wrapper=Path(wrappers.pop())
    body=wrapper.read_text()
    report['wrapper']=str(wrapper)
    report['wrapper_sha256']=sha(wrapper)
    check('wrapper_source',f'exec uv run --project {SOURCE} --frozen --no-dev swingset' in body)
    check('wrapper_revision',f'export SWINGSET_REVISION=uncommitted:{SOURCE.name}\n' in body)
    check('wrapper_config',f'export SWINGSET_CONFIG_DIR={SOURCE}/config\n' in body)
    check('production_hold',Path('/var/lib/swingset/operator-hold').is_file())
    report['production_units']={}
    for task in ('cycle','backup','summary'):
        for kind in ('service','timer'):
            name=f'swingset-{task}.{kind}'
            state=subprocess.run(['systemctl','show',name,'--property=ActiveState','--value'],check=True,capture_output=True,text=True).stdout.strip()
            report['production_units'][name]=state
            check(name+'_inactive',state=='inactive')
    before=json.loads((EVIDENCE/'nix-build-002.json').read_bytes())['active_before']
    current={p:str(Path(p).resolve()) for p in before}
    report['current_systems']=current
    check('active_and_persistent_unchanged',current==before)
    report['impure_host_config_hashes']={str(p):sha(p) for p in (Path('/etc/nixos/configuration.nix'),Path('/etc/nixos/orbstack.nix')) if p.is_file()}
    report['checkpoint_manifest_sha256']=sha(CHECKPOINT/'checkpoint.json')
    report['checkpoint_database_sha256']=sha(CHECKPOINT/'state.sqlite')
    check('checkpoint_manifest_pin',report['checkpoint_manifest_sha256']=='700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444')
    check('checkpoint_database_pin',report['checkpoint_database_sha256']=='d81b2e459471c25c430a57c2a4d096180f1094e57c1b8b1e896d66774f12e8dc')
    with sqlite3.connect((CHECKPOINT/'state.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True) as conn:
        report['checkpoint_running_attempts']=conn.execute("SELECT count(*) FROM work_attempts WHERE outcome='running'").fetchone()[0]
        check('no_inherited_running_attempts',report['checkpoint_running_attempts']==0)
    report['passed']=True
except BaseException as error:
    report['error']={'type':type(error).__name__,'message':str(error)}
    raise
finally:
    OUTPUT.open('x').write(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'passed':report['passed'],'system':str(SYSTEM),'output':str(OUTPUT),'sha256':sha(OUTPUT),'error':report.get('error')}),flush=True)
