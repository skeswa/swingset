"""Independent read-only proof after the finite replay supervisor reports current."""

import argparse
import fcntl
import hashlib
import json
import re
import signal
import sqlite3
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from swingset.state import db as db_module, derivations

SOURCE = Path('/nix/store/z689qy41inndill3d92ym8im852x3649-source')
SOURCE_SHA = '0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb'
SCRATCH = Path('/var/tmp/swingset-h16-event-preservation-replay')
CHECKPOINT = Path('/var/lib/swingset/checkpoints/h15-before-h16-20260913')
CHECKPOINT_SHA = '700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444'
EVIDENCE = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16')
OUTPUT = EVIDENCE / 'replay-final-verification.json'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(value, reason):
    if not value:
        raise ValueError(reason)


def database_files():
    return {p.name: {'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns} for p in SCRATCH.glob('state.sqlite*')}


def expired(*_):
    raise TimeoutError('Independent verification exceeded180 seconds')


def load_binding(path, expected_sha256):
    require(sha(path) == expected_sha256, 'Input binding receipt changed')
    binding = json.loads(path.read_bytes())
    require(binding.get('source') == str(SOURCE) and binding.get('source_receipt_sha256') == SOURCE_SHA and binding.get('scratch') == str(SCRATCH), 'Wrong input binding source/scratch')
    require(all(isinstance(binding.get(key), str) and re.fullmatch('[0-9a-f]{64}', binding[key]) for key in ('input_bundle_hash', 'marker_sha256')), 'Invalid input binding hashes')
    return binding


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-binding', type=Path, default=EVIDENCE / 'replay-input-binding.json')
    parser.add_argument('--input-binding-sha256', required=True)
    args = parser.parse_args()
    binding = load_binding(args.input_binding, args.input_binding_sha256)
    BUNDLE = binding['input_bundle_hash']
    MARKER_SHA = binding['marker_sha256']
    require(not OUTPUT.exists(), 'Verification receipt already exists')
    report = {'format': 'h16-event-preservation-replay-final-verification-v1', 'verified_at': datetime.now(UTC).isoformat(), 'source': str(SOURCE), 'source_receipt_sha256': SOURCE_SHA, 'scratch': str(SCRATCH), 'marker_sha256': MARKER_SHA, 'input_bundle_hash': BUNDLE, 'network_requests': 0, 'parse_executed': False, 'published': False, 'passed': False, 'script_sha256': sha(Path(__file__)), 'input_binding_sha256': args.input_binding_sha256}
    started = time.monotonic()
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(180)
    try:
        require(Path(derivations.__file__).resolve().is_relative_to(SOURCE / 'src') and Path(db_module.__file__).resolve() == SOURCE / 'src/swingset/state/db.py' and db_module.SCHEMA_VERSION == 14, 'Wrong loaded source/schema')
        require(sha(SOURCE / 'h16-source.json') == SOURCE_SHA, 'Source receipt changed')
        require(sha(SCRATCH / 'offline-derivation-scratch.json') == MARKER_SHA, 'Scratch marker changed')
        require(sha(CHECKPOINT / 'checkpoint.json') == CHECKPOINT_SHA, 'Checkpoint manifest changed')
        supervisor_path = EVIDENCE / 'replay-supervisor.json'
        supervisor = json.loads(supervisor_path.read_bytes())
        require(supervisor.get('passed') is True and supervisor.get('status') == 'current' and supervisor.get('source_receipt_sha256') == SOURCE_SHA and supervisor.get('input_bundle_hash') == BUNDLE, 'Supervisor not complete for these inputs')
        last = supervisor['invocations'][-1]
        final_path = Path(f'/var/tmp/swingset-h16-event-preservation-replay-{last["number"]:03d}.json')
        require(sha(final_path) == last['receipt_sha256'], 'Final replay receipt changed')
        final = json.loads(final_path.read_bytes())
        expected = {'status': 'current', 'scratch': str(SCRATCH), 'source_receipt_sha256': SOURCE_SHA, 'input_bundle_hash': BUNDLE, 'marker_sha256': MARKER_SHA, 'unfinished_by_scope': {}, 'network_requests': 0, 'parse_executed': False, 'published': False}
        require(all(final.get(key) == value for key, value in expected.items()), 'Final receipt does not bind exact current replay')
        report.update(supervisor_receipt_sha256=sha(supervisor_path), replay_receipt=str(final_path), replay_receipt_sha256=sha(final_path))
        with (SCRATCH / 'state.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report['lock_exclusive_acquired'] = True
            report['database_files_before'] = database_files()
            with closing(sqlite3.connect((SCRATCH / 'state.sqlite').as_uri() + '?mode=ro', uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute('PRAGMA query_only=ON')
                conn.execute('BEGIN')
                conn.set_progress_handler(lambda: int(time.monotonic() - started > 175), 10000)
                require(conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0] == BUNDLE, 'Accepted inputs changed')
                report['schema'] = conn.execute('PRAGMA user_version').fetchone()[0]
                require(report['schema'] == 14, 'Unexpected scratch schema')
                report['unfinished_by_scope'] = dict(Counter(unit.stage + '/' + unit.unit_kind for stage in ('project', 'link') for unit in derivations.pending_units(conn, stage)))
                report['attempt_outcomes'] = dict(conn.execute('SELECT outcome,count(*) FROM work_attempts GROUP BY outcome'))
                report['admission_states'] = dict(conn.execute('SELECT state,count(*) FROM execution_admissions GROUP BY state'))
                report['materialized_by_scope'] = {stage + '/' + kind: count for stage, kind, count in conn.execute('SELECT stage,unit_kind,count(*) FROM derivation_scopes WHERE materialized_generation_id IS NOT NULL GROUP BY stage,unit_kind')}
                report['generation_count'] = conn.execute('SELECT count(*) FROM derivation_generations').fetchone()[0]
                report['inherited_unfinished_runs'] = [list(row) for row in conn.execute('SELECT run_id,started_at,finished_at FROM runs WHERE finished_at IS NULL ORDER BY run_id')]
                with closing(sqlite3.connect((CHECKPOINT / 'state.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)) as old:
                    inherited = [list(row) for row in old.execute('SELECT run_id,started_at,finished_at FROM runs WHERE finished_at IS NULL ORDER BY run_id')]
                report['inherited_unfinished_runs_unchanged'] = report['inherited_unfinished_runs'] == inherited
                report['foreign_key_violations'] = len(list(conn.execute('PRAGMA foreign_key_check')))
                report['quick_check'] = [row[0] for row in conn.execute('PRAGMA quick_check')]
                report['connection_total_changes'] = conn.total_changes
            report['database_files_after'] = database_files()
            report['main_database_metadata_unchanged'] = report['database_files_before']['state.sqlite'] == report['database_files_after']['state.sqlite']
            require(not report['unfinished_by_scope'] and report['generation_count'] == 34986 and sum(report['materialized_by_scope'].values()) == 34986, 'Missing current generation support')
            require(set(report['attempt_outcomes']) == {'succeeded'} and set(report['admission_states']) <= {'settled'}, 'Unsuccessful attempt or unsettled admission')
            require(report['inherited_unfinished_runs_unchanged'] and not report['foreign_key_violations'] and report['quick_check'] == ['ok'], 'Integrity or inherited-run mismatch')
            require(report['connection_total_changes'] == 0 and report['main_database_metadata_unchanged'], 'Read-only verification metadata changed')
            report['passed'] = True
    except BaseException as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        signal.alarm(0)
        report['elapsed_seconds'] = time.monotonic() - started
        with OUTPUT.open('x') as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
        print(json.dumps({'output': str(OUTPUT), 'sha256': sha(OUTPUT), 'passed': report['passed'], 'generation_count': report.get('generation_count'), 'error': report.get('error')}), flush=True)


if __name__ == '__main__':
    main()
