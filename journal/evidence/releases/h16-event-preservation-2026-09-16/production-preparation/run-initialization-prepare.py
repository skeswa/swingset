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
INPUT = PREP / 'initialization-preparation'
OPS = Path('/var/lib/swingset/operations/h16-event-preservation-release-20260916')
SOURCE = Path('/nix/store/z689qy41inndill3d92ym8im852x3649-source')
CAPTURE = Path('/var/tmp/h16-event-preservation-initialization-prepare-20260916')
UNIT = 'swingset-h16-event-preservation-initialization-prepare.service'
GATE_SHA = '45c7abb4843ba7e90c2a93f6326dba939d2f8a321f407dcbd7fecee99582c4da'
REVIEW_SHA = '0ec45915e60e83d5201afd4de526cf0a28b53b57c82ca89c9021ece9e044ffcd'
DRIVER_SHA = 'b54c02a15c3d86a603bef4d6926e66bde4cf1ef9d23f273aa5db771d3a99d233'

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
require(sha(INPUT / 'initialization-gate.json') == GATE_SHA, 'approved gate changed')
require(sha(INPUT / 'coordinator-review.json') == REVIEW_SHA, 'approved review changed')
require(sha(OPS / 'initialize.py') == DRIVER_SHA, 'initializer changed')
gate = json.loads((INPUT / 'initialization-gate.json').read_bytes())
review = json.loads((INPUT / 'coordinator-review.json').read_bytes())
require(review['passed'] is True and review['initialization_gate_sha256'] == GATE_SHA, 'prepare review rejected')
require(gate['driver_sha256'] == DRIVER_SHA and gate['source'] == str(SOURCE), 'gate authority differs')
for name, digest in gate['evidence_files'].items():
    path = OPS / name
    require(Path(name).name == name and not path.is_symlink() and path.is_file() and sha(path) == digest, 'nested staged evidence differs: ' + name)
for name in ('initialization-gate.json', 'initialization-prepare-review.json', 'initialization-marker.json', 'prepare-001.json'):
    require(not (OPS / name).exists(), 'prepare path already exists: ' + name)
require(status(UNIT)['LoadState'] == 'not-found', 'prepare unit already exists')
held()
CAPTURE.mkdir(mode=0o700)
owner = pwd.getpwnam('swingset')
for original, name, digest in [('initialization-gate.json','initialization-gate.json',GATE_SHA),('coordinator-review.json','initialization-prepare-review.json',REVIEW_SHA)]:
    data = (INPUT / original).read_bytes(); require(hashlib.sha256(data).hexdigest() == digest, 'staging input changed')
    descriptor = os.open(OPS / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchown(stream.fileno(), owner.pw_uid, owner.pw_gid)
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    require(sha(OPS / name) == digest, 'staged prepare authority differs')
env = {'PYTHONPATH': str(SOURCE / 'src') + ':' + str(SOURCE), 'SWINGSET_REVISION': 'uncommitted:' + SOURCE.name, 'PYTHONDONTWRITEBYTECODE': '1', 'LD_LIBRARY_PATH': '/nix/store/x03dxqva88ax4w45hyms977zv3f8a8i9-gcc-14.3.0-lib/lib:/nix/store/5zq11bibj72nvrhlx9fm0xl0xhxd6388-zlib-1.3.2/lib'}
worker = ['/var/lib/swingset/venv/bin/python', str(OPS / 'initialize.py'), 'prepare', '--gate', str(OPS / 'initialization-gate.json'), '--marker', str(OPS / 'initialization-marker.json'), '--output', str(OPS / 'prepare-001.json')]
argv = ['systemd-run', '--wait', '--unit=' + UNIT, '--property=Type=exec', '--property=User=swingset', '--property=Group=swingset', '--property=UMask=0077', '--property=WorkingDirectory=' + str(SOURCE), '--property=RuntimeMaxSec=1500', '--property=TimeoutStopSec=180', '--property=MemoryAccounting=yes', '--property=KillSignal=SIGTERM', *['--setenv=' + key + '=' + value for key, value in env.items()], *worker]
report = {'format': 'h16-initialization-prepare-orchestration-v1', 'started_at': datetime.now(UTC).isoformat(), 'script_sha256': sha(Path(__file__)), 'worker_argv': argv, 'gate_sha256': GATE_SHA, 'driver_sha256': DRIVER_SHA, 'coordinator_review_sha256': REVIEW_SHA, 'nested_evidence_verified': len(gate['evidence_files']), 'passed': False, 'run_executed': False, 'published': False}
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
    result = json.loads((OPS / 'prepare-001.json').read_bytes()); marker = json.loads((OPS / 'initialization-marker.json').read_bytes())
    require(result.get('status') == 'prepared' and result.get('mode') == 'prepare' and result.get('attempted') == result.get('completed') == 0, 'unexpected prepare result')
    require(result.get('gate_sha256') == GATE_SHA and result.get('driver_sha256') == DRIVER_SHA, 'prepare receipt authority mismatch')
    require(result.get('network_requests') == 0 and result.get('parse_executed') is False and result.get('build_executed') is False and result.get('published') is False and result.get('parse_execution_authorized') is False, 'prepare exceeded authorized work')
    require(result['marker_sha256'] == sha(OPS / 'initialization-marker.json'), 'marker hash differs')
    require(marker['input_bundle_hash'] == gate['input_bundle_hash'] and marker['gate_sha256'] == GATE_SHA, 'marker authority differs')
    held()
    report.update(passed=True, prepare_sha256=sha(OPS / 'prepare-001.json'), marker_sha256=sha(OPS / 'initialization-marker.json'), changed_inputs=result['changed_inputs'], extract_cache_invalidated=result['extract_cache_invalidated'])
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
    for name in ('prepare-001.json', 'initialization-marker.json'):
        path = OPS / name
        if path.is_file():
            with (CAPTURE / name).open('xb') as stream:
                stream.write(path.read_bytes()); stream.flush(); os.fsync(stream.fileno())
    journal = subprocess.run(['journalctl','-u',UNIT,'--no-pager','-o','short-iso'],capture_output=True,text=True,timeout=30)
    with (CAPTURE / 'journal.log').open('x') as stream:
        stream.write(journal.stdout)
    report['finished_at'] = datetime.now(UTC).isoformat(); save(CAPTURE / 'orchestration.json', report)
print(json.dumps({'passed': report['passed'], 'capture': str(CAPTURE), 'marker_sha256': report.get('marker_sha256'), 'prepare_sha256': report.get('prepare_sha256')}), flush=True)
