"""Read-only timing of the retained failed build; never release acceptance."""

import cProfile
import fcntl
import hashlib
import json
import pstats
import resource
import signal
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import importlib.util
import sys
WORK = Path('/Users/skeswa/repos/skeswa/swingset')
for name in ('closure_validation', 'closure'):
    spec = importlib.util.spec_from_file_location('swingset.build.' + name, WORK / 'src/swingset/build' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
from swingset.build.closure_validation import validation_scope

from swingset.build import closure

SOURCE = Path('/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source')
STATE = Path('/var/tmp/swingset-h16-closure-build')
CANDIDATE = STATE / 'candidates/cand_95d350f1e29a4e19'
OUTPUT = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-validation-2026-09-16/readonly-proof-reuse.json')
assert Path(closure.__file__).resolve() == WORK / 'src/swingset/build/closure.py'
assert not OUTPUT.exists()

def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def database_stats():
    return {p.name: {'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
            for p in STATE.glob('state.sqlite*')}

report = {
    'format': 'h16-readonly-proof-reuse-diagnostic-v1', 'inputs_accepted': False, 'module_overrides': {name: file_sha(WORK / 'src/swingset/build' / (name + '.py')) for name in ('closure_validation', 'closure')},
    'at': datetime.now(UTC).isoformat(),
    'source': str(SOURCE), 'state': str(STATE),
    'source_receipt_sha256': file_sha(SOURCE / 'h16-source.json'),
    'script_sha256': file_sha(Path(__file__)),
    'candidate_manifest_sha256': file_sha(CANDIDATE / '_meta/manifest.json'),
    'diagnostic_only': True, 'production_mutated': False,
    'build_executed': False, 'network_requests': 0, 'passes': [],
}
assert report['source_receipt_sha256'] == 'f8f24c2d8b839bbfcd25bf87e551e9f2777d1236f89ed84da16c3c07d4d75b9f'
assert report['candidate_manifest_sha256'] == '7ce49736b44da6e0afc007a2da9d8692008f29daff9a3e06ee65926eb5991168'

def expired(*_):
    raise TimeoutError('read-only diagnostic exceeded 180 seconds')

signal.signal(signal.SIGALRM, expired)
signal.alarm(180)
resource.setrlimit(resource.RLIMIT_AS, (6 * 1024**3, 6 * 1024**3))
started = time.monotonic()
try:
    with (STATE / 'state.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        wal = STATE / 'state.sqlite-wal'
        assert not wal.exists() or wal.stat().st_size == 0
        report['database_before'] = database_stats()
        conn = sqlite3.connect((STATE / 'state.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            manifest = json.loads((CANDIDATE / '_meta/manifest.json').read_bytes())
            mark = time.monotonic()
            pinned = closure.hydrate(conn, manifest['release_policy']['closure'])
            report['hydrate_seconds'] = time.monotonic() - mark
            report['inventory'] = {k: len(pinned[k]) for k in ('selected', 'source_support', 'dependency_sets')}
            report['accepted_support_receipts'] = sum(len(x['source_generations']) for x in pinned['source_support'])
            scope = validation_scope(conn)
            scope.__enter__()
            for index in range(3):
                mark = time.monotonic()
                closure.validate(conn, pinned)
                report['passes'].append({'index': index + 1, 'seconds': time.monotonic() - mark, 'passed': True})
                print(json.dumps(report['passes'][-1]), flush=True)
            profiler = cProfile.Profile()
            profiler.runcall(closure.validate, conn, pinned)
            stats = pstats.Stats(profiler)
            report['profile_top_cumulative'] = [
                {'function': str(k), 'calls': v[1], 'self_seconds': v[2], 'cumulative_seconds': v[3]}
                for k, v in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:30]
            ]
            scope.__exit__(None, None, None)
            report['connection_total_changes'] = conn.total_changes
            report['passed'] = True
        finally:
            conn.close()
        report['database_after'] = database_stats()
        report['database_unchanged'] = report['database_before'] == report['database_after']
except BaseException as error:
    report['error'] = {'type': type(error).__name__, 'message': str(error)}
    report['passed'] = False
    raise
finally:
    signal.alarm(0)
    report['elapsed_seconds'] = time.monotonic() - started
    report['max_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with OUTPUT.open('x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps({'output': str(OUTPUT), 'sha256': file_sha(OUTPUT), 'passed': report.get('passed')}), flush=True)
