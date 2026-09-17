"""Report evidence sizes and reject tracked files over 1 MiB; never modify files."""

from __future__ import annotations

import hashlib
import subprocess
from collections import defaultdict
from pathlib import Path

MIB = 1024 * 1024
TEXT_REVIEW_BYTES = 256 * 1024


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    # Use jj's tracked inventory, not the much larger ignored scratch output.
    result = subprocess.run(
        [
            "jj",
            "--config",
            "snapshot.max-new-file-size=16MiB",
            "file",
            "list",
            "-T",
            'path ++ "\\0"',
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    rows: list[tuple[int, str]] = []
    oversized: list[tuple[int, str]] = []
    for raw in result.stdout.split(b"\0"):
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
    if oversized:
        print("FAIL: archive these files externally before retaining their compact receipts:")
        for size, name in sorted(oversized, reverse=True):
            print(f"  {size:,} bytes  {name}")
        raise SystemExit(1)
    print("PASS: no tracked file exceeds 1 MiB.")


if __name__ == "__main__":
    main()
