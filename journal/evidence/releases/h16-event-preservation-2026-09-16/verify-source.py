"""Verify the later frozen overlay and optionally make a new exact test mirror.

Preparation only until final code review, hashes and assembly are authorized.
No subprocess, deployment, database, service, or source acquisition operation.
"""

import argparse
import hashlib
import importlib.util
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-receipt-sha256', required=True)
    parser.add_argument('--reviewed-files', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mirror', type=Path)
    args = parser.parse_args()
    folder = Path(__file__).parent
    spec = importlib.util.spec_from_file_location('assembly', folder / 'assemble-source.py')
    assembly = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(assembly)
    require, inventory, digest = assembly.require, assembly.inventory, assembly.digest
    require(not args.output.exists() and not args.output.is_symlink(), 'Verification receipt already exists')
    require(args.source.parent == Path('/nix/store') and args.source.name.endswith('-source'), 'Expected immutable Nix source')
    require(not args.output.resolve().is_relative_to(args.source.resolve()) and not args.output.resolve().is_relative_to(assembly.BASE), 'Receipt must be outside immutable sources')
    if args.mirror:
        require(not args.output.resolve().is_relative_to(args.mirror.resolve()), 'Receipt must be outside mirror')
        require(not args.mirror.exists() and not args.mirror.is_symlink(), 'Mirror must be new')
    before, after = inventory(assembly.BASE), inventory(args.source)
    raw = (args.source / 'h16-source.json').read_bytes()
    require(digest(raw) == args.source_receipt_sha256, 'Frozen receipt does not match launch pin')
    receipt = json.loads(raw)
    require(before['h16-source.json'] == assembly.BASE_RECEIPT_SHA256, 'Base receipt pin')
    require(receipt['base'] == str(assembly.BASE) and receipt['base_files'] == before, 'Base identity/inventory')
    base_receipt = json.loads((assembly.BASE / 'h16-source.json').read_bytes())
    require({k: v for k, v in before.items() if k != 'h16-source.json'} == base_receipt['files'], 'Actual base inventory')
    require({k: v for k, v in after.items() if k != 'h16-source.json'} == receipt['files'], 'Actual source inventory')
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    require(set(changed) == {*assembly.REVIEWED, 'h16-source.json', assembly.PRIOR_RECEIPT}, 'Exact overlay allowlist')
    require(receipt['changed'] == changed, 'Receipt delta')
    require(after[assembly.PRIOR_RECEIPT] == assembly.BASE_RECEIPT_SHA256, 'Prior receipt retained')
    reviewed_body = args.reviewed_files.read_bytes()
    reviewed = json.loads(reviewed_body)
    require(set(reviewed) == set(assembly.REVIEWED), 'Reviewed file allowlist')
    require(receipt['reviewed_input_files'] == reviewed and receipt['reviewed_manifest_sha256'] == digest(reviewed_body), 'Reviewed manifest identity')
    require(receipt['assembly_script_sha256'] == digest((folder / 'assemble-source.py').read_bytes()), 'Assembler identity')
    require(all(after[name] == reviewed[name] for name in (*assembly.RUNTIME, *assembly.TESTS)), 'Runtime/test bytes')
    require(receipt['runtime_files'] == {name: after[name] for name in assembly.RUNTIME}, 'Runtime receipt')
    require('SCHEMA_VERSION = 14\n' in (args.source / 'src/swingset/state/db.py').read_text(), 'Schema14')
    require(after['src/swingset/project/process.py'] == before['src/swingset/project/process.py'], 'Process unchanged')
    require(all(after.get(name) == sha for name, sha in before.items() if name not in {*assembly.REVIEWED, 'h16-source.json'}), 'Every other base file unchanged')
    # The assembler records the full reviewed pyproject hash but deliberately
    # imports only its pytest discovery setting into the old dependency file.
    import tomllib
    base_project = tomllib.loads((assembly.BASE / 'pyproject.toml').read_text())
    expected = base_project['tool']['pytest']['ini_options']
    expected['norecursedirs'] = ['.*', '__pycache__', 'fixtures']
    require(tomllib.loads((args.source / 'pyproject.toml').read_text()) == base_project, 'Only pytest discovery changed')
    if args.mirror:
        shutil.copytree(args.source, args.mirror, copy_function=shutil.copyfile)
        for path in [args.mirror, *args.mirror.rglob('*')]:
            path.chmod(0o755 if path.is_dir() else 0o644)
        require(inventory(args.mirror) == after, 'Mirror is byte-identical')
    result = {
        'format': 'h16-event-preservation-source-verification-v1',
        'at': datetime.now(UTC).isoformat(), 'passed': True,
        'source': str(args.source), 'source_receipt_sha256': args.source_receipt_sha256,
        'base': str(assembly.BASE), 'base_receipt_sha256': assembly.BASE_RECEIPT_SHA256,
        'changed': changed, 'files': len(after), 'mirror': str(args.mirror) if args.mirror else None,
        'verification_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'tests_executed': False, 'production_mutated': False,
    }
    with args.output.open('x') as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
