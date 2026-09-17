"""Gate-bound production initialization of project/link generations only.

The prepare mode captures and accepts the exact rehearsed runtime bundle. The run mode uses
real bounded workers and existing retry deadlines. Neither mode parses, fetches,
builds, reconciles publication, publishes, or changes operator controls.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import signal
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

STATE = Path('/var/lib/swingset')
FORMAT = 'h16-production-initialization-v1'
SOURCE_TABLES = (
    'snapshots','observations','registry_verifications','registry_cursors',
    'cursors','hosts','host_budget','robots','robots_cache','archive_captures','archive_queries',
    'source_units','source_generations','admission_policies','admission_reviews','admission_decisions',
    'identity_decisions','identity_journal_acceptances','identity_reference_migrations',
    'history_acceptance','scheduler_requests','scheduler_watch_state','scheduler_parent_links',
)
CONTROL_TABLES = ('operator_pauses','control_state','control_events')


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def encoded(value: object) -> bytes:
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()+b'\n'


def readonly():
    conn=sqlite3.connect((STATE/'state.sqlite').as_uri()+'?mode=ro',uri=True,isolation_level=None)
    conn.row_factory=sqlite3.Row
    return conn


def require_schema14() -> None:
    # Check before the migrating runtime opener, under the existing writer lock.
    with closing(readonly()) as conn:
        if conn.execute('PRAGMA user_version').fetchone()[0]!=14:
            raise ValueError('initialization requires existing schema14; migration is forbidden')


def new_json(path: Path, value: object) -> None:
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    fd=os.open(path.parent,os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_json(path: Path, value: object) -> None:
    temporary=path.with_suffix(path.suffix+'.tmp')
    new_json(temporary,value)
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def output_path(path: Path, *, existing: bool=False) -> Path:
    root=STATE/'operations'
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.resolve()==root.resolve():
        raise ValueError('initialization receipts must remain in private state/operations')
    if any(parent.is_symlink() for parent in path.parents if parent!=STATE.parent):
        raise ValueError('receipt ancestors may not be symlinks')
    if path.exists() and not existing:
        raise ValueError('receipt output must be new')
    if path.with_suffix(path.suffix+'.tmp').exists() or path.with_suffix(path.suffix+'.tmp').is_symlink():
        raise ValueError('temporary receipt already exists')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    return path.resolve()


def table_digest(conn, table, *, exclude=()):
    from research.accept_h11 import query_digest
    columns=[str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")') if row[1] not in exclude]
    selected=','.join('"'+name+'"' for name in columns)
    return query_digest(conn,f'SELECT {selected} FROM "{table}" ORDER BY '+','.join(str(n+1) for n in range(len(columns))))


def protected(conn):
    from research.accept_h11 import query_digest
    tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {
        'tables':{name:table_digest(conn,name) for name in SOURCE_TABLES if name in tables},
        'watches_except_extract_cache':table_digest(conn,'watches',exclude=('extract_version',)),
        'schema':conn.execute('PRAGMA user_version').fetchone()[0],
        'schema_objects':query_digest(conn,'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'),
        'journal_meta':query_digest(conn,"SELECT key,value FROM meta WHERE key IN ('identity_journal_digest','identity_journal_generation') ORDER BY key"),
        'identity_revision':query_digest(conn,"SELECT name,value FROM revisions WHERE name='identity_decisions'"),
    }


def controls(conn):
    return {name:table_digest(conn,name) for name in CONTROL_TABLES}


def input_authority(conn):
    from research.accept_h11 import query_digest
    return {
        'accepted_inputs':table_digest(conn,'accepted_inputs'),
        'bundle':query_digest(conn,"SELECT key,value FROM meta WHERE key='input_bundle_hash'"),
    }


def check_input_authority(conn,marker):
    if input_authority(conn)!=marker['prepared_input_authority']:
        raise ValueError('accepted input authority differs from the rehearsed preparation')


def parse_tokens(conn):
    from research.accept_h11 import query_digest
    return {table:query_digest(conn,f"SELECT * FROM {table} WHERE stage='parse' ORDER BY 1,2,3") for table in ('pending_work','work_generations','work_attempts')}


def check_idle(conn, *, allow_abandoned: bool):
    if conn.execute("SELECT 1 FROM execution_admissions WHERE state!='settled' AND (action_kind NOT IN ('project','link') OR state!='active')").fetchone():
        raise ValueError('non-derivation or uncertain admission requires coordinator reconciliation')
    if conn.execute("SELECT 1 FROM work_attempts WHERE outcome='running' AND stage NOT IN ('project','link')").fetchone():
        raise ValueError('non-derivation attempt requires coordinator reconciliation')
    if not allow_abandoned and (conn.execute("SELECT 1 FROM execution_admissions WHERE state!='settled'").fetchone() or conn.execute("SELECT 1 FROM work_attempts WHERE outcome='running'").fetchone()):
        raise ValueError('first preparation requires settled existing execution')


def gate_inputs(path: Path):
    from research import accept_h16
    from research.accept_h11 import verify_files
    from research.replay_derivations import verify_runtime
    from swingset.state import db as state_db
    gate=json.loads(path.read_bytes())
    if gate.get('format')!='h16-production-initialization-gate-v1' or gate.get('state')!=str(STATE) or gate.get('stages')!=['project','link'] or gate.get('capture_accept_authorized') is not True:
        raise ValueError('explicit production project/link and exact input acceptance gate required')
    if gate.get('driver_sha256')!=sha(Path(__file__)):
        raise ValueError('production driver differs from independently reviewed hash')
    verify_files(path.parent,gate['evidence_files'])
    source=Path(gate['source']).resolve(strict=True)
    verify_runtime(source,gate['source_receipt_sha256'])
    if state_db.SCHEMA_VERSION!=14 or Path(accept_h16.__file__).resolve()!=source/'research/accept_h16.py':
        raise ValueError('only the exact reviewed schema14 helpers may run')
    if any(name not in gate['evidence_files'] for name in gate['receipts'].values()):
        raise ValueError('every prerequisite receipt must be separately hashed')
    evidence={key:json.loads((path.parent/name).read_bytes()) for key,name in gate['receipts'].items()}
    acceptance=evidence['production_acceptance']
    if not acceptance.get('passed') or not acceptance.get('executed') or acceptance.get('migrated') or acceptance.get('service_executed') or acceptance.get('network_requests')!=0 or acceptance.get('before')!=acceptance.get('after'):
        raise ValueError('successful read-only H16 production acceptance required')
    if acceptance.get('gate',{}).get('source_receipt_sha256')!=gate['source_receipt_sha256']:
        raise ValueError('production acceptance belongs to another source pin')
    replay=evidence['scratch_replay']
    if replay.get('source_receipt_sha256')!=gate['source_receipt_sha256'] or replay.get('status')!='current' or replay.get('unfinished_by_scope') or replay.get('network_requests')!=0 or replay.get('parse_executed') is not False:
        raise ValueError('successful same-pin full project/link rehearsal required')
    if replay.get('input_bundle_hash')!=gate['input_bundle_hash']:
        raise ValueError('replay belongs to another accepted input bundle')
    build,audit=evidence['scratch_build'],evidence['scratch_audit']
    if build.get('replay_receipt_sha256')!=sha(path.parent/gate['receipts']['scratch_replay']):
        raise ValueError('build does not descend from the supplied current replay receipt')
    if not build.get('passed') or build.get('source')!=str(source) or not build.get('semantic_publication_preflight') or build.get('network_requests')!=0 or build.get('published') is not False:
        raise ValueError('successful same-pin ordinary build rehearsal required')
    if not audit.get('passed') or not audit.get('checks') or not all(value is True for value in audit['checks'].values()) or audit.get('candidate')!=build['candidate'] or audit.get('network_requests')!=0:
        raise ValueError('all substantive rehearsal candidate audits must pass')
    if audit.get('manifest_hash')!=build['manifest_hash'] or audit.get('candidate_id')!=build['candidate_id']:
        raise ValueError('audit does not bind the exact built candidate artifact')
    from swingset.build.closure import validate
    from swingset.build.generations import completed
    from swingset.publish.safety import verify_candidate_files
    candidate=Path(build['candidate']).resolve(strict=True)
    scratch=Path(build['state']).resolve(strict=True)
    if build['candidate_id']!=candidate.name or audit.get('state')!=str(scratch):
        raise ValueError('audit state or candidate ID differs from actual rehearsal paths')
    if scratch.is_relative_to(STATE) or not candidate.is_relative_to(scratch/'candidates'):
        raise ValueError('rehearsal evidence must belong to independent scratch')
    verify_candidate_files(candidate)
    built=json.loads((candidate/'BUILT').read_bytes())
    manifest=json.loads((candidate/'_meta/manifest.json').read_bytes())
    if built['manifest_hash']!=build['manifest_hash'] or manifest['release_policy']['input_bundle_hash']!=gate['input_bundle_hash'] or manifest['release_policy']['mode']!='closure':
        raise ValueError('rehearsal candidate does not bind the approved bundle and artifact')
    with closing(sqlite3.connect((scratch/'state.sqlite').as_uri()+'?mode=ro',uri=True)) as checked:
        checked.row_factory=sqlite3.Row
        checked.execute('BEGIN')
        validate(checked,manifest['release_policy']['closure'])
        if not completed(checked,candidate.name,built['manifest_hash']):
            raise ValueError('rehearsal files have no committed build generation')
        gate['_rehearsed_input_authority']=input_authority(checked)
    return gate,source


def hold_and_baseline(gate, gate_path):
    from research.accept_h11 import system_hold
    from research.accept_h16 import baseline
    from swingset.publish.service import pending_candidates
    if STATE.resolve()!=STATE or (STATE/'RESTORE_PENDING').exists():
        raise ValueError('unexpected state locator or unfinished restore')
    if pending_candidates(STATE):
        raise ValueError('publication intent requires separate reconciliation')
    hold=system_hold(STATE)
    preflight=json.loads((gate_path.parent/gate['receipts']['preflight_gate']).read_bytes())
    return {'hold':hold,'baseline':baseline(STATE,preflight)}


def capture_bundle(gate,source):
    from swingset.schedule.cycle import versions
    from swingset.state.inputs import capture
    bundle=capture(Path(gate['config']),Path(gate['overrides']),STATE,versions())
    if bundle.digest!=gate['input_bundle_hash']:
        raise ValueError('configuration/runtime no longer reproduces the rehearsed bundle')
    return bundle


def check_cache(conn,marker):
    from research.accept_h11 import query_digest
    if marker['extract_cache_invalidated']:
        if conn.execute('SELECT 1 FROM watches WHERE extract_version IS NOT NULL LIMIT 1').fetchone():
            raise ValueError('source extractor cache advanced outside this initialization')
    elif query_digest(conn,'SELECT watch_id,extract_version FROM watches ORDER BY watch_id')!=marker['watch_extract_versions']:
        raise ValueError('watch extractor cache changed')


def prepare(gate,source,gate_path,marker_path):
    from research import accept_h16
    from research.accept_h11 import query_digest
    from swingset.clock import SystemClock
    from swingset.state.db import open_database
    from swingset.state.inputs import accept
    from swingset.state.recipes import captured_recipe_inputs
    if marker_path.exists():
        marker=json.loads(marker_path.read_bytes())
        if marker.get('gate_sha256')!=sha(gate_path) or marker.get('driver_sha256')!=sha(Path(__file__)) or marker.get('format')!=FORMAT or marker.get('state')!=str(STATE) or marker.get('input_bundle_hash')!=gate['input_bundle_hash']:
            raise ValueError('existing preparation marker belongs to another gate/driver')
    else:
        before=accept_h16.preflight(STATE,source,gate_path.parent/gate['receipts']['preflight_gate'],gate['source_receipt_sha256'])
        with closing(readonly()) as conn:
            conn.execute('BEGIN')
            check_idle(conn,allow_abandoned=False)
            prior_bundle=conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0]
            basis=protected(conn)
            control_basis=controls(conn)
            prior_recipe_row=conn.execute("SELECT digest FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'").fetchone()
            prior_recipe=None if prior_recipe_row is None else prior_recipe_row[0]
            cache=query_digest(conn,'SELECT watch_id,extract_version FROM watches ORDER BY watch_id')
        bundle=capture_bundle(gate,source)
        old_manifest=json.loads((STATE/'inputs'/prior_bundle/'manifest.json').read_bytes())
        def policies(values):
            return {name:value for name,value in values.items() if not name.startswith(('runtime/','recipes/')) and name!='versions.json'}
        if policies(old_manifest)!=policies(bundle.file_hashes):
            raise ValueError('initialization may change runtime identity, not source/identity/scheduling policy bytes')
        recipe=captured_recipe_inputs(bundle.files,history_start=bundle.config.history_start.isoformat())['recipe/runtime']
        marker={'format':FORMAT,'state':str(STATE),'source':str(source),'source_receipt_sha256':gate['source_receipt_sha256'],'gate_sha256':sha(gate_path),'driver_sha256':sha(Path(__file__)),'created_at':datetime.now(UTC).isoformat(),'previous_input_bundle_hash':prior_bundle,'input_bundle_hash':bundle.digest,'preflight_baseline':before['baseline'],'checkpoint':before['gate']['checkpoint'],'checkpoint_manifest_sha256':before['gate']['checkpoint_manifest_sha256'],'protected':basis,'controls':control_basis,'extract_cache_invalidated':prior_recipe!=recipe,'watch_extract_versions':cache}
        marker['prepared_input_authority']=gate['_rehearsed_input_authority']
        new_json(marker_path,marker) # Intent before commit makes a crash resumable from SQLite truth.
    bundle=capture_bundle(gate,source)
    with open_database(STATE,lock=False) as db:
        actual=db.connection.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0]
        if actual not in {marker['previous_input_bundle_hash'],marker['input_bundle_hash']}:
            raise ValueError('another input acceptance superseded preparation')
        if protected(db.connection)!=marker['protected'] or controls(db.connection)!=marker['controls']:
            raise ValueError('protected source evidence or controls changed since preparation intent')
        from swingset.state.control_lock import control_lock
        with control_lock(STATE,timeout=60):
            with db.transaction():
                if controls(db.connection)!=marker['controls']:
                    raise ValueError('operator controls changed before input acceptance')
                if actual!=marker['input_bundle_hash']:
                    changed=accept(db,bundle,SystemClock())
                else:
                    changed=set()
                if protected(db.connection)!=marker['protected'] or controls(db.connection)!=marker['controls']:
                    raise ValueError('input acceptance changed protected source evidence or journal/controls')
                check_cache(db.connection,marker)
                check_input_authority(db.connection,marker)
        return {'status':'prepared','input_bundle_hash':bundle.digest,'previous_input_bundle_hash':marker['previous_input_bundle_hash'],'changed_inputs':sorted(changed),'extract_cache_invalidated':marker['extract_cache_invalidated'],'parse_execution_authorized':False}


def read_marker(path,gate,gate_path, *, depth=0):
    if depth>=32:
        raise ValueError('continuation chain exceeds bounded review depth')
    path=output_path(path,existing=True)
    marker=json.loads(path.read_bytes())
    if marker.get('format')!=FORMAT or marker.get('gate_sha256')!=sha(gate_path) or marker.get('driver_sha256')!=sha(Path(__file__)) or marker.get('state')!=str(STATE) or marker.get('input_bundle_hash')!=gate['input_bundle_hash']:
        raise ValueError('production initialization marker differs from reviewed invocation')
    if link:=marker.get('continuation'):
        previous=output_path(Path(link['previous_marker']),existing=True)
        review=output_path(Path(link['review']),existing=True)
        if sha(previous)!=link['previous_marker_sha256'] or sha(review)!=link['review_sha256']:
            raise ValueError('continuation predecessor or review evidence changed')
        prior=read_marker(previous,gate,gate_path,depth=depth+1)
        checked=json.loads(review.read_bytes())
        validate_continuation_review(checked,previous,prior,gate,gate_path)
        expected=dict(prior,controls=checked['controls'],continuation=link)
        if marker!=expected:
            raise ValueError('continuation changed protected marker authority')
    return marker


def validate_continuation_review(review,previous,marker,gate,gate_path):
    expected={'format':'h16-initialization-continuation-review-v1','state':str(STATE),
        'previous_marker':str(previous),'previous_marker_sha256':sha(previous),
        'gate_sha256':sha(gate_path),'driver_sha256':sha(Path(__file__)),
        'source_receipt_sha256':gate['source_receipt_sha256'],
        'input_bundle_hash':marker['input_bundle_hash'],'coordinator_reviewed':True}
    if any(review.get(key)!=value for key,value in expected.items()):
        raise ValueError('continuation review does not bind the original marker and gate')
    try:
        reviewed_at=datetime.fromisoformat(review['reviewed_at'])
        if reviewed_at.tzinfo is None or not str(review['reviewed_by']).strip():
            raise ValueError('missing reviewer/time')
    except (KeyError,TypeError,ValueError) as error:
        raise ValueError('continuation requires recorded coordinator review') from error
    if not isinstance(review.get('controls'),dict):
        raise ValueError('continuation review must contain exact current control digests')


def continue_initialization(gate,source,gate_path,previous_path,marker_path,review_path):
    # No input acceptance, source calls, control writes, or original-checkpoint
    # comparison: committed derivation outputs are legitimate continuation state.
    from swingset.state.control_lock import control_lock
    previous=output_path(previous_path,existing=True)
    review_path=output_path(review_path,existing=True)
    marker_path=output_path(marker_path)
    if len({previous,review_path,marker_path,gate_path.resolve()})!=4:
        raise ValueError('continuation evidence and new marker must be separate')
    marker=read_marker(previous,gate,gate_path)
    reviewed_sha=sha(review_path)
    review=json.loads(review_path.read_bytes())
    validate_continuation_review(review,previous,marker,gate,gate_path)
    capture_bundle(gate,source)
    held=hold_and_baseline(gate,gate_path)
    if held['baseline']!=marker['preflight_baseline']:
        raise ValueError('confirmed baseline changed since original preparation')
    with control_lock(STATE,timeout=60), closing(readonly()) as conn:
        conn.execute('BEGIN')
        check_idle(conn,allow_abandoned=True)
        if protected(conn)!=marker['protected']:
            raise ValueError('protected source evidence changed before continuation')
        check_input_authority(conn,marker)
        check_cache(conn,marker)
        if controls(conn)!=review['controls']:
            raise ValueError('controls differ from the reviewed continuation state')
        if sha(previous)!=review['previous_marker_sha256'] or sha(review_path)!=reviewed_sha:
            raise ValueError('original preparation marker changed during continuation')
        continued=dict(marker,controls=review['controls'],continuation={
            'previous_marker':str(previous),'previous_marker_sha256':sha(previous),
            'review':str(review_path),'review_sha256':sha(review_path)})
        new_json(marker_path,continued)
    return {'status':'continued','previous_marker_sha256':sha(previous),
        'continuation_review_sha256':sha(review_path),'controls_written':False,
        'input_acceptance_executed':False}


def initialize(gate,source,gate_path,marker_path,receipt,save,max_seconds,max_units):
    from research.replay_derivations import COHORTS, ReplayLimit, selection_window
    from swingset.clock import SystemClock
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one
    from swingset.state import derivations
    from swingset.state.attempts import eligible, recover_interrupted
    from swingset.state.controls import recover_admissions
    from swingset.state.db import open_database
    marker=read_marker(marker_path,gate,gate_path)
    bundle=capture_bundle(gate,source)
    clock=SystemClock()
    started=monotonic()
    deadline=started+max_seconds
    counts=Counter()
    attempted=set()
    with open_database(STATE,lock=False) as db:
        if db.schema_version!=14 or db.connection.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0]!=marker['input_bundle_hash']:
            raise ValueError('prepared schema or input authority changed')
        check_idle(db.connection,allow_abandoned=True)
        if protected(db.connection)!=marker['protected']:
            raise ValueError('protected source evidence changed before initialization')
        check_cache(db.connection,marker)
        check_input_authority(db.connection,marker)
        control_before=controls(db.connection)
        parse_before=parse_tokens(db.connection)
        if control_before!=marker['controls']:
            raise ValueError('operator controls changed; obtain a reviewed continuation gate')
        with db.transaction() as conn:
            receipt['recovered_admissions']=recover_admissions(conn,now=clock.now())
            receipt['interrupted_attempts']=recover_interrupted(db,now=clock.now())
        run_id=db.start_run(clock.now())
        receipt['run_id']=run_id
        receipt['status']='running'
        save()
        try:
            while monotonic()<deadline-50:
                progress=0
                receipt['sweeps']+=1
                for stage,kind in COHORTS:
                    with selection_window(db.connection,deadline):
                        units=[unit for unit in derivations.known_units(db.connection,stage) if unit.unit_kind==kind]
                    for unit in units:
                        if monotonic()>=deadline-50 or (max_units is not None and receipt['attempted']>=max_units):
                            receipt['status']='bounded_stop'
                            return
                        if controls(db.connection)!=control_before:
                            receipt['status']='operator_control_changed'
                            return
                        with selection_window(db.connection,deadline):
                            if derivations.current(db.connection,unit) or not derivations.ready(db.connection,unit):
                                continue
                            selected=derivations.desired(db.connection,unit)
                            key=(unit,selected.fingerprint)
                            if key in attempted or not eligible(db.connection,unit,now=clock.now(),fingerprint=selected.fingerprint):
                                continue
                        attempted.add(key)
                        receipt['attempted']+=1
                        at=monotonic()
                        outcome=derive_one(db,Archive(STATE),unit,bundle,clock,run_id)
                        receipt['worker_seconds']+=monotonic()-at
                        reason=outcome.reason or 'succeeded'
                        counts[reason]+=1
                        if not outcome.failed and outcome.reason is None:
                            receipt['completed']+=1
                            progress+=1
                        if receipt['attempted']%100==0:
                            hold_and_baseline(gate,gate_path)
                            receipt['outcomes']=dict(counts)
                            save()
                if not progress:
                    receipt['status']='no_runnable_progress'
                    break
            else:
                receipt['status']='bounded_stop'
            with selection_window(db.connection,deadline):
                receipt['unfinished_by_scope']=dict(Counter(unit.stage+'/'+unit.unit_kind for stage in ('project','link') for unit in derivations.pending_units(db.connection,stage)))
            if not receipt['unfinished_by_scope']:
                receipt['status']='current'
        except ReplayLimit:
            receipt['status']='bounded_stop'
        finally:
            receipt['outcomes']=dict(counts)
            receipt['protected_unchanged']=protected(db.connection)==marker['protected']
            receipt['parse_tokens_unchanged']=parse_tokens(db.connection)==parse_before
            receipt['controls_unchanged']=controls(db.connection)==control_before
            receipt['input_authority_unchanged']=input_authority(db.connection)==marker['prepared_input_authority']
            check_cache(db.connection,marker)
            receipt['generation_count']=db.connection.execute('SELECT count(*) FROM derivation_generations').fetchone()[0]
            receipt['final_hold']=hold_and_baseline(gate,gate_path)
            with db.transaction() as conn:
                conn.execute('UPDATE runs SET finished_at=? WHERE run_id=?',(clock.now().isoformat(),run_id))
            save()
            if not receipt['protected_unchanged'] or not receipt['parse_tokens_unchanged'] or not receipt['input_authority_unchanged']:
                raise ValueError('initializer changed protected source, parse, or accepted input state')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('prepare','continue','run'))
    for name in ('gate','marker','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--previous-marker',type=Path)
    parser.add_argument('--continuation-review',type=Path)
    parser.add_argument('--max-seconds',type=float,default=900)
    parser.add_argument('--max-units',type=int)
    args=parser.parse_args()
    if not math.isfinite(args.max_seconds) or not 90<=args.max_seconds<=3600 or (args.max_units is not None and args.max_units<1):
        raise ValueError('bounded invocation requires 90–3600 seconds and positive optional unit cap')
    if args.mode=='continue' and (args.previous_marker is None or args.continuation_review is None):
        raise ValueError('continue requires original marker and recorded coordinator review')
    if args.mode!='continue' and (args.previous_marker is not None or args.continuation_review is not None):
        raise ValueError('continuation arguments require continue mode')
    marker=output_path(args.marker,existing=args.mode!='continue' and (args.mode=='run' or args.marker.exists()))
    output=output_path(args.output)
    if len({marker,output,output.with_suffix(output.suffix+'.tmp'),args.gate.resolve()})!=4:
        raise ValueError('gate, immutable marker and per-invocation receipt must differ')
    gate,source=gate_inputs(args.gate)
    receipt={'format':'h16-production-initialization-receipt-v1','gate_sha256':sha(args.gate),'driver_sha256':sha(Path(__file__)),'source_receipt_sha256':gate['source_receipt_sha256'],'marker_sha256':sha(marker) if marker.exists() else None,'mode':args.mode,'started_at':datetime.now(UTC).isoformat(),'status':'preflight','attempted':0,'completed':0,'sweeps':0,'worker_seconds':0.0,'network_requests':0,'parse_executed':False,'build_executed':False,'published':False,'fairness_claimed':False}
    started=monotonic()
    new_json(output,receipt)
    def save():
        receipt['elapsed_seconds']=monotonic()-started
        replace_json(output,receipt)
    def interrupted(*_):
        raise KeyboardInterrupt('bounded initialization interrupted')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        with (STATE/'state.lock').open('r+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            require_schema14()
            receipt['initial_hold']=hold_and_baseline(gate,args.gate)
            if args.mode=='prepare':
                receipt.update(prepare(gate,source,args.gate,marker))
            elif args.mode=='continue':
                receipt.update(continue_initialization(gate,source,args.gate,args.previous_marker,marker,args.continuation_review))
            else:
                initialize(gate,source,args.gate,marker,receipt,save,args.max_seconds,args.max_units)
    except BaseException as error:
        receipt.update(status='interrupted',error={'type':type(error).__name__,'message':str(error)})
        raise
    finally:
        receipt['marker_sha256']=sha(marker) if marker.exists() else None
        receipt['finished_at']=datetime.now(UTC).isoformat()
        save()

if __name__=='__main__':
    main()
