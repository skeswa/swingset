"""Stage reviewed private H16 files and check lightweight authority; never execute drivers."""
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

INPUTS = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16/production-preparation')
MANIFEST_SHA = '4d8c591592c44a64b393f839d51c2575258a9787871ca3f07459feda0285ace8'
OUTPUT = Path('/var/tmp/h16-event-preservation-production-staging-20260916.json')

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def require(value, message):
    if not value:
        raise ValueError(message)

def no_symlinks(path):
    require(not any(item.is_symlink() for item in (path, *path.parents)), 'symlink path: ' + str(path))

def write_new(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())

require(os.geteuid() == 0, 'staging requires root for reviewed ownership')
require(not OUTPUT.exists(), 'staging evidence already exists')
report = {'format': 'h16-private-staging-v1', 'started_at': datetime.now(UTC).isoformat(),
          'passed': False, 'preflight_executed': False, 'activation_executed': False,
          'initializer_executed': False, 'build_executed': False, 'published': False,
          'remote_verification_performed': False, 'production_database_opened': False,
          'checkpoint_opened': False, 'staged_files': {}, 'script_sha256': sha(Path(__file__))}
try:
    require(sha(INPUTS / 'staging-inputs.json') == MANIFEST_SHA, 'staging manifest changed')
    packet = json.loads((INPUTS / 'staging-inputs.json').read_bytes())
    source, system, destination = (Path(packet[name]) for name in ('source', 'system', 'destination'))
    no_symlinks(destination)
    require(not destination.exists(), 'private operation path already exists; preserve it')
    require(sha(source / 'h16-source.json') == packet['source_receipt_sha256'], 'source receipt differs')
    source_receipt = json.loads((source / 'h16-source.json').read_bytes())
    actual = {path.relative_to(source).as_posix(): sha(path) for path in source.rglob('*') if path.is_file() and path != source / 'h16-source.json'}
    require(actual == source_receipt['files'], 'frozen source file closure differs')
    require(source_receipt['schema'] == 14, 'expected schema14 source')
    report.update(source=str(source), source_receipt_sha256=packet['source_receipt_sha256'], source_files=len(actual), system=str(system))
    report['system_units'] = {}
    wrappers = set()
    for name in ('cycle', 'backup', 'summary'):
        path = system / ('etc/systemd/system/swingset-' + name + '.service')
        unit = path.read_text()
        for required in (f'WorkingDirectory={source}\n', f'--project {source} --frozen --no-dev', f'--config {source}/config ', 'ConditionPathExists=!/var/lib/swingset/operator-hold\n', 'User=swingset\n', 'Group=swingset\n'):
            require(required in unit, 'system service binding differs: ' + name)
        matched = re.search(r'^ExecStart=(\S+) ', unit, re.M)
        require(matched is not None, 'missing service wrapper')
        wrappers.add(matched.group(1))
        report['system_units'][name] = {'path': str(path), 'sha256': sha(path)}
    require(len(wrappers) == 1, 'service wrappers differ')
    wrapper = Path(wrappers.pop())
    text = wrapper.read_text()
    require(f'exec uv run --project {source} --frozen --no-dev swingset' in text, 'wrapper source differs')
    require(f'export SWINGSET_REVISION=uncommitted:{source.name}\n' in text, 'wrapper revision differs')
    report['wrapper'] = {'path': str(wrapper), 'sha256': sha(wrapper)}
    report['current_systems'] = {name: str(Path(name).resolve(strict=True)) for name in ('/run/current-system', '/nix/var/nix/profiles/system')}
    require(all(value == packet['old_system'] for value in report['current_systems'].values()), 'active/persistent predecessor differs')
    require(Path('/var/lib/swingset/operator-hold').is_file(), 'operator hold absent')
    report['operator_hold'] = True
    report['ordinary_units'] = {}
    for task in ('cycle', 'backup', 'summary'):
        for kind in ('service', 'timer'):
            unit = f'swingset-{task}.{kind}'
            state = subprocess.run(['systemctl', 'show', unit, '--property=ActiveState', '--value'], check=True, capture_output=True, text=True, timeout=10).stdout.strip()
            report['ordinary_units'][unit] = state
            require(state == 'inactive', 'ordinary unit active: ' + unit)
    for name, digest in packet['files'].items():
        require(Path(name).name == name, 'staging filename must be flat')
        path = INPUTS / name
        no_symlinks(path)
        require(path.is_file() and sha(path) == digest, 'reviewed staging input changed: ' + name)
    require(packet['files']['accept_h16.py'] == sha(source / 'research/accept_h16.py'), 'operational helper differs from pin')
    gate = json.loads((INPUTS / 'gate.json').read_bytes())
    require(gate['driver_sha256'] == packet['files']['accept_h16.py'], 'gate helper differs')
    require(gate['source_receipt_sha256'] == packet['source_receipt_sha256'], 'gate source differs')
    require(all(packet['files'].get(name) == digest for name, digest in gate['evidence_files'].items()), 'gate evidence binding differs')
    owner = pwd.getpwnam('swingset')
    destination.mkdir(mode=0o700)
    os.chown(destination, owner.pw_uid, owner.pw_gid)
    for name, digest in packet['files'].items():
        data = (INPUTS / name).read_bytes()
        require(hashlib.sha256(data).hexdigest() == digest, 'input changed while copying')
        target = destination / name
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchown(stream.fileno(), owner.pw_uid, owner.pw_gid)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        info = target.stat()
        require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == owner.pw_uid and info.st_gid == owner.pw_gid and sha(target) == digest, 'private staged file verification failed')
        report['staged_files'][name] = {'sha256': digest, 'uid': info.st_uid, 'gid': info.st_gid, 'mode': '0600'}
    descriptor = os.open(destination, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    require(stat.S_IMODE(destination.stat().st_mode) == 0o700, 'private directory mode differs')
    report.update(passed=True, destination=str(destination), directory_mode='0700', manifest_sha256=MANIFEST_SHA, gate_sha256=packet['files']['gate.json'])
except BaseException as error:
    report['error'] = {'type': type(error).__name__, 'message': str(error)}
    raise
finally:
    report['finished_at'] = datetime.now(UTC).isoformat()
    write_new(OUTPUT, report)
print(json.dumps({'passed': report['passed'], 'output': str(OUTPUT), 'sha256': sha(OUTPUT), 'files': len(report['staged_files'])}))
