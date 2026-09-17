"""Independently verify the frozen H16 proof overlay and copy a host test mirror."""

import hashlib
import json
import re
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path

BASE = Path('/nix/store/2d5q3lgljfkmm78hf44g954lzwx79yhv-source')
SOURCE = Path('/nix/store/4c4q8rkiz6jqizcy93bid6cz8mfvp20b-source')
SYSTEM = Path('/nix/store/nxvnvfrzdn4xj82im2hlj0pa42ck2zqn-nixos-system-swingset-lxc-25.11.20260630.b6018f8')
MIRROR = Path('/Users/skeswa/.cache/swingset-h16-proof-tests-20260916')
EVIDENCE = Path('/Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-validation-2026-09-16')
OUTPUT = EVIDENCE / 'frozen-source-and-mirror-verification.json'
RUNTIME = {
    'src/swingset/build/closure_validation.py',
    'src/swingset/build/closure.py',
    'src/swingset/build/generations.py',
}
TEST = 'tests/build/test_closure_validation_scope.py'
PROVENANCE = 'provenance/h16-before-proof-validation.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f'Nonregular path: {path}')
        if {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'} & set(path.parts) or path.suffix == '.pyc':
            raise ValueError(f'Unexpected generated cache: {path}')
        if stat.S_ISREG(mode):
            result[path.relative_to(root).as_posix()] = sha(path)
    return result


report = {
    'format': 'h16-proof-frozen-source-independent-verification-v1',
    'at': datetime.now(UTC).isoformat(),
    'base': str(BASE), 'source': str(SOURCE), 'system': str(SYSTEM),
    'mirror': str(MIRROR), 'checks': {}, 'passed': False,
    'verification_script_sha256': sha(Path(__file__)),
    'production_mutated': False, 'tests_executed': False,
    'limitations': ['Source and service binding verification only; no deployment, replay, build-candidate acceptance, initialization or publication.'],
    'copy_command': "shutil.copytree(SOURCE, MIRROR, copy_function=shutil.copyfile)",
}


def check(name, value):
    report['checks'][name] = bool(value)
    if not value:
        raise ValueError(name)


if OUTPUT.exists():
    raise FileExistsError(OUTPUT)
try:
    check('mirror_new', not MIRROR.exists() and not MIRROR.is_symlink())
    check('source_receipt_pin', sha(SOURCE / 'h16-source.json') == '71dc14101ca19880f3addfa2dc0073dbe50bce0e4e2be27d7bc91811b6d1e338')
    check('base_receipt_pin', sha(BASE / 'h16-source.json') == 'f8f24c2d8b839bbfcd25bf87e551e9f2777d1236f89ed84da16c3c07d4d75b9f')
    receipt = json.loads((SOURCE / 'h16-source.json').read_bytes())
    base_receipt = json.loads((BASE / 'h16-source.json').read_bytes())
    before, after = inventory(BASE), inventory(SOURCE)
    report['source_file_hashes'] = after
    report['source_file_count'] = len(after)
    check('base_actual_inventory', {k: v for k, v in before.items() if k != 'h16-source.json'} == base_receipt['files'])
    check('source_actual_inventory', {k: v for k, v in after.items() if k != 'h16-source.json'} == receipt['files'])
    check('receipt_base_inventory', receipt['base_files'] == before and receipt['base'] == str(BASE))
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    report['changed_paths'] = sorted(changed)
    check('exact_allowlist', changed == RUNTIME | {TEST, 'pyproject.toml', 'h16-source.json', PROVENANCE})
    check('receipt_changed_paths', sorted(changed) == receipt['changed'])
    check('runtime_inventory', receipt['runtime_files'] == {name: after[name] for name in RUNTIME})
    check('prior_receipt_preserved', after[PROVENANCE] == before['h16-source.json'])
    check('support_unchanged', before['src/swingset/build/closure_support.py'] == after['src/swingset/build/closure_support.py'])
    check('protected_paths_unchanged', all(after.get(name) == value for name, value in before.items() if name.startswith(('config/', 'overrides/', 'nix/', 'src/swingset/state/')) or name in ('uv.lock', 'flake.lock', 'flake.nix')))
    check('schema14_no_wp16', 'SCHEMA_VERSION = 14\n' in (SOURCE / 'src/swingset/state/db.py').read_text() and 'src/swingset/state/migrations/0015_origin_backfill.sql' not in after and 'src/swingset/history/origin.py' not in after)
    check('no_activation_in_receipt', receipt['schema'] == 14 and receipt['acquisition_enabled'] is False and receipt['repairs_activated'] is False)
    expected_project, count = re.subn(r'(?m)^pythonpath = \[[^\n]*\]$', 'pythonpath = ["src", ".", "tests"]', (BASE / 'pyproject.toml').read_text())
    check('only_pytest_path_changed', count == 1 and (SOURCE / 'pyproject.toml').read_text() == expected_project)
    check('assembly_script_pin', sha(EVIDENCE / 'assemble-source.py') == receipt['assembly_script_sha256'])
    check('review_manifest_pin', sha(EVIDENCE / 'reviewed-input-hashes.json') == receipt['reviewed_manifest_sha256'])
    reviewed = json.loads((EVIDENCE / 'reviewed-input-hashes.json').read_bytes())
    check('reviewed_runtime_and_test', set(reviewed) == RUNTIME | {TEST, 'pyproject.toml'} and receipt['reviewed_input_files'] == reviewed and all(after[name] == reviewed[name] for name in RUNTIME | {TEST}))
    wrappers = set()
    report['units'] = {}
    for name in ('cycle', 'backup', 'summary'):
        unit = (SYSTEM / f'etc/systemd/system/swingset-{name}.service').read_text()
        report['units'][name] = unit
        check(f'{name}_workdir', f'WorkingDirectory={SOURCE}\n' in unit)
        check(f'{name}_sync', f'--project {SOURCE} --frozen --no-dev' in unit)
        check(f'{name}_config', f'--config {SOURCE}/config ' in unit)
        check(f'{name}_hold', 'ConditionPathExists=!/var/lib/swingset/operator-hold\n' in unit)
        check(f'{name}_owner', 'User=swingset\n' in unit)
        match = re.search(r'^ExecStart=(\S+) ', unit, re.M)
        check(f'{name}_wrapper_path', match is not None)
        wrappers.add(match.group(1))
    check('shared_service_wrapper', len(wrappers) == 1)
    wrapper_path = Path(wrappers.pop())
    wrapper = wrapper_path.read_text()
    report['wrapper_path'] = str(wrapper_path)
    report['wrapper_sha256'] = sha(wrapper_path)
    check('wrapper_pins_runtime', f'exec uv run --project {SOURCE} --frozen --no-dev swingset' in wrapper)
    check('wrapper_pins_revision', f'export SWINGSET_REVISION=uncommitted:{SOURCE.name}\n' in wrapper)
    check('wrapper_pins_config', f'export SWINGSET_CONFIG_DIR={SOURCE}/config\n' in wrapper)
    shutil.copytree(SOURCE, MIRROR, copy_function=shutil.copyfile)
    for path in [MIRROR, *MIRROR.rglob('*')]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    mirror = inventory(MIRROR)
    check('mirror_exact_every_file', mirror == after)
    check('mirror_writable_bits', all(path.stat().st_mode & stat.S_IWUSR for path in [MIRROR, *MIRROR.rglob('*')]))
    report['mirror_file_count'] = len(mirror)
    report['passed'] = True
except BaseException as error:
    report['error'] = {'type': type(error).__name__, 'message': str(error)}
    raise
finally:
    with OUTPUT.open('x') as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'output': str(OUTPUT), 'sha256': sha(OUTPUT), 'passed': report['passed'], 'mirror': str(MIRROR)}, indent=2), flush=True)
