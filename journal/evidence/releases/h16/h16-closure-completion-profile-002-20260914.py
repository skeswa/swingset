"""Diagnostic completion on an independent copy; never release acceptance."""
import hashlib
import importlib.util
import json
import resource
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

source = Path('/var/tmp/swingset-h16-changelog-build')
state = Path('/var/tmp/swingset-h16-closure-completion-diagnostic-002')
output = Path('/var/tmp/swingset-h16-closure-completion-diagnostic-002.json')
assert not state.exists() and not output.exists()
support_path = Path('/Users/skeswa/repos/skeswa/swingset/src/swingset/build/closure_support.py')
support_spec = importlib.util.spec_from_file_location('swingset.build.closure_support', support_path)
support_module = importlib.util.module_from_spec(support_spec)
sys.modules[support_spec.name] = support_module
support_spec.loader.exec_module(support_module)
patch = Path('/Users/skeswa/repos/skeswa/swingset/src/swingset/build/closure.py')
spec = importlib.util.spec_from_file_location('swingset.build.closure', patch)
closure = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = closure
spec.loader.exec_module(closure)
from swingset.build import generations
from swingset.build.builder import BuildResult
from swingset.state.db import open_database

assert generations.validate is closure.validate
report = {'format': 'h16-completion-diagnostic-v1', 'state': str(state), 'predecessor': str(source), 'closure_file_sha256': hashlib.sha256(patch.read_bytes()).hexdigest(), 'support_file_sha256': hashlib.sha256(support_path.read_bytes()).hexdigest(), 'diagnostic_only': True, 'production_mutated': False, 'inputs_accepted': False, 'network_requests': 0, 'passed': False}
started = time.monotonic()
state.mkdir(mode=0o700)
saved = sqlite3.connect((source / 'state.sqlite').as_uri() + '?mode=ro', uri=True)
copied = sqlite3.connect(state / 'state.sqlite')
try:
    saved.backup(copied)
finally:
    saved.close()
    copied.close()
shutil.copytree(source / 'candidates', state / 'candidates', copy_function=shutil.copyfile)
(state / 'baseline').symlink_to('candidates/cand_7f8cf9bcbf7e4a60')
candidate = state / 'candidates/cand_d80b9025652f4e10'
manifest = json.loads((candidate / '_meta/manifest.json').read_bytes())
built = json.loads((candidate / 'BUILT').read_bytes())
try:
    with open_database(state) as db:
        report['completion_before'] = generations.completed(db.connection, candidate.name, built['manifest_hash'])
        assert not report['completion_before']
        mark = time.monotonic()
        with db.transaction(immediate=False):
            selected = generations.select(db, bundle_digest=manifest['release_policy']['input_bundle_hash'], correction_only=False, closure=manifest['release_policy']['closure'])
        report['select_seconds'] = time.monotonic() - mark
        result = BuildResult(candidate.name, candidate, built['content_hash'], built['manifest_hash'], True, False)
        mark = time.monotonic()
        generations.complete(db, selected, result, now=datetime.now(UTC), run_id=manifest['run_id'])
        report['complete_seconds'] = time.monotonic() - mark
        report['completion_after'] = generations.completed(db.connection, candidate.name, built['manifest_hash'])
        assert report['completion_after']
        report['passed'] = True
except BaseException as error:
    report['error'] = {'type': type(error).__name__, 'message': str(error)}
    raise
finally:
    report.update(elapsed_seconds=time.monotonic()-started, max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    with output.open('x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')
print(json.dumps(report, indent=2))
