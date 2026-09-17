"""Run host-only checks on a verified exact mirror; never assemble or run a VM.

The coordinator supplies the later immutable source pin and receipt. This script
is prepared now but must not run until source assembly and mirror verification.
"""

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

TOOLS = Path('/Users/skeswa/repos/skeswa/swingset/.venv/bin')
REQUIRED_BUILD_TESTS = (
    'tests/build/test_closure_validation_scope.py',
    'tests/build/test_completion_validation.py',
    'tests/build/test_closure_event_alternatives.py',
    'tests/build/test_event_preservation_release.py',
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(mirror, receipt_sha):
    if sha(mirror / 'h16-source.json') != receipt_sha:
        raise ValueError('Mirror receipt changed')
    receipt = json.loads((mirror / 'h16-source.json').read_bytes())
    for name, expected in receipt['files'].items():
        path = mirror / name
        if not path.is_file() or path.is_symlink() or sha(path) != expected:
            raise ValueError(f'Mirror file changed: {name}')
    expected = set(receipt['files']) | {'h16-source.json'}
    actual = {path.relative_to(mirror).as_posix() for path in mirror.rglob('*') if path.is_file()
              and not {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}.intersection(path.parts)
              and path.suffix != '.pyc'}
    if actual != expected:
        raise ValueError('Mirror contains unexpected source/test files')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--source-receipt-sha256', required=True)
    parser.add_argument('--mirror', type=Path, required=True)
    parser.add_argument('--source-verification', type=Path, required=True)
    parser.add_argument('--source-verification-sha256', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if not args.source.startswith('/nix/store/') or not args.source.endswith('-source'):
        raise ValueError('Immutable source pin required')
    if sha(args.source_verification) != args.source_verification_sha256:
        raise ValueError('Source verification receipt hash changed')
    binding = json.loads(args.source_verification.read_bytes())
    if not binding.get('passed') or binding.get('source') != args.source or binding.get('source_receipt_sha256') != args.source_receipt_sha256 or binding.get('mirror') != str(args.mirror):
        raise ValueError('Source/mirror binding differs from verified receipt')
    if args.evidence.resolve().is_relative_to(args.mirror.resolve()) or args.evidence.resolve().is_relative_to('/nix/store'):
        raise ValueError('Evidence must be outside the source and mirror')
    if not args.evidence.is_dir():
        raise ValueError('Evidence directory must exist')
    names = ('frozen-collection.log', 'frozen-tests.log', 'frozen-ruff.log', 'frozen-mypy.log', 'frozen-validation.json')
    if any((args.evidence / name).exists() or (args.evidence / name).is_symlink() for name in names):
        raise FileExistsError('Validation evidence already exists')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=f'{args.mirror}/src:{args.mirror}', SWINGSET_REVISION=f'uncommitted:{Path(args.source).name}')
    result = {'format': 'h16-event-preservation-frozen-validation-v1', 'source': args.source,
              'source_receipt_sha256': args.source_receipt_sha256, 'mirror': str(args.mirror),
              'source_verification_sha256': args.source_verification_sha256,
              'at': datetime.now(UTC).isoformat(), 'passed': False, 'checks': {}, 'production_mutated': False}
    def run(label, tool, arguments, log_name):
        started = time.monotonic()
        command = [str(TOOLS / tool), *arguments]
        with (args.evidence / log_name).open('xb') as log:
            process = subprocess.run(command, cwd=args.mirror, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
        result['checks'][label] = {'command': command, 'exit_code': process.returncode, 'seconds': time.monotonic() - started, 'log_sha256': sha(args.evidence / log_name)}
        if process.returncode:
            raise RuntimeError(f'{label} failed; inspect {log_name}')
    try:
        verify(args.mirror, args.source_receipt_sha256)
        run('collection', 'pytest', ['--collect-only', '-q', '-p', 'no:cacheprovider', 'tests'], names[0])
        collection = (args.evidence / names[0]).read_text()
        nodes = [line for line in collection.splitlines() if line.startswith('tests/') and '::' in line]
        if not all(any(node.startswith(name + '::') for node in nodes) for name in REQUIRED_BUILD_TESTS):
            raise ValueError('Required build tests were not recursively collected')
        result['collected_tests'] = len(nodes)
        result['collected_build_tests'] = sum(node.startswith('tests/build/') for node in nodes)
        run('tests', 'pytest', ['-q', '-p', 'no:cacheprovider', 'tests'], names[1])
        run('ruff', 'ruff', ['check', 'src', 'tests'], names[2])
        run('mypy', 'mypy', ['src/swingset'], names[3])
        verify(args.mirror, args.source_receipt_sha256)
        result['passed'] = True
    except BaseException as error:
        result['error'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        with (args.evidence / names[4]).open('x') as stream:
            stream.write(json.dumps(result, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
