"""Prepared, unexecuted memory guard for the unchanged reviewed initializer supervisor."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import types
from pathlib import Path

SUPERVISOR_SHA256 = '7d2746bd079a0e0fb608ae666f4cec2146ab23561c46e2d808f0f2ba2216324f'
ANON_LIMIT = 6 * 1024**3
INTERVAL = 5.0
START_SECONDS = 30.0
CGROUP_ROOT = Path('/sys/fs/cgroup')
PROPERTIES = 'LoadState,ActiveState,SubState,MainPID,ControlGroup,MemoryCurrent,MemoryPeak,Result,ExecMainStatus'


def load_supervisor(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('absolute nonsymlink supervisor required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('regular supervisor required')
        data = stream.read(128 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != SUPERVISOR_SHA256:
        raise ValueError('reviewed supervisor hash mismatch')
    module = types.ModuleType('reviewed_initialization_supervisor')
    module.__file__ = str(path)
    # Execute the checked bytes, not a second mutable path read.
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


def unit_state(unit):
    result = subprocess.run(['systemctl', 'show', unit, '--property=' + PROPERTIES, '--no-pager'],
                            check=True, capture_output=True, text=True, timeout=3)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def no_other_workers():
    result = subprocess.run(['systemctl', 'list-units', '--all', '--type=service',
        '--state=activating,active,deactivating', '--no-legend', '--plain', 'swingset-h16-*'],
        check=True, capture_output=True, text=True, timeout=3)
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        unit = line.split()[0]
        if not re.fullmatch(r'swingset-h16-[A-Za-z0-9:._@-]+\.service', unit):
            raise ValueError('unrecognized H16 unit listing; cannot establish serial work')
        fields = unit_state(unit)
        no_process = fields.get('MainPID') == '0' and fields.get('ControlGroup') == ''
        inactive = fields.get('LoadState') == 'not-found' or fields.get('ActiveState') == 'inactive'
        retained_exit = (fields.get('ActiveState') == 'active' and fields.get('SubState') == 'exited'
            and fields.get('Result') == 'success' and fields.get('ExecMainStatus') == '0')
        if not no_process or not (inactive or retained_exit):
            raise ValueError(f'another H16 unit is live or unassessed: {unit}; heavy work must remain serial')


def anonymous_bytes(group):
    relative = Path(group.lstrip('/'))
    if not group.startswith('/') or not relative.parts or '..' in relative.parts:
        raise ValueError('invalid unit cgroup path')
    path = CGROUP_ROOT / relative / 'memory.stat'
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('nonsymlink cgroup telemetry required')
    with path.open() as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('oversized cgroup memory telemetry')
    values = dict(line.split() for line in raw.splitlines())
    value = int(values['anon'])
    if value < 0:
        raise ValueError('invalid anonymous memory value')
    return value


def guarded_backend(supervisor):
    class GuardedBackend(supervisor.Backend):
        def __init__(self, args):
            super().__init__(args)
            self.unit = None
            self.stream = None
            self.stopping = False
            self.settled_ok = True

        def retain(self, sample):
            info = os.fstat(self.stream.fileno())
            visible = self.sample_path.stat(follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (visible.st_dev, visible.st_ino):
                raise ValueError('resource log path replaced or unlinked')
            self.stream.write(json.dumps(sample, sort_keys=True) + '\n')
            self.stream.flush()
            os.fsync(self.stream.fileno())

        def launch(self, cmd, log):
            if not self.settled_ok:
                raise ValueError('previous initializer has not settled')
            no_other_workers()
            units = [part.removeprefix('--unit=') for part in cmd if part.startswith('--unit=')]
            if len(units) != 1 or not re.fullmatch(r'swingset-h16-init-[a-z0-9-]+\.service', units[0]):
                raise ValueError('one exact initializer unit required')
            self.unit, self.stopping, self.settled_ok = units[0], False, False
            self.started = time.monotonic()
            self.observed_live = False
            self.sample_path = log.with_suffix('.resources.jsonl')
            fd = os.open(self.sample_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            self.stream = os.fdopen(fd, 'w')
            self.retain({'format': 'h16-initialization-memory-guard-v1', 'unit': self.unit,
                'supervisor_sha256': SUPERVISOR_SHA256,
                'guard_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'anonymous_limit_bytes': ANON_LIMIT, 'sample_interval_seconds': INTERVAL,
                'start_transition_seconds': START_SECONDS, 'started_at': supervisor.now()})
            return super().launch(cmd, log)

        def sample(self, *, process_finished=False):
            sample = {'at': supervisor.now(), 'unit': self.unit,
                'elapsed_seconds': time.monotonic() - self.started}
            try:
                fields = unit_state(self.unit)
                sample.update(fields)
                pid = int(fields.get('MainPID', '0'))
                active = fields.get('ActiveState')
                group = fields.get('ControlGroup')
                terminal = not pid and (active in ('inactive', 'failed') or fields.get('SubState') == 'exited')
                if group:
                    try:
                        sample['anonymous_bytes'] = anonymous_bytes(group)
                    except FileNotFoundError:
                        after = unit_state(self.unit)
                        if int(after.get('MainPID', '0')) or after.get('ActiveState') not in ('inactive', 'failed'):
                            raise
                        sample.update(after)
                        sample['anonymous_bytes'] = None
                        sample['telemetry'] = 'cgroup_removed_after_exit'
                    if sample['anonymous_bytes'] is not None and sample['anonymous_bytes'] >= ANON_LIMIT:
                        raise ValueError('anonymous memory reached 6 GiB')
                    if pid and sample['anonymous_bytes'] is not None:
                        self.observed_live = True
                elif terminal:
                    sample['telemetry'] = 'no_live_cgroup'
                elif pid or active == 'active':
                    raise ValueError('active initializer has no cgroup telemetry')
                elif active in ('inactive', 'failed') or fields.get('LoadState') == 'not-found':
                    sample['telemetry'] = 'no_live_cgroup'
                elif active == 'activating' and sample['elapsed_seconds'] <= START_SECONDS:
                    sample['telemetry'] = 'bounded_start_transition'
                else:
                    raise ValueError('initializer has no assessable cgroup telemetry')
                if (fields.get('LoadState') == 'not-found' and sample['elapsed_seconds'] > START_SECONDS
                    and not process_finished and not self.observed_live):
                    raise ValueError('initializer did not appear within start transition')
            except Exception as error:
                sample['guard_error'] = {'type': type(error).__name__, 'message': str(error)}
                self.retain(sample)
                raise
            sample['observed_live_unit'] = self.observed_live
            self.retain(sample)

        def wait(self, process, seconds):
            # Cleanup must never re-enter a failing guard and interrupt stop/wait.
            if self.stopping:
                return super().wait(process, seconds)
            deadline = time.monotonic() + seconds
            while True:
                self.sample()
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                code = super().wait(process, min(INTERVAL, left))
                if code is not None:
                    self.sample(process_finished=True)
                    return code

        def stop(self, unit):
            self.stopping = True
            return super().stop(unit)

        def settled(self, unit):
            result = super().settled(unit)  # Includes inactive/MainPID and actual writer flock.
            self.settled_ok = True
            if self.stream is not None:
                try:
                    self.retain({'at': supervisor.now(), 'unit': unit, 'settled': result,
                        'stopping': self.stopping})
                finally:
                    self.stream.close()
                    self.stream = None
            return result

    return GuardedBackend


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--supervisor', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    supervisor = load_supervisor(args.supervisor)
    supervisor.Backend = guarded_backend(supervisor)
    sys.argv = [str(args.supervisor), *remaining]
    return supervisor.main()


if __name__ == '__main__':
    raise SystemExit(main())
