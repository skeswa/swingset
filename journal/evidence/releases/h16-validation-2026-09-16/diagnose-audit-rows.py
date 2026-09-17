"""Bounded read-only comparison of failed scratch audit rows."""

import json
import signal
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import duckdb

ROOT = Path('/var/tmp/swingset-h16-proof-build')
OLD = ROOT / 'candidates/cand_7f8cf9bcbf7e4a60'
NEW = ROOT / 'candidates/cand_cbbeeadd90634cc9'
OUT = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-validation-2026-09-16/audit-row-diagnostic.json')


def rows(db, sql, values=None):
    cursor = db.execute(sql, values or [])
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def expired(*_):
    raise TimeoutError('Diagnostic exceeded180 seconds')


signal.signal(signal.SIGALRM, expired)
signal.alarm(180)
report = {'at': datetime.now(UTC).isoformat(), 'read_only': True, 'passed': False}
start = time.monotonic()
try:
    with tempfile.TemporaryDirectory(prefix='h16-audit-rows-', dir='/var/tmp') as temporary, closing(duckdb.connect()) as db, closing(sqlite3.connect((ROOT / 'state.sqlite').as_uri() + '?mode=ro', uri=True)) as state:
        state.execute('PRAGMA query_only=ON')
        state.execute('BEGIN')
        db.execute("SET memory_limit='512MB'")
        db.execute('SET threads=1')
        db.execute("SET temp_directory='" + temporary + "'")
        for prefix, candidate in (('old', OLD), ('new', NEW)):
            for table in ('events', 'judges', 'registry_placements'):
                db.execute(f"CREATE VIEW {prefix}_{table} AS SELECT * FROM read_parquet('{candidate}/data/{table}/*.parquet',hive_partitioning=false)")
        db.execute("CREATE TEMP VIEW missing_judges AS SELECT o.* FROM old_judges o LEFT JOIN new_judges n USING(judge_id) WHERE o.name_raw IS NOT NULL AND o.name_raw<>'' AND (n.judge_id IS NULL OR n.name_raw IS DISTINCT FROM o.name_raw OR (o.wsdc_id IS NULL AND n.wsdc_id IS NOT NULL))")
        report['judge_totals'] = rows(db, "SELECT 'old' side,count(*) total,count(*) FILTER(WHERE name_raw IS NOT NULL AND name_raw<>'') named FROM old_judges UNION ALL SELECT 'new',count(*),count(*) FILTER(WHERE name_raw IS NOT NULL AND name_raw<>'') FROM new_judges")
        report['missing_judges_by_event'] = rows(db, 'SELECT event_id,source,count(*) n FROM missing_judges GROUP BY 1,2 ORDER BY n DESC,event_id LIMIT30'.replace('LIMIT30', 'LIMIT 30'))
        report['missing_judges_examples'] = rows(db, 'SELECT judge_id,event_id,name_raw,wsdc_id,source,snapshot_id FROM missing_judges ORDER BY judge_id LIMIT 8')
        report['same_name_snapshot_different_id'] = rows(db, 'SELECT o.judge_id old_id,n.judge_id new_id,o.event_id old_event,n.event_id new_event,o.name_raw,o.snapshot_id FROM missing_judges o JOIN new_judges n ON n.name_raw=o.name_raw AND n.snapshot_id=o.snapshot_id ORDER BY o.judge_id LIMIT 15')
        report['state_totals'] = {table: state.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ('events', 'judges', 'registry_placements', 'source_event_map')}
        occurrences = state.execute("SELECT DISTINCT series_id,substr(event_month,1,7) FROM registry_placements WHERE event_month>='2010-01'").fetchall()
        db.execute('CREATE TEMP TABLE occurrences(series_id VARCHAR,event_month VARCHAR)')
        db.executemany('INSERT INTO occurrences VALUES (?,?)', occurrences)
        report['occurrence_mismatches'] = rows(db, 'SELECT r.series_id,r.event_month,count(e.event_id) n,list(e.event_id) events FROM occurrences r LEFT JOIN new_events e ON e.series_id=r.series_id AND e.event_month=r.event_month GROUP BY 1,2 HAVING n<>1 ORDER BY r.event_month,r.series_id LIMIT 30')
        report['occurrence_mismatch_count'] = rows(db, 'SELECT count(*) n FROM (SELECT r.series_id,r.event_month,count(e.event_id) n FROM occurrences r LEFT JOIN new_events e ON e.series_id=r.series_id AND e.event_month=r.event_month GROUP BY 1,2 HAVING n<>1)')
        report['registry_mismatch_count'] = rows(db, "SELECT count(*) n FROM new_registry_placements r LEFT JOIN new_events e USING(event_id) WHERE CAST(r.event_month AS VARCHAR)>='2010-01' AND (e.event_id IS NULL OR e.series_id IS DISTINCT FROM r.series_id OR e.event_month IS DISTINCT FROM substr(CAST(r.event_month AS VARCHAR),1,7))")
        report['registry_mismatch_examples'] = rows(db, "SELECT DISTINCT r.event_id,r.series_id,r.event_month,e.series_id event_series,e.event_month canonical_month,e.name FROM new_registry_placements r LEFT JOIN new_events e USING(event_id) WHERE CAST(r.event_month AS VARCHAR)>='2010-01' AND (e.event_id IS NULL OR e.series_id IS DISTINCT FROM r.series_id OR e.event_month IS DISTINCT FROM substr(CAST(r.event_month AS VARCHAR),1,7)) ORDER BY r.event_month,r.series_id LIMIT 20")
        event_ids = sorted({row['event_id'] for row in report['missing_judges_examples']} | {row['event_id'] for row in report['registry_mismatch_examples'] if row['event_id']})[:12]
        report['events'] = []
        for event_id in event_ids:
            evidence = {'event_id': event_id}
            for prefix in ('old', 'new'):
                evidence[prefix] = rows(db, f'SELECT event_id,series_id,event_month,name,year,source,snapshot_id FROM {prefix}_events WHERE event_id=?', [event_id])
                evidence[prefix + '_judges'] = rows(db, f'SELECT count(*) n FROM {prefix}_judges WHERE event_id=?', [event_id])
            evidence['state'] = rows(state, 'SELECT event_id,series_id,event_month,name,year,source,snapshot_id FROM events WHERE event_id=?', [event_id])
            evidence['state_judges'] = rows(state, 'SELECT count(*) n FROM judges WHERE event_id=?', [event_id])
            evidence['mapping'] = rows(state, 'SELECT * FROM source_event_map WHERE event_id=? LIMIT 10', [event_id])
            evidence['scope'] = rows(state, "SELECT * FROM derivation_scopes WHERE unit_id=? AND stage IN ('project','link')", [event_id])
            for generation in evidence['scope']:
                generation['output_tables'] = rows(state, 'SELECT table_name,count(*) n FROM derivation_rows WHERE generation_id=? GROUP BY table_name', [generation['materialized_generation_id']])
                generation['event_rows'] = rows(state, "SELECT payload_json FROM derivation_rows WHERE generation_id=? AND table_name='events' LIMIT 3", [generation['materialized_generation_id']])
            report['events'].append(evidence)
        snapshots = sorted({row['snapshot_id'] for row in report['missing_judges_examples']})[:5]
        report['missing_judge_observations'] = [dict(snapshot_id=snapshot, rows=rows(state, 'SELECT observation_id,kind,scope_kind,scope_id,extract_version,parser_version FROM observations WHERE snapshot_id=? LIMIT 8', [snapshot])) for snapshot in snapshots]
        report['connection_total_changes'] = state.total_changes
        report['passed'] = True
except BaseException as error:
    report['error'] = {'type': type(error).__name__, 'message': str(error)}
    raise
finally:
    signal.alarm(0)
    report['elapsed_seconds'] = time.monotonic() - start
    with OUT.open('x') as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True, default=str) + '\n')
    print(json.dumps({'output': str(OUT), 'passed': report['passed'], 'error': report.get('error')}), flush=True)
