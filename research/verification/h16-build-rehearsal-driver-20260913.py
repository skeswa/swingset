"""Offline full build: independent copy of a settled H16 replay, no remote client."""
import argparse
import fcntl
import hashlib
import json
import resource
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

SOURCE = Path('/nix/store/d8bwqgbqi94b19l0wfgqr7a2gir1xkf5-source')
RECEIPT = 'abb69e5f559769bd2b6ae782c290c10ff746df3783f4118a2b107e440c462983'
BUNDLE = 'e50f13d2ecb79b48826243ece52f5ac8fdc6be492c76aaf324f668c51716fe19'
V4 = '81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653'

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def require_current_replay(predecessor, *, replay, receipt, bundle, marker_sha256):
    expected = {
        'format': 'offline-derivation-replay-v1', 'status': 'current',
        'scratch': str(replay), 'source_receipt_sha256': receipt,
        'input_bundle_hash': bundle, 'marker_sha256': marker_sha256,
        'network_requests': 0, 'parse_executed': False, 'published': False,
    }
    if any(predecessor.get(key) != value for key, value in expected.items()) or predecessor.get('unfinished_by_scope') != {}:
        raise ValueError('current replay receipt does not bind this exact scratch, runtime, marker and bundle')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--replay', type=Path, required=True)
    p.add_argument('--replay-receipt', type=Path, required=True)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source', type=Path, default=SOURCE)
    p.add_argument('--source-receipt-sha256', default=RECEIPT)
    p.add_argument('--bundle-digest', default=BUNDLE)
    args = p.parse_args()
    source, receipt, expected_bundle = args.source.resolve(), args.source_receipt_sha256, args.bundle_digest
    replay, state, output = (value.resolve() for value in (args.replay, args.state, args.output))
    if any(value.is_relative_to('/var/lib/swingset') or 'checkpoints' in value.parts for value in (state, output)):
        raise ValueError('new build state and receipt must be outside production/checkpoints')
    if state.exists() or args.state.is_symlink() or output.exists() or args.output.is_symlink():
        raise ValueError('build state and receipt must be new')
    if state.is_relative_to(replay) or output.is_relative_to(replay) or output.is_relative_to(state):
        raise ValueError('independent state and external receipt required')
    from research.replay_derivations import validate_scratch
    from swingset.build import generations
    from swingset.build.closure import hydrate, validate
    from swingset.build.service import build_release
    from swingset.clock import SystemClock
    from swingset.publish.safety import _validate_candidate
    from swingset.schedule.cycle import versions
    from swingset.state.db import open_database
    from swingset.state.inputs import capture
    from swingset.state.work import unfinished_units
    report = {'network_requests': 0, 'published': False, 'passed': False, 'state': str(state), 'source': str(source)}
    started = time.monotonic()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Existing replay lock only: preparation cannot modify or interrupt its writer.
    with (replay / 'state.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = validate_scratch(replay, source, receipt)
        predecessor = json.loads(args.replay_receipt.read_bytes())
        require_current_replay(predecessor, replay=replay, receipt=receipt, bundle=expected_bundle, marker_sha256=sha(replay/'offline-derivation-scratch.json'))
        checkpoint = Path(marker['checkpoint'])
        manifest = json.loads((checkpoint / 'checkpoint.json').read_bytes())
        candidate_id = manifest['baseline_candidate']
        prefix = 'candidates/' + candidate_id + '/'
        old = checkpoint / prefix
        if json.loads((old / 'PUBLISHED').read_bytes())['commit'] != V4:
            raise ValueError('expected verified V4 baseline')
        with closing(sqlite3.connect((replay / 'state.sqlite').as_uri() + '?mode=ro', uri=True)) as source_db:
            source_db.row_factory = sqlite3.Row
            source_db.execute('BEGIN')
            if source_db.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0] != expected_bundle:
                raise ValueError('accepted replay inputs changed')
            if any(unfinished_units(source_db, stages=('project', 'link'))):
                raise ValueError('derived work is still unfinished')
            if source_db.execute("SELECT 1 FROM execution_admissions WHERE state!='settled'").fetchone():
                raise ValueError('replay has an active or uncertain admission')
            state.mkdir(mode=0o700)
            with closing(sqlite3.connect(state / 'state.sqlite')) as copied:
                source_db.backup(copied)
        local_baseline = state / 'candidates' / candidate_id
        shutil.copytree(old, local_baseline, copy_function=shutil.copyfile)
        expected = {name[len(prefix):]: record for name, record in manifest['files'].items() if name.startswith(prefix)}
        actual = {path.relative_to(local_baseline).as_posix() for path in local_baseline.rglob('*') if path.is_file()}
        if actual != set(expected):
            raise ValueError('copied baseline file closure differs')
        for name, record in expected.items():
            path = local_baseline / name
            if path.stat().st_size != record['size'] or sha(path) != record['sha256']:
                raise ValueError('copied baseline differs: ' + name)
        (state / 'baseline').symlink_to(Path('candidates') / candidate_id)
        shutil.copytree(replay / 'inputs' / expected_bundle, state / 'inputs' / expected_bundle, copy_function=shutil.copyfile)
        report.update(baseline=str(local_baseline), baseline_commit=V4, replay_receipt_sha256=sha(args.replay_receipt))
    # Build only: never accept changed inputs, derive, fetch, reconcile or publish.
    try:
        with open_database(state) as db:
            clock = SystemClock()
            captured = state / 'inputs' / expected_bundle
            bundle = capture(captured / 'config', captured / 'overrides', state, versions())
            if bundle.digest != expected_bundle:
                raise ValueError('build environment does not reproduce accepted input bundle')
            before = {row[0]: row[1] for row in db.connection.execute('SELECT name,value FROM revisions')}
            run = db.start_run(clock.now())
            built_at = time.monotonic()
            result = build_release(db, bundle, clock, run, remote=None, correction_only=False)
            report.update(candidate=str(result.path), build_seconds=time.monotonic()-built_at, candidate_id=result.candidate_id, manifest_hash=result.manifest_hash)
            manifest = json.loads((result.path / '_meta/manifest.json').read_bytes())
            if manifest['release_policy']['mode'] != 'closure':
                raise ValueError('ordinary closure release required')
            public = manifest['release_policy']['closure']
            expected_keys = {'format','private_digest','cutoff','selected_generations','support_token','inventory_digest','baseline','digest'}
            if set(public) != expected_keys or public['format'] != 'release-closure-public-v1':
                raise ValueError('public manifest includes unexpected private closure fields')
            if not public['selected_generations']:
                raise ValueError('full-replay rehearsal unexpectedly selected an empty closure')
            validate(db.connection, public)
            private = hydrate(db.connection, public)
            report.update(selected_generations=len(public['selected_generations']), selected_source_support=len(private['source_support']), row_counts=manifest['row_counts'])
            del private
            assert generations.completed(db.connection, result.candidate_id, result.manifest_hash)
            if before != {row[0]: row[1] for row in db.connection.execute('SELECT name,value FROM revisions')}:
                raise ValueError('build mutated canonical or identity revisions')
            with closing(sqlite3.connect((state / 'state.sqlite').as_uri() + '?mode=ro', uri=True)) as checked:
                checked.row_factory = sqlite3.Row
                checked.execute('BEGIN')
                _validate_candidate(checked, state, result.path, json.loads((result.path / 'BUILT').read_bytes()), manifest)
            report['semantic_publication_preflight'] = True
            report['operator_admission_exercised'] = False
            report['passed'] = True
            with db.transaction() as conn:
                conn.execute('UPDATE runs SET finished_at=? WHERE run_id=?', (clock.now().isoformat(), run))
    except BaseException as error:
        report['error'] = {'type':type(error).__name__, 'message':str(error)}
        raise
    finally:
        report.update(elapsed_seconds=time.monotonic()-started, max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        with output.open('x') as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write('\n')

if __name__ == '__main__':
    main()
