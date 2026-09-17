"""Execute only the reviewed H16 activation driver; capture locally before retention."""
import hashlib
import json
import os
import pwd
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PREP = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16/production-preparation')
OPS = Path('/var/lib/swingset/operations/h16-event-preservation-release-20260916')
CAPTURE = Path('/var/tmp/h16-event-preservation-production-activation-20260916')
DRIVER_SHA = 'db4cd1a26e3f1c593570316759b779e44f98cf1d691b1b32872a35416e81cf2e'
REVIEW_SHA = '081d347703d648d94d76df3552e44ad448856acd2c68983b8a2ff2176e34d626'

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def require(value, message):
    if not value:
        raise ValueError(message)

def save(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())

require(os.geteuid() == 0 and sys.flags.optimize == 0, 'root and unoptimized Python required')
require(sha(OPS / 'activate-system.py') == DRIVER_SHA, 'activation driver changed')
require(sha(PREP / 'activation-coordinator-review.json') == REVIEW_SHA, 'activation review changed')
require(not (OPS / 'deployment-intent.json').exists() and not (OPS / 'deployment.json').exists(), 'deployment output already exists')
review = json.loads((PREP / 'activation-coordinator-review.json').read_bytes())
CAPTURE.mkdir(mode=0o700)
owner = pwd.getpwnam('swingset')
descriptor = os.open(OPS / 'activation-coordinator-review.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(descriptor, 'wb') as stream:
    os.fchown(stream.fileno(), owner.pw_uid, owner.pw_gid)
    stream.write((PREP / 'activation-coordinator-review.json').read_bytes()); stream.flush(); os.fsync(stream.fileno())
require(sha(OPS / 'activation-coordinator-review.json') == REVIEW_SHA, 'staged review differs')
argv = ['/var/lib/swingset/venv/bin/python', str(OPS / 'activate-system.py')]
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
env.pop('PYTHONOPTIMIZE', None)
report = {'format': 'h16-production-activation-orchestration-v1', 'started_at': datetime.now(UTC).isoformat(), 'argv': argv, 'driver_sha256': DRIVER_SHA, 'coordinator_review_sha256': REVIEW_SHA, 'script_sha256': sha(Path(__file__)), 'passed': False, 'acceptance_executed': False, 'initializer_executed': False, 'published': False}
save(CAPTURE / 'launch.json', report)
process = None
try:
    with (CAPTURE / 'activation.log').open('xb') as log:
        process = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT)
        started = time.monotonic()
        while process.poll() is None:
            require(time.monotonic() - started < 900, 'activation launcher bound exceeded')
            print(json.dumps({'elapsed_seconds': round(time.monotonic()-started, 2), 'pid': process.pid, 'activation_running': True}), flush=True)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        report['driver_exit_code'] = process.returncode
        require(process.returncode == 0, 'activation driver failed')
    deployment = json.loads((OPS / 'deployment.json').read_bytes())
    require(deployment.get('passed') is True and deployment.get('driver_sha256') == DRIVER_SHA, 'deployment receipt rejected')
    require(deployment['active_system'] == deployment['persistent_system'] == deployment['system'], 'system binding differs')
    require(all(value == 'inactive' for value in deployment['unit_states'].values()) and Path('/var/lib/swingset/operator-hold').is_file(), 'post-activation holds differ')
    report.update(passed=True, deployment_sha256=sha(OPS / 'deployment.json'), active_system=deployment['active_system'], persistent_system=deployment['persistent_system'])
except BaseException as error:
    report['error'] = {'type': type(error).__name__, 'message': str(error)}
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            report['driver_exit_code'] = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            report['driver_still_running'] = True
    raise
finally:
    for name in ('deployment-intent.json', 'deployment.json'):
        path = OPS / name
        if path.is_file():
            with (CAPTURE / name).open('xb') as stream:
                stream.write(path.read_bytes()); stream.flush(); os.fsync(stream.fileno())
    report['finished_at'] = datetime.now(UTC).isoformat()
    save(CAPTURE / 'orchestration.json', report)
print(json.dumps({'passed': report['passed'], 'capture': str(CAPTURE), 'deployment_sha256': report.get('deployment_sha256')}), flush=True)
