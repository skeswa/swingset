"""Report evidence sizes and enforce the two evidence limits; never modify files.

The limits, from D-0106 and D-0175:

- no tracked file may exceed 1 MiB;
- one change may add or modify at most 200 KiB under ``journal/evidence/``,
  measured from ``trunk()`` to the working copy. ``--from REV`` measures from
  another revision, such as ``@-`` for the newest change of an unpushed stack.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from collections import defaultdict
from pathlib import Path

MIB = 1024 * 1024
TEXT_REVIEW_BYTES = 256 * 1024
NEW_EVIDENCE_BUDGET = 200 * 1024


def _jj(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["jj", "--config", "snapshot.max-new-file-size=16MiB", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout


def changed_evidence(root: Path, base: str = "trunk()") -> list[tuple[int, str]] | None:
    """Evidence files added or modified since `base`, with working-tree sizes.

    Returns None when trunk cannot be resolved, for example in a clone with
    no remote, so the caller can say the budget was not checked.
    """
    try:
        out = _jj(root, "diff", "--from", base, "--to", "@", "--summary")
    except subprocess.CalledProcessError:
        return None
    rows: list[tuple[int, str]] = []
    for line in out.decode().splitlines():
        status, _, name = line.partition(" ")
        if status not in {"A", "M"} or not name.startswith("journal/evidence/"):
            continue
        path = root / name
        if path.is_file() and not path.is_symlink():
            rows.append((path.stat().st_size, name))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from",
        dest="base",
        default="trunk()",
        help="revision the new-evidence budget is measured from (default trunk())",
    )
    base = parser.parse_args().base
    root = Path(__file__).resolve().parents[2]
    # Use jj's tracked inventory, not the much larger ignored scratch output.
    rows: list[tuple[int, str]] = []
    oversized: list[tuple[int, str]] = []
    for raw in _jj(root, "file", "list", "-T", 'path ++ "\\0"').split(b"\0"):
        name = raw.decode()
        if not name:
            continue
        path = root / name
        if path.is_file() and not path.is_symlink():
            size = path.stat().st_size
            if size > MIB:
                oversized.append((size, name))
            if name.startswith("journal/evidence/"):
                rows.append((size, name))
    total = sum(size for size, _ in rows)
    large = [(size, name) for size, name in rows if size > MIB]
    print(f"Tracked evidence: {len(rows):,} files, {total / MIB:.2f} MiB")
    print(f"Over 1 MiB: {len(large)} files, {sum(size for size, _ in large) / MIB:.2f} MiB")
    print("Largest files (existing sealed evidence must remain unchanged):")
    for size, name in sorted(rows, reverse=True)[:15]:
        print(f"  {size / MIB:7.2f} MiB  {name}")

    by_size: dict[int, list[str]] = defaultdict(list)
    for size, name in rows:
        if size > TEXT_REVIEW_BYTES:
            by_size[size].append(name)
    duplicates = []
    for size, names in by_size.items():
        if len(names) < 2:
            continue
        by_hash: dict[str, list[str]] = defaultdict(list)
        for name in names:
            digest = hashlib.sha256()
            with (root / name).open("rb") as source:
                for chunk in iter(lambda: source.read(MIB), b""):
                    digest.update(chunk)
            by_hash[digest.hexdigest()].append(name)
        for matches in by_hash.values():
            if len(matches) > 1:
                duplicates.append((size * (len(matches) - 1), matches))
    redundant = sum(size for size, _ in duplicates)
    print(
        f"Exact duplicate groups above 256 KiB: {len(duplicates)}, "
        f"{redundant / MIB:.2f} MiB repeated working-tree bytes"
    )
    print("Version-control storage can already deduplicate identical bytes.")
    for size, names in sorted(duplicates, reverse=True)[:10]:
        print(f"  {size / MIB:.2f} MiB repeated:")
        for name in names:
            print(f"    {name}")

    failed = False
    changed = changed_evidence(root, base)
    if changed is None:
        print(f"New-evidence budget not checked: {base} did not resolve.")
    else:
        added = sum(size for size, _ in changed)
        print(
            f"New or changed evidence since {base}: {len(changed)} files, "
            f"{added / 1024:.0f} KiB (budget {NEW_EVIDENCE_BUDGET // 1024} KiB)"
        )
        if added > NEW_EVIDENCE_BUDGET:
            failed = True
            print("FAIL: keep one compact receipt per operation; leave the rest in scratch:")
            for size, name in sorted(changed, reverse=True):
                print(f"  {size:,} bytes  {name}")
    if oversized:
        failed = True
        print("FAIL: archive these files externally before retaining their compact receipts:")
        for size, name in sorted(oversized, reverse=True):
            print(f"  {size:,} bytes  {name}")
    if failed:
        raise SystemExit(1)
    print("PASS: no tracked file exceeds 1 MiB and new evidence is within budget.")


if __name__ == "__main__":
    main()
