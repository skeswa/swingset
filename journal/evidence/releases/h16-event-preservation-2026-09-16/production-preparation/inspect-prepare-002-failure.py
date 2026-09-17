"""Bounded metadata-only equality check after prepare failed at control-lock open before input acceptance."""
import fcntl
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

STATE = Path('/var/lib/swingset')
OPS = STATE / 'operations/h16-event-preservation-release-20260916'
OUT = Path('/var/tmp/h16-event-preservation-initialization-prepare-002-20260916/failure-preservation.json')
TABLES = ('accepted_inputs','pending_work','work_generations','work_attempts','operator_pauses','control_state','control_events','execution_admissions','runs')

def digest(conn, sql):
    # Exact frozen accept_h11.query_digest encoding, restricted here to small metadata tables.
    count, result = 0, hashlib.sha256()
    for row in conn.execute(sql):
        result.update(json.dumps(tuple(row), separators=(',', ':')).encode() + b'\n')
        count += 1
    return {'count': count, 'sha256': result.hexdigest()}

report = {'at': datetime.now(UTC).isoformat(), 'database_writes': False, 'tables': {}, 'passed': False}
expected = json.loads((OPS / 'preflight.json').read_bytes())
started = time.monotonic()
with (STATE / 'state.lock').open('rb') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with closing(sqlite3.connect((STATE / 'state.sqlite').as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON'); conn.execute('BEGIN')
        conn.set_progress_handler(lambda: int(time.monotonic() - started > 10), 10000)
        for table in TABLES:
            count = conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
            if count > 5000:
                raise ValueError('diagnostic row budget exceeded')
            columns = len(conn.execute('PRAGMA table_info(' + table + ')').fetchall())
            order = ','.join(str(i+1) for i in range(columns))
            actual = digest(conn, 'SELECT * FROM ' + table + ' ORDER BY ' + order)
            report['tables'][table] = {'actual': actual, 'equals_preflight': actual == expected['before']['tables'][table], 'equals_verified_checkpoint': actual == expected['checkpoint']['tables'][table]}
        actual = digest(conn, "SELECT key,value FROM meta WHERE key NOT LIKE 'last_backup%' ORDER BY key")
        report['meta'] = {'actual': actual, 'equals_preflight': actual == expected['before']['backup_neutral_meta'], 'equals_verified_checkpoint': actual == expected['checkpoint']['backup_neutral_meta']}
        report['recipe_runtime_rows'] = conn.execute("SELECT count(*) FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'").fetchone()[0]
        report['input_bundle_hash'] = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0]
        report['schema'] = conn.execute('PRAGMA user_version').fetchone()[0]
    fcntl.flock(lock, fcntl.LOCK_UN)
marker_path = OPS / 'initialization-marker-002.json'
marker_bytes = marker_path.read_bytes()
marker_sha = hashlib.sha256(marker_bytes).hexdigest()
assert marker_sha == 'dad425983041d0a27ab2eae1127db1fc9aeec4a401f50d678d6fe87e176baece'
report['marker_sha256'] = marker_sha
report['marker'] = json.loads(marker_bytes)
report['control_lock'] = dict(zip(('inode','uid','gid','mode','size','mtime_ns'), (lambda st: (st.st_ino,st.st_uid,st.st_gid,oct(st.st_mode),st.st_size,st.st_mtime_ns))((STATE/'control.lock').lstat())))
report['writer_lock_released'] = True
report['passed'] = all(row['equals_preflight'] and row['equals_verified_checkpoint'] for row in [*report['tables'].values(), report['meta']])
report['seconds'] = time.monotonic() - started
fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(report, stream, indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
print(json.dumps({'passed': report['passed'], 'recipe_runtime_rows': report['recipe_runtime_rows'], 'input_bundle_hash': report['input_bundle_hash'], 'seconds': report['seconds']}))
if not report['passed']:
    raise ValueError('metadata differs from preflight/checkpoint')
