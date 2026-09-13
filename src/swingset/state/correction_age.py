"""Measure unresolved correction age from retained clocks and published receipts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except ValueError:
        return None


def record_pending(conn: sqlite3.Connection, state_dir: Path, *, now: str) -> None:
    """Keep the first input correction until a different receipt is confirmed."""
    baseline = state_dir / "baseline"
    commit = ""
    if baseline.is_symlink():
        receipt = json.loads((baseline.resolve(strict=True) / "PUBLISHED").read_bytes())
        commit = str(receipt["commit"])
    previous = dict(
        conn.execute(
            "SELECT key,value FROM meta WHERE key IN ('correction_pending_since','correction_pending_baseline_commit')"
        )
    )
    if previous.get("correction_pending_baseline_commit") == commit and previous.get(
        "correction_pending_since"
    ):
        return
    conn.executemany(
        "INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (("correction_pending_since", now), ("correction_pending_baseline_commit", commit)),
    )


def correction_age(
    conn: sqlite3.Connection,
    *,
    baseline_receipt: Mapping[str, Any] | None,
    baseline_policy: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the earliest evidenced pending detection; unknown history stays explicit."""
    receipt, policy = baseline_receipt or {}, baseline_policy or {}
    verified = _instant(receipt.get("verified_at"))
    generation = policy.get("token", {}).get("journal_generation")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    evidence: list[datetime] = []
    exact = generation is not None and verified is not None
    if "identity_journal_acceptances" in tables:
        for at, identifier in conn.execute(
            "SELECT accepted_at,generation FROM identity_journal_acceptances"
        ):
            instant = _instant(at)
            if instant is not None and (
                (generation is not None and identifier > int(generation))
                or (generation is None and verified is not None and instant > verified)
            ):
                evidence.append(instant)
    if verified is not None:
        for table, column, where in (
            ("identity_reference_migrations", "recorded_at", "1"),
            ("admission_decisions", "decided_at", "state='revoked'"),
        ):
            if table in tables:
                for (at,) in conn.execute(f"SELECT {column} FROM {table} WHERE {where}"):
                    instant = _instant(at)
                    if instant is not None and instant > verified:
                        evidence.append(instant)
    clocks = dict(
        conn.execute(
            "SELECT key,value FROM meta WHERE key IN ('correction_pending_since','correction_pending_baseline_commit')"
        )
    )
    pending = _instant(clocks.get("correction_pending_since"))
    if pending is not None and clocks.get("correction_pending_baseline_commit") == str(
        receipt.get("commit") or ""
    ):
        evidence.append(pending)
    detected = min(evidence) if evidence else None
    if now is not None and now.tzinfo is None:
        raise ValueError("correction age requires a timezone-aware clock")
    return {
        "detected_at": detected.isoformat() if detected else None,
        "age_seconds": max(0.0, (now - detected).total_seconds())
        if now is not None and detected is not None
        else None,
        "basis": "recorded_pending_corrections"
        if exact
        else "known_lower_bound"
        if detected is not None
        else "unknown",
    }
