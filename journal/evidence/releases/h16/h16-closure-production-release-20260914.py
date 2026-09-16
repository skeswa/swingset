"""Reviewed H16 production build/publication; no input acceptance or derivation."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import pwd
import resource
import signal
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

STATE = Path('/var/lib/swingset')
SOURCE = Path('/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source')
SOURCE_RECEIPT = 'f8f24c2d8b839bbfcd25bf87e551e9f2777d1236f89ed84da16c3c07d4d75b9f'
BUNDLE = '558bb5f4e88b47fe80a691254d3b21ac9e491296e80bb18f4599b64a88bfe9c0'
FORMAT = 'h16-production-release-v1'
AUDIT_CHECKS = frozenset({
    'ordinary_closure', 'compact_private_proof_only', 'nonempty_structural_closure',
    'unique_keys', 'no_orphans',
    'durable_build_completion', 'baseline_named_judge_count',
    'all_baseline_named_judges_preserved', 'baseline_entry_id_count',
    'one_event_per_retained_occurrence', 'registry_points_to_matching_occurrence',
    'all_17_years_disclosed', 'no_unreviewed_year_acceptance',
    'coverage_cutoff_matches', 'honest_discovery_denominator',
    'coverage_denominator_chain', 'coverage_counts_match_emitted_facts',
    'baseline_66_coverage_year_rows', 'all_legacy_coverage_year_transitions_accounted',
    'entries_no_new_or_replaced_default_ids', 'judges_no_new_or_replaced_default_ids',
    'accepted_identities_match_retained_support', 'public_identity_assertions_respect_decisions',
    'identity_state_journal_matches_release', 'default_ids_have_accepted_assertions',
    'accepted_assertions_have_default_ids', 'public_assertions_have_structural_subjects',
    'identity_baseline_commit_matches', 'identity_expansions_remain_withheld',
    'unique_public_assertion_per_subject', 'public_assertion_acceptance_fields_match_release',
    'accepted_assertions_have_confirmation_method_and_references',
    'placement_leader_identity_and_points_supported', 'placement_follower_identity_and_points_supported',
    'placement_identity_flags_supported', 'named_null_id_judges_preserved_where_source_persists',
})


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def ref(path, record):
    target = Path(record['path'])
    if not target.is_absolute():
        target = path.parent / target
    require(not target.is_symlink() and target.is_file(), 'evidence must be a regular existing file')
    require(sha(target) == record['sha256'], 'reviewed evidence bytes changed: ' + str(target))
    return target.resolve()


def read_ref(path, record):
    target = ref(path, record)
    return target, json.loads(target.read_bytes())


def gate_header(gate, mode):
    require(gate.get('format') == FORMAT + '-gate', 'release gate format differs')
    require(gate.get('mode') == mode and gate.get('authorized') is True, 'exact operation authority required')
    require(gate.get('state') == str(STATE), 'production state is fixed')
    require(gate.get('source') == str(SOURCE) and gate.get('source_receipt_sha256') == SOURCE_RECEIPT,
            'release source differs from reviewed schema14 pin')
    require(gate.get('input_bundle_hash') == BUNDLE, 'release bundle differs')
    require(gate.get('driver_sha256') == sha(Path(__file__)), 'release driver changed')
    require(bool(str(gate.get('reviewed_by', '')).strip()), 'reviewer attribution missing')
    require(datetime.fromisoformat(gate['reviewed_at']).tzinfo is not None, 'review time must be aware')


def initialization_receipt(receipt, *, gate_path, marker_path, initializer):
    expected = {
        'format': 'h16-production-initialization-receipt-v1', 'mode': 'run', 'status': 'current',
        'gate_sha256': sha(gate_path), 'marker_sha256': sha(marker_path),
        'driver_sha256': sha(initializer), 'source_receipt_sha256': SOURCE_RECEIPT,
        'network_requests': 0, 'parse_executed': False, 'build_executed': False, 'published': False,
        'protected_unchanged': True, 'parse_tokens_unchanged': True,
        'controls_unchanged': True, 'input_authority_unchanged': True,
        'unfinished_by_scope': {},
    }
    require(all(receipt.get(k) == v for k, v in expected.items()) and bool(receipt.get('finished_at')),
            'actual current initialization receipt required')


def load_gate(path, mode):
    gate = json.loads(path.read_bytes())
    gate_header(gate, mode)
    initializer = ref(path, gate['initializer_driver'])
    spec = importlib.util.spec_from_file_location('h16_reviewed_initializer', initializer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    init_gate_path = ref(path, gate['initialization_gate'])
    init_gate, source = module.gate_inputs(init_gate_path)
    require(source == SOURCE and init_gate['input_bundle_hash'] == BUNDLE, 'initializer uses another source/bundle')
    marker_path = ref(path, gate['initialization_marker'])
    marker = module.read_marker(marker_path, init_gate, init_gate_path)
    current_path, current = read_ref(path, gate['initialization_receipt'])
    initialization_receipt(current, gate_path=init_gate_path, marker_path=marker_path, initializer=initializer)
    require(current['final_hold']['baseline'] == marker['preflight_baseline'], 'initialization baseline mismatch')
    return gate, module, init_gate, init_gate_path, marker


def validate_audit(gate_path, gate, build):
    _, audit = read_ref(gate_path, gate['audit'])
    ref(gate_path, gate['audit_driver'])
    expected = {'state': str(STATE), 'candidate': build['candidate'],
                'candidate_id': build['candidate_id'], 'manifest_hash': build['manifest_hash'],
                'baseline_commit': build['expected_parent'], 'network_requests': 0, 'passed': True}
    require(all(audit.get(k) == v for k, v in expected.items()), 'audit does not bind exact production build')
    checks = audit.get('checks', {})
    from swingset.build.schema import SCHEMAS
    required = AUDIT_CHECKS | {'row_count_' + table for table in SCHEMAS}
    require(required <= set(checks) and all(v is True for v in checks.values()),
            'substantive audit incomplete or failing')
    return audit


def reviewed_build(path, gate):
    build_gate_path, build_gate = read_ref(path, gate['build_gate'])
    gate_header(build_gate, 'build')
    for key in ('initializer_driver', 'initialization_gate', 'initialization_marker', 'initialization_receipt'):
        require(ref(path, gate[key]) == ref(build_gate_path, build_gate[key]), 'build and publish initialization chain differs')
    _, build = read_ref(path, gate['build_receipt'])
    expected = {'format': FORMAT, 'mode': 'build', 'state': str(STATE), 'source': str(SOURCE),
                'source_receipt_sha256': SOURCE_RECEIPT, 'input_bundle_hash': BUNDLE,
                'gate_sha256': sha(build_gate_path), 'driver_sha256': sha(Path(__file__)),
                'status': 'built', 'passed': True, 'published': False, 'network_requests': 0,
                'protected_unchanged': True, 'controls_unchanged': True,
                'input_authority_unchanged': True, 'parse_tokens_unchanged': True,
                'revisions_unchanged': True, 'semantic_publication_preflight': True}
    require(all(build.get(k) == v for k,v in expected.items()) and bool(build.get('finished_at')),
            'successful actual production build receipt required')
    validate_audit(path, gate, build)
    return build


def check_pending(pending, candidate, resume):
    paths = [Path(p).resolve() for p in pending]
    require(not paths or (resume and paths == [candidate]), 'unreviewed publication intent or missing resume flag')


def check_execution(conn, candidate=None, resume=False):
    require(not conn.execute("SELECT 1 FROM work_attempts WHERE outcome='running'").fetchone(),
            'unfinished work attempt requires normal coordinator recovery')
    for row in conn.execute("SELECT action_kind,candidate_id FROM execution_admissions WHERE state!='settled'"):
        require(resume and candidate is not None and row[0] in {'publish','publication'} and row[1] == candidate.name,
                'unrelated active/uncertain execution requires coordinator recovery')


def authority(conn, helper, marker):
    require(helper.protected(conn) == marker['protected'], 'protected evidence changed since initialization')
    helper.check_input_authority(conn, marker)
    helper.check_cache(conn, marker)
    require(helper.controls(conn) == marker['controls'], 'operator controls changed since reviewed initialization')
    return {'protected': marker['protected'], 'inputs': helper.input_authority(conn),
            'controls': helper.controls(conn), 'parse': helper.parse_tokens(conn),
            'revisions': helper.table_digest(conn, 'revisions')}


def candidate_files(candidate, build):
    from swingset.publish.safety import verify_candidate_files
    require(candidate.parent == STATE/'candidates' and candidate.resolve()==candidate and not candidate.is_symlink(), 'candidate must be direct production artifact')
    verify_candidate_files(candidate)
    built = json.loads((candidate/'BUILT').read_bytes())
    manifest = json.loads((candidate/'_meta/manifest.json').read_bytes())
    require(candidate.name == build['candidate_id'] and sha(candidate/'_meta/manifest.json') == build['manifest_hash']
            and built['manifest_hash'] == build['manifest_hash'], 'reviewed candidate identity differs')
    require(built.get('baseline_commit') == build['expected_parent'] and built.get('expected_parent') == build['expected_parent'],
            'built parent differs from reviewed baseline')
    require(manifest['release_policy']['mode'] == 'closure' and manifest['release_policy']['input_bundle_hash'] == BUNDLE,
            'ordinary closure with exact initialized bundle required')
    return built, manifest


def baseline_before(helper, init_gate, init_path, marker, candidate=None, resume=False):
    from research.accept_h11 import system_hold
    from swingset.publish.service import pending_candidates
    require(STATE.resolve() == STATE and not (STATE/'RESTORE_PENDING').exists(), 'state/restore locator unsafe')
    system_hold(STATE)
    check_pending(pending_candidates(STATE), candidate, resume)
    baseline = (STATE/'baseline').resolve(strict=True)
    if resume and candidate is not None and baseline == candidate:
        require((candidate/'PUBLISHED').is_file(), 'promoted candidate lacks publication receipt')
        return 'candidate'
    actual = helper.hold_and_baseline(init_gate, init_path) if not pending_candidates(STATE) else None
    if actual is None:
        from research.accept_h16 import baseline as verify_baseline
        preflight = json.loads((init_path.parent/init_gate['receipts']['preflight_gate']).read_bytes())
        actual = {'baseline': verify_baseline(STATE, preflight)}
    require(actual['baseline'] == marker['preflight_baseline'], 'initialized public baseline changed')
    return 'parent'


def build(db, helper, init_gate, marker, report):
    from swingset.build.service import build_release
    from swingset.clock import SystemClock
    from swingset.publish.safety import _validate_candidate
    from swingset.state.controls import ActionScope, operation
    from swingset.state.work import unfinished_units
    require(not any(unfinished_units(db.connection, stages=('project','link'))), 'production derivations are not current')
    bundle = helper.capture_bundle(init_gate, SOURCE)  # Capture only, never accept.
    clock = SystemClock()
    run = db.start_run(clock.now())
    report['run_id'] = run
    try:
        with operation(db, action_id='h16-build-'+uuid.uuid4().hex, action_kind='build',
                       scope=ActionScope(all_sources=True, all_kinds=True), clock=clock, run_id=run):
            result = build_release(db, bundle, clock, run, remote=None, correction_only=False)
        report.update(candidate=str(result.path), candidate_id=result.candidate_id, manifest_hash=result.manifest_hash,
                      expected_parent=marker['preflight_baseline']['commit'])
        built, manifest = candidate_files(result.path, report)
        _validate_candidate(db.connection, STATE, result.path, built, manifest)
        report.update(status='built', semantic_publication_preflight=True)
    finally:
        with db.transaction() as conn:
            conn.execute('UPDATE runs SET finished_at=? WHERE run_id=?', (clock.now().isoformat(),run))


def publish_reviewed(db, candidate, reviewed, *, resume, promoted, report):
    from swingset.clock import SystemClock
    from swingset.publish.huggingface import HuggingFaceHub
    from swingset.publish.safety import _validate_candidate
    from swingset.publish.service import _verify_remote, publish, reconcile
    from swingset.state.controls import recover_admissions
    built, manifest = candidate_files(candidate, reviewed)
    if not promoted:
        _validate_candidate(db.connection, STATE, candidate, built, manifest)
    hub = HuggingFaceHub('skeswa/swingset', token=os.environ['HF_TOKEN'])
    if promoted:
        receipt = json.loads((candidate/'PUBLISHED').read_bytes())
        require(hub.head() == receipt['commit'], 'remote head differs from already promoted reviewed candidate')
        _verify_remote(candidate, hub.inspect(receipt['commit']), built)
    else:
        # A crash may leave an active reservation. Exclusive state.lock proves
        # the old local writer is gone; only this candidate's publication is allowed here.
        if resume:
            with db.transaction() as conn:
                report['recovered_admissions'] = recover_admissions(conn, now=SystemClock().now())
            recovered = reconcile(STATE, hub, dry_run=True)
            report['reconciliation'] = asdict(recovered)
            if recovered.state in {'promoted','recovered'}:
                report.update(status=recovered.state, published=True, publication_receipt=json.loads((candidate/'PUBLISHED').read_bytes()))
                return
            require(recovered.state in {'none','pending'}, 'publication remains draining; do not submit again')
        require(hub.head() == reviewed['expected_parent'], 'remote parent differs from reviewed build')
    result = publish(STATE, candidate, hub)
    require(result.candidate_id in {None,candidate.name}, 'publisher resolved a different candidate')
    report.update(status=result.state, publication_result=asdict(result), published=(candidate/'PUBLISHED').is_file())
    if report['published']:
        report['publication_receipt'] = json.loads((candidate/'PUBLISHED').read_bytes())


def publication_complete(report, candidate):
    # A normal 'unchanged' result can also mean an unsubmitted changed=False
    # artifact. Only an actual acknowledged and promoted reviewed artifact is success.
    if not report.get('published') or (STATE/'baseline').resolve() != candidate:
        return False
    published=json.loads((candidate/'PUBLISHED').read_bytes())
    if not published.get('commit') or sha(candidate/'_meta/manifest.json') != report['manifest_hash']:
        return False
    report['final_baseline']={'candidate':str(candidate),'commit':published['commit'],
                              'manifest_hash':report['manifest_hash']}
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('build','publish'))
    parser.add_argument('--gate',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    require(not args.resume or args.mode=='publish', 'resume is publication reconciliation only')
    require(pwd.getpwuid(os.geteuid()).pw_name=='swingset', 'run as swingset service account')
    gate,helper,init_gate,init_path,marker=load_gate(args.gate,args.mode)
    output=helper.output_path(args.output)
    # Receipts cannot overwrite any gate or evidence, including initializer inputs.
    protected_paths={args.gate.resolve(),Path(__file__).resolve()}
    for value in gate.values():
        if isinstance(value,dict) and set(value)>={'path','sha256'}:
            protected_paths.add(ref(args.gate,value))
    require(output not in protected_paths and output.with_suffix(output.suffix+'.tmp') not in protected_paths,'output overlaps reviewed evidence')
    report={'format':FORMAT,'mode':args.mode,'state':str(STATE),'source':str(SOURCE),
            'source_receipt_sha256':SOURCE_RECEIPT,'input_bundle_hash':BUNDLE,
            'driver_sha256':sha(Path(__file__)),'gate_sha256':sha(args.gate),
            'started_at':datetime.now(UTC).isoformat(),'status':'preflight','passed':False,
            'published':False,'network_requests':0 if args.mode=='build' else None,
            'parse_executed':False,'derive_executed':False,'input_acceptance_executed':False,
            'pid':os.getpid(),'systemd_invocation_id':os.environ.get('INVOCATION_ID')}
    helper.new_json(output,report)
    started=time.monotonic()
    def save():
        report.update(elapsed_seconds=time.monotonic()-started,max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        helper.replace_json(output,report)
    def stop(*_):raise KeyboardInterrupt('controlled release stop')
    signal.signal(signal.SIGTERM,stop)
    try:
        with (STATE/'state.lock').open('r+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            helper.require_schema14()
            reviewed=reviewed_build(args.gate,gate) if args.mode=='publish' else None
            candidate=Path(reviewed['candidate']) if reviewed else None
            promoted=baseline_before(helper,init_gate,init_path,marker,candidate,args.resume)=='candidate'
            from swingset.state.db import open_database
            with open_database(STATE,lock=False) as db:
                check_execution(db.connection,candidate,args.resume)
                before=authority(db.connection,helper,marker)
                report['authority_before']=before
                report['status']='running'
                save()
                try:
                    if reviewed is None:
                        build(db,helper,init_gate,marker,report)
                    else:
                        report.update(candidate=reviewed['candidate'],candidate_id=reviewed['candidate_id'],manifest_hash=reviewed['manifest_hash'],expected_parent=reviewed['expected_parent'])
                        publish_reviewed(db,candidate,reviewed,resume=args.resume,promoted=promoted,report=report)
                finally:
                    after=authority(db.connection,helper,marker)
                    report.update(protected_unchanged=after['protected']==before['protected'],
                                  controls_unchanged=after['controls']==before['controls'],
                                  input_authority_unchanged=after['inputs']==before['inputs'],
                                  parse_tokens_unchanged=after['parse']==before['parse'],
                                  revisions_unchanged=after['revisions']==before['revisions'])
                    require(all(report[k] for k in ('protected_unchanged','controls_unchanged','input_authority_unchanged','parse_tokens_unchanged','revisions_unchanged')), 'release changed protected initialization state')
                    from research.accept_h11 import system_hold
                    report['final_hold']=system_hold(STATE)
                    if args.mode=='build':
                        baseline_before(helper,init_gate,init_path,marker)
                report['passed']=report['status'] in {'built','published','recovered','promoted','unchanged'}
                if args.mode=='publish':
                    report['passed']=report['passed'] and publication_complete(report,candidate)
                require(report['passed'],'release held or incomplete; controls remain in force')
    except BaseException as exc:
        report.update(passed=False,error_type=type(exc).__name__,status='interrupted')
        raise
    finally:
        report['finished_at']=datetime.now(UTC).isoformat()
        save()
    print(json.dumps({'status':report['status'],'candidate':report.get('candidate'),'receipt':str(output)}),flush=True)

if __name__=='__main__':
    main()
