"""Run only the authorized frozen production preflight, retaining VM-local evidence."""
import hashlib
import json
import os
import pwd
import signal
import stat
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

BASE = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16')
PREP = BASE / 'production-preparation'
OPS = Path('/var/lib/swingset/operations/h16-event-preservation-release-20260916')
CAPTURE = Path('/var/tmp/h16-event-preservation-production-preflight-20260916')
UNIT = 'swingset-h16-event-preservation-production-preflight.service'
COMMAND_SHA = '9cb3091924fc6ad60ecaa43a2e0996f7a2e43625f06a48e770ece219871e6f5e'
INPUTS = {'build-result.json': ('scratch-build.json', '59a2e82b5cc8dfe0a4451b679fa89c0dcf84aa59ee944ee8c1d7b9efa37c61a3'), 'audit-result.json': ('scratch-audit.json', '862a880eaf2d0263b5f35ed8733c90bd21a818c3eab3a0929c875ef24fb7b81d'), 'scratch-acceptance-review.json': ('scratch-acceptance-review.json', '14fc10b57b01518d802fd3340aca6e683a4d22bba0f137b697fc9a0e7fe6c198')}

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def require(value, message):
    if not value:
        raise ValueError(message)

def save(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())

def status(unit):
    response = subprocess.run(['systemctl', 'show', unit, '--property=LoadState,ActiveState,SubState,MainPID,ControlGroup,Result,ExecMainStatus,MemoryCurrent,MemoryPeak'], check=True, capture_output=True, text=True, timeout=15)
    return dict(line.split('=', 1) for line in response.stdout.splitlines() if '=' in line)

def held():
    require(Path('/var/lib/swingset/operator-hold').is_file(), 'operator hold disappeared')
    for name in ('cycle', 'backup', 'summary'):
        for kind in ('service', 'timer'):
            require(status(f'swingset-{name}.{kind}')['ActiveState'] == 'inactive', 'ordinary unit active')

def interrupt(*args):
    raise KeyboardInterrupt('preflight supervisor interrupted')

require(os.geteuid() == 0, 'root launcher required; worker is service-owned')
require(sha(PREP / 'staged-preflight-command.json') == COMMAND_SHA, 'reviewed command changed')
command = json.loads((PREP / 'staged-preflight-command.json').read_bytes())
require('--execute' not in command['worker_argv'], 'only preflight authorized')
require(sha(OPS / 'gate.json') == command['gate_sha256'], 'staged gate changed')
require(sha(OPS / 'accept_h16.py') == command['driver_sha256'], 'staged helper changed')
require(status(UNIT)['LoadState'] == 'not-found', 'unit already exists')
held()
require(not (OPS / 'preflight.json').exists() and not (OPS / 'preflight.sizes.json').exists(), 'preflight output already exists')
owner = pwd.getpwnam('swingset')
require(not OPS.is_symlink() and stat.S_IMODE(OPS.stat().st_mode) == 0o700 and OPS.stat().st_uid == owner.pw_uid, 'private directory differs')
for original, (name, digest) in INPUTS.items():
    path = BASE / original
    require(not path.is_symlink() and sha(path) == digest, 'scratch evidence changed')
    require(not (OPS / name).exists(), 'scratch evidence output already exists')
review = json.loads((BASE / 'scratch-acceptance-review.json').read_bytes())
require(review['passed'] is True and review['checks_passed'] == 54, 'scratch acceptance not passed')
CAPTURE.mkdir(mode=0o700)
for original, (name, digest) in INPUTS.items():
    data = (BASE / original).read_bytes()
    require(hashlib.sha256(data).hexdigest() == digest, 'scratch evidence changed during copy')
    descriptor = os.open(OPS / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchown(stream.fileno(), owner.pw_uid, owner.pw_gid)
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    require(sha(OPS / name) == digest, 'staged evidence differs')
argv = ['systemd-run', '--wait', '--unit=' + UNIT, '--property=Type=exec', '--property=User=swingset', '--property=Group=swingset', '--property=UMask=0077', '--property=WorkingDirectory=' + command['working_directory'], '--property=RuntimeMaxSec=1200', '--property=TimeoutStopSec=180', '--property=MemoryAccounting=yes', '--property=KillSignal=SIGTERM', *['--setenv=' + key + '=' + value for key, value in command['environment'].items()], *command['worker_argv']]
report = {'format': 'h16-production-preflight-orchestration-v1', 'started_at': datetime.now(UTC).isoformat(), 'script_sha256': sha(Path(__file__)), 'command_sha256': COMMAND_SHA, 'worker_argv': argv, 'gate_sha256': command['gate_sha256'], 'staged_scratch_evidence': {name: digest for name, digest in INPUTS.values()}, 'passed': False, 'activation_executed': False, 'initializer_executed': False, 'published': False}
save(CAPTURE / 'launch.json', report)
process = None
samples = []
signal.signal(signal.SIGTERM, interrupt)
try:
    with (CAPTURE / 'systemd-wait.log').open('xb') as log:
        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT)
        started = time.monotonic()
        while process.poll() is None:
            current = status(UNIT)
            sample = {'at': datetime.now(UTC).isoformat(), 'elapsed_seconds': round(time.monotonic() - started, 3), **current}
            group = current.get('ControlGroup')
            if group:
                memory = Path('/sys/fs/cgroup') / group.lstrip('/') / 'memory.stat'
                if memory.is_file():
                    sample['memory_stat'] = {line.split()[0]: int(line.split()[1]) for line in memory.read_text().splitlines()}
                    require(sample['memory_stat'].get('anon', 0) <= 6 * 1024**3, 'anonymous memory above 6GiB')
            samples.append(sample)
            with (CAPTURE / 'resources.jsonl').open('a') as resource:
                resource.write(json.dumps(sample, sort_keys=True) + '\n'); resource.flush(); os.fsync(resource.fileno())
            held()
            require(time.monotonic() - started < 1380, 'launcher deadline exceeded')
            print(json.dumps({'elapsed_seconds': sample['elapsed_seconds'], 'state': current.get('ActiveState'), 'anonymous_bytes': sample.get('memory_stat', {}).get('anon')}), flush=True)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        report['systemd_wait_exit_code'] = process.returncode
        report['final_unit'] = status(UNIT)
        require(process.returncode == 0, 'systemd-run --wait returned failure')
    result = json.loads((OPS / 'preflight.json').read_bytes())
    require(result.get('preflight_passed') is True and result.get('executed') is False and result.get('migrated') is False and result.get('service_executed') is False and result.get('network_requests') == 0, 'preflight result rejected')
    require(result.get('gate_sha256') == command['gate_sha256'], 'preflight gate mismatch')
    held()
    report['preflight_sha256'] = sha(OPS / 'preflight.json')
    report['passed'] = True
except BaseException as error:
    report['error'] = {'type': type(error).__name__, 'message': str(error)}
    stopped = subprocess.run(['systemctl', 'stop', UNIT], capture_output=True, text=True, timeout=200)
    report['stop_exit_code'] = stopped.returncode
    if process is not None:
        try:
            report['systemd_wait_exit_code'] = process.wait(timeout=210)
        except subprocess.TimeoutExpired:
            report['wait_did_not_settle'] = True
    raise
finally:
    report['peak_sampled_anonymous_bytes'] = max((sample.get('memory_stat', {}).get('anon', 0) for sample in samples), default=0)
    report['resource_samples'] = len(samples)
    for name in ('preflight.json', 'preflight.sizes.json'):
        path = OPS / name
        if path.is_file():
            with (CAPTURE / name).open('xb') as retained:
                retained.write(path.read_bytes()); retained.flush(); os.fsync(retained.fileno())
    journal = subprocess.run(['journalctl', '-u', UNIT, '--no-pager', '-o', 'short-iso'], capture_output=True, text=True, timeout=30)
    with (CAPTURE / 'journal.log').open('x') as stream:
        stream.write(journal.stdout)
    report['finished_at'] = datetime.now(UTC).isoformat()
    save(CAPTURE / 'orchestration.json', report)
print(json.dumps({'passed': report['passed'], 'capture': str(CAPTURE), 'preflight_sha256': report.get('preflight_sha256')}), flush=True)
