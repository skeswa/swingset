"""Assemble the narrow original-H16 event-preservation fixes; never build or activate it.

Run on the VM after formatting and reviewing a JSON object mapping each path
in REVIEWED to its current SHA-256. Pass that file as --reviewed-files. The
manifest pins worktree inputs; the generated h16-source.json pins the assembly.
Existing output is refused, including an incomplete earlier attempt.
"""

import argparse
import hashlib
import json
import shutil
import stat
import tomllib
from pathlib import Path

BASE = Path("/nix/store/4c4q8rkiz6jqizcy93bid6cz8mfvp20b-source")
BASE_RECEIPT_SHA256 = "71dc14101ca19880f3addfa2dc0073dbe50bce0e4e2be27d7bc91811b6d1e338"
RUNTIME = (
    "src/swingset/project/map.py",
    "src/swingset/build/closure_rows.py",
)
TESTS = (
    "tests/test_map_event_retention.py",
    "tests/build/test_closure_event_alternatives.py",
    "tests/build/test_event_preservation_release.py",
)
REVIEWED = (*RUNTIME, *TESTS, "pyproject.toml")
PRIOR_RECEIPT = "provenance/h16-before-event-preservation.json"


def digest(body):
    return hashlib.sha256(body).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), f"Special path: {path}")
        require(
            not {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}.intersection(
                path.parts
            )
            and path.suffix != ".pyc",
            f"Generated cache: {path}",
        )
        if stat.S_ISREG(mode):
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return result


def pytest_only(base_body, reviewed_body):
    base_text = base_body.decode()
    reviewed = tomllib.loads(reviewed_body.decode())["tool"]["pytest"]["ini_options"]
    require(reviewed["pythonpath"] == ["src", ".", "tests"], "Unexpected pytest path")
    require(reviewed["norecursedirs"] == [".*", "__pycache__", "fixtures"], "Unexpected pytest discovery exclusions")
    before = tomllib.loads(base_text)
    require(before["tool"]["pytest"]["ini_options"]["pythonpath"] == ["src", ".", "tests"], "Base pytest path changed")
    require("norecursedirs" not in before["tool"]["pytest"]["ini_options"], "Base discovery already changed")
    anchor = 'pythonpath = ["src", ".", "tests"]\n'
    require(base_text.count(anchor) == 1, "Pytest path line is not unique")
    patched = base_text.replace(anchor, anchor + 'norecursedirs = [".*", "__pycache__", "fixtures"]\n')
    before["tool"]["pytest"]["ini_options"]["norecursedirs"] = reviewed["norecursedirs"]
    require(tomllib.loads(patched) == before, "Unexpected pyproject change")
    return patched.encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--work", type=Path, default=Path("/Users/skeswa/repos/skeswa/swingset"))
    parser.add_argument(
        "--output", type=Path, default=Path("/var/tmp/swingset-h16-event-preservation-release-source")
    )
    parser.add_argument("--reviewed-files", type=Path, required=True)
    args = parser.parse_args()
    base, work, output = args.base, args.work, args.output
    require(base.resolve() == BASE, "Only the exact frozen 4c4 source is allowed")
    require(not output.exists() and not output.is_symlink(), "Output already exists")
    require(output.parent.is_dir(), "Output parent must already exist")
    receipt_body = (base / "h16-source.json").read_bytes()
    require(digest(receipt_body) == BASE_RECEIPT_SHA256, "Base receipt hash mismatch")
    base_receipt = json.loads(receipt_body)
    before = inventory(base)
    require(
        {name: sha for name, sha in before.items() if name != "h16-source.json"}
        == base_receipt["files"],
        "Base inventory does not match its frozen receipt",
    )
    require(PRIOR_RECEIPT not in before, "Provenance target already exists")
    require(all(name in before for name in RUNTIME), "Expected existing runtime modules")
    require(all(name not in before for name in TESTS[1:]), "Standalone regression already exists")
    require(
        "SCHEMA_VERSION = 14\n" in (base / "src/swingset/state/db.py").read_text(),
        "Expected schema 14",
    )
    require(
        "src/swingset/state/migrations/0015_origin_backfill.sql" not in before
        and "src/swingset/history/origin.py" not in before,
        "Unexpected schema15/WP16 base",
    )
    reviewed_body = args.reviewed_files.read_bytes()
    reviewed = json.loads(reviewed_body)
    require(isinstance(reviewed, dict) and set(reviewed) == set(REVIEWED), "Review allowlist mismatch")
    replacements = {}
    for name in REVIEWED:
        path = Path(__file__).parent / "test_event_preservation_release.py" if name == TESTS[2] else work / name
        require(path.is_file() and not path.is_symlink(), f"Not a regular input: {name}")
        body = path.read_bytes()
        require(digest(body) == reviewed[name], f"Reviewed input changed: {name}")
        replacements[name] = body
    replacements["pyproject.toml"] = pytest_only(
        (base / "pyproject.toml").read_bytes(), replacements["pyproject.toml"]
    )
    expected = dict(before)
    del expected["h16-source.json"]
    expected[PRIOR_RECEIPT] = digest(receipt_body)
    expected.update({name: digest(body) for name, body in replacements.items()})
    changed = sorted(name for name in before.keys() | expected.keys() if before.get(name) != expected.get(name))
    require(
        set(changed) == {*REVIEWED, "h16-source.json", PRIOR_RECEIPT},
        "Expected every reviewed file to change and no other change",
    )
    # No worktree tree-copy: the base's old research paths and dependency files stay intact.
    shutil.copytree(base, output, copy_function=shutil.copyfile)
    for directory in [output, *(path for path in output.rglob("*") if path.is_dir())]:
        directory.chmod(0o755)
    for name, body in replacements.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    (output / PRIOR_RECEIPT).parent.mkdir(exist_ok=True)
    (output / "h16-source.json").rename(output / PRIOR_RECEIPT)
    after = inventory(output)
    require(after == expected, "Assembled inventory differs from exact allowed overlay")
    receipt = {
        "format": "h16-reviewed-source-v1",
        "base": str(base),
        "reviewed": str(work),
        "base_receipt_sha256": BASE_RECEIPT_SHA256,
        "assembly_script_sha256": digest(Path(__file__).read_bytes()),
        "reviewed_manifest_sha256": digest(reviewed_body),
        "reviewed_input_files": reviewed,
        "runtime_files": {name: after[name] for name in RUNTIME},
        "base_files": before,
        "files": after,
        "changed": changed,
        "schema": 14,
        "acquisition_enabled": False,
        "repairs_activated": False,
        "purpose": "Original H16 inventory collision and closure support preservation; unchanged schema, policies and completion deadline",
        "pyproject_change": "Only include build tests in pytest recursive discovery; preserve base metadata and dependencies",
        "acceptance": "Source assembly only; no build, replay, deployment, initialization, or publication",
    }
    with (output / "h16-source.json").open("x") as stream:
        stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "output": str(output),
                "files": len(after),
                "changed": changed,
                "receipt_sha256": digest((output / "h16-source.json").read_bytes()),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
