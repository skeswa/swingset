"""Replay the exact retained phase-one newsletter bodies with parser 8.

This helper is deliberately narrower than an ordinary worker. It accepts only a
disposable copy of the packet-005 input-004 schema-29 rehearsal, performs no
requests or subprocess work, and changes only the 28 pinned newsletter parses,
their ledger receipts, and the phase-one year findings derived from that ledger.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

SOURCE = Path("/nix/store/rgyryll4d55rgzscdqhjcwmkr325a76f-source")
SOURCE_RECEIPT_SHA256 = "a2704c7c99ce5aef4fbd0f0df8b23343be5df2a688945e236b8e6856b320ad5f"
PACKET_SHA256 = "0d455559b7e0cd767cb1739f10271eb12e8178bebb5a4ab283bd8e74733b6ff3"
CHECKPOINT = Path("/var/lib/swingset/checkpoints/extension28-held-20260917-004")
CHECKPOINT_SHA256 = "6ac7bb2d291136ef502abcb33f8762bf4449fd2fdc0160fb995f2aba0921f9d9"
INPUT_BUNDLE_SHA256 = "abdf538777c1e4fc05c7f8b9079701bd5a37f276cabe83a8be023673a941d644"
CATALOG_SHA256 = "0355e34d69e23fc5a7096e07a99d420d4353bdb4fc59284497483ae856d084d9"
LEDGER_SHA256 = "370ad76d175e4e4fa9cf344ec2d47268338dbba128194ecef65904a74632653c"
PREPARE_RECEIPT_SHA256 = "751962638a5264641d07d3994e22ed81c38da9a1e9b6c91fb8ce1eacd6dc29c1"
ACCEPT_RECEIPT_SHA256 = "9ba0ca7bed02b165ca64fc5595ea3466ca28358740e52498a2fe11770b27beff"
SCRATCH_MARKER_SHA256 = "f573f831fa002e4a875767f17e78fb47895f4a97bad840ca9ce7bb42631332dd"
INPUT_HELPER_SHA256 = "aaa702614294edbacb1dce2bd33f7b881c78e8e67d1418e6261d17a8e46336d5"
TARGETS_SHA256 = "1b40cf24822f4b76e5eb727b19d45db703c327c658920daf18383ab6a9877b43"
ORIGINAL_INPUT004 = Path("/var/tmp/swingset-schema29-input-20260917-004")
PARSER = "wsdc_newsletter.events"
PARSER_VERSION = "8"
ACCEPTED_INPUT_NAMES = (
    "config/hosts.toml",
    "config/sources.toml",
    "overrides/event_aliases.csv",
    "recipe/runtime",
    "version/extract/scoringdance.round",
    "version/extract/steprightsolutions.event",
    "version/extract/steprightsolutions.round",
    "version/parser/scoringdance.round",
    "version/parser/steprightsolutions.event",
    "version/parser/steprightsolutions.round",
    "version/parser/wsdc_newsletter.events",
    "version/repository",
)

TARGET_PAIRS = (
    ("0226ef50385bfa517c9ef79c", "snap_20260913T035045Z_008aaf4c6933"),
    ("0b9f1347030560c4c5502e28", "snap_20260913T053204Z_ea0a1cfa6f68"),
    ("0e8afed4a25aa5c6fd5f2741", "snap_20260913T052940Z_0177184cf9eb"),
    ("168739e4497cfe804a117c5e", "snap_20260913T053153Z_b3d4d7e4936b"),
    ("20080cca8335d832e9581711", "snap_20260913T052910Z_44c788523cf4"),
    ("203bf571ded3f493732cea58", "snap_20260913T053313Z_e10a45b1cbaf"),
    ("2722818d1b9f0149372dd827", "snap_20260913T053404Z_622d98a01fec"),
    ("33973c1ce87271a5a7076911", "snap_20260913T053423Z_68c1360eb9f6"),
    ("41a01dbd519400746ec38193", "snap_20260913T053435Z_ed5072b4dea1"),
    ("50d5b572f43b42b244112e92", "snap_20260913T053303Z_220a0f22f0f6"),
    ("5d65c2e1953fcff210252487", "snap_20260913T053354Z_b7a224d7799f"),
    ("5e5ae7f44f03ced7ae98122e", "snap_20260913T053233Z_b00e313eecfa"),
    ("646b6b20f1b95ef1b7984f7e", "snap_20260913T053323Z_59a6bf606025"),
    ("670d9cf6939abc7cfc8e5e58", "snap_20260913T053223Z_aafbd00bb657"),
    ("78a33801c49176cffe273d23", "snap_20260913T053243Z_3347ef6a3141"),
    ("8b5d17fed1c38675081c8342", "snap_20260913T053214Z_085674f10a5d"),
    ("9674911cc8f2d2803682db49", "snap_20260913T053010Z_478d7ddb5edd"),
    ("a85d2b013a50698e8e0a5c49", "snap_20260913T053414Z_e2920d5d80be"),
    ("b64c2d8f8445f615c078e0bb", "snap_20260913T053253Z_e19d60bb1ba4"),
    ("b93c413685e1202255468b9e", "snap_20260913T053334Z_793ee2d9e081"),
    ("b961cd353369476360a0b7d9", "snap_20260913T053443Z_bf5de4bbd205"),
    ("bcf0cbe3c6fa734ee64625e0", "snap_20260913T053454Z_10de9e3a897e"),
    ("c16974cf6a719bb6f6e3c9da", "snap_20260913T053000Z_11d8e6dbdaab"),
    ("c2b50fd841877f25729aeb0c", "snap_20260913T052950Z_43d0406b9025"),
    ("e34b2344655e659de5b60d89", "snap_20260913T052930Z_9938b86a7094"),
    ("e993372b27c920b7bf25238f", "snap_20260913T052919Z_08ef5aac2666"),
    ("f0d02027903ab0803154542c", "snap_20260913T053450Z_ce3c79f4aa99"),
    ("fe170ad2de8e63895afa7eb1", "snap_20260913T053343Z_12545410f858"),
)

TARGET_URL_REDIRECTS = {
    "0226ef50385bfa517c9ef79c": (
        "http://www.worldsdc.com/wp-content/uploads/2020/12/WSDC-Newsletter-Vol-6-April-9-2018.pdf",
        "https://www.worldsdc.com/wp-content/uploads/2020/12/"
        "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
    )
}

# These tables contain authority, acquisition, publication, identity, or request
# state. Parser replay has no reason to change any of them.
PROTECTED_TABLES = (
    "accepted_inputs",
    "admission_policies",
    "admission_reviews",
    "archive_captures",
    "archive_queries",
    "backup_uploads",
    "control_events",
    "control_state",
    "execution_admissions",
    "execution_dependencies",
    "history_acceptance",
    "history_origin_intents",
    "history_origin_operator_refs",
    "history_origin_requests",
    "host_budget",
    "host_request_spacing",
    "host_request_spacing_baselines",
    "hosts",
    "identity_decisions",
    "identity_journal_acceptances",
    "identity_link_history",
    "identity_link_resolutions",
    "identity_links",
    "identity_reference_bindings",
    "identity_reference_migrations",
    "identity_source_refs",
    "operator_pauses",
    "registry_verifications",
)

MUTABLE_TABLES = frozenset(
    {
        "derivation_input_versions",
        "derivation_scopes",
        "event_gap_revisions",
        "finding_support",
        "findings",
        "history_dispatch_fence",
        "observations",
        "pending_work",
        "revisions",
        "requirement_transitions",
        "runs",
        "snapshots",
        "source_generations",
        "source_units",
        "watches",
        "work_generations",
    }
)

SNAPSHOT_PARSE_COLUMNS = frozenset(
    {
        "extract_status",
        "extract_sha256",
        "parse_status",
        "parsed_at",
        "extract_version",
        "parser_version",
        "extract_recipe_sha256",
    }
)

WATCH_PARSE_COLUMNS = frozenset(
    {"current_observation_snapshot_id", "extract_version", "fingerprint"}
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path, expected_sha256: str, label: str) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), f"{label} must be a regular file")
    require(sha(path) == expected_sha256, f"{label} hash differs")
    value = json.loads(path.read_bytes())
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def install_offline_audit_hook() -> None:
    denied = frozenset(
        {
            "os.exec",
            "os.fork",
            "os.forkpty",
            "os.posix_spawn",
            "os.spawn",
            "os.system",
            "pty.spawn",
            "subprocess.Popen",
        }
    )

    def audit(event: str, _args: tuple[object, ...]) -> None:
        # CPython has separate audit events for getaddrinfo, gethostbyname,
        # gethostbyaddr and getnameinfo. Blocking the namespace also covers new
        # DNS/socket variants without relying on a list that can go stale.
        if event.startswith("socket.") or event in denied or event.startswith("os.exec"):
            raise RuntimeError(f"offline replay denied audit event: {event}")

    sys.addaudithook(audit)


def verify_packet_and_receipts(packet_path: Path, prepare_path: Path, accept_path: Path) -> None:
    packet = read_json(packet_path, PACKET_SHA256, "packet-005 manifest")
    require(
        packet.get("format") == "schema28-to29-current-checkpoint-packet-v1"
        and packet.get("source") == str(SOURCE)
        and packet.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and packet.get("checkpoint") == str(CHECKPOINT)
        and packet.get("checkpoint_sha256") == CHECKPOINT_SHA256
        and packet.get("target_schema") == 29
        and packet.get("top_level_sidecars", {}).get("phase1-catalog.json", {}).get("sha256")
        == CATALOG_SHA256
        and packet.get("top_level_sidecars", {}).get("phase1-ledger.json", {}).get("sha256")
        == LEDGER_SHA256,
        "packet-005 authority differs",
    )
    prepare = read_json(prepare_path, PREPARE_RECEIPT_SHA256, "input-004 prepare receipt")
    accept = read_json(accept_path, ACCEPT_RECEIPT_SHA256, "input-004 acceptance receipt")
    require(
        prepare.get("passed") is True
        and prepare.get("phase") == "prepare"
        and prepare.get("source") == str(SOURCE)
        and prepare.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and prepare.get("checkpoint_sha256") == CHECKPOINT_SHA256
        and prepare.get("network_requests") == 0
        and prepare.get("production_acceptance") is False,
        "input-004 preparation authority differs",
    )
    require(
        accept.get("passed") is True
        and accept.get("phase") == "accept"
        and accept.get("source") == str(SOURCE)
        and accept.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and accept.get("checkpoint_sha256") == CHECKPOINT_SHA256
        and accept.get("input_bundle_hash") == INPUT_BUNDLE_SHA256
        and accept.get("network_requests") == 0
        and accept.get("production_acceptance") is False,
        "input-004 acceptance authority differs",
    )


def verify_runtime() -> None:
    require(SOURCE.is_dir() and not SOURCE.is_symlink(), "candidate-006 source is unavailable")
    receipt = SOURCE / "extension-source.json"
    require(
        receipt.is_file() and not receipt.is_symlink() and sha(receipt) == SOURCE_RECEIPT_SHA256,
        "candidate-006 source receipt differs",
    )
    require(not os.environ.get("SWINGSET_REVISION"), "repository identity override is forbidden")
    from swingset.sources.wsdc_newsletter import adapter
    from swingset.state import db

    require(db.SCHEMA_VERSION == 29, "imported schema is not 29")
    require(str(adapter.EventsPage.PARSER_VERSION) == PARSER_VERSION, "newsletter parser is not 8")
    for name, module in tuple(sys.modules.items()):
        module_path = getattr(module, "__file__", None)
        if name == "swingset" or name.startswith("swingset."):
            require(
                module_path is not None
                and Path(module_path).resolve().is_relative_to(SOURCE / "src"),
                "mixed imported runtime module: " + name,
            )


def _marker_authority(marker: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in marker.items() if key not in {"scratch", "prepared_at"}}


def verify_marker_derivation(
    marker: dict[str, Any], reference_marker: dict[str, Any], resolved: Path
) -> None:
    require(
        marker.get("format") == "extension-input-scratch-v1"
        and marker.get("scratch") == str(resolved)
        and marker.get("checkpoint") == str(CHECKPOINT)
        and marker.get("checkpoint_sha256") == CHECKPOINT_SHA256
        and marker.get("source") == str(SOURCE)
        and marker.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and marker.get("schema") == 29,
        "scratch marker authority differs",
    )
    require(
        _marker_authority(marker) == _marker_authority(reference_marker),
        "fresh scratch does not match the pinned input-004 before-state authority",
    )


def _packet_query_hash(
    conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()
) -> dict[str, Any]:
    digest, count = hashlib.sha256(), 0
    for row in conn.execute(query, params):
        body = json.dumps(list(row), separators=(",", ":"), default=str).encode()
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def _packet_protected(conn: sqlite3.Connection, marker: dict[str, Any]) -> dict[str, Any]:
    ordinary = (
        "hosts",
        "host_budget",
        "operator_pauses",
        "control_state",
        "control_events",
        "host_request_spacing",
        "host_request_spacing_baselines",
        "scheduler_event_turns",
        "scheduler_event_requests",
        "scheduler_capacity_requests",
        "history_origin_requests",
    )
    history = (
        "event_stage_operations",
        "event_progress_receipts",
        "event_accounting_receipts",
        "event_retirement_receipts",
        "source_event_retirement_receipts",
        "event_timing_history",
    )
    result = {
        name: _packet_query_hash(conn, f'SELECT * FROM "{name}" ORDER BY rowid')
        for name in ordinary
    }
    limits = marker.get("history_limits")
    require(isinstance(limits, dict) and set(limits) == set(history), "history limits differ")
    limits = cast(dict[str, int], limits)
    for name in history:
        result["original_" + name] = _packet_query_hash(
            conn, f'SELECT * FROM "{name}" WHERE rowid<=? ORDER BY rowid', (limits[name],)
        )
    result["acquired_operations"] = _packet_query_hash(
        conn, "SELECT * FROM event_stage_operations WHERE stage='acquired' ORDER BY rowid"
    )
    result["original_execution_admissions"] = _packet_query_hash(
        conn,
        "SELECT * FROM execution_admissions WHERE rowid<=? ORDER BY rowid",
        (marker["admission_highwater"],),
    )
    result["request_admissions"] = _packet_query_hash(
        conn, "SELECT * FROM execution_admissions WHERE action_kind='request' ORDER BY rowid"
    )
    result["admission_policies"] = _packet_query_hash(
        conn,
        "SELECT * FROM admission_policies WHERE rowid<=? OR mode<>'shadow' ORDER BY rowid",
        (marker["policy_highwater"],),
    )
    return result


def verify_fresh_receipts(prepare_path: Path, accept_path: Path, marker_sha256: str) -> None:
    prepare = read_json(prepare_path, sha(prepare_path), "fresh scratch prepare receipt")
    accept = read_json(accept_path, sha(accept_path), "fresh scratch accept receipt")
    common = {
        "source": str(SOURCE),
        "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "helper_sha256": INPUT_HELPER_SHA256,
        "passed": True,
        "network_requests": 0,
        "production_acceptance": False,
    }
    require(
        all(prepare.get(key) == value for key, value in common.items())
        and prepare.get("format") == "extension-input-rehearsal-v1"
        and prepare.get("phase") == "prepare"
        and prepare.get("marker_sha256") == marker_sha256,
        "fresh scratch prepare receipt differs",
    )
    require(
        all(accept.get(key) == value for key, value in common.items())
        and accept.get("format") == "extension-input-rehearsal-v1"
        and accept.get("phase") == "accept"
        and accept.get("input_bundle_hash") == INPUT_BUNDLE_SHA256
        and tuple(accept.get("accepted_inputs", ())) == ACCEPTED_INPUT_NAMES,
        "fresh scratch accept receipt differs",
    )


def verify_state_path(
    state: Path,
    reference_marker_path: Path,
    fresh_prepare_path: Path,
    fresh_accept_path: Path,
) -> Path:
    require(state.is_absolute(), "scratch path must be absolute")
    require(state != ORIGINAL_INPUT004, "the original input-004 scratch is immutable")
    require(not state.is_symlink(), "scratch path cannot be a symlink")
    resolved = state.resolve(strict=True)
    require(
        resolved.parent == Path("/var/tmp")
        and resolved.name.startswith("swingset-schema29-newsletter-replay-"),
        "scratch must be an explicit disposable /var/tmp newsletter replay copy",
    )
    for name in (
        "state.sqlite",
        "phase1-catalog.json",
        "phase1-ledger.json",
        "extension-input-scratch.json",
        "extension-input-acceptance.json",
        "operator-hold",
    ):
        path = resolved / name
        require(path.is_file() and not path.is_symlink(), f"scratch {name} must be a regular file")
    require((resolved / "state.sqlite").stat().st_nlink == 1, "scratch database is shared")
    reference_marker = read_json(
        reference_marker_path, SCRATCH_MARKER_SHA256, "pinned input-004 scratch marker"
    )
    marker_path = resolved / "extension-input-scratch.json"
    marker = read_json(marker_path, sha(marker_path), "fresh packet-005 scratch marker")
    marker_sha256 = sha(marker_path)
    acceptance_path = resolved / "extension-input-acceptance.json"
    acceptance = read_json(acceptance_path, sha(acceptance_path), "scratch input acceptance")
    verify_marker_derivation(marker, reference_marker, resolved)
    require(
        acceptance.get("bundle_digest") == INPUT_BUNDLE_SHA256
        and acceptance.get("marker_sha256") == marker_sha256
        and isinstance(acceptance.get("external_files"), dict),
        "scratch input acceptance differs",
    )
    expected_external = {
        "config/hosts.toml": sha(SOURCE / "config/hosts.toml"),
        "config/sources.toml": sha(SOURCE / "config/sources.toml"),
        **{"overrides/" + path.name: sha(path) for path in (SOURCE / "overrides").glob("*.csv")},
    }
    require(
        acceptance["external_files"] == expected_external,
        "scratch external input bytes differ from candidate 006",
    )
    baseline = resolved / "baseline"
    require(
        baseline.is_symlink()
        and baseline.resolve() == resolved / "candidates/cand_8f31cad7226643ae",
        "scratch baseline link differs",
    )
    for path in resolved.rglob("*"):
        require(not path.is_symlink() or path == baseline, "unexpected scratch symlink")
    verify_fresh_receipts(fresh_prepare_path, fresh_accept_path, marker_sha256)
    return resolved


def _normalize(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, float):
        if math.isnan(value):
            return {"float": "nan"}
        if math.isinf(value):
            return {"float": "+infinity" if value > 0 else "-infinity"}
        if value == 0:
            return 0.0
    return value


def query_hash(
    conn: sqlite3.Connection, statement: str, parameters: tuple[object, ...] = ()
) -> dict[str, Any]:
    cursor = conn.execute(statement, parameters)
    columns = tuple(item[0] for item in (cursor.description or ()))
    rows = [[_normalize(value) for value in row] for row in cursor.fetchall()]
    rows.sort(key=canonical)
    return {
        "rows": len(rows),
        "sha256": hashlib.sha256(canonical({"columns": columns, "rows": rows})).hexdigest(),
    }


def table_hash(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    require(re.fullmatch(r"[a-z_][a-z0-9_]*", table) is not None, "unsafe table name")
    schema = [
        [_normalize(value) for value in row]
        for row in conn.execute(f'PRAGMA table_xinfo("{table}")')
    ]
    require(bool(schema), "table schema is missing: " + table)
    columns = tuple(str(row[1]) for row in schema)
    primary = tuple(
        str(row[1]) for row in sorted(schema, key=lambda item: int(item[5])) if int(row[5])
    )

    def quote(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    projection = ",".join(quote(column) for column in columns)
    ordering_terms = [quote(column) for column in primary]
    for column in columns:
        quoted = quote(column)
        ordering_terms.extend(
            (
                f"CASE typeof({quoted}) WHEN 'null' THEN 0 WHEN 'integer' THEN 1 "
                "WHEN 'real' THEN 2 WHEN 'text' THEN 3 WHEN 'blob' THEN 4 ELSE 5 END",
                f"CASE WHEN typeof({quoted}) IN ('integer','real') THEN {quoted} END",
                f"(CASE WHEN typeof({quoted})='text' THEN {quoted} END) COLLATE BINARY",
                f"(CASE WHEN typeof({quoted})='blob' THEN hex({quoted}) END) COLLATE BINARY",
                f"quote({quoted}) COLLATE BINARY",
            )
        )
    ordering = ",".join(ordering_terms)
    cursor = conn.execute(f'SELECT {projection} FROM "{table}" ORDER BY {ordering}')
    digest = hashlib.sha256()

    def update(value: object) -> None:
        body = canonical(value)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)

    update({"format": "streamed-table-hash-v1", "schema": schema})
    rows = 0
    for row in cursor:
        update([_normalize(value) for value in row])
        rows += 1
    update({"rows": rows})
    return {"rows": rows, "sha256": digest.hexdigest()}


def existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def protected_hashes(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    tables = existing_tables(conn)
    require(set(PROTECTED_TABLES) <= tables, "schema omits a protected table")
    return {table: table_hash(conn, table) for table in PROTECTED_TABLES}


def snapshot_hashes(
    conn: sqlite3.Connection, snapshot_ids: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(snapshots)")]
    immutable = [column for column in columns if column not in SNAPSHOT_PARSE_COLUMNS]
    require("snapshot_id" in immutable and "body_sha256" in immutable, "snapshot schema differs")
    projection = ",".join(f'"{column}"' for column in immutable)
    placeholders = ",".join("?" for _ in snapshot_ids)
    return {
        "acquisition_identity": query_hash(conn, f"SELECT {projection} FROM snapshots"),
        "non_targets_full": query_hash(
            conn,
            f"SELECT * FROM snapshots WHERE snapshot_id NOT IN ({placeholders})",
            tuple(snapshot_ids),
        ),
    }


def watch_hashes(
    conn: sqlite3.Connection, snapshot_ids: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    placeholders = ",".join("?" for _ in snapshot_ids)
    target_rows = tuple(
        conn.execute(
            f"SELECT snapshot_id,watch_id FROM snapshots WHERE snapshot_id IN ({placeholders})",
            snapshot_ids,
        )
    )
    require(len(target_rows) == len(snapshot_ids), "target watch closure differs")
    watch_ids = tuple(sorted({str(row[1]) for row in target_rows}))
    require(bool(watch_ids), "target watch closure is empty")
    watch_placeholders = ",".join("?" for _ in watch_ids)
    columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(watches)")]
    immutable = [column for column in columns if column not in WATCH_PARSE_COLUMNS]
    require("watch_id" in immutable, "watch schema differs")
    projection = ",".join(f'"{column}"' for column in immutable)
    return {
        "target_immutable": query_hash(
            conn,
            f"SELECT {projection} FROM watches WHERE watch_id IN ({watch_placeholders})",
            watch_ids,
        ),
        "non_targets_full": query_hash(
            conn,
            f"SELECT * FROM watches WHERE watch_id NOT IN ({watch_placeholders})",
            watch_ids,
        ),
    }


def non_target_mutable_hashes(
    conn: sqlite3.Connection, snapshot_ids: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    placeholders = ",".join("?" for _ in snapshot_ids)
    target_rows = {
        str(row[0]): tuple(str(value) for value in row[1:])
        for row in conn.execute(
            f"SELECT s.snapshot_id,s.watch_id,s.via,w.kind,w.source,w.parser "
            f"FROM snapshots s JOIN watches w USING(watch_id) "
            f"WHERE s.snapshot_id IN ({placeholders})",
            snapshot_ids,
        )
    }
    require(len(target_rows) == len(snapshot_ids), "target snapshot closure differs")
    watch_ids = tuple(target_rows[snapshot][0] for snapshot in snapshot_ids)
    require(len(watch_ids) == len(snapshot_ids), "target watch closure differs")
    watch_placeholders = ",".join("?" for _ in watch_ids)
    unit_keys = tuple(
        sorted(
            {
                expected_source_unit_key(
                    target_rows[snapshot][0],
                    snapshot,
                    target_rows[snapshot][1],
                    target_rows[snapshot][2],
                    target_rows[snapshot][3],
                    target_rows[snapshot][4],
                )
                for snapshot in snapshot_ids
            }
        )
    )
    unit_placeholders = ",".join("?" for _ in unit_keys)
    return {
        "observations": query_hash(
            conn,
            f"SELECT * FROM observations WHERE watch_id NOT IN ({watch_placeholders})",
            watch_ids,
        ),
        "source_units": query_hash(
            conn,
            f"SELECT * FROM source_units WHERE unit_key NOT IN ({unit_placeholders})",
            unit_keys,
        ),
        "source_generations": query_hash(
            conn,
            f"SELECT * FROM source_generations WHERE unit_key NOT IN ({unit_placeholders})",
            unit_keys,
        ),
        "admission_decisions": query_hash(
            conn,
            f"SELECT * FROM admission_decisions WHERE generation_id NOT IN "
            f"(SELECT generation_id FROM source_generations WHERE unit_key IN ({unit_placeholders}))",
            unit_keys,
        ),
        "findings": query_hash(
            conn,
            f"SELECT * FROM findings WHERE owner_kind<>'phase1_year' "
            f"AND (snapshot_id IS NULL OR snapshot_id NOT IN ({placeholders})) "
            f"AND (watch_id IS NULL OR watch_id NOT IN ({watch_placeholders}))",
            (*snapshot_ids, *watch_ids),
        ),
        "finding_support": query_hash(
            conn,
            f"SELECT s.* FROM finding_support s JOIN findings f USING(finding_id) "
            f"WHERE f.owner_kind<>'phase1_year' "
            f"AND (f.snapshot_id IS NULL OR f.snapshot_id NOT IN ({placeholders})) "
            f"AND (f.watch_id IS NULL OR f.watch_id NOT IN ({watch_placeholders}))",
            (*snapshot_ids, *watch_ids),
        ),
        "pending_parse_and_link": query_hash(
            conn,
            f"SELECT * FROM pending_work WHERE stage='link' OR "
            f"(stage='parse' AND unit_id NOT IN ({placeholders}))",
            snapshot_ids,
        ),
        "work_generations_parse_and_link": query_hash(
            conn,
            f"SELECT * FROM work_generations WHERE stage='link' OR "
            f"(stage='parse' AND unit_id NOT IN ({placeholders}))",
            snapshot_ids,
        ),
        "event_pressure_dirty": query_hash(
            conn,
            f"SELECT * FROM event_pressure_dirty WHERE watch_id NOT IN ({watch_placeholders})",
            watch_ids,
        ),
        "other_source_event_progress_observations": query_hash(
            conn, "SELECT * FROM event_progress_observations WHERE source<>'wsdc_newsletter'"
        ),
        "other_source_event_progress_receipts": query_hash(
            conn, "SELECT * FROM event_progress_receipts WHERE source<>'wsdc_newsletter'"
        ),
        "other_source_event_gap_observations": query_hash(
            conn, "SELECT * FROM event_gap_observations WHERE source<>'wsdc_newsletter'"
        ),
        "other_source_event_gap_revisions": query_hash(
            conn, "SELECT * FROM event_gap_revisions WHERE source<>'wsdc_newsletter'"
        ),
        "other_source_event_timing": query_hash(
            conn, "SELECT * FROM event_timing WHERE source<>'wsdc_newsletter'"
        ),
        "other_source_event_timing_history": query_hash(
            conn, "SELECT * FROM event_timing_history WHERE source<>'wsdc_newsletter'"
        ),
    }


def invariant_hashes(conn: sqlite3.Connection, snapshot_ids: tuple[str, ...]) -> dict[str, Any]:
    return {
        "request_admissions": table_hash(conn, "execution_admissions"),
        "watches": watch_hashes(conn, snapshot_ids),
        "snapshots": snapshot_hashes(conn, snapshot_ids),
        "non_target_mutable": non_target_mutable_hashes(conn, snapshot_ids),
        "accepted_years": table_hash(conn, "history_acceptance"),
        "accepted_year_count": int(
            conn.execute("SELECT COUNT(*) FROM history_acceptance").fetchone()[0]
        ),
        "protected_tables": protected_hashes(conn),
        "foreign_keys": query_hash(conn, "PRAGMA foreign_key_check"),
    }


def all_table_hashes(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {table: table_hash(conn, table) for table in sorted(existing_tables(conn))}


def changed_tables(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> tuple[str, ...]:
    require(set(before) == set(after), "SQLite table closure changed")
    changed = tuple(sorted(table for table in before if before[table] != after[table]))
    unexpected = tuple(sorted(set(changed) - MUTABLE_TABLES))
    require(
        not unexpected,
        "unexpected parser-mutated tables: " + ",".join(unexpected),
    )
    return changed


QueueKey = tuple[str, str, str]
QueueRows = dict[QueueKey, tuple[object, ...]]


def queue_rows(conn: sqlite3.Connection, table: str) -> QueueRows:
    require(table in {"pending_work", "work_generations"}, "queue table differs")
    columns = tuple(str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")'))
    require(
        columns[:3] == ("stage", "unit_kind", "unit_id"),
        "queue key schema differs: " + table,
    )
    rows: QueueRows = {}
    for row in conn.execute(f'SELECT * FROM "{table}"'):
        key = (str(row[0]), str(row[1]), str(row[2]))
        require(key not in rows, "duplicate queue key: " + "/".join(key))
        rows[key] = tuple(row)
    return rows


def target_project_queue_keys(
    conn: sqlite3.Connection, snapshot_ids: tuple[str, ...]
) -> frozenset[QueueKey]:
    placeholders = ",".join("?" for _ in snapshot_ids)
    target_rows = tuple(
        conn.execute(
            f"SELECT snapshot_id,watch_id FROM snapshots WHERE snapshot_id IN ({placeholders})",
            snapshot_ids,
        )
    )
    require(len(target_rows) == len(snapshot_ids), "target queue watch closure differs")
    watch_ids = tuple(sorted({str(row[1]) for row in target_rows}))
    require(bool(watch_ids), "target queue watch closure is empty")
    watch_placeholders = ",".join("?" for _ in watch_ids)
    scopes = {
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            f"SELECT DISTINCT scope_kind,scope_id FROM observations "
            f"WHERE watch_id IN ({watch_placeholders})",
            watch_ids,
        )
    }
    keys = {("project", kind, identifier) for kind, identifier in scopes}
    if any(kind in {"calendar", "source_index"} for kind, _identifier in scopes):
        keys.add(("project", "map", "all"))
    return frozenset(keys)


def _changed_queue_keys(before: QueueRows, after: QueueRows) -> frozenset[QueueKey]:
    return frozenset(
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    )


def _queue_key_list(keys: frozenset[QueueKey]) -> str:
    return ",".join("/".join(key) for key in sorted(keys))


def verify_queue_transition(
    before_pending: QueueRows,
    after_pending: QueueRows,
    before_generations: QueueRows,
    after_generations: QueueRows,
    snapshot_ids: tuple[str, ...],
    before_project_keys: frozenset[QueueKey],
    after_project_keys: frozenset[QueueKey],
) -> None:
    target_parse_keys = frozenset(("parse", "snapshot", snapshot) for snapshot in snapshot_ids)
    allowed = target_parse_keys | before_project_keys | after_project_keys
    changed_pending = _changed_queue_keys(before_pending, after_pending)
    unexpected_pending = changed_pending - allowed
    require(
        not unexpected_pending,
        "unexpected pending-work transition: " + _queue_key_list(unexpected_pending),
    )
    changed_generations = _changed_queue_keys(before_generations, after_generations)
    expected_generation_changes = frozenset(key for key in changed_pending if key in after_pending)
    generation_difference = changed_generations ^ expected_generation_changes
    require(
        not generation_difference,
        "work-generation transition differs from pending-work trigger: "
        + _queue_key_list(generation_difference),
    )


def difference_paths(before: object, after: object, prefix: str = "") -> tuple[str, ...]:
    if before == after:
        return ()
    if isinstance(before, dict) and isinstance(after, dict):
        differences: list[str] = []
        for key in sorted(set(before) | set(after), key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                differences.append(path)
            else:
                differences.extend(difference_paths(before[key], after[key], path))
        return tuple(differences)
    return (prefix or "<root>",)


def require_same(before: object, after: object, label: str) -> None:
    differences = difference_paths(before, after)
    if not differences:
        return
    shown = differences[:24]
    suffix = f" (+{len(differences) - len(shown)} more)" if len(differences) > len(shown) else ""
    raise ValueError(f"{label}: {','.join(shown)}{suffix}")


def require_exact_tables(
    conn: sqlite3.Connection,
    expected: dict[str, dict[str, Any]],
    label: str,
) -> dict[str, dict[str, Any]]:
    """Require every application-table row to match a reviewed derivation."""
    actual = all_table_hashes(conn)
    require_same(expected, actual, label + " table state differs from the sealed derivation")
    return actual


def install_deterministic_sql_clock(conn: sqlite3.Connection, at: datetime) -> None:
    """Make trigger-created timestamps repeatable in dry and committed replays."""
    fixed = at.astimezone(UTC)

    def strftime(format_string: str, value: str) -> str:
        require(value == "now", "replay SQL requested a non-current clock value")
        if format_string == "%Y-%m-%dT%H:%M:%f+00:00":
            return fixed.isoformat(timespec="milliseconds")
        if format_string == "%Y-%m-%dT%H:%M:%fZ":
            return (
                fixed.strftime("%Y-%m-%dT%H:%M:")
                + f"{fixed.second:02d}.{fixed.microsecond // 1000:03d}Z"
            )
        raise ValueError("replay SQL requested an unexpected clock format")

    conn.create_function("strftime", 2, strftime, deterministic=True)


@contextlib.contextmanager
def disk_backed_database_copy(source: sqlite3.Connection, state: Path) -> Iterator[Any]:
    """Yield an exact disposable SQLite copy without writing the source."""
    from swingset.state.db import Database

    tmpdir = Path(os.environ.get("TMPDIR", ""))
    require(tmpdir == Path("/var/tmp"), "seal requires TMPDIR=/var/tmp")
    root_stat = tmpdir.lstat()
    require(
        stat.S_ISDIR(root_stat.st_mode) and not tmpdir.is_symlink(),
        "seal TMPDIR must be the real /var/tmp directory",
    )
    copy_dir = Path(tempfile.mkdtemp(prefix="swingset-phase1-parser8-", dir=tmpdir))
    copy_stat = copy_dir.lstat()
    if (
        copy_dir.parent != tmpdir
        or not stat.S_ISDIR(copy_stat.st_mode)
        or copy_dir.is_symlink()
        or stat.S_IMODE(copy_stat.st_mode) != 0o700
    ):
        if copy_dir.is_symlink():
            copy_dir.unlink()
        elif copy_dir.is_dir():
            copy_dir.rmdir()
        raise ValueError("disposable SQLite directory differs")
    database_path = copy_dir / "state.sqlite"
    conn: sqlite3.Connection | None = None
    try:
        descriptor = os.open(
            database_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
        path_stat = database_path.lstat()
        require(
            stat.S_ISREG(path_stat.st_mode)
            and not database_path.is_symlink()
            and stat.S_IMODE(path_stat.st_mode) == 0o600,
            "disposable SQLite path differs",
        )
        conn = sqlite3.connect(f"file:{database_path}?mode=rw", uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=FILE")
        source.backup(conn)
        require(
            database_path.lstat().st_ino == path_stat.st_ino
            and database_path.lstat().st_dev == path_stat.st_dev
            and not database_path.is_symlink(),
            "disposable SQLite path was substituted",
        )
        database = Database(state, conn, None)
        yield database
    finally:
        if conn is not None:
            conn.close()
        require(
            copy_dir.exists()
            and not copy_dir.is_symlink()
            and copy_dir.lstat().st_ino == copy_stat.st_ino
            and copy_dir.lstat().st_dev == copy_stat.st_dev,
            "disposable SQLite directory was substituted",
        )
        for child in copy_dir.iterdir():
            require(
                child.is_file() and not child.is_symlink(),
                "unexpected disposable SQLite artifact",
            )
            child.unlink()
        copy_dir.rmdir()


def verify_catalog_and_ledger(state: Path) -> tuple[tuple[Any, ...], dict[str, Any]]:
    from swingset.history.catalog import load_catalog

    catalog_path, ledger_path = state / "phase1-catalog.json", state / "phase1-ledger.json"
    require(sha(catalog_path) == CATALOG_SHA256, "phase-one catalog hash differs")
    require(sha(ledger_path) == LEDGER_SHA256, "phase-one ledger hash differs")
    catalog = load_catalog(catalog_path)
    ledger = json.loads(ledger_path.read_bytes())
    require(
        len(catalog) == 213 and len(ledger.get("targets", {})) == 213, "phase-one closure differs"
    )
    selected = tuple(
        sorted(
            (target.target_id, str(ledger["targets"].get(target.target_id, {}).get("snapshot_id")))
            for target in catalog
            if target.parser == PARSER
        )
    )
    require(selected == TARGET_PAIRS, "the exact 28 newsletter target/snapshot pairs differ")
    require(
        hashlib.sha256(canonical(selected)).hexdigest() == TARGETS_SHA256, "target hash differs"
    )
    return catalog, cast(dict[str, Any], ledger)


def verify_database_authority(
    conn: sqlite3.Connection, state: Path, *, require_fresh: bool, verify_bundle: bool = True
) -> None:
    require(conn.execute("PRAGMA user_version").fetchone()[0] == 29, "scratch pragma is not 29")
    schema = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    bundle = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
    require(schema is not None and schema[0] == "29", "scratch meta schema is not 29")
    require(
        bundle is not None and bundle[0] == INPUT_BUNDLE_SHA256,
        "scratch accepted input bundle differs",
    )
    require(
        conn.execute("SELECT 1 FROM execution_admissions WHERE state<>'settled' LIMIT 1").fetchone()
        is None,
        "scratch has unsettled request or control admissions",
    )
    require(
        conn.execute("PRAGMA foreign_key_check").fetchone() is None, "scratch foreign keys fail"
    )
    marker = json.loads((state / "extension-input-scratch.json").read_bytes())
    require(
        _packet_protected(conn, marker) == marker.get("protected"),
        "scratch differs from the packet-sealed protected before-state",
    )
    prepared_at = marker.get("prepared_at")
    require(isinstance(prepared_at, str), "scratch preparation time differs")
    if require_fresh:
        require(
            conn.execute("SELECT 1 FROM runs WHERE started_at>? LIMIT 1", (prepared_at,)).fetchone()
            is None,
            "scratch has been operated after packet preparation",
        )
    if not verify_bundle:
        return
    bundle = state / "inputs" / INPUT_BUNDLE_SHA256
    manifest_path = bundle / "manifest.json"
    require(
        bundle.is_dir() and not bundle.is_symlink() and manifest_path.is_file(),
        "accepted input bundle bytes are missing",
    )
    manifest = json.loads(manifest_path.read_bytes())
    require(isinstance(manifest, dict) and bool(manifest), "accepted input manifest differs")
    require(
        hashlib.sha256(canonical(manifest)).hexdigest() == INPUT_BUNDLE_SHA256,
        "accepted input manifest digest differs",
    )
    actual_files = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path != manifest_path
    }
    require(actual_files == set(manifest), "accepted input bundle closure differs")
    files: dict[str, bytes] = {}
    for name, digest in manifest.items():
        path = bundle / str(name)
        require(
            not Path(str(name)).is_absolute()
            and ".." not in Path(str(name)).parts
            and path.is_file()
            and not path.is_symlink()
            and sha(path) == digest,
            "accepted input bundle file differs: " + str(name),
        )
        files[str(name)] = path.read_bytes()
    from swingset.config import parse_history_start
    from swingset.fetch.archive import digest as artifact_digest
    from swingset.state.recipes import captured_recipe_inputs

    expected_inputs = {
        name: artifact_digest(body)
        for name, body in files.items()
        if not name.startswith(("runtime/", "recipes/"))
    }
    expected_inputs.update(
        captured_recipe_inputs(
            files,
            history_start=parse_history_start(files["config/sources.toml"]).isoformat(),
        )
    )
    expected_inputs["policy/inventory_year"] = artifact_digest(canonical(2026))
    versions = json.loads(files.pop("versions.json"))
    del expected_inputs["versions.json"]
    expected_inputs.update({"version/" + name: value for name, value in versions.items()})
    actual_inputs = dict(
        conn.execute("SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline'")
    )
    empty = artifact_digest(b"")
    require(
        all(actual_inputs.get(name) == digest for name, digest in expected_inputs.items())
        and all(
            name in expected_inputs or (name.startswith("overrides/") and digest == empty)
            for name, digest in actual_inputs.items()
        ),
        "accepted semantic inputs differ from retained bundle bytes",
    )


def verify_targets_and_bodies(
    conn: sqlite3.Connection, state: Path, catalog: tuple[Any, ...], ledger: dict[str, Any]
) -> dict[str, str]:
    from swingset.fetch.archive import Archive

    targets = {target.target_id: target for target in catalog}
    archive = Archive(state)
    bodies: dict[str, str] = {}
    for target_id, snapshot_id in TARGET_PAIRS:
        target = targets[target_id]
        receipt = ledger["targets"][target_id]
        require(
            target.source == "wsdc_newsletter"
            and target.parser == PARSER
            and receipt.get("snapshot_id") == snapshot_id,
            "newsletter target identity differs: " + target_id,
        )
        row = conn.execute(
            "SELECT s.body_sha256,s.url,w.source,w.parser,w.url FROM snapshots s "
            "JOIN watches w USING(watch_id) WHERE s.snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        require(
            row is not None
            and row[0]
            and row[2] == target.source
            and row[3] == target.parser
            and isinstance(receipt.get("url"), str),
            "newsletter snapshot identity differs: " + snapshot_id,
        )
        verify_target_url_binding(
            target_id,
            str(target.url),
            str(receipt["url"]),
            str(row[1]),
            str(row[4]),
        )
        body_path = archive.blob_path(str(row[0]))
        require(body_path.is_file() and not body_path.is_symlink(), "newsletter body is linked")
        archive.verify_body(str(row[0]))
        bodies[snapshot_id] = str(row[0])
    return bodies


def verify_target_url_binding(
    target_id: str,
    retained_url: str,
    ledger_url: str,
    snapshot_url: str,
    watch_url: str,
) -> None:
    redirect = TARGET_URL_REDIRECTS.get(target_id)
    if redirect is None:
        require(
            retained_url == ledger_url == snapshot_url == watch_url,
            "newsletter target URL differs: " + target_id,
        )
        return
    require(
        (retained_url, ledger_url, snapshot_url, watch_url)
        == (redirect[0], redirect[0], redirect[1], redirect[1]),
        "newsletter target redirect binding differs: " + target_id,
    )


def non_target_ledger_hash(ledger: dict[str, Any]) -> dict[str, Any]:
    selected = {target_id for target_id, _snapshot_id in TARGET_PAIRS}
    rows = {key: value for key, value in ledger["targets"].items() if key not in selected}
    require(len(rows) == 185, "non-target ledger closure differs")
    return {"rows": len(rows), "sha256": hashlib.sha256(canonical(rows)).hexdigest()}


def expected_source_unit_key(
    watch_id: str, snapshot_id: str, via: str, watch_kind: str, source: str, parser: str
) -> str:
    from swingset.history.acquisition import PHASE1_KINDS

    independent_capture = (
        via == "wayback"
        and watch_kind == "index"
        and parser in PHASE1_KINDS
        and parser.split(".")[0] == source
    )
    return f"{watch_id}/{snapshot_id}" if independent_capture else watch_id


def verify_promoted(
    conn: sqlite3.Connection,
    target_id: str,
    snapshot_id: str,
    replay_run_id: str,
    replay_created_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = conn.execute(
        "SELECT s.watch_id,s.parse_status,s.parser_version,s.parsed_at,w.parser,"
        "s.via,w.kind,w.source "
        "FROM snapshots s JOIN watches w USING(watch_id) WHERE s.snapshot_id=?",
        (snapshot_id,),
    ).fetchone()
    require(
        row is not None
        and row[1] == "ok"
        and str(row[2]) == PARSER_VERSION
        and row[3] is not None
        and row[4] == PARSER,
        "parser-8 snapshot promotion failed: " + target_id,
    )
    unit_key = expected_source_unit_key(
        str(row[0]), snapshot_id, str(row[5]), str(row[6]), str(row[7]), str(row[4])
    )
    generation = conn.execute(
        "SELECT g.generation_id,g.state,u.accepted_generation_id,g.recipe_json,g.run_id,"
        "u.watch_id,u.page_kind,g.created_at,g.report_json,g.manifest_json,g.result_json "
        "FROM source_generations g JOIN source_units u USING(unit_key) "
        "WHERE g.unit_key=? ORDER BY g.created_at DESC,g.generation_id DESC LIMIT 1",
        (unit_key,),
    ).fetchone()
    require(generation is not None, "parser-8 generation is missing: " + target_id)
    recipe = json.loads(generation[3])
    require(
        recipe.get("parser_version") == PARSER_VERSION
        and recipe.get("context", {}).get("snapshot_id") == snapshot_id,
        "parser-8 generation recipe differs: " + target_id,
    )
    require(
        generation[4] == replay_run_id and generation[5] == row[0] and generation[6] == PARSER,
        "parser-8 generation was not created by this replay: " + target_id,
    )
    require(
        generation[7] == replay_created_at
        and conn.execute(
            "SELECT COUNT(*) FROM source_generations WHERE unit_key=? AND run_id=?",
            (unit_key, replay_run_id),
        ).fetchone()[0]
        == 1,
        "parser-8 replay generation time or cardinality differs: " + target_id,
    )
    report = json.loads(generation[8])
    failures = set(report.get("failures", ()))
    expected_state = (
        "staged"
        if not failures
        else "waiting_for_inputs"
        if failures
        <= {"coverage_missing_page", "coverage_no_terminal", "manifest_artifact_missing"}
        else "needs_review"
    )
    require(
        report.get("page_kind") == PARSER
        and report.get("state") == expected_state
        and generation[1] == expected_state,
        "parser-8 generation report state differs: " + target_id,
    )
    manifest = json.loads(generation[9])
    require(
        isinstance(manifest, list)
        and len(manifest) == 1
        and isinstance(manifest[0], dict)
        and manifest[0].get("slot") == snapshot_id
        and manifest[0].get("snapshot_id") == snapshot_id
        and manifest[0].get("watch_id") == row[0]
        and str(manifest[0].get("parser_version")) == PARSER_VERSION,
        "parser-8 generation manifest differs: " + target_id,
    )
    result = json.loads(generation[10])
    result_observations = result.get("observations") if isinstance(result, dict) else None
    require(
        isinstance(result_observations, list)
        and isinstance(result.get("watches"), list)
        and isinstance(result.get("warnings"), list)
        and isinstance(result.get("legitimate_empty"), bool)
        and all(
            isinstance(observation, dict)
            and isinstance(observation.get("kind"), str)
            and isinstance(observation.get("scope"), dict)
            and isinstance(observation["scope"].get("kind"), str)
            and isinstance(observation["scope"].get("ref"), str)
            and "payload" in observation
            for observation in result_observations
        ),
        "parser-8 generation result differs: " + target_id,
    )
    result_observations = cast(list[Any], result_observations)
    policy = conn.execute(
        "SELECT mode FROM admission_policies WHERE page_kind=?", (PARSER,)
    ).fetchone()
    mode = "shadow" if policy is None else str(policy[0])
    require(mode == "shadow", "newsletter admission must remain shadow for this replay")
    require(
        generation[2] is None,
        "parser-8 shadow generation state differs",
    )
    observations = len(result_observations)
    status = "parsed" if observations else "empty"
    receipt = {
        "status": status,
        "snapshot_id": snapshot_id,
        "observations": observations,
        "parser_version": PARSER_VERSION,
    }
    promotion = {
        "target_id": target_id,
        "snapshot_id": snapshot_id,
        "mode": mode,
        "generation_id": str(generation[0]),
        "generation_state": str(generation[1]),
        "observations": observations,
        "result_sha256": hashlib.sha256(str(generation[10]).encode()).hexdigest(),
        "materialized_observations": query_hash(
            conn,
            "SELECT * FROM observations WHERE snapshot_id=?",
            (snapshot_id,),
        ),
    }
    return receipt, promotion


def _target_phase(conn: sqlite3.Connection) -> str:
    versions = {
        str(row[0])
        for _target_id, snapshot_id in TARGET_PAIRS
        for row in conn.execute(
            "SELECT parser_version FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
        )
    }
    if versions == {"6"}:
        return "before"
    if versions == {PARSER_VERSION}:
        return "committed"
    raise ValueError("scratch has a partial or unexpected newsletter parser state")


def _updated_ledger(
    original: bytes, replacements: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], bytes]:
    updated = json.loads(original)
    for target_id, receipt in replacements.items():
        updated["targets"][target_id] = {**updated["targets"][target_id], **receipt}
    body = canonical(updated) + b"\n"
    return cast(dict[str, Any], updated), body


def _write_once(path: Path, body: bytes, label: str) -> None:
    from swingset.fetch.archive import durable_write

    if path.exists() or path.is_symlink():
        require(
            path.is_file() and not path.is_symlink() and path.read_bytes() == body,
            label + " differs",
        )
        return
    durable_write(path, body)
    require(path.read_bytes() == body, label + " write differs")


def seal_before_state(
    state: Path,
    output: Path,
    reference_marker: Path,
    fresh_prepare: Path,
    fresh_accept: Path,
) -> dict[str, Any]:
    from swingset.clock import FakeClock, SystemClock
    from swingset.history.closure import synchronize_year_findings, year_gaps
    from swingset.schedule.parse import parse_snapshot
    from swingset.state.db import open_database

    require(output == state / "phase1-newsletter-parser8-before.json", "before-state path differs")
    require(not output.exists() and not output.is_symlink(), "before-state seal must be new")
    catalog, ledger = verify_catalog_and_ledger(state)
    original_ledger = (state / "phase1-ledger.json").read_bytes()
    snapshot_ids = tuple(snapshot for _target, snapshot in TARGET_PAIRS)
    with open_database(state, lock=True, lock_timeout=0, read_only=True) as database:
        conn = database.connection
        verify_database_authority(conn, state, require_fresh=True)
        verify_targets_and_bodies(conn, state, catalog, ledger)
        require(_target_phase(conn) == "before", "before-state is not parser 6")
        before_invariants = invariant_hashes(conn, snapshot_ids)
        before_tables = all_table_hashes(conn)
        operation_at = SystemClock().now()
        with disk_backed_database_copy(conn, state) as dry_database:
            install_deterministic_sql_clock(dry_database.connection, operation_at)
            expected_after_tables, replay_run_id = apply_database_transaction(
                dry_database,
                state,
                catalog,
                original_ledger,
                before_invariants,
                before_tables,
                FakeClock(operation_at),
                parse_snapshot,
                synchronize_year_findings,
                year_gaps,
            )
        document = {
            "format": "phase1-newsletter-parser8-before-v1",
            "helper_sha256": sha(Path(__file__)),
            "state": str(state),
            "source": str(SOURCE),
            "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
            "packet_sha256": PACKET_SHA256,
            "checkpoint": str(CHECKPOINT),
            "checkpoint_sha256": CHECKPOINT_SHA256,
            "reference_marker_sha256": sha(reference_marker),
            "fresh_marker_sha256": sha(state / "extension-input-scratch.json"),
            "fresh_prepare_receipt_sha256": sha(fresh_prepare),
            "fresh_accept_receipt_sha256": sha(fresh_accept),
            "input_helper_sha256": INPUT_HELPER_SHA256,
            "input_bundle_sha256": INPUT_BUNDLE_SHA256,
            "catalog_sha256": CATALOG_SHA256,
            "ledger_sha256": LEDGER_SHA256,
            "target_pairs_sha256": TARGETS_SHA256,
            "operation_at": operation_at.isoformat(),
            "replay_run_id": replay_run_id,
            "before_invariants": before_invariants,
            "before_tables": before_tables,
            "expected_after_tables": expected_after_tables,
            "network_requests": 0,
            "database_changes": 0,
        }
    _write_once(output, canonical(document) + b"\n", "before-state seal")
    return document


def recovery_marker_document(
    state: Path,
    output: Path,
    ledger_output: Path,
    before_state_sha256: str,
    before_seal: dict[str, Any],
) -> dict[str, Any]:
    """Derive the complete recovery authority from the reviewed seal."""
    return {
        "format": "phase1-newsletter-parser8-recovery-v2",
        "state": str(state),
        "receipt": str(output),
        "ledger_output": str(ledger_output),
        "before_state_sha256": before_state_sha256,
        "reviewed_before_state": before_seal,
    }


def verify_recovery_marker(path: Path, expected: dict[str, Any]) -> None:
    expected_body = canonical(expected) + b"\n"
    if path.exists() or path.is_symlink():
        require(path.is_file() and not path.is_symlink(), "replay recovery marker differs")
        require(path.read_bytes() == expected_body, "replay recovery marker authority differs")
        return
    _write_once(path, expected_body, "replay recovery marker")


@contextlib.contextmanager
def maintenance_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Own one long disposable maintenance write without the worker deadline."""
    require(not conn.in_transaction, "maintenance transaction is already active")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    else:
        try:
            require(conn.in_transaction, "maintenance transaction ended before validation")
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise


def apply_database_transaction(
    database: Any,
    state: Path,
    catalog: tuple[Any, ...],
    original_ledger: bytes,
    before_invariants: dict[str, Any],
    before_tables: dict[str, dict[str, Any]],
    clock: Any,
    parse: Any,
    synchronize: Any,
    gaps: Any,
    expected_after_tables: dict[str, dict[str, Any]] | None = None,
    expected_run_id: str | None = None,
) -> tuple[dict[str, dict[str, Any]], str]:
    from swingset.fetch.archive import Archive
    from swingset.state.work import WorkUnit

    conn = database.connection
    with maintenance_transaction(conn):
        snapshot_ids = tuple(snapshot for _target, snapshot in TARGET_PAIRS)
        before_pending = queue_rows(conn, "pending_work")
        before_generations = queue_rows(conn, "work_generations")
        before_project_keys = target_project_queue_keys(conn, snapshot_ids)
        operation_at = clock.now()
        run_id = database.start_run(operation_at, dry_run=True)
        if expected_run_id is not None:
            require(run_id == expected_run_id, "replay run identity differs from sealed derivation")
        archive = Archive(state)
        for target_id, snapshot_id in TARGET_PAIRS:
            attempt = parse(
                database,
                archive,
                WorkUnit("parse", "snapshot", snapshot_id),
                clock,
                run_id,
            )
            require(
                not attempt.failed and attempt.selection is None,
                "newsletter parse was not accepted or shadowed: " + target_id,
            )
        replacements = {
            target_id: verify_promoted(
                conn, target_id, snapshot_id, run_id, operation_at.isoformat()
            )[0]
            for target_id, snapshot_id in TARGET_PAIRS
        }
        updated, _ledger_body = _updated_ledger(original_ledger, replacements)
        synchronize(
            conn,
            gaps(
                conn,
                catalog,
                updated["targets"],
                final_year=clock.now().year,
                history_start=datetime(2010, 1, 1, tzinfo=UTC).date(),
            ),
            now=clock.now().isoformat(),
            run_id=run_id,
        )
        verify_queue_transition(
            before_pending,
            queue_rows(conn, "pending_work"),
            before_generations,
            queue_rows(conn, "work_generations"),
            snapshot_ids,
            before_project_keys,
            target_project_queue_keys(conn, snapshot_ids),
        )
        after = invariant_hashes(conn, snapshot_ids)
        require_same(
            before_invariants,
            after,
            "protected state changed during newsletter replay",
        )
        after_tables = all_table_hashes(conn)
        changed_tables(before_tables, after_tables)
        if expected_after_tables is not None:
            require_same(
                expected_after_tables,
                after_tables,
                "replay differs from the sealed dry derivation",
            )
        require(sha(state / "phase1-catalog.json") == CATALOG_SHA256, "catalog changed")
        require(conn.execute("PRAGMA foreign_key_check").fetchone() is None, "foreign keys fail")
    return after_tables, run_id


def replay(
    state: Path,
    output: Path,
    ledger_output: Path,
    before_state_path: Path,
    before_state_sha256: str,
) -> dict[str, Any]:
    from swingset.clock import FakeClock
    from swingset.history.closure import synchronize_year_findings, year_gaps
    from swingset.schedule.parse import parse_snapshot
    from swingset.state.db import open_database

    require(
        output == state / "phase1-newsletter-parser8-receipt.json"
        and ledger_output == state / "phase1-ledger-parser8.json",
        "outputs must use the fixed scratch-local recovery paths",
    )
    require(output.parent.is_dir() and not output.parent.is_symlink(), "receipt parent differs")
    before_seal = read_json(before_state_path, before_state_sha256, "reviewed before-state seal")
    helper_sha256 = sha(Path(__file__))
    require(
        before_state_path == state / "phase1-newsletter-parser8-before.json"
        and before_seal.get("format") == "phase1-newsletter-parser8-before-v1"
        and before_seal.get("helper_sha256") == helper_sha256
        and before_seal.get("state") == str(state)
        and before_seal.get("source") == str(SOURCE)
        and before_seal.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and before_seal.get("packet_sha256") == PACKET_SHA256
        and before_seal.get("checkpoint") == str(CHECKPOINT)
        and before_seal.get("checkpoint_sha256") == CHECKPOINT_SHA256
        and before_seal.get("reference_marker_sha256") == SCRATCH_MARKER_SHA256
        and before_seal.get("fresh_marker_sha256") == sha(state / "extension-input-scratch.json")
        and before_seal.get("input_helper_sha256") == INPUT_HELPER_SHA256
        and before_seal.get("input_bundle_sha256") == INPUT_BUNDLE_SHA256
        and before_seal.get("catalog_sha256") == CATALOG_SHA256
        and before_seal.get("ledger_sha256") == LEDGER_SHA256
        and before_seal.get("target_pairs_sha256") == TARGETS_SHA256
        and isinstance(before_seal.get("fresh_prepare_receipt_sha256"), str)
        and isinstance(before_seal.get("fresh_accept_receipt_sha256"), str)
        and isinstance(before_seal.get("before_invariants"), dict)
        and isinstance(before_seal.get("before_tables"), dict)
        and isinstance(before_seal.get("expected_after_tables"), dict)
        and isinstance(before_seal.get("operation_at"), str)
        and isinstance(before_seal.get("replay_run_id"), str)
        and before_seal.get("database_changes") == 0,
        "reviewed before-state authority differs",
    )
    catalog, ledger = verify_catalog_and_ledger(state)
    original_ledger = (state / "phase1-ledger.json").read_bytes()
    before_non_targets = non_target_ledger_hash(ledger)
    snapshot_ids = tuple(snapshot_id for _target_id, snapshot_id in TARGET_PAIRS)
    operation_at = datetime.fromisoformat(str(before_seal["operation_at"]))
    require(operation_at.tzinfo is not None, "reviewed operation clock is not UTC-aware")
    clock = FakeClock(operation_at)
    replay_run_id = str(before_seal["replay_run_id"])
    promotions: list[dict[str, Any]] = []
    bodies: dict[str, str] = {}
    before = cast(dict[str, Any], before_seal["before_invariants"])
    before_tables = cast(dict[str, dict[str, Any]], before_seal["before_tables"])
    expected_after_tables = cast(dict[str, dict[str, Any]], before_seal["expected_after_tables"])
    after: dict[str, Any] = {}
    marker_path = state / "phase1-newsletter-parser8-replay.pending.json"
    expected_marker = recovery_marker_document(
        state, output, ledger_output, before_state_sha256, before_seal
    )
    with open_database(state, lock=True, lock_timeout=0) as database:
        conn = database.connection
        bodies = verify_targets_and_bodies(conn, state, catalog, ledger)
        phase = _target_phase(conn)
        marker_exists = marker_path.exists() or marker_path.is_symlink()
        if marker_exists:
            verify_recovery_marker(marker_path, expected_marker)
        if phase == "before":
            verify_database_authority(conn, state, require_fresh=True)
            require_same(
                before,
                invariant_hashes(conn, snapshot_ids),
                "scratch invariants changed after the reviewed before-state seal",
            )
            require(
                all_table_hashes(conn) == before_tables,
                "scratch tables changed after the reviewed before-state seal",
            )
            require(
                not output.exists() and not ledger_output.exists(),
                "before-state outputs must be absent",
            )
            if not marker_exists:
                verify_recovery_marker(marker_path, expected_marker)
            install_deterministic_sql_clock(conn, operation_at)
            apply_database_transaction(
                database,
                state,
                catalog,
                original_ledger,
                before,
                before_tables,
                clock,
                parse_snapshot,
                synchronize_year_findings,
                year_gaps,
                expected_after_tables,
                replay_run_id,
            )
        else:
            require(marker_exists, "committed replay has no reviewed recovery marker")
            verify_database_authority(conn, state, require_fresh=False)
            require_exact_tables(conn, expected_after_tables, "resumed replay")
        require(_target_phase(conn) == "committed", "database replay did not commit atomically")
        post_replacements: dict[str, dict[str, Any]] = {}
        for target_id, snapshot_id in TARGET_PAIRS:
            receipt, promotion = verify_promoted(
                conn,
                target_id,
                snapshot_id,
                replay_run_id,
                operation_at.isoformat(),
            )
            post_replacements[target_id] = receipt
            promotions.append(promotion)
        updated, ledger_body = _updated_ledger(original_ledger, post_replacements)
        require(non_target_ledger_hash(updated) == before_non_targets, "non-target ledger changed")
        after = invariant_hashes(conn, snapshot_ids)
        require_same(before, after, "post-commit protected state changed")
        final_tables = require_exact_tables(conn, expected_after_tables, "committed replay")
        changed = changed_tables(before_tables, final_tables)
        require(sha(state / "phase1-ledger.json") == LEDGER_SHA256, "original ledger changed")
        require(sha(state / "phase1-catalog.json") == CATALOG_SHA256, "catalog changed")
        _write_once(ledger_output, ledger_body, "sealed parser-8 ledger")
    receipt = {
        "format": "phase1-newsletter-parser8-replay-v1",
        "passed": True,
        "source": str(SOURCE),
        "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
        "packet_sha256": PACKET_SHA256,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "input_bundle_sha256": INPUT_BUNDLE_SHA256,
        "catalog": {"rows": 213, "sha256": CATALOG_SHA256},
        "ledger_before_sha256": LEDGER_SHA256,
        "ledger_after_sha256": sha(ledger_output),
        "original_ledger_preserved": sha(state / "phase1-ledger.json") == LEDGER_SHA256,
        "target_pairs_sha256": TARGETS_SHA256,
        "before_state_sha256": before_state_sha256,
        "replay_run_id": replay_run_id,
        "body_sha256_by_snapshot": bodies,
        "targets": promotions,
        "non_target_ledger": {"before": before_non_targets, "after": before_non_targets},
        "invariants": {"before": before, "after": after},
        "table_hashes": {"before": before_tables, "after": final_tables, "changed": changed},
        "recovery_marker_sha256": sha(marker_path),
        "network_requests": 0,
        "subprocesses": 0,
        "production_operations": 0,
        "production_acceptance": False,
        "year_acceptance": False,
        "publication": False,
    }
    _write_once(output, canonical(receipt) + b"\n", "parser-8 replay receipt")
    return receipt


def main() -> None:
    sys.dont_write_bytecode = True
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--prepare-receipt", type=Path, required=True)
    parser.add_argument("--accept-receipt", type=Path, required=True)
    parser.add_argument("--reference-marker", type=Path, required=True)
    parser.add_argument("--fresh-prepare-receipt", type=Path, required=True)
    parser.add_argument("--fresh-accept-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger-output", type=Path, required=True)
    parser.add_argument("--before-state", type=Path, required=True)
    parser.add_argument("--before-state-sha256")
    parser.add_argument("--seal-only", action="store_true")
    args = parser.parse_args()
    verify_packet_and_receipts(args.packet, args.prepare_receipt, args.accept_receipt)
    state = verify_state_path(
        args.state,
        args.reference_marker,
        args.fresh_prepare_receipt,
        args.fresh_accept_receipt,
    )
    install_offline_audit_hook()
    verify_runtime()
    if args.seal_only:
        require(args.before_state_sha256 is None, "seal cannot accept its own future hash")
        result = seal_before_state(
            state,
            args.before_state,
            args.reference_marker,
            args.fresh_prepare_receipt,
            args.fresh_accept_receipt,
        )
    else:
        require(
            isinstance(args.before_state_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", args.before_state_sha256) is not None,
            "run requires an independently reviewed before-state SHA-256",
        )
        result = replay(
            state,
            args.output,
            args.ledger_output,
            args.before_state,
            args.before_state_sha256,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
