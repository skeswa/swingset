"""Stage the reviewed initialization gate and execute prepare only, never run."""
import hashlib
import json
import os
import pwd
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

PREP = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16/production-preparation')
INPUT = PREP / 'initialization-preparation-002'
OPS = Path('/var/lib/swingset/operations/h16-event-preservation-release-20260916')
SOURCE = Path('/nix/store/z689qy41inndill3d92ym8im852x3649-source')
CAPTURE = Path('/var/tmp/h16-event-preservation-initialization-prepare-002-20260916')
UNIT = 'swingset-h16-event-preservation-initialization-prepare-002.service'
GATE_SHA = '00da07082f1cea7a20a6d3e04849e7e2bac8aa4eae8b2ad8d344da9951cc5af6'
REVIEW_SHA = '6ed770cc8723f2a47ef584a8ceb2bcdef84254d57412649d03e86afa98b08f92'
DRIVER_SHA = 'c3259a023436e2cc3ee56e1ad83f3170b455f42187ff71421eeac25387bf96d7'
HELPERS = {'legacy-runtime-fix/initialize-002.py': {'target': 'initialize-002.py', 'sha256': 'c3259a023436e2cc3ee56e1ad83f3170b455f42187ff71421eeac25387bf96d7'}, 'supervise-initialization-002.py': {'target': 'supervise-initialization-002.py', 'sha256': '7d2746bd079a0e0fb608ae666f4cec2146ab23561c46e2d808f0f2ba2216324f'}, 'monitoring/guard-initialization-003.py': {'target': 'monitoring/guard-initialization-003.py', 'sha256': 'b39cedadd065dad76eb5a47f0c5de3dd39a737895a7198bb7a48a75f5ea1623b'}, 'final-verification/verify-initialization-002.py': {'target': 'final-verification/verify-initialization-002.py', 'sha256': 'de8708f555c34a97afcdae2085c266eac3d4e1230e6879d4069cd8a7bd7410db'}}

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def require(value, message):
    if not value:
        raise ValueError(message)

def save(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())

def status(unit):
    response = subprocess.run(['systemctl', 'show', unit, '--property=LoadState,ActiveState,SubState,MainPID,ControlGroup,Result,ExecMainStatus,MemoryCurrent,MemoryPeak'], check=True, capture_output=True, text=True, timeout=15)
    return dict(line.split('=', 1) for line in response.stdout.splitlines() if '=' in line)

def held():
    require(Path('/var/lib/swingset/operator-hold').is_file(), 'operator hold disappeared')
    for name in ('cycle', 'backup', 'summary'):
        for kind in ('service', 'timer'):
            require(status(f'swingset-{name}.{kind}')['ActiveState'] == 'inactive', 'ordinary unit active')

def interrupted(*args):
    raise KeyboardInterrupt('prepare launcher interrupted')

require(os.geteuid() == 0, 'root launcher required; worker is service-owned')
require(sha(INPUT / 'initialization-gate-002.json') == GATE_SHA, 'approved gate changed')
require(sha(INPUT / 'coordinator-review.json') == REVIEW_SHA, 'approved review changed')
require(sha(PREP / 'legacy-runtime-fix/initialize-002.py') == DRIVER_SHA, 'reviewed initializer changed')
gate = json.loads((INPUT / 'initialization-gate-002.json').read_bytes())
review = json.loads((INPUT / 'coordinator-review.json').read_bytes())
require(review['passed'] is True and review['initialization_gate_sha256'] == GATE_SHA, 'prepare review rejected')
require(gate['driver_sha256'] == DRIVER_SHA and gate['source'] == str(SOURCE), 'gate authority differs')
for name, digest in gate['evidence_files'].items():
    path = OPS / name
    require(Path(name).name == name and not path.is_symlink() and path.is_file() and sha(path) == digest, 'nested staged evidence differs: ' + name)
for name in ('initialization-gate-002.json', 'initialization-prepare-review-002.json', 'initialization-marker-002.json', 'prepare-002.json'):
    require(not (OPS / name).exists(), 'prepare path already exists: ' + name)
for original, binding in HELPERS.items():
    path, target = PREP / original, OPS / binding['target']
    require(not path.is_symlink() and sha(path) == binding['sha256'], 'reviewed helper changed: ' + original)
    require(not target.exists() and not target.is_symlink(), 'new helper path already exists')
    require(target.parent.is_dir() and not any(part.is_symlink() for part in (target.parent, *target.parent.parents)), 'unsafe helper parent')
require(status(UNIT)['LoadState'] == 'not-found', 'prepare unit already exists')
held()
CAPTURE.mkdir(mode=0o700)
owner = pwd.getpwnam('swingset')
for original, name, digest in [('initialization-gate-002.json','initialization-gate-002.json',GATE_SHA),('coordinator-review.json','initialization-prepare-review-002.json',REVIEW_SHA)]:
    data = (INPUT / original).read_bytes(); require(hashlib.sha256(data).hexdigest() == digest, 'staging input changed')
    descriptor = os.open(OPS / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchown(stream.fileno(), owner.pw_uid, owner.pw_gid)
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    require(sha(OPS / name) == digest, 'staged prepare authority differs')
for original, binding in HELPERS.items():
    data = (PREP / original).read_bytes()
    require(hashlib.sha256(data).hexdigest() == binding['sha256'], 'helper changed during staging')
    target = OPS / binding['target']
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchown(stream.fileno(), owner.pw_uid, owner.pw_gid)
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    info = target.stat()
    require(sha(target) == binding['sha256'] and info.st_uid == owner.pw_uid and info.st_gid == owner.pw_gid and info.st_mode & 0o777 == 0o600, 'staged helper verification failed')
env = {'PYTHONPATH': str(SOURCE / 'src') + ':' + str(SOURCE), 'SWINGSET_REVISION': 'uncommitted:' + SOURCE.name, 'PYTHONDONTWRITEBYTECODE': '1', 'LD_LIBRARY_PATH': '/nix/store/x03dxqva88ax4w45hyms977zv3f8a8i9-gcc-14.3.0-lib/lib:/nix/store/5zq11bibj72nvrhlx9fm0xl0xhxd6388-zlib-1.3.2/lib'}
worker = ['/var/lib/swingset/venv/bin/python', str(OPS / 'initialize-002.py'), 'prepare', '--gate', str(OPS / 'initialization-gate-002.json'), '--marker', str(OPS / 'initialization-marker-002.json'), '--output', str(OPS / 'prepare-002.json')]
argv = ['systemd-run', '--wait', '--unit=' + UNIT, '--property=Type=exec', '--property=User=swingset', '--property=Group=swingset', '--property=UMask=0077', '--property=WorkingDirectory=' + str(SOURCE), '--property=RuntimeMaxSec=1500', '--property=TimeoutStopSec=180', '--property=MemoryAccounting=yes', '--property=KillSignal=SIGTERM', *['--setenv=' + key + '=' + value for key, value in env.items()], *worker]
report = {'format': 'h16-initialization-prepare-orchestration-v1', 'started_at': datetime.now(UTC).isoformat(), 'script_sha256': sha(Path(__file__)), 'worker_argv': argv, 'gate_sha256': GATE_SHA, 'driver_sha256': DRIVER_SHA, 'coordinator_review_sha256': REVIEW_SHA, 'nested_evidence_verified': len(gate['evidence_files']), 'staged_helpers': HELPERS, 'passed': False, 'run_executed': False, 'published': False}
save(CAPTURE / 'launch.json', report)
process = None; samples = []
signal.signal(signal.SIGTERM, interrupted)
try:
    with (CAPTURE / 'systemd-wait.log').open('xb') as log:
        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT); started = time.monotonic()
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
            with (CAPTURE / 'resources.jsonl').open('a') as stream:
                stream.write(json.dumps(sample, sort_keys=True) + '\n'); stream.flush(); os.fsync(stream.fileno())
            held(); require(time.monotonic() - started < 1680, 'launcher deadline exceeded')
            print(json.dumps({'elapsed_seconds': sample['elapsed_seconds'], 'state': current.get('ActiveState'), 'anonymous_bytes': sample.get('memory_stat', {}).get('anon')}), flush=True)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        report['systemd_wait_exit_code'] = process.returncode; report['final_unit'] = status(UNIT)
        require(process.returncode == 0, 'initializer prepare failed')
    result = json.loads((OPS / 'prepare-002.json').read_bytes()); marker = json.loads((OPS / 'initialization-marker-002.json').read_bytes())
    require(result.get('status') == 'prepared' and result.get('mode') == 'prepare' and result.get('attempted') == result.get('completed') == 0, 'unexpected prepare result')
    require(result.get('gate_sha256') == GATE_SHA and result.get('driver_sha256') == DRIVER_SHA, 'prepare receipt authority mismatch')
    require(result.get('network_requests') == 0 and result.get('parse_executed') is False and result.get('build_executed') is False and result.get('published') is False and result.get('parse_execution_authorized') is False, 'prepare exceeded authorized work')
    require(result['marker_sha256'] == sha(OPS / 'initialization-marker-002.json'), 'marker hash differs')
    require(marker['input_bundle_hash'] == gate['input_bundle_hash'] and marker['gate_sha256'] == GATE_SHA, 'marker authority differs')
    held()
    report.update(passed=True, prepare_sha256=sha(OPS / 'prepare-002.json'), marker_sha256=sha(OPS / 'initialization-marker-002.json'), changed_inputs=result['changed_inputs'], extract_cache_invalidated=result['extract_cache_invalidated'])
except BaseException as error:
    report['error'] = {'type': type(error).__name__, 'message': str(error)}
    stopped = subprocess.run(['systemctl','stop',UNIT],capture_output=True,text=True,timeout=200); report['stop_exit_code'] = stopped.returncode
    if process is not None:
        try:
            report['systemd_wait_exit_code'] = process.wait(timeout=210)
        except subprocess.TimeoutExpired:
            report['wait_did_not_settle'] = True
    raise
finally:
    report['peak_sampled_anonymous_bytes'] = max((sample.get('memory_stat', {}).get('anon', 0) for sample in samples), default=0); report['resource_samples'] = len(samples)
    for name in ('prepare-002.json', 'initialization-marker-002.json'):
        path = OPS / name
        if path.is_file():
            with (CAPTURE / name).open('xb') as stream:
                stream.write(path.read_bytes()); stream.flush(); os.fsync(stream.fileno())
    journal = subprocess.run(['journalctl','-u',UNIT,'--no-pager','-o','short-iso'],capture_output=True,text=True,timeout=30)
    with (CAPTURE / 'journal.log').open('x') as stream:
        stream.write(journal.stdout)
    report['finished_at'] = datetime.now(UTC).isoformat(); save(CAPTURE / 'orchestration.json', report)
print(json.dumps({'passed': report['passed'], 'capture': str(CAPTURE), 'marker_sha256': report.get('marker_sha256'), 'prepare_sha256': report.get('prepare_sha256')}), flush=True)
