"""Read-only closure validation timing; never a build acceptance receipt."""
import argparse
import hashlib
import importlib.util
import json
import resource
import sqlite3
import sys
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--closure-file', type=Path)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
assert not a.output.exists()
if a.closure_file:
    spec = importlib.util.spec_from_file_location('swingset.build.closure', a.closure_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
from swingset.build import closure
if a.closure_file:
    assert Path(closure.__file__).resolve() == a.closure_file.resolve()

state = Path('/var/tmp/swingset-h16-changelog-build')
candidate = state / 'candidates/cand_d80b9025652f4e10'
manifest_path = candidate / '_meta/manifest.json'
manifest = json.loads(manifest_path.read_bytes())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
report = {'format': 'h16-readonly-closure-validation-profile-v1', 'state': str(state), 'candidate': str(candidate), 'candidate_manifest_sha256': sha(manifest_path), 'closure_file': closure.__file__, 'closure_file_sha256': sha(Path(closure.__file__)), 'diagnostic_only': True, 'production_mutated': False, 'inputs_accepted': False, 'network_requests': 0}
conn = sqlite3.connect((state / 'state.sqlite').as_uri() + '?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
try:
    conn.execute('BEGIN')
    started = time.monotonic()
    pinned = closure.hydrate(conn, manifest['release_policy']['closure'])
    report['hydrate_seconds'] = time.monotonic() - started
    report['selected_generations'] = len(pinned['selected'])
    started = time.monotonic()
    closure.validate(conn, pinned)
    report['validate_seconds'] = time.monotonic() - started
    report['validation_passed'] = True
finally:
    conn.close()
    report['max_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with a.output.open('x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')
print(json.dumps(report, indent=2))
