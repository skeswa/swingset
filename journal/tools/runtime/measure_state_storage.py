"""Attribute state storage on one disposable copy of a held backup.

Step 1 of the bounded-state plan measures before anything is designed. This
tool opens a state database read-only and reports bytes per table and index
from SQLite `dbstat`, file and write-ahead-log sizes, free-list bytes, and per
stage and unit kind: generations, rows, payload bytes, and distinct payload
digests against total rows. That last ratio predicts how much interning
payloads can save.

The tool never writes to the state directory it measures. It opens the
database `mode=ro&immutable=1`, which makes SQLite skip the write-ahead log and
shared-memory files entirely. It refuses a path under `/var/lib/swingset`, and
it refuses a database with a nonempty write-ahead log or any shared-memory
file, because both mean a connection is open and an immutable reader would
silently ignore pending changes. Run it on a throwaway copy, never on a live
state directory.

Every path the tool writes to -- `--output`, `--receipt`, and `--scratch` -- is
fenced the same way and checked before the measurement starts: none may sit
under `/var/lib/swingset`, none may sit inside the copy being measured, and
none may already exist. A stray file inside a copied checkpoint breaks that
checkpoint's own verification, a stray file under the live root is copied by
every later backup, and a destination that already exists would otherwise throw
away a measurement that can take hours.

The exit status is the gates, not the optional extras: the tool exits non-zero
when page accounting does not close, when generations declare more output rows
than are readable, when rows name no generation, or when `--time-backup` fails.

`--time-backup` additionally times a checkpoint, its verification, and a
restore, writing only under `--scratch`. A held checkpoint is not a state
directory, so for one of those the tool restores first and backs up what came
out; for a plain state directory it backs up first and restores that. Either
way it refuses to start unless free disk covers two copies of the whole state
tree, which is what the run writes.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOL_VERSION = "measure-state-storage-v4"
REPORT_FORMAT = "state-storage-measurement-v3"
RECEIPT_FORMAT = "state-storage-measurement-receipt-v3"

# SQLite reserves the page holding byte 0x40000000 and never stores data there.
# It is counted by `page_count` but appears in neither `dbstat` nor the free list.
PENDING_BYTE = 0x40000000

# The live state directory on the worker. Measuring it would read a file a
# writer is changing underneath, so the tool refuses it outright.
PROTECTED_ROOTS = ("/var/lib/swingset",)

# Operating history named by the plan's step 1, beside every identity_* table.
HISTORY_TABLES = ("control_events", "work_attempts", "runs", "findings", "finding_support")
# Both shapes are listed because a held backup may predate the payload split:
# before it, derivation_rows is a table; after it, a view over the other two.
DERIVATION_TABLES = (
    "derivation_generations",
    "derivation_rows",
    "derivation_rows_legacy",
    "derivation_row_refs",
    "derivation_payloads",
    "derivation_dependency_sets",
    "derivation_scopes",
    "derivation_input_versions",
)
SOURCE_GENERATION_JSON_COLUMNS = (
    "manifest_json",
    "recipe_json",
    "result_json",
    "report_json",
)
# These are the tables whose indexes D-0166 asks to inspect. Both derivation
# layouts are listed: schema 29 stores rows inline, while schema 32 splits row
# references from payloads and leaves `derivation_rows` as a view.
COSTLY_INDEX_TABLES = (
    "derivation_rows",
    "derivation_row_refs",
    "derivation_payloads",
    "source_generations",
)
RECEIPT_TOP_OBJECTS = 10
# Stage and unit-kind pairs are a handful, but a receipt must stay small whatever
# it measures, so the per-scope table is capped and the cap is recorded.
RECEIPT_MAX_SCOPES = 200

LIMITS = (
    "Measures one disposable copy at one moment. It is not a growth rate and not a forecast.",
    "Distinct payload digests are counted per stage and unit kind. The totals figure sums those "
    "sets, so it is an upper bound on the whole-database distinct count: a payload shared by two "
    "scopes is counted twice.",
    "dbstat reports bytes that pages hold, not the file size. A file keeps freed pages on its "
    "free list, so bytes in use and file size are reported separately.",
    "Unverified: that SQLite keeps its reserved lock page off the free list. The accounting check "
    "allows exactly one such page in a database larger than 1 GiB and reports the difference.",
    "Declared row counts come from derivation_generations.row_count; readable rows are counted "
    "by streaming derivation_rows. A generation whose payload bytes are gone declares rows that "
    "cannot be read, so the two are reported separately and compared.",
    "Output rows are read through derivation_rows, which is a table before the payload split "
    "and a view over derivation_row_refs and derivation_payloads after it. The report says which "
    "shape it found; payload bytes are the bytes the rows name, not the bytes stored.",
    "Source-generation JSON column sizes are logical UTF-8 byte counts, not physical SQLite "
    "page sizes or predicted savings. Table and index page sizes come separately from dbstat.",
    "Index physical bytes come from dbstat. Index definitions, columns, expressions, sort order, "
    "collations, uniqueness, origins, and partial flags come from SQLite schema pragmas.",
    "Backup timing is one run on one machine with a cold or warm cache that is not controlled. "
    "Treat it as an order of magnitude, not a service level.",
    "Timing a held checkpoint restores it and then backs up the restored tree, so the restore "
    "figure includes verifying the source and the backup figure covers a real state directory. "
    "Timing a plain state directory does the reverse. The report says which order ran.",
    "Nothing here reads production, publishes, or uses the network.",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def signature(path: Path) -> list[int]:
    """Cheap proof a file did not change: size, modification time, inode."""
    status = path.stat()
    return [status.st_size, status.st_mtime_ns, status.st_ino]


def canonical(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def resolve_database(target: Path) -> tuple[Path, Path]:
    """Accept a state directory or an explicit database path; return both."""
    target = target.expanduser()
    database = target if target.suffix in {".sqlite", ".db"} else target / "state.sqlite"
    require(database.is_file(), f"no state database at {database}")
    require(not database.is_symlink(), "state database must not be a symlink")
    state_dir = database.parent.resolve(strict=True)
    for root in PROTECTED_ROOTS:
        for guarded in {Path(root), Path(root).resolve(strict=False)}:
            require(
                not state_dir.is_relative_to(guarded),
                f"refusing {state_dir}: {root} is the live state. Measure a disposable copy.",
            )
    return state_dir, database.resolve(strict=True)


def resolve_destination(path: Path, label: str, state_dir: Path, *, must_be_new: bool) -> Path:
    """Fence a path this tool writes to, the same way for every one of them.

    A destination inside the measured copy breaks that copy: a sealed checkpoint
    stops verifying against its own manifest the moment a file appears in it. A
    destination under the live root is worse, because every later backup copies
    it. Both are refused here rather than trusted to a runbook.
    """
    resolved = path.expanduser().resolve(strict=False)
    for root in PROTECTED_ROOTS:
        for guarded in {Path(root), Path(root).resolve(strict=False)}:
            require(
                not resolved.is_relative_to(guarded),
                f"refusing to write {label} under {root}: that is the live state. Use scratch.",
            )
    require(not resolved.is_relative_to(state_dir), f"{label} must be outside the measured state")
    require(not state_dir.is_relative_to(resolved), f"{label} must not contain the measured state")
    if must_be_new:
        require(not resolved.exists(), f"{label} must be new; {resolved} already exists")
    return resolved


def source_kind(state_dir: Path) -> str:
    """A sealed checkpoint carries a manifest at its top level; a state dir does not."""
    return "checkpoint" if (state_dir / "checkpoint.json").is_file() else "state"


def sidecar_bytes(database: Path) -> dict[str, int]:
    sizes = {}
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(database) + suffix)
        sizes[suffix.lstrip("-") + "_bytes"] = sidecar.stat().st_size if sidecar.exists() else 0
    return sizes


def open_readonly(database: Path) -> sqlite3.Connection:
    """Open the measured database without any chance of writing to it."""
    sidecars = sidecar_bytes(database)
    require(
        sidecars["wal_bytes"] == 0,
        "database has a nonempty write-ahead log; an immutable reader would ignore it. "
        "Close every connection to the copy, or run PRAGMA wal_checkpoint(TRUNCATE) on it. "
        "A plain PRAGMA wal_checkpoint is not enough: it reuses the file instead of truncating.",
    )
    # Existence, not size: SQLite creates the shared-memory file and only then
    # sizes it, so a copy taken inside that window has a zero-length one.
    require(
        not Path(str(database) + "-shm").exists(),
        "database has a shared-memory file, so a connection is open on it. Measure a closed "
        "disposable copy, never a database something else is using.",
    )
    return sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)


def pragma(connection: sqlite3.Connection, name: str) -> int:
    return int(connection.execute(f"PRAGMA {name}").fetchone()[0])


def schema_kinds(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Map every schema object name to its kind and owning table."""
    kinds: dict[str, dict[str, str]] = {
        "sqlite_schema": {"kind": "schema", "table": "sqlite_schema"},
        "sqlite_master": {"kind": "schema", "table": "sqlite_schema"},
    }
    for name, kind, owner in connection.execute(
        "SELECT name,type,tbl_name FROM sqlite_master WHERE type IN ('table','index')"
    ):
        kinds[str(name)] = {"kind": str(kind), "table": str(owner)}
    return kinds


def storage_objects(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Bytes and pages each table and index occupies, from dbstat."""
    kinds = schema_kinds(connection)
    objects: list[dict[str, Any]] = []
    for name, pages, size in connection.execute(
        "SELECT name,count(*),sum(pgsize) FROM dbstat GROUP BY name"
    ):
        entry = kinds.get(str(name), {"kind": "unknown", "table": str(name)})
        objects.append(
            {
                "name": str(name),
                "kind": entry["kind"],
                "table": entry["table"],
                "pages": int(pages),
                "bytes": int(size),
            }
        )
    objects.sort(key=lambda item: (-int(item["bytes"]), str(item["name"])))
    return objects


def page_accounting(
    connection: sqlite3.Connection, objects: list[dict[str, Any]]
) -> dict[str, Any]:
    """Check that dbstat bytes plus the free list account for the whole file."""
    page_size = pragma(connection, "page_size")
    page_count = pragma(connection, "page_count")
    freelist_count = pragma(connection, "freelist_count")
    used_pages = sum(int(item["pages"]) for item in objects)
    used_bytes = sum(int(item["bytes"]) for item in objects)
    lock_page = PENDING_BYTE // page_size + 1
    expected_reserved = 1 if page_count >= lock_page else 0
    unaccounted_pages = page_count - used_pages - freelist_count
    return {
        "page_size": page_size,
        "page_count": page_count,
        "page_count_bytes": page_count * page_size,
        "freelist_count": freelist_count,
        "freelist_bytes": freelist_count * page_size,
        "used_pages": used_pages,
        "used_bytes": used_bytes,
        "unaccounted_pages": unaccounted_pages,
        "unaccounted_bytes": unaccounted_pages * page_size,
        "reserved_lock_pages_expected": expected_reserved,
        "accounted": unaccounted_pages == expected_reserved,
    }


def table_profile(
    connection: sqlite3.Connection, objects: list[dict[str, Any]], names: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Row counts and dbstat bytes for named tables and views, with their indexes."""
    present = {
        str(row[0]): str(row[1])
        for row in connection.execute(
            "SELECT name,type FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    profile = []
    for name in names:
        kind = present.get(name)
        if kind is None:
            # Absent, not empty. A before-and-after comparison must tell those apart,
            # and so must a table that became a view and now stores no pages itself.
            profile.append({"name": name, "present": False, "kind": None})
            continue
        rows = int(connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])
        table_bytes = sum(
            int(item["bytes"])
            for item in objects
            if item["name"] == name and item["kind"] != "index"
        )
        index_bytes = sum(
            int(item["bytes"])
            for item in objects
            if item["table"] == name and item["kind"] == "index"
        )
        profile.append(
            {
                "name": name,
                "present": True,
                "kind": kind,
                "rows": rows,
                "table_bytes": table_bytes,
                "index_bytes": index_bytes,
                "total_bytes": table_bytes + index_bytes,
            }
        )
    return profile


def _quoted_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _nearest_rank(
    distribution: list[tuple[int, int]], rows: int, numerator: int, denominator: int
) -> int | None:
    """Return an exact nearest-rank percentile from a length/count distribution."""
    if rows == 0:
        return None
    rank = (rows * numerator + denominator - 1) // denominator
    seen = 0
    for length, count in distribution:
        seen += count
        if seen >= rank:
            return length
    raise AssertionError("byte-length distribution did not contain its declared rows")


def source_generation_json_profile(
    connection: sqlite3.Connection, objects: list[dict[str, Any]]
) -> dict[str, Any]:
    """Measure JSON as logical UTF-8 lengths without returning or retaining any body."""
    table_row = connection.execute(
        "SELECT type FROM sqlite_master WHERE name='source_generations'"
    ).fetchone()
    base: dict[str, Any] = {
        "table": "source_generations",
        "present": table_row is not None and str(table_row[0]) == "table",
        "measurement": "logical UTF-8 bytes; not physical SQLite page bytes",
        "physical_table_bytes": sum(
            int(item["bytes"])
            for item in objects
            if item["name"] == "source_generations" and item["kind"] == "table"
        ),
        "physical_index_bytes": sum(
            int(item["bytes"])
            for item in objects
            if item["table"] == "source_generations" and item["kind"] == "index"
        ),
    }
    if not base["present"]:
        return {
            **base,
            "rows": 0,
            "logical_utf8_bytes": 0,
            "columns": [
                {"name": name, "present": False} for name in SOURCE_GENERATION_JSON_COLUMNS
            ],
        }

    table_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(source_generations)")
    }
    table_rows = int(connection.execute("SELECT count(*) FROM source_generations").fetchone()[0])
    columns: list[dict[str, Any]] = []
    for name in SOURCE_GENERATION_JSON_COLUMNS:
        if name not in table_columns:
            columns.append({"name": name, "present": False})
            continue
        identifier = _quoted_identifier(name)
        # SQLite computes and groups only byte lengths. JSON bodies never cross
        # the connection boundary and are never hashed or included in output.
        lengths = [
            (int(length), int(count))
            for length, count in connection.execute(
                f"SELECT octet_length({identifier}),count(*) FROM source_generations "
                f"WHERE {identifier} IS NOT NULL GROUP BY octet_length({identifier}) "
                "ORDER BY octet_length(" + identifier + ")"
            )
        ]
        value_rows = sum(count for _, count in lengths)
        logical_bytes = sum(length * count for length, count in lengths)
        columns.append(
            {
                "name": name,
                "present": True,
                "rows": value_rows,
                "null_rows": table_rows - value_rows,
                "logical_utf8_bytes": logical_bytes,
                "distinct_byte_lengths": len(lengths),
                "min_logical_utf8_bytes": lengths[0][0] if lengths else None,
                "p50_logical_utf8_bytes": _nearest_rank(lengths, value_rows, 50, 100),
                "p90_logical_utf8_bytes": _nearest_rank(lengths, value_rows, 90, 100),
                "p99_logical_utf8_bytes": _nearest_rank(lengths, value_rows, 99, 100),
                "max_logical_utf8_bytes": lengths[-1][0] if lengths else None,
            }
        )
    return {
        **base,
        "rows": table_rows,
        "logical_utf8_bytes": sum(int(column.get("logical_utf8_bytes", 0)) for column in columns),
        "columns": columns,
    }


def index_profiles(
    connection: sqlite3.Connection,
    objects: list[dict[str, Any]],
    table_names: tuple[str, ...] = COSTLY_INDEX_TABLES,
) -> list[dict[str, Any]]:
    """Describe exact index shape beside physical dbstat bytes for selected tables."""
    schema_objects = {
        str(name): str(kind)
        for name, kind in connection.execute(
            "SELECT name,type FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    physical = {str(item["name"]): item for item in objects}
    profiles: list[dict[str, Any]] = []
    for table in table_names:
        kind = schema_objects.get(table)
        if kind is None:
            profiles.append({"table": table, "present": False, "kind": None, "indexes": []})
            continue
        indexes: list[dict[str, Any]] = []
        for _, index_name, unique, origin, partial in connection.execute(
            'SELECT seq,name,"unique",origin,partial FROM pragma_index_list(?)', (table,)
        ):
            definition_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
            ).fetchone()
            columns = []
            for sequence, column_id, column_name, descending, collation, key in connection.execute(
                "SELECT seqno,cid,name,desc,coll,key FROM pragma_index_xinfo(?) ORDER BY seqno",
                (index_name,),
            ):
                cid = int(column_id)
                columns.append(
                    {
                        "sequence": int(sequence),
                        "column_id": cid,
                        "name": None if column_name is None else str(column_name),
                        "kind": "column" if cid >= 0 else ("expression" if cid == -2 else "rowid"),
                        "descending": bool(descending),
                        "collation": None if collation is None else str(collation),
                        "key": bool(key),
                    }
                )
            stored = physical.get(str(index_name))
            indexes.append(
                {
                    "name": str(index_name),
                    "physical_pages": int(stored["pages"]) if stored is not None else 0,
                    "physical_bytes": int(stored["bytes"]) if stored is not None else 0,
                    "unique": bool(unique),
                    "origin": str(origin),
                    "partial": bool(partial),
                    "definition_sql": None
                    if definition_row is None or definition_row[0] is None
                    else str(definition_row[0]),
                    "definition_source": "implicit table constraint"
                    if definition_row is None or definition_row[0] is None
                    else "sqlite_schema.sql",
                    "columns": columns,
                }
            )
        indexes.sort(key=lambda item: str(item["name"]))
        stored_table = physical.get(table)
        profiles.append(
            {
                "table": table,
                "present": True,
                "kind": kind,
                "physical_table_bytes": int(stored_table["bytes"])
                if stored_table is not None and stored_table["kind"] != "index"
                else 0,
                "physical_index_bytes": sum(int(item["physical_bytes"]) for item in indexes),
                "indexes": indexes,
            }
        )
    return profiles


def identity_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'identity/_%' "
            "ESCAPE '/' ORDER BY name"
        )
    )


def derivation_profile(connection: sqlite3.Connection) -> dict[str, Any]:
    """Stream every output row once, keeping only payload digests per scope."""
    shape = connection.execute(
        "SELECT type FROM sqlite_master WHERE name='derivation_rows'"
    ).fetchone()
    if shape is None:
        return {
            "derivation_rows_kind": None,
            "by_scope": [],
            "totals": {
                "generations": 0,
                "declared_row_count": 0,
                "rows": 0,
                "payload_bytes": 0,
                "distinct_payload_sha256_upper_bound": 0,
                "distinct_to_total_rows_upper_bound": None,
            },
            "rows_without_a_generation": 0,
        }
    scopes: dict[tuple[str, str], dict[str, Any]] = {}
    digests: dict[tuple[str, str], set[bytes]] = {}

    def bucket(key: tuple[str, str]) -> dict[str, Any]:
        if key not in scopes:
            scopes[key] = {
                "stage": key[0],
                "unit_kind": key[1],
                "generations": 0,
                "rows": 0,
                "payload_bytes": 0,
            }
            digests[key] = set()
        return scopes[key]

    for stage, unit_kind, generations, declared_rows in connection.execute(
        "SELECT stage,unit_kind,count(*),coalesce(sum(row_count),0) "
        "FROM derivation_generations GROUP BY stage,unit_kind"
    ):
        entry = bucket((str(stage), str(unit_kind)))
        entry["generations"] = int(generations)
        entry["declared_row_count"] = int(declared_rows)

    cursor = connection.execute(
        "SELECT g.stage,g.unit_kind,r.payload_json FROM derivation_rows r "
        "JOIN derivation_generations g ON g.generation_id=r.generation_id"
    )
    for stage, unit_kind, payload in cursor:
        key = (str(stage), str(unit_kind))
        entry = bucket(key)
        body = str(payload).encode()
        entry["rows"] += 1
        entry["payload_bytes"] += len(body)
        digests[key].add(hashlib.sha256(body).digest())

    by_scope = []
    for key in sorted(scopes):
        entry = dict(scopes[key])
        entry.setdefault("declared_row_count", 0)
        entry["distinct_payload_sha256"] = len(digests[key])
        entry["distinct_to_total_rows"] = (
            len(digests[key]) / entry["rows"] if entry["rows"] else None
        )
        by_scope.append(entry)

    total_rows = sum(int(item["rows"]) for item in by_scope)
    total_distinct = sum(int(item["distinct_payload_sha256"]) for item in by_scope)
    orphan_rows = int(
        connection.execute(
            "SELECT count(*) FROM derivation_rows r WHERE NOT EXISTS "
            "(SELECT 1 FROM derivation_generations g WHERE g.generation_id=r.generation_id)"
        ).fetchone()[0]
    )
    return {
        "derivation_rows_kind": str(shape[0]),
        "by_scope": by_scope,
        "totals": {
            "generations": sum(int(item["generations"]) for item in by_scope),
            # What the labels say they hold, against what can actually be read.
            # A generation whose payload bytes are gone declares rows that no
            # longer stream, and that gap skews the distinct-to-total ratio.
            "declared_row_count": sum(int(item["declared_row_count"]) for item in by_scope),
            "rows": total_rows,
            "payload_bytes": sum(int(item["payload_bytes"]) for item in by_scope),
            "distinct_payload_sha256_upper_bound": total_distinct,
            "distinct_to_total_rows_upper_bound": total_distinct / total_rows
            if total_rows
            else None,
        },
        "rows_without_a_generation": orphan_rows,
    }


def gate_results(report: dict[str, Any]) -> dict[str, Any]:
    """The checks plan step 1 is done by. `passed` decides the exit status."""
    derivations = report["derivations"]
    totals = derivations["totals"]
    timing = report["backup_timing"]
    gates: dict[str, Any] = {
        "page_accounting": bool(report["accounting"]["accounted"]),
        "declared_rows_match": int(totals["declared_row_count"]) == int(totals["rows"]),
        "rows_all_name_a_generation": int(derivations["rows_without_a_generation"]) == 0,
        # None means the optional timing leg was not asked for.
        "backup_timing": None if not isinstance(timing, dict) else "error" not in timing,
    }
    gates["passed"] = all(value for value in gates.values() if value is not None)
    return gates


def failed_gates(gates: dict[str, Any]) -> list[str]:
    """The named checks that failed. `passed` is the summary, not a check."""
    return sorted(name for name, value in gates.items() if value is False and name != "passed")


def findings(report: dict[str, Any]) -> list[str]:
    notes = []
    accounting = report["accounting"]
    if accounting["accounted"]:
        notes.append("Every page is accounted for by dbstat, the free list, and reserved pages.")
    else:
        notes.append(
            f"{accounting['unaccounted_pages']} pages "
            f"({accounting['unaccounted_bytes']} bytes) are unaccounted for."
        )
    database = report["database"]
    notes.append(
        f"File size {database['file_bytes']} bytes; bytes in use {accounting['used_bytes']}; "
        f"free list {accounting['freelist_bytes']} bytes."
    )
    totals = report["derivations"]["totals"]
    if totals["declared_row_count"] != totals["rows"]:
        notes.append(
            f"Generations declare {totals['declared_row_count']} output rows but "
            f"{totals['rows']} read back, a difference of "
            f"{totals['declared_row_count'] - totals['rows']}. A row whose payload bytes are not "
            "local does not stream. Every figure below covers the rows that read back."
        )
    else:
        notes.append(
            f"Generations declare {totals['declared_row_count']} output rows and that many read "
            "back."
        )
    if totals["rows"]:
        notes.append(
            f"Output rows {totals['rows']} carry {totals['payload_bytes']} payload bytes; "
            f"distinct payload digests are at most {totals['distinct_payload_sha256_upper_bound']} "
            f"({totals['distinct_to_total_rows_upper_bound']:.4f} of rows)."
        )
    else:
        notes.append("This database holds no derivation output rows.")
    if report["derivations"]["rows_without_a_generation"]:
        notes.append(
            f"{report['derivations']['rows_without_a_generation']} output rows name no generation."
        )
    largest = report["storage"][:3]
    if largest:
        notes.append(
            "Largest objects: "
            + ", ".join(f"{item['name']} {item['bytes']} bytes" for item in largest)
            + "."
        )
    source_json = report["source_generation_json"]
    if source_json["present"]:
        present_columns = [item for item in source_json["columns"] if item["present"]]
        if present_columns:
            largest_column = max(
                present_columns,
                key=lambda item: (int(item["logical_utf8_bytes"]), str(item["name"])),
            )
            notes.append(
                "Source-generation JSON carries "
                f"{source_json['logical_utf8_bytes']} logical UTF-8 bytes; "
                f"{largest_column['name']} is largest at "
                f"{largest_column['logical_utf8_bytes']} logical bytes. These are not physical "
                "SQLite page sizes."
            )
    broken = failed_gates(report["gates"])
    notes.append("Gates failed: " + ", ".join(broken) + "." if broken else "Every gate passed.")
    timing = report["backup_timing"]
    if isinstance(timing, dict) and timing.get("error"):
        notes.append(
            f"Backup timing failed and produced no figures: {timing['error']}. "
            "The measurement above is unaffected."
        )
    elif isinstance(timing, dict):
        notes.append(
            f"Backup timing ({timing['order']}): restore {timing['restore_seconds']:.3f}s, "
            f"create {timing['create_seconds']:.3f}s, verify {timing['verify_seconds']:.3f}s; "
            f"{timing['scratch_bytes']} bytes written under scratch."
        )
    return notes


def measure(target: Path, *, hash_database: bool = False) -> dict[str, Any]:
    """Read one state database and report where its bytes are."""
    state_dir, database = resolve_database(target)
    started = datetime.now(UTC).isoformat()
    # Hashing a multi-gigabyte file twice is the opt-in proof; size, modification
    # time, and inode are the default one, and cost nothing.
    before = sha(database) if hash_database else None
    before_signature = signature(database)
    connection = open_readonly(database)
    try:
        objects = storage_objects(connection)
        accounting = page_accounting(connection, objects)
        history = identity_tables(connection) + HISTORY_TABLES
        report: dict[str, Any] = {
            "format": REPORT_FORMAT,
            "tool_version": TOOL_VERSION,
            "started_at": started,
            "database": {
                "state_dir": str(state_dir),
                "source_kind": source_kind(state_dir),
                "source_checkpoint_manifest_sha256": checkpoint_manifest_sha256(state_dir),
                "path": str(database),
                "file_bytes": database.stat().st_size,
                "sha256": before,
                "user_version": pragma(connection, "user_version"),
                "schema_version": _meta_schema(connection),
                **sidecar_bytes(database),
            },
            "storage": objects,
            "accounting": accounting,
            "derivations": derivation_profile(connection),
            "source_generation_json": source_generation_json_profile(connection, objects),
            "costly_index_profiles": index_profiles(connection, objects),
            "history_tables": table_profile(connection, objects, history),
            "derivation_tables": table_profile(connection, objects, DERIVATION_TABLES),
            "backup_timing": None,
        }
    finally:
        connection.close()
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["gates"] = gate_results(report)
    report["findings"] = findings(report)
    report["limits"] = list(LIMITS)
    if hash_database:
        require(sha(database) == before, "measured database changed while it was read")
    require(signature(database) == before_signature, "measured database changed while it was read")
    report["database_unchanged"] = True
    report["unchanged_check"] = "sha256" if hash_database else "size-mtime-inode"
    return report


def _meta_schema(connection: sqlite3.Connection) -> int | None:
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if present is None:
        return None
    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row is not None else None


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _manifest_schema(checkpoint: Path) -> int:
    manifest = json.loads((checkpoint / "checkpoint.json").read_text())
    return int(manifest["schema_version"])


def checkpoint_manifest_sha256(state_dir: Path) -> str | None:
    """The manifest hash of a copied checkpoint, so a receipt names its source."""
    manifest = state_dir / "checkpoint.json"
    return sha(manifest) if manifest.is_file() else None


def _timed_backup(
    module: Any, state_dir: Path, destination: Path, schema_version: int
) -> dict[str, Any]:
    connection = open_readonly(state_dir / "state.sqlite")
    try:
        start = time.perf_counter()
        result = module.create_checkpoint(
            state_dir,
            connection,
            destination,
            schema_version=schema_version,
            versions={"tool": TOOL_VERSION},
            input_bundle_hash=None,
        )
        created = time.perf_counter()
    finally:
        connection.close()
    module.verify_checkpoint(destination, maximum_schema_version=schema_version)
    verified = time.perf_counter()
    return {
        "create_seconds": created - start,
        "verify_seconds": verified - created,
        "checkpoint_manifest_sha256": result.manifest_hash,
        "checkpoint_file_count": len(result.files),
    }


def time_backup(target: Path, scratch: Path) -> dict[str, Any]:
    """Time a checkpoint, its verification, and a restore, under one scratch tree."""
    state_dir, database = resolve_database(target)
    kind = source_kind(state_dir)
    scratch = resolve_destination(scratch, "scratch", state_dir, must_be_new=True)
    parent = scratch.parent
    require(parent.is_dir(), f"scratch parent {parent} does not exist")
    # The run writes two whole copies of the state tree: one checkpoint and one
    # restore of it. The database file alone is far smaller than that tree.
    tree_bytes = _tree_bytes(state_dir)
    file_bytes = database.stat().st_size
    required_bytes = 2 * tree_bytes
    free_bytes = shutil.disk_usage(parent).free
    require(
        free_bytes >= required_bytes,
        f"free disk {free_bytes} is under the {required_bytes} bytes this run needs: two copies "
        f"of the {tree_bytes}-byte state tree, one checkpoint and one restore",
    )
    checkpoint_module = importlib.import_module("swingset.backup.checkpoint")
    scratch.mkdir(mode=0o700, parents=True)
    destination = scratch / "checkpoint"
    restored = scratch / "restored"
    before = signature(database)
    if kind == "checkpoint":
        # A sealed checkpoint is not a state directory: it carries its own
        # checkpoint.json, which create_checkpoint would copy and then overwrite,
        # failing verification. Restore it first and back up what came out. That
        # is also the order an operator recovers in.
        schema_version = _manifest_schema(state_dir)
        start = time.perf_counter()
        checkpoint_module.restore_checkpoint(
            state_dir, restored, maximum_schema_version=schema_version
        )
        restore_seconds = time.perf_counter() - start
        timings = _timed_backup(checkpoint_module, restored, destination, schema_version)
        order = "restore-then-backup"
        backed_up = restored
    else:
        connection = open_readonly(database)
        try:
            schema_version = pragma(connection, "user_version")
        finally:
            connection.close()
        timings = _timed_backup(checkpoint_module, state_dir, destination, schema_version)
        start = time.perf_counter()
        checkpoint_module.restore_checkpoint(
            destination, restored, maximum_schema_version=schema_version
        )
        restore_seconds = time.perf_counter() - start
        order = "backup-then-restore"
        backed_up = state_dir
    require(signature(database) == before, "measured database changed during backup timing")
    checkpoint_bytes = _tree_bytes(destination)
    restored_bytes = _tree_bytes(restored)
    return {
        "order": order,
        "source_kind": kind,
        "scratch": str(scratch),
        "backed_up_state_dir": str(backed_up),
        "source_database_bytes": file_bytes,
        "source_tree_bytes": tree_bytes,
        "required_free_bytes": required_bytes,
        "free_bytes_before": free_bytes,
        "checkpoint_bytes": checkpoint_bytes,
        "restored_bytes": restored_bytes,
        "scratch_bytes": checkpoint_bytes + restored_bytes,
        "schema_version": schema_version,
        "restore_seconds": restore_seconds,
        "total_seconds": restore_seconds + timings["create_seconds"] + timings["verify_seconds"],
        "network_requests": 0,
        **timings,
    }


def receipt_scopes(by_scope: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-stage rows and ratios, capped so a receipt stays small."""
    if len(by_scope) <= RECEIPT_MAX_SCOPES:
        return list(by_scope)
    largest = sorted(by_scope, key=lambda item: -int(item["rows"]))[:RECEIPT_MAX_SCOPES]
    return sorted(largest, key=lambda item: (str(item["stage"]), str(item["unit_kind"])))


def receipt_index_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep costly index facts while omitting auxiliary rowid terms and full SQL."""
    compact = []
    for profile in profiles:
        if not profile["present"]:
            compact.append(
                {
                    "table": profile["table"],
                    "present": False,
                    "kind": None,
                    "indexes": [],
                }
            )
            continue
        compact.append(
            {
                "table": profile["table"],
                "present": True,
                "kind": profile["kind"],
                "physical_table_bytes": profile["physical_table_bytes"],
                "physical_index_bytes": profile["physical_index_bytes"],
                "indexes": [
                    {
                        "name": index["name"],
                        "physical_bytes": index["physical_bytes"],
                        "unique": index["unique"],
                        "origin": index["origin"],
                        "partial": index["partial"],
                        "key_columns": [
                            {
                                key: column[key]
                                for key in ("column_id", "name", "kind", "descending", "collation")
                            }
                            for column in index["columns"]
                            if column["key"]
                        ],
                    }
                    for index in profile["indexes"]
                ],
            }
        )
    return compact


def build_receipt(
    report: dict[str, Any],
    *,
    command: list[str] | None = None,
    code_revision: str | None = None,
) -> dict[str, Any]:
    """A compact receipt: inputs, headline numbers, findings, and limits."""
    accounting = report["accounting"]
    return {
        "format": RECEIPT_FORMAT,
        "tool_version": TOOL_VERSION,
        "started_at": report["started_at"],
        "finished_at": report["finished_at"],
        "inputs": {
            "state_dir": report["database"]["state_dir"],
            "source_kind": report["database"]["source_kind"],
            "source_checkpoint_manifest_sha256": report["database"][
                "source_checkpoint_manifest_sha256"
            ],
            "database_path": report["database"]["path"],
            "database_bytes": report["database"]["file_bytes"],
            "database_sha256": report["database"]["sha256"],
            "wal_bytes": report["database"]["wal_bytes"],
            "shm_bytes": report["database"]["shm_bytes"],
            "schema_version": report["database"]["schema_version"],
            "command": command,
            "code_revision": code_revision,
        },
        "results": {
            "page_size": accounting["page_size"],
            "page_count": accounting["page_count"],
            "used_bytes": accounting["used_bytes"],
            "freelist_bytes": accounting["freelist_bytes"],
            "unaccounted_bytes": accounting["unaccounted_bytes"],
            "accounted": accounting["accounted"],
            "largest_objects": [
                {key: item[key] for key in ("name", "kind", "bytes")}
                for item in report["storage"][:RECEIPT_TOP_OBJECTS]
            ],
            "derivation_totals": report["derivations"]["totals"],
            # Per-stage ratios are what step 2 is argued from, and the full
            # report is deleted with the scratch tree, so they are kept here.
            "derivation_rows_kind": report["derivations"]["derivation_rows_kind"],
            "derivations_by_scope": receipt_scopes(report["derivations"]["by_scope"]),
            "derivation_scopes_truncated": len(report["derivations"]["by_scope"])
            > RECEIPT_MAX_SCOPES,
            "rows_without_a_generation": report["derivations"]["rows_without_a_generation"],
            "source_generation_json": report["source_generation_json"],
            "costly_index_profiles": receipt_index_profiles(report["costly_index_profiles"]),
            "backup_timing": report["backup_timing"],
        },
        "gates": report["gates"],
        "database_unchanged": report["database_unchanged"],
        "unchanged_check": report["unchanged_check"],
        "findings": report["findings"],
        "limits": report["limits"],
        "production_operations": 0,
        "network_requests": 0,
    }


def run(
    target: Path,
    *,
    output: Path | None,
    receipt: Path | None,
    hash_database: bool,
    scratch: Path | None,
    command: list[str] | None = None,
    code_revision: str | None = None,
) -> dict[str, Any]:
    # Every destination is checked before the measurement, not after it. On a
    # multi-gigabyte copy the measurement is the hours; a destination that is
    # refused afterwards throws all of them away.
    state_dir, _ = resolve_database(target)
    if output is not None:
        output = resolve_destination(output, "--output", state_dir, must_be_new=True)
        output.parent.mkdir(parents=True, exist_ok=True)
    if receipt is not None:
        receipt = resolve_destination(receipt, "--receipt", state_dir, must_be_new=True)
        receipt.parent.mkdir(parents=True, exist_ok=True)
    if scratch is not None:
        resolve_destination(scratch, "scratch", state_dir, must_be_new=True)
    report = measure(target, hash_database=hash_database)
    if scratch is not None:
        # Timing is the optional half of step 1. A failure there must not throw
        # away a measurement that can take hours on a multi-gigabyte copy, so it
        # is recorded as a finding and the report and receipt are still written.
        try:
            report["backup_timing"] = time_backup(target, scratch)
        except Exception as error:
            report["backup_timing"] = {"error": f"{type(error).__name__}: {error}"}
        report["gates"] = gate_results(report)
        report["findings"] = findings(report)
    if output is not None:
        with output.open("x") as handle:
            handle.write(canonical(report))
    if receipt is not None:
        with receipt.open("x") as handle:
            handle.write(
                canonical(build_receipt(report, command=command, code_revision=code_revision))
            )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="state directory or .sqlite path")
    parser.add_argument("--output", type=Path, help="write the full report here")
    parser.add_argument("--receipt", type=Path, help="write a compact receipt here")
    parser.add_argument(
        "--hash-database",
        action="store_true",
        help="record the database SHA-256 in the receipt (reads the whole file twice)",
    )
    parser.add_argument(
        "--code-revision",
        help="the commit this tool was run from, recorded in the receipt",
    )
    parser.add_argument(
        "--time-backup",
        action="store_true",
        help="also time a checkpoint, verify, and restore under --scratch",
    )
    parser.add_argument("--scratch", type=Path, help="new scratch directory for --time-backup")
    args = parser.parse_args()
    if args.time_backup and args.scratch is None:
        parser.error("--time-backup requires --scratch")
    report = run(
        args.state,
        output=args.output,
        receipt=args.receipt,
        hash_database=args.hash_database,
        scratch=args.scratch if args.time_backup else None,
        command=list(sys.argv),
        code_revision=args.code_revision,
    )
    if args.output is None:
        print(canonical(report), end="")
    else:
        print(json.dumps({"output": str(args.output), "findings": report["findings"]}))
    if not report["gates"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
