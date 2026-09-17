"""NON-ACCEPTANCE: read retained replay through one explicit reconstruction override."""
import fcntl
import hashlib
import importlib.util
import json
import socket
import sqlite3
import sys
import tempfile
import time
from contextlib import ExitStack, closing
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

SOURCE=Path('/nix/store/4c4q8rkiz6jqizcy93bid6cz8mfvp20b-source')
REPLAY=Path('/var/tmp/swingset-h16-proof-replay')
BUILD=Path('/var/tmp/swingset-h16-proof-build')
CANDIDATE=BUILD/'candidates/cand_cbbeeadd90634cc9'
BASELINE=BUILD/'candidates/cand_7f8cf9bcbf7e4a60'
EVIDENCE=Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-event-preservation-2026-09-16')
OVERRIDE=EVIDENCE/'closure_rows.diagnostic.py'
OUTPUT=Path('/var/tmp/swingset-h16-event-preservation-diagnostic.json')
SPOOL=Path('/var/tmp/swingset-h16-event-preservation-diagnostic')
SOURCE_HASH='71dc14101ca19880f3addfa2dc0073dbe50bce0e4e2be27d7bc91811b6d1e338'
OVERRIDE_HASH='982f8b4eb1bdd96ad8473882b312242ec4a45b3cf0c68bc4a0426de2683798e7'
MANIFEST_HASH='cf0deef44cc7a71099fea643431b5f899e0e2f42b79f2ec8435074e4ed7c0c08'
PRIVATE_HASH='545008d8578294c60d3a8c3343335f9ec3a333093afe78b194c4defddda62769'

def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()

def require(value, reason):
    if not value: raise ValueError(reason)

def forbidden(*args, **kwargs):
    raise RuntimeError('Network forbidden in retained-state diagnostic')

def stamps():
    return {str(p):{'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for root in (REPLAY,BUILD) for p in (root/'state.sqlite',root/'state.sqlite-wal') if p.exists()}

def readonly(path):
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')
    return conn

socket.socket.connect=forbidden
socket.socket.connect_ex=forbidden
started=time.monotonic()
report={'format':'h16-event-preservation-reconstruction-diagnostic-v1','at':datetime.now(UTC).isoformat(),'diagnostic_only':True,'release_acceptance':False,'production_mutated':False,'network_requests':0,'passed':False,'base_source':str(SOURCE),'source_receipt_sha256':SOURCE_HASH,'override_module':'swingset.build.closure_rows','override_path':str(OVERRIDE),'override_sha256':OVERRIDE_HASH,'candidate':str(CANDIDATE),'candidate_manifest_sha256':MANIFEST_HASH,'private_closure_digest':PRIVATE_HASH}
try:
    require(not OUTPUT.exists() and not SPOOL.exists(),'Diagnostic output must be new')
    require(sha(SOURCE/'h16-source.json')==SOURCE_HASH,'Frozen source differs')
    require(sha(OVERRIDE)==OVERRIDE_HASH,'Override changed')
    require(sha(CANDIDATE/'_meta/manifest.json')==MANIFEST_HASH,'Candidate changed')
    require(Path('/var/lib/swingset/operator-hold').is_file(),'Production hold absent')
    public=json.loads((CANDIDATE/'_meta/manifest.json').read_bytes())['release_policy']['closure']
    require(public['private_digest']==PRIVATE_HASH,'Closure commitment differs')
    spec=importlib.util.spec_from_file_location('swingset.build.closure_rows',OVERRIDE)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    from swingset.build import closure
    from swingset.build.closure_manifest import digest
    require(Path(closure.__file__).resolve().is_relative_to(SOURCE),'Unexpected closure validator source')
    with ExitStack() as stack:
        for root in (REPLAY,BUILD):
            lock=stack.enter_context((root/'state.lock').open('rb'))
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=stamps()
        build=stack.enter_context(closing(readonly(BUILD/'state.sqlite')))
        private=closure.hydrate(build,public)
        require(private['digest']==PRIVATE_HASH,'Hydrated closure differs')
        selected=closure.ReleaseClosure(**{f.name:private[f.name] for f in fields(closure.ReleaseClosure)})
        require(selected.manifest()['digest']==PRIVATE_HASH,'Dataclass commitment differs')
        replay=stack.enter_context(closing(readonly(REPLAY/'state.sqlite')))
        report['replay_schema_version']=replay.execute('PRAGMA user_version').fetchone()[0]
        report['selected_generations']=len(selected.selected)
        report['selected_source_support']=len(selected.source_support)
        report['replay_file_stamps_before']=before
        SPOOL.mkdir(mode=0o700)
        import pyarrow.parquet as pq
        old_judges={row['judge_id']:(row['name_raw'],row['wsdc_id']) for file in (BASELINE/'data/judges').glob('*.parquet') for batch in pq.ParquetFile(file).iter_batches() for row in batch.to_pylist()}
        with module.reconstruct(replay,selected,directory=SPOOL,baseline=BASELINE) as reconstructed:
            report['row_counts']={table:n for table,n in reconstructed.connection.execute('SELECT table_name,count(*) FROM rows GROUP BY table_name')}
            current_judges={row['judge_id']:(row['name_raw'],row.get('wsdc_id')) for row in reconstructed.iter_table('judges')}
            missing=[{'judge_id':k,'baseline':v,'reconstructed':current_judges.get(k)} for k,v in old_judges.items() if current_judges.get(k)!=v]
            report['baseline_judges']=len(old_judges)
            report['reconstructed_judges']=len(current_judges)
            report['judge_differences']=missing[:20]
            report['judge_difference_count']=len(missing)
            report['all_baseline_judges_preserved']=not missing
            report['reconstruction_counts']=dict(reconstructed.counts)
            report['event_omission_count']=len(reconstructed.omissions)
            report['event_omission_examples']=reconstructed.omissions[:20]
            events={row['event_id']:row for row in reconstructed.iter_table('events')}
            occurrences={}
            for e in events.values(): occurrences.setdefault((e['series_id'],e['event_month']),[]).append(e['event_id'])
            mismatch=0
            retained=set()
            for row in reconstructed.iter_table('registry_placements'):
                if str(row['event_month'])<'2010-01': continue
                occurrence=(row['series_id'],str(row['event_month'])[:7])
                retained.add(occurrence)
                e=events.get(row.get('event_id'))
                mismatch+=e is None or (e['series_id'],e['event_month'])!=occurrence
            report['remaining_registry_mismatch_rows']=mismatch
            report['remaining_occurrence_mismatches']=sum(len(occurrences.get(key,[]))!=1 for key in retained)
            report['scope_limit']='Only closure_rows overridden; map/history/link generations remain old. This diagnostic cannot accept a release.'
        report['replay_file_stamps_after']=stamps()
        require(report['replay_file_stamps_after']==before,'Retained database/WAL changed')
        require(replay.total_changes==0 and build.total_changes==0,'Unexpected database changes')
        report['database_total_changes']=0
        require(sha(OVERRIDE)==OVERRIDE_HASH,'Override changed during diagnostic')
        require(sha(CANDIDATE/'_meta/manifest.json')==MANIFEST_HASH,'Candidate changed during diagnostic')
        report['passed']=report['all_baseline_judges_preserved']
except BaseException as error:
    report['error']={'type':type(error).__name__,'message':str(error)}
    raise
finally:
    report['elapsed_seconds']=time.monotonic()-started
    report['finished_at']=datetime.now(UTC).isoformat()
    OUTPUT.open('x').write(json.dumps(report,indent=2,sort_keys=True,default=str)+'\n')
    print(json.dumps({'output':str(OUTPUT),'passed':report['passed'],'diagnostic_only':True,'release_acceptance':False,'error':report.get('error')}),flush=True)
require(report['passed'],'Diagnostic did not preserve all baseline judges')
