"""Finite scratch-only VM replay. Requires a separately accepted frozen-test gate."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path('/nix/store/4c4q8rkiz6jqizcy93bid6cz8mfvp20b-source')
SOURCE_SHA = '71dc14101ca19880f3addfa2dc0073dbe50bce0e4e2be27d7bc91811b6d1e338'
CHECKPOINT = Path('/var/lib/swingset/checkpoints/h15-before-h16-20260913')
CHECKPOINT_SHA = '700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444'
DATABASE_SHA = 'd81b2e459471c25c430a57c2a4d096180f1094e57c1b8b1e896d66774f12e8dc'
SCRATCH = Path('/var/tmp/swingset-h16-proof-replay')
EVIDENCE = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-validation-2026-09-16')
PYTHON = '/var/lib/swingset/venv/bin/python'
LD = '/nix/store/x03dxqva88ax4w45hyms977zv3f8a8i9-gcc-14.3.0-lib/lib:/nix/store/5zq11bibj72nvrhlx9fm0xl0xhxd6388-zlib-1.3.2/lib'
PREFIX = 'swingset-h16-proof-replay'
PROPERTIES = ('LoadState', 'ActiveState', 'SubState', 'Result', 'ExecMainStatus', 'MemoryCurrent', 'MemoryPeak', 'CPUUsageNSec', 'MainPID', 'InvocationID', 'ControlGroup')
OWNED_UNITS = set()


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def write_new(path, value):
    with path.open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def command(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60).stdout


def unit_status(unit):
    raw = command(['systemctl', 'show', unit, *(f'--property={key}' for key in PROPERTIES)])
    return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)


def held():
    require(Path('/var/lib/swingset/operator-hold').is_file(), 'Production operator hold absent')
    for task in ('cycle', 'backup', 'summary'):
        for kind in ('service', 'timer'):
            state = unit_status(f'swingset-{task}.{kind}')
            require(state['ActiveState'] == 'inactive', f'Production {task}.{kind} is not inactive')


def attempts(after_id):
    with sqlite3.connect((SCRATCH / 'state.sqlite').as_uri() + '?mode=ro', uri=True, timeout=5) as conn:
        return dict(conn.execute('SELECT outcome,count(*) FROM work_attempts WHERE attempt_id>? GROUP BY outcome', (after_id,)))


def retain(unit, remote=None):
    journal = command(['journalctl', '-u', unit, '--no-pager', '-o', 'short-iso'])
    with (EVIDENCE / f'{unit}.log').open('x') as stream:
        stream.write(journal)
        stream.flush()
        os.fsync(stream.fileno())
    if remote is not None and remote.exists():
        with (EVIDENCE / remote.name).open('xb') as stream:
            stream.write(remote.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())


def launch(unit, tail):
    held()
    require(unit_status(unit).get('LoadState') == 'not-found', f'Unit already exists: {unit}')
    args = [
        'systemd-run', '--unit=' + unit, '--property=User=swingset', '--property=Group=swingset',
        '--property=Type=exec', '--property=RuntimeMaxSec=900', '--property=MemoryAccounting=yes', '--property=RemainAfterExit=yes',
        '--property=UMask=0077', '--property=TimeoutStopSec=45', '--property=KillMode=control-group',
        '--setenv=PYTHONDONTWRITEBYTECODE=1', f'--setenv=PYTHONPATH={SOURCE}/src:{SOURCE}',
        f'--setenv=SWINGSET_REVISION=uncommitted:{SOURCE.name}', '--setenv=LD_LIBRARY_PATH=' + LD,
        PYTHON, str(SOURCE / 'research/replay_derivations.py'), '--scratch', str(SCRATCH),
        '--source', str(SOURCE), '--source-receipt-sha256', SOURCE_SHA, *tail,
    ]
    write_new(EVIDENCE / f'{unit}.command.json', {'argv': args, 'at': datetime.now(UTC).isoformat()})
    OWNED_UNITS.add(unit)
    command(args)


def monitor(unit, remote, after_id, binding, samples):
    started = time.monotonic()
    while True:
        state = unit_status(unit)
        sample = {'at': datetime.now(UTC).isoformat(), 'unit': unit, 'service': state}
        group = state.get('ControlGroup')
        memory = Path('/sys/fs/cgroup') / group.lstrip('/') / 'memory.stat' if group else None
        if memory and memory.exists():
            sample['cgroup_memory'] = {key: int(value) for key, value in (line.split() for line in memory.read_text().splitlines())}
        receipt = None
        if remote is not None and remote.exists() and remote.stat().st_size:
            receipt = json.loads(remote.read_bytes())
            sample['receipt'] = receipt
        if after_id is not None:
            sample['attempt_outcomes'] = attempts(after_id)
        samples.write(json.dumps(sample, sort_keys=True) + '\n')
        samples.flush()
        os.fsync(samples.fileno())
        print(json.dumps({'unit': unit, 'service': state, 'anon': sample.get('cgroup_memory', {}).get('anon'), 'status': (receipt or {}).get('status'), 'completed': (receipt or {}).get('completed')}), flush=True)
        require(sample.get('cgroup_memory', {}).get('anon', 0) < 6 * 1024**3, '6 GiB anonymous-memory limit reached')
        require(time.monotonic() - started < 960, 'Supervisor per-invocation deadline reached')
        held()
        require(set(sample.get('attempt_outcomes', {})) <= {'running', 'succeeded'}, 'Non-successful new work attempt')
        if receipt:
            require(receipt.get('source_receipt_sha256') == SOURCE_SHA and receipt.get('scratch') == str(SCRATCH), 'Replay receipt binding changed')
            require(receipt.get('marker_sha256') == binding['marker_sha256'], 'Scratch marker binding changed')
            require(receipt.get('network_requests') == 0 and receipt.get('parse_executed') is False and receipt.get('published') is False, 'Replay scope violation')
            bundle = receipt.get('input_bundle_hash')
            if bundle:
                require(re.fullmatch('[0-9a-f]{64}', bundle) is not None, 'Invalid bundle hash')
                if binding.get('input_bundle_hash') is None:
                    binding['input_bundle_hash'] = bundle
                    write_new(EVIDENCE / 'replay-input-binding.json', dict(binding))
                require(bundle == binding['input_bundle_hash'], 'Replay input bundle changed')
            require(receipt.get('status') not in ('interrupted', 'no_runnable_progress'), 'Replay requires review')
        exited = state.get('SubState') == 'exited' and state.get('MainPID') == '0'
        if exited:
            require(state.get('Result') == 'success' and state.get('ExecMainStatus') == '0', 'Worker failed')
            require(not sample.get('attempt_outcomes', {}).get('running'), 'Running attempt after worker exit')
            if remote is not None:
                require(receipt is not None and receipt.get('status') in ('bounded_stop', 'current'), 'Missing safe final receipt')
                require(receipt.get('input_bundle_hash') == binding.get('input_bundle_hash') and binding.get('input_bundle_hash'), 'No bound input bundle')
            return receipt
        require(state.get('ActiveState') not in ('failed', 'inactive'), 'Worker stopped or failed')
        time.sleep(20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen-tests-receipt', type=Path, required=True)
    parser.add_argument('--frozen-tests-sha256', required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Run on VM as root; workers run as swingset')
    require(sha(args.frozen_tests_receipt) == args.frozen_tests_sha256, 'Frozen test gate digest mismatch')
    gate = json.loads(args.frozen_tests_receipt.read_bytes())
    require(gate.get('passed') is True and gate.get('source') == str(SOURCE) and gate.get('source_receipt_sha256') == SOURCE_SHA, 'Frozen tests have not passed for exact source')
    require(sha(SOURCE / 'h16-source.json') == SOURCE_SHA, 'Source receipt changed')
    require(sha(CHECKPOINT / 'checkpoint.json') == CHECKPOINT_SHA, 'Checkpoint manifest changed')
    require(sha(CHECKPOINT / 'state.sqlite') == DATABASE_SHA, 'Checkpoint database changed')
    require(not SCRATCH.exists() and not SCRATCH.is_symlink(), 'Scratch must be new')
    require(not (EVIDENCE / 'replay-supervisor.json').exists(), 'Supervisor receipt already exists')
    held()
    report = {'format': 'h16-proof-replay-supervisor-v1', 'at': datetime.now(UTC).isoformat(), 'source': str(SOURCE), 'source_receipt_sha256': SOURCE_SHA, 'scratch': str(SCRATCH), 'checkpoint': str(CHECKPOINT), 'checkpoint_manifest_sha256': CHECKPOINT_SHA, 'frozen_tests_sha256': args.frozen_tests_sha256, 'script_sha256': sha(Path(__file__)), 'max_invocations': 20, 'production_mutated': False, 'passed': False, 'invocations': []}
    current_unit, remote = None, None
    binding = {'source': str(SOURCE), 'source_receipt_sha256': SOURCE_SHA, 'scratch': str(SCRATCH)}
    try:
        with (EVIDENCE / 'replay-resource-samples.jsonl').open('x') as samples:
            current_unit = PREFIX + '-prepare.service'
            launch(current_unit, ['--prepare-from', str(CHECKPOINT)])
            monitor(current_unit, None, None, binding, samples)
            retain(current_unit)
            current_unit = None
            marker = SCRATCH / 'offline-derivation-scratch.json'
            value = json.loads(marker.read_bytes())
            require(value['checkpoint_manifest_sha256'] == CHECKPOINT_SHA and value['origin_database_sha256'] == DATABASE_SHA and value['source_receipt_sha256'] == SOURCE_SHA, 'Prepared scratch marker mismatch')
            binding['marker_sha256'] = sha(marker)
            write_new(EVIDENCE / 'replay-preparation.json', value)
            with sqlite3.connect((SCRATCH / 'state.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
                after_id = conn.execute('SELECT coalesce(max(attempt_id),0) FROM work_attempts').fetchone()[0]
                generation_count = conn.execute('SELECT count(*) FROM derivation_generations').fetchone()[0]
                require(conn.execute("SELECT count(*) FROM work_attempts WHERE outcome='running'").fetchone()[0] == 0, 'Checkpoint has running attempts requiring review')
            report['baseline_attempt_id'] = after_id
            for number in range(1, 21):
                current_unit = f'{PREFIX}-{number:03d}.service'
                remote = Path(f'/var/tmp/{PREFIX}-{number:03d}.json')
                require(not remote.exists() and not remote.is_symlink(), 'Invocation output already exists')
                launch(current_unit, ['--config', str(SOURCE / 'config'), '--overrides', str(SOURCE / 'overrides'), '--output', str(remote), '--max-seconds', '600'])
                receipt = monitor(current_unit, remote, after_id, binding, samples)
                retain(current_unit, remote)
                current_unit = None
                report['invocations'].append({'number': number, 'receipt_sha256': sha(remote), 'status': receipt['status'], 'completed': receipt['completed'], 'generation_count': receipt['generation_count']})
                if receipt['status'] == 'current':
                    require(receipt.get('unfinished_by_scope') == {}, 'Current receipt has unfinished or unknown scopes')
                    report['passed'] = True
                    report['status'] = 'current'
                    report['input_bundle_hash'] = binding['input_bundle_hash']
                    return
                require(receipt['completed'] > 0 and receipt['generation_count'] > generation_count, 'Bounded invocation made no durable progress')
                generation_count = receipt['generation_count']
            raise RuntimeError('Finite 20-invocation limit reached; no further worker authorized')
    except BaseException as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)}
        if current_unit in OWNED_UNITS:
            # Stop only this script's current scratch unit. Never touch production units.
            stopped = subprocess.run(['systemctl', 'stop', current_unit], capture_output=True, text=True, timeout=60)
            report['stop'] = {'unit': current_unit, 'returncode': stopped.returncode, 'stderr': stopped.stderr}
            try:
                retain(current_unit, remote)
            except BaseException as retention_error:
                report['retention_error'] = str(retention_error)
        raise
    finally:
        report['finished_at'] = datetime.now(UTC).isoformat()
        write_new(EVIDENCE / 'replay-supervisor.json', report)
        print(json.dumps({'supervisor_passed': report['passed'], 'invocations': len(report['invocations']), 'status': report.get('status'), 'error': report.get('error')}), flush=True)


if __name__ == '__main__':
    main()
