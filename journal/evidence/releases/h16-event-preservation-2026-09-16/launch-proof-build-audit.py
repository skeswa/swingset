"""Explicitly gated scratch build OR audit; never deploy, initialize, or publish."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path('/nix/store/z689qy41inndill3d92ym8im852x3649-source')
SOURCE_SHA = '0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb'
BUNDLE = 'fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a'
MARKER = 'a2eb6255beddabe1917808da3916e219433fb9bf7fc85dd6427d9e28bad1a1e2'
REPLAY = Path('/var/tmp/swingset-h16-event-preservation-replay')
STATE = Path('/var/tmp/swingset-h16-event-preservation-build')
BUILD_RECEIPT = Path('/var/tmp/swingset-h16-event-preservation-build.json')
AUDIT_RECEIPT = Path('/var/tmp/swingset-h16-event-preservation-audit.json')
EVIDENCE = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16')
PYTHON = '/var/lib/swingset/venv/bin/python'
LD = '/nix/store/x03dxqva88ax4w45hyms977zv3f8a8i9-gcc-14.3.0-lib/lib:/nix/store/5zq11bibj72nvrhlx9fm0xl0xhxd6388-zlib-1.3.2/lib'
DRIVERS = {
    '/var/tmp/h16-changelog-build-driver.py': 'f0a78d6524e5af9228c8391a9e3585210c9a64a4e1bfbaabafd57bb4b9ce1fbe',
    '/var/tmp/h16-changelog-build-monitor.py': '452d75f1023a0085cfdc7c14da0df2142f125ca2c2dbebfc1bf81b8e8bf5cf7f',
    '/var/tmp/h16-changelog-audit-driver.py': '9de88bd2b5d988a00ee0529504e301b23c93eaf5018485a3f6f4db0deb2deebf',
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(value, reason):
    if not value:
        raise ValueError(reason)


def save(path, value):
    with path.open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def run(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=60).stdout


def status(unit):
    fields = run(['systemctl', 'show', unit, '--property=LoadState,ActiveState,SubState,MainPID,Result,ExecMainStatus,InvocationID'])
    return dict(line.split('=', 1) for line in fields.splitlines() if '=' in line)


def held():
    require(Path('/var/lib/swingset/operator-hold').is_file(), 'Production hold absent')
    for task in ('cycle', 'backup', 'summary'):
        for kind in ('service', 'timer'):
            require(status(f'swingset-{task}.{kind}')['ActiveState'] == 'inactive', 'Production unit active')


def successful_build(value):
    require(value.get('passed') is True and value.get('semantic_publication_preflight') is True and 'error' not in value, 'Build receipt not accepted')
    require(value.get('network_requests') == 0 and value.get('published') is False, 'Build scope violation')
    require(value.get('source') == str(SOURCE) and value.get('state') == str(STATE), 'Build source/state mismatch')
    candidate, baseline = Path(value['candidate']).resolve(), Path(value['baseline']).resolve()
    require(candidate.is_relative_to(STATE / 'candidates') and baseline.is_relative_to(STATE / 'candidates') and candidate != baseline, 'Candidate/baseline outside new state or identical')
    require(candidate.name == value['candidate_id'], 'Candidate identity mismatch')
    require(sha(candidate / '_meta/manifest.json') == value['manifest_hash'], 'Manifest hash differs from build receipt')
    return candidate, baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('build', 'audit'))
    parser.add_argument('--gate', type=Path, required=True)
    parser.add_argument('--gate-sha256', required=True)
    parser.add_argument('--replay-receipt', type=Path)
    parser.add_argument('--replay-receipt-sha256')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Run launcher on VM as root; worker runs as swingset')
    require(sha(args.gate) == args.gate_sha256, 'Gate hash mismatch')
    gate = json.loads(args.gate.read_bytes())
    require(gate.get('passed') is True and gate.get('source') == str(SOURCE) and gate.get('source_receipt_sha256') == SOURCE_SHA, 'Gate does not accept exact source')
    require(sha(SOURCE / 'h16-source.json') == SOURCE_SHA, 'Source receipt mismatch')
    for path, expected in DRIVERS.items():
        require(sha(Path(path)) == expected, 'Driver changed: ' + path)
    held()
    mode = args.mode
    unit, monitor = f'swingset-h16-event-preservation-{mode}.service', f'swingset-h16-event-preservation-{mode}-monitor.service'
    resource_receipt = Path(f'/var/tmp/swingset-h16-event-preservation-{mode}-resources.json')
    output = BUILD_RECEIPT if mode == 'build' else AUDIT_RECEIPT
    require(not output.exists() and not output.is_symlink(), 'Worker output must be new')
    require(not resource_receipt.exists() and not resource_receipt.with_suffix('.tmp').exists(), 'Monitor output must be new')
    require(status(unit)['LoadState'] == 'not-found' and status(monitor)['LoadState'] == 'not-found', 'Worker/monitor unit already exists')
    supervisor = json.loads((EVIDENCE / 'replay-supervisor.json').read_bytes())
    require(supervisor.get('passed') is True and supervisor.get('status') == 'current' and supervisor.get('input_bundle_hash') == BUNDLE, 'Replay supervisor has not completed')
    if mode == 'build':
        require(not STATE.exists() and not STATE.is_symlink(), 'Build state must be new')
        require(args.replay_receipt is not None and args.replay_receipt_sha256 is not None, 'Final replay receipt and hash required')
        require(sha(args.replay_receipt) == args.replay_receipt_sha256, 'Replay receipt changed')
        expected = {'scratch': str(REPLAY), 'source_receipt_sha256': SOURCE_SHA, 'marker_sha256': MARKER, 'input_bundle_hash': BUNDLE, 'unfinished_by_scope': {}}
        require(all(gate.get(k) == v for k, v in expected.items()), 'Independent replay verification binding mismatch')
        replay = json.loads(args.replay_receipt.read_bytes())
        require(replay.get('status') == 'current' and all(replay.get(k) == v for k, v in expected.items()), 'Final replay receipt mismatch')
        driver = [PYTHON, '/var/tmp/h16-changelog-build-driver.py', '--replay', str(REPLAY), '--replay-receipt', str(args.replay_receipt), '--state', str(STATE), '--output', str(output), '--source', str(SOURCE), '--source-receipt-sha256', SOURCE_SHA, '--bundle-digest', BUNDLE]
    else:
        # A separate root-approved gate is required; build completion never calls this mode.
        require(gate.get('build_receipt_sha256') == sha(BUILD_RECEIPT), 'Build-pass gate receipt mismatch')
        build = json.loads(BUILD_RECEIPT.read_bytes())
        candidate, baseline = successful_build(build)
        require(gate.get('candidate_id') == build['candidate_id'] and gate.get('manifest_hash') == build['manifest_hash'], 'Build-pass gate candidate mismatch')
        driver = [PYTHON, '/var/tmp/h16-changelog-audit-driver.py', '--state', str(STATE), '--candidate', str(candidate), '--baseline', str(baseline), '--output', str(output), '--temp-parent', '/var/tmp']
    argv = ['systemd-run', '--wait', '--unit=' + unit, '--property=Type=exec', '--property=User=swingset', '--property=Group=swingset', '--property=RuntimeMaxSec=35min', '--property=TimeoutStopSec=3min', '--property=MemoryAccounting=yes', '--property=UMask=0077', '--setenv=PYTHONDONTWRITEBYTECODE=1', f'--setenv=PYTHONPATH={SOURCE}/src:{SOURCE}', f'--setenv=SWINGSET_REVISION=uncommitted:{SOURCE.name}', '--setenv=LD_LIBRARY_PATH=' + LD, *driver]
    monitor_argv = ['systemd-run', '--unit=' + monitor, '--property=Type=exec', '--property=RuntimeMaxSec=40min', '--property=TimeoutStopSec=30', '--property=UMask=0077', '--setenv=PYTHONDONTWRITEBYTECODE=1', PYTHON, '/var/tmp/h16-changelog-build-monitor.py', '--unit', unit, '--output', str(resource_receipt), '--max-seconds', '2100']
    report = {'format': 'h16-event-preservation-' + mode + '-orchestration-v1', 'at': datetime.now(UTC).isoformat(), 'source': str(SOURCE), 'source_receipt_sha256': SOURCE_SHA, 'input_bundle_hash': BUNDLE, 'gate_sha256': args.gate_sha256, 'script_sha256': sha(Path(__file__)), 'driver_hashes': DRIVERS, 'worker_argv': argv, 'monitor_argv': monitor_argv, 'production_mutated': False, 'passed': False}
    result_path = EVIDENCE / f'{mode}-orchestration.json'
    require(not result_path.exists(), 'Orchestration receipt already exists')
    save(EVIDENCE / f'{mode}-launch.json', report)
    process = None
    try:
        with (EVIDENCE / f'{mode}-systemd-wait.log').open('xb') as log:
            process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT)
            started = time.monotonic()
            while True:
                current = status(unit)
                if current.get('ActiveState') == 'active' and current.get('MainPID') != '0':
                    break
                require(process.poll() is None and time.monotonic() - started < 30, 'Worker did not start for monitor')
                time.sleep(0.25)
            report['initial_unit'] = current
            run(monitor_argv)
            # --wait retains direct exit evidence independently of transient-unit garbage collection.
            while process.poll() is None:
                held()
                require(time.monotonic() - started < 2400, 'Launcher deadline exceeded')
                print(json.dumps({'mode': mode, 'unit': unit, 'elapsed_seconds': round(time.monotonic() - started), 'state': status(unit)}), flush=True)
                time.sleep(20)
            report['systemd_wait_exit_code'] = process.returncode
            require(process.returncode == 0, 'Worker/systemd-run exit failed')
            report['post_wait_unit'] = status(unit)
            deadline = time.monotonic() + 30
            while True:
                resources = json.loads(resource_receipt.read_bytes()) if resource_receipt.exists() else {}
                if resources.get('finished_at'):
                    break
                require(time.monotonic() < deadline, 'Monitor did not retain final status')
                time.sleep(1)
            report['resource_receipt_sha256'] = sha(resource_receipt)
            require(resources.get('unit') == unit and resources.get('terminated_reason') is None, 'Resource monitor rejected worker')
            require(any(sample.get('MainPID') == current['MainPID'] and sample.get('ActiveState') == 'active' for sample in resources.get('samples', [])), 'No resource observation of the actual running worker')
            if report['post_wait_unit'].get('LoadState') == 'not-found':
                # systemctl returns default success/zero for a garbage-collected unit.
                # Those defaults are not evidence; the direct --wait exit is authoritative.
                report['exit_evidence'] = 'systemd-run --wait exit0; transient unit already garbage collected'
            else:
                require(report['post_wait_unit'].get('Result') == 'success' and report['post_wait_unit'].get('ExecMainStatus') == '0', 'Retained unit rejected worker')
                report['exit_evidence'] = 'systemd-run --wait exit0 and retained unit success/zero'
            value = json.loads(output.read_bytes())
            if mode == 'build':
                successful_build(value)
                report['build_receipt_sha256'] = sha(output)
            else:
                require(value.get('passed') is True and value.get('checks') and all(value['checks'].values()), 'Independent audit rejected candidate')
                require(value.get('candidate_id') == build['candidate_id'] and value.get('manifest_hash') == build['manifest_hash'] and value.get('network_requests') == 0, 'Audit candidate binding/scope mismatch')
                report['audit_receipt_sha256'] = sha(output)
            report.update(candidate_id=value['candidate_id'], manifest_hash=value['manifest_hash'], passed=True)
    except BaseException as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)}
        if process is not None and process.poll() is None:
            stopped = subprocess.run(['systemctl', 'stop', unit], capture_output=True, text=True, timeout=200)
            report['stop'] = {'returncode': stopped.returncode, 'stderr': stopped.stderr}
        raise
    finally:
        for remote, name in ((output, f'{mode}-result.json'), (resource_receipt, f'{mode}-resources.json')):
            try:
                if remote.exists():
                    with (EVIDENCE / name).open('xb') as stream:
                        stream.write(remote.read_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
            except BaseException as error:
                report.setdefault('retention_errors', []).append({'path': str(remote), 'error': str(error)})
                report['passed'] = False
        for retained_unit in (unit, monitor):
            try:
                with (EVIDENCE / f'{retained_unit}.log').open('x') as stream:
                    stream.write(run(['journalctl', '-u', retained_unit, '--no-pager', '-o', 'short-iso']))
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException as error:
                report.setdefault('retention_errors', []).append({'unit': retained_unit, 'error': str(error)})
                report['passed'] = False
        report['finished_at'] = datetime.now(UTC).isoformat()
        save(result_path, report)
        print(json.dumps({'mode': mode, 'passed': report['passed'], 'candidate_id': report.get('candidate_id'), 'error': report.get('error')}), flush=True)
    require(report['passed'], 'Orchestration evidence did not pass')


if __name__ == '__main__':
    main()
