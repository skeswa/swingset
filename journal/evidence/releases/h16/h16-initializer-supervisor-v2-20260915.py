"""Run-only bounded supervision; prepare offline, execute only with reviewed hashes."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path('/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source')
SOURCE_RECEIPT = 'f8f24c2d8b839bbfcd25bf87e551e9f2777d1236f89ed84da16c3c07d4d75b9f'
BUNDLE = '558bb5f4e88b47fe80a691254d3b21ac9e491296e80bb18f4599b64a88bfe9c0'
DRIVER_SHA = 'b54c02a15c3d86a603bef4d6926e66bde4cf1ef9d23f273aa5db771d3a99d233'
STATE = Path('/var/lib/swingset')
OPS = STATE / 'operations/h16-closure-release-20260914'
PYTHON = STATE / 'venv/bin/python'
LD = '/nix/store/x03dxqva88ax4w45hyms977zv3f8a8i9-gcc-14.3.0-lib/lib:/nix/store/5zq11bibj72nvrhlx9fm0xl0xhxd6388-zlib-1.3.2/lib'
PRESERVATION = ('protected_unchanged', 'parse_tokens_unchanged', 'controls_unchanged', 'input_authority_unchanged')


def now():
    return datetime.now(UTC).isoformat()


def regular(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('absolute nonsymlink file required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('regular file required')
        return stream.read()


def checked(path, expected):
    if not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('reviewed SHA256 required')
    data = regular(path)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f'reviewed file changed: {path}')
    return data


def write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(json.dumps(data, sort_keys=True, separators=(',', ':')).encode() + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def review(args):
    if args.driver_sha256 != DRIVER_SHA:
        raise ValueError('only the unchanged reviewed initializer is supported')
    checked(args.driver, args.driver_sha256)
    gate = json.loads(checked(args.gate, args.gate_sha256))
    marker = json.loads(checked(args.marker, args.marker_sha256))
    expected = {'state': str(STATE), 'source': str(SOURCE), 'source_receipt_sha256': SOURCE_RECEIPT,
                'input_bundle_hash': BUNDLE, 'driver_sha256': DRIVER_SHA}
    if any(gate.get(k) != v or marker.get(k) != v for k, v in expected.items()):
        raise ValueError('gate/marker source, bundle or driver authority mismatch')
    if (gate.get('format') != 'h16-production-initialization-gate-v1'
            or gate.get('stages') != ['project', 'link'] or gate.get('capture_accept_authorized') is not True
            or marker.get('format') != 'h16-production-initialization-v1'
            or marker.get('gate_sha256') != args.gate_sha256):
        raise ValueError('actual reviewed initializer gate and prepared marker required')
    if not isinstance(marker.get('controls'), dict) or not marker['controls']:
        raise ValueError('prepared controls required')
    if not isinstance(marker.get('prepared_input_authority'), dict) or not marker['prepared_input_authority']:
        raise ValueError('prepared input authority required')
    if len({args.driver, args.gate, args.marker}) != 3:
        raise ValueError('reviewed input paths must differ')
    return gate, marker


def command(args, unit, output):
    return ['systemd-run', '--wait', '--unit=' + unit, '--service-type=exec',
            '--property=User=swingset', '--property=Group=swingset', '--property=UMask=0077',
            '--property=RuntimeMaxSec=1500', '--property=TimeoutStopSec=180',
            '--property=KillSignal=SIGTERM', '--property=MemoryAccounting=yes',
            '--property=WorkingDirectory=' + str(SOURCE),
            '--setenv=PYTHONPATH=' + str(SOURCE / 'src') + ':' + str(SOURCE),
            '--setenv=SWINGSET_REVISION=uncommitted:' + SOURCE.name, '--setenv=LD_LIBRARY_PATH=' + LD,
            '--setenv=PYTHONDONTWRITEBYTECODE=1',
            str(PYTHON), str(args.driver), 'run', '--gate', str(args.gate), '--marker', str(args.marker),
            '--output', str(output), '--max-seconds', '900']


def validate_receipt(receipt, args, authority, exit_code):
    if exit_code != 0:
        raise ValueError('initializer/unit exited unsuccessfully')
    expected = {'format': 'h16-production-initialization-receipt-v1', 'mode': 'run',
                'gate_sha256': args.gate_sha256, 'marker_sha256': args.marker_sha256,
                'driver_sha256': args.driver_sha256, 'source_receipt_sha256': SOURCE_RECEIPT}
    if any(receipt.get(k) != v for k, v in expected.items()):
        raise ValueError('receipt authority mismatch')
    if receipt.get('error') or not receipt.get('finished_at'):
        raise ValueError('receipt is not a clean final result')
    if any(receipt.get(k) is not True for k in PRESERVATION):
        raise ValueError('preservation check failed/missing')
    if (type(receipt.get('network_requests')) is not int or receipt['network_requests'] != 0
            or any(receipt.get(k) is not False for k in ('parse_executed', 'build_executed', 'published'))):
        raise ValueError('forbidden execution or absent execution accounting')
    if receipt.get('initial_hold') != authority or receipt.get('final_hold') != authority:
        raise ValueError('holds or confirmed baseline changed')
    attempted, completed = receipt.get('attempted'), receipt.get('completed')
    if type(attempted) is not int or type(completed) is not int or completed < 0 or attempted != completed:
        raise ValueError('failed, unaccounted or incomplete attempt')
    outcomes = receipt.get('outcomes')
    # Driver counts reason=None as succeeded; ledger may call that output_committed.
    if (not isinstance(outcomes, dict) or any(k not in ('succeeded', 'output_committed')
            or type(v) is not int or v < 0 for k, v in outcomes.items()) or sum(outcomes.values()) != completed):
        raise ValueError('non-success or unaccounted worker outcome')
    if receipt.get('status') == 'current':
        if receipt.get('unfinished_by_scope') != {}:
            raise ValueError('current requires explicit empty unfinished scopes')
        return 'current'
    if receipt.get('status') != 'bounded_stop' or completed == 0:
        raise ValueError('only clean bounded progress permits continuation')
    return 'bounded_stop'


class Backend:
    def __init__(self, args):
        self.args = args
        checked(args.driver, args.driver_sha256)
        spec = importlib.util.spec_from_file_location('reviewed_initializer', args.driver)
        self.driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.driver)

    def authority(self):
        gate, marker = review(self.args)
        held = self.driver.hold_and_baseline(gate, self.args.gate)
        if held['baseline'] != marker['preflight_baseline']:
            raise ValueError('confirmed baseline differs from prepared marker')
        with closing(self.driver.readonly()) as conn:
            conn.execute('BEGIN')
            if self.driver.controls(conn) != marker['controls']:
                raise ValueError('operator controls changed; supervisor cannot continue or edit them')
            self.driver.check_input_authority(conn, marker)
        return held

    def launch(self, cmd, log):
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            return subprocess.Popen(cmd, stdout=fd, stderr=subprocess.STDOUT)
        finally:
            os.close(fd)

    def stop(self, unit):
        result = subprocess.run(['systemctl', 'stop', '--no-block', unit], capture_output=True, timeout=20)
        if result.returncode:
            self.settled(unit)  # A collected completed unit needs no stop; active failures still reject.

    def wait(self, process, seconds):
        try:
            return process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return None

    def settled(self, unit):
        result = subprocess.run(['systemctl', 'show', unit, '--property=LoadState,ActiveState,MainPID', '--no-pager'],
                                check=True, capture_output=True, text=True, timeout=30)
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if fields.get('LoadState') != 'not-found' and (fields.get('ActiveState') not in ('inactive', 'failed') or fields.get('MainPID') != '0'):
            raise ValueError('initializer unit is not inactive')
        with (STATE / 'state.lock').open('r+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
        return {'unit': fields, 'writer_lock_released': True}


def supervise(args, backend, directory):
    started = now()
    batches = []
    authority = backend.authority()
    write_new(directory / 'preflight.json', {'format': 'h16-initialization-supervision-v1', 'started_at': started,
        'source': str(SOURCE), 'source_receipt_sha256': SOURCE_RECEIPT, 'input_bundle_hash': BUNDLE,
        'gate_sha256': args.gate_sha256, 'marker_sha256': args.marker_sha256, 'driver_sha256': args.driver_sha256,
        'supervisor_sha256': hashlib.sha256(regular(Path(__file__).resolve())).hexdigest(),
        'max_invocations': args.max_invocations, 'authority': authority})
    status = 'invocation_cap'
    try:
        for number in range(1, args.max_invocations + 1):
            if backend.authority() != authority:
                raise ValueError('authority changed before launch')
            unit = 'swingset-h16-init-' + args.session + '-' + str(number).zfill(3) + '.service'
            output = directory / f'initializer-{number:03}.json'
            for path in (output, output.with_suffix('.json.tmp')):
                if path.exists() or path.is_symlink():
                    raise ValueError('initializer output must be new')
            cmd = command(args, unit, output)
            write_new(directory / f'batch-{number:03}-start.json', {'at': now(), 'unit': unit, 'command': cmd})
            process = None
            code = None
            batch = {'number': number, 'unit': unit, 'output': str(output), 'started_at': now()}
            try:
                process = backend.launch(cmd, directory / f'initializer-{number:03}.log')
                deadline = time.monotonic() + 1740
                while code is None:
                    code = backend.wait(process, 20)
                    if backend.authority() != authority:
                        raise ValueError('authority changed during invocation')
                    if time.monotonic() > deadline:
                        raise TimeoutError('unit exceeded runtime and shutdown bound')
                batch['exit_code'] = code
                batch['settled'] = backend.settled(unit)
                if stat.S_IMODE(output.stat().st_mode) & 0o077:
                    raise ValueError('initializer receipt is not private')
                payload = regular(output)
                batch['receipt_sha256'] = hashlib.sha256(payload).hexdigest()
                receipt = json.loads(payload)
                batch['status'] = validate_receipt(receipt, args, authority, code)
                if backend.authority() != authority:
                    raise ValueError('authority changed after final receipt')
                batch['attempted'] = receipt['attempted']
                batch['completed'] = receipt['completed']
            except BaseException as error:
                batch['error'] = {'type': type(error).__name__, 'message': str(error)}
                # The unit may exist even when launch was interrupted before returning a process.
                backend.stop(unit)
                stop_deadline = time.monotonic() + 210
                while time.monotonic() < stop_deadline:
                    if process is not None and code is None:
                        code = backend.wait(process, 20)
                    try:
                        batch['settled'] = backend.settled(unit)
                        if process is None or code is not None:
                            break
                    except (ValueError, BlockingIOError):
                        if process is None or code is not None:
                            time.sleep(20)
                batch['stop_exit_code'] = code
                raise
            finally:
                batch['finished_at'] = now()
                batches.append(batch)
                write_new(directory / f'batch-{number:03}-final.json', batch)
            print(json.dumps(batch, sort_keys=True), flush=True)
            if batch['status'] == 'current':
                status = 'current'
                break
    except BaseException as error:
        status = 'stopped'
        write_new(directory / 'stop.json', {'at': now(), 'error': {'type': type(error).__name__, 'message': str(error)}})
        raise
    finally:
        write_new(directory / 'final.json', {'format': 'h16-initialization-supervision-v1', 'started_at': started,
            'finished_at': now(), 'status': status, 'current': status == 'current', 'invocations': len(batches),
            'completed': sum(b.get('completed', 0) for b in batches), 'batches': batches})
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('gate', 'marker', 'driver'):
        parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--' + name + '-sha256', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--max-invocations', type=int, default=12, choices=range(1, 13))
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch('[a-z0-9][a-z0-9-]{0,47}', args.session):
        raise ValueError('root supervisor and a fresh bounded session name required')
    review(args)
    if any(p.is_symlink() for p in (OPS, *OPS.parents)) or not OPS.is_dir():
        raise ValueError('existing nonsymlink private operation directory required')
    owner = pwd.getpwnam('swingset')
    info = OPS.stat()
    if info.st_uid != owner.pw_uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('operation directory must be swingset-owned mode0700')
    directory = OPS / ('supervision-' + args.session)
    directory.mkdir(mode=0o700)  # Exclusive: no existing session can be overwritten.
    os.chown(directory, owner.pw_uid, owner.pw_gid)
    def interrupted(*_):
        raise KeyboardInterrupt('supervisor interrupted; stop its active unit')
    signal.signal(signal.SIGTERM, interrupted)
    return 0 if supervise(args, Backend(args), directory) == 'current' else 2


if __name__ == '__main__':
    raise SystemExit(main())
