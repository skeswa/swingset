"""Assemble reviewed H16 performance changes over the immutable 093 source."""
import hashlib
import json
import shutil
from pathlib import Path

base = Path('/nix/store/093lylp4naslq5yb2mygbkkgkbsar3ym-source')
work = Path('/Users/skeswa/repos/skeswa/swingset')
output = Path('/var/tmp/swingset-h16-closure-release-source')
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert not output.exists()
assert sha(base / 'h16-source.json') == '3930dbc9f6023250a5253043ade60bd3fc90c57bda27e2c64f8520aa4c588a92'
runtime = {
    'src/swingset/build/closure.py': '5573f5a0ebd41b3b7320ab5fb15e2ae09d26c045449516fc1c92b5ebeed94668',
    'src/swingset/build/closure_support.py': 'fa6c6b37d7b6d71e7e7ba4d2ea775db41567d4883120cb1d8c33b7192ea079f1',
    'src/swingset/build/generations.py': '3508fbc303a44ffb56963a4c7db7f5875cda8cccff8771077d5495f1254edeb9',
}
for name, expected in runtime.items():
    assert sha(work / name) == expected, name
selected = (*runtime,
    'tests/build/test_closure_graph_cost.py',
    'tests/build/test_closure_support_queries.py',
    'tests/build/test_completion_validation.py',
    'research/h16-closure-performance-2026-09-14.md',
    'research/verification/h16-changelog-build-20260914.json',
    'research/verification/h16-changelog-build-resources-20260914.json',
    'research/verification/h16-changelog-build-journal-20260914.log',
    'research/verification/h16-closure-validation-original-20260914.json',
    'research/verification/h16-closure-validation-optimized-20260914.json',
    'research/verification/h16-closure-completion-diagnostic-003-20260914.json',
    'research/verification/2026-09-12/event-list/asp/r00_web_archive_org.json',
    'research/verification/2026-09-12/event-list/asp3/r05_web_archive_org.json',
)
def inventory(root):
    assert not any(p.is_symlink() for p in root.rglob('*'))
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}

before = inventory(base)
assert {k:v for k,v in before.items() if k != 'h16-source.json'} == json.loads((base / 'h16-source.json').read_bytes())['files']
shutil.copytree(base, output, copy_function=shutil.copyfile, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
for p in [output, *(p for p in output.rglob('*') if p.is_dir())]:
    p.chmod(0o755)
for name in selected:
    target = output / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(work / name, target)
(output / 'provenance').mkdir(exist_ok=True)
shutil.move(output / 'h16-source.json', output / 'provenance/h16-before-closure-validation.json')
after = inventory(output)
changed = sorted(n for n in before.keys() | after.keys() if before.get(n) != after.get(n))
assert {n for n in changed if n.startswith('src/')} == set(runtime)
assert all(after[n] == value for n,value in before.items() if n.startswith(('config/', 'overrides/', 'nix/')))
assert 'SCHEMA_VERSION = 14\n' in (output / 'src/swingset/state/db.py').read_text()
assert not (output / 'src/swingset/state/migrations/0015_origin_backfill.sql').exists()
assert not (output / 'src/swingset/history/origin.py').exists()
receipt = {'format': 'h16-reviewed-source-v1', 'base': str(base), 'reviewed': str(work), 'base_files': before, 'files': after, 'changed': changed, 'schema': 14, 'acquisition_enabled': False, 'repairs_activated': False, 'purpose': 'H16 bounded closure validation with unchanged 45-second completion and source policies'}
(output / 'h16-source.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
print(json.dumps({'output': str(output), 'files': len(after), 'changed': changed, 'receipt_sha256': sha(output / 'h16-source.json')}, indent=2))
