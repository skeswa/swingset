"""Sample known event obligations without creating completion authority."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from swingset.admission.evidence_budget import BudgetExceeded
from swingset.admission.page_evidence import Session
from swingset.admission.parent_evidence import parent_support

from . import event_gaps

STAGES = ("acquired", "interpreted")
ERRORS = (BudgetExceeded, ValueError, KeyError, TypeError, RecursionError)


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='event_accounting_receipts'"
        ).fetchone()
        is not None
    )


def unknown(reason: str) -> dict[str, Any]:
    return dict(
        assessment="unassessed",
        reason=reason,
        listed_pages=None,
        stages=None,
        unavailable=None,
        unsupported=None,
        page_accounting=None,
        parents=None,
        earliest_checked_at=None,
        valid_until=None,
        retirement="unassessed",
        pagination="unknown",
        current_stage_authority=False,
    )


def fresh(row: dict[str, Any], fence: dict[str, Any], now: datetime, max_age: float) -> bool:
    try:
        value = json.loads(row["token_json"])
        observed = datetime.fromisoformat(row["observed_at"])
        expires = datetime.fromisoformat(row["valid_until"])
        return (
            isinstance(value, dict)
            and value.keys() == fence.keys()
            and all(type(value[k]) is type(fence[k]) and value[k] == fence[k] for k in fence)
            and row["enumeration_id"] == fence["enumeration_id"]
            and observed <= now < expires <= observed + timedelta(seconds=max_age)
        )
    except (ValueError, TypeError, KeyError, RecursionError):
        return False


def pages_ready(
    session: Session,
    batch: dict[str, Any],
    members: list[dict[str, Any]],
    now: datetime,
    max_age: float,
) -> bool:
    """Prioritize parents only after every page has a fresh accounting branch."""
    assessment = observe(session, batch, members, now, max_age, verify_parents=False)["assessment"]
    pages, parents = assessment.get("page_accounting"), assessment.get("parents")
    return bool(
        members
        and pages
        and pages["positive"] == len(members)
        and parents
        and parents["unknown"] > 0
    )


def observe(
    session: Session,
    batch: dict[str, Any],
    members: list[dict[str, Any]],
    now: datetime,
    max_age: float,
    *,
    verify_parents: bool = True,
) -> dict[str, Any]:
    """Use page results already assessed by progress and the very same session."""
    source, ref, enum = (batch[k] for k in ("source", "source_ref", "enumeration_id"))
    result: dict[str, Any] = {"supports": [], "assessment": unknown("accounting_not_assessed")}
    try:
        header = session.read(
            "SELECT parent_support_json,pagination FROM source_event_enumerations WHERE enumeration_id=?",
            (enum,),
            ("parent_support_json", "pagination"),
            cap=1,
        )[0]
        parents = json.loads(header["parent_support_json"])
        if not isinstance(parents, dict) or len(parents) > session.limits.candidates:
            raise ValueError("parent_set_unassessed")
        rows = session.read(
            "SELECT request_id,stage,enumeration_id,availability,observed_at,valid_until,token_json FROM event_progress_observations WHERE source=? AND source_ref=? AND enumeration_id=?",
            (source, ref, enum),
            (
                "request_id",
                "stage",
                "enumeration_id",
                "availability",
                "observed_at",
                "valid_until",
                "token_json",
            ),
            cap=256,
        )
        pages = {(r["request_id"], r["stage"]): r for r in rows}
        gaps = event_gaps.load(session, batch)
        expiry = (now + timedelta(seconds=max_age)).isoformat()
        common = dict(
            enumeration_id=enum,
            observed_at=now.isoformat(),
            valid_until=expiry,
            token_json=encoded(batch["token"]),
        )
        for page in batch["pages"]:
            if page.get("gap") is not None:
                gaps[page["request_id"]] = {
                    **common,
                    "request_id": page["request_id"],
                    **page["gap"],
                }
            for stage, value in page["stages"].items():
                pages[page["request_id"], stage] = {**common, "availability": value["availability"]}
        rows = session.read(
            "SELECT generation_id,enumeration_id,availability,observed_at,valid_until,token_json FROM event_accounting_support_observations WHERE source=? AND source_ref=? AND enumeration_id=?",
            (source, ref, enum),
            (
                "generation_id",
                "enumeration_id",
                "availability",
                "observed_at",
                "valid_until",
                "token_json",
            ),
            cap=session.limits.candidates,
        )
        supports = {r["generation_id"]: r for r in rows}
        gap_values = []
        unsupported_values = []
        page_values = []
        for member in members:
            gap = gaps.get(member["request_id"])
            missing = (
                event_gaps.value(gap, member["request"], batch.get("gap_revision"))
                if gap is not None and fresh(gap, batch["token"], now, max_age)
                else None
            )
            unsupported = (
                event_gaps.value(
                    gap, member["request"], batch.get("gap_revision"), classification="unsupported"
                )
                if gap is not None and fresh(gap, batch["token"], now, max_age)
                else None
            )
            unsupported_values.append(unsupported)
            row = pages.get((member["request_id"], "interpreted"))
            interpreted = (
                bool(row["availability"])
                if row is not None
                and fresh(row, batch["token"], now, max_age)
                and row["availability"] in (0, 1)
                else None
            )
            gap_values.append(missing)
            page_values.append(event_gaps.accounted(interpreted, missing, unsupported))
        all_pages = bool(members) and all(value is True for value in page_values)
        if verify_parents and all_pages:
            # Missing/oldest first: a costly first parent cannot hide later ones.
            for key in sorted(
                parents, key=lambda k: (supports.get(k, {}).get("observed_at", ""), k)
            ):
                if session.exhausted():
                    break
                proof = parent_support(session, source=source, generation_id=key)
                observed = {
                    **common,
                    "generation_id": key,
                    "availability": proof["usable"],
                    "reasons_json": encoded(
                        proof.get("reasons") or ([proof["reason"]] if proof["reason"] else [])
                    ),
                }
                supports[key] = observed
                result["supports"].append(observed)
        required = {
            stage: [pages.get((m["request_id"], stage)) for m in members] for stage in STAGES
        }
        required["parents"] = [supports.get(key) for key in parents]
        counts = {}
        checked = []
        for kind, values in required.items():
            count = dict(positive=0, negative=0, unknown=0)
            for row in values:
                valid = row is not None and fresh(row, batch["token"], now, max_age)
                value = row["availability"] if valid and row is not None else None
                count["positive" if value == 1 else "negative" if value == 0 else "unknown"] += 1
                if valid and value is not None and row is not None:
                    checked.append(row)
            counts[kind] = count

        def total(values: list[bool | None]) -> dict[str, int]:
            return dict(
                positive=sum(v is True for v in values),
                negative=sum(v is False for v in values),
                unknown=sum(v is None for v in values),
            )

        gap_counts, page_counts = total(gap_values), total(page_values)
        for member, value, unsupported in zip(members, gap_values, unsupported_values, strict=True):
            if value is not None or unsupported is not None:
                checked.append(gaps[member["request_id"]])
        negative = bool(page_counts["negative"] or counts["parents"]["negative"])
        complete = bool(members and parents) and not any(
            c["unknown"] or c["negative"] for c in (page_counts, counts["parents"])
        )
        result["assessment"] = dict(
            assessment="unfinished"
            if negative
            else "locally_accounted"
            if complete
            else "unassessed",
            reason=None if negative or complete else "required_support_unassessed",
            listed_pages=len(members),
            stages={s: counts[s] for s in STAGES},
            parents=counts["parents"],
            unavailable=gap_counts,
            unsupported=total(unsupported_values),
            page_accounting=page_counts,
            earliest_checked_at=min(
                (r["observed_at"] for r in checked), key=datetime.fromisoformat, default=None
            ),
            valid_until=min(
                (r["valid_until"] for r in checked), key=datetime.fromisoformat, default=None
            ),
            retirement="unassessed",
            pagination=header["pagination"],
            current_stage_authority=False,
        )
    except ERRORS as exc:
        result["assessment"] = unknown(str(exc))
    return result


def persist(conn: sqlite3.Connection, batch: dict[str, Any], *, now: datetime) -> int:
    """Caller has rechecked the fence and owns the observation transaction."""
    source, ref, enum = (batch[k] for k in ("source", "source_ref", "enumeration_id"))
    value = batch.get(
        "accounting",
        {"supports": [], "assessment": unknown(batch.get("reason", "accounting_not_assessed"))},
    )
    conn.execute(
        "DELETE FROM event_accounting_support_observations WHERE source=? AND source_ref=? AND enumeration_id IS NOT ?",
        (source, ref, enum),
    )
    for row in value["supports"]:
        conn.execute(
            "INSERT INTO event_accounting_support_observations VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source,source_ref,generation_id) DO UPDATE SET enumeration_id=excluded.enumeration_id,availability=excluded.availability,observed_at=excluded.observed_at,valid_until=excluded.valid_until,token_json=excluded.token_json,reasons_json=excluded.reasons_json",
            (
                source,
                ref,
                enum,
                row["generation_id"],
                row["availability"],
                row["observed_at"],
                row["valid_until"],
                row["token_json"],
                row["reasons_json"],
            ),
        )
    prior = conn.execute(
        "SELECT receipt_id,enumeration_id,assessment FROM event_accounting_receipts WHERE source=? AND source_ref=? ORDER BY receipt_id DESC LIMIT 1",
        (source, ref),
    ).fetchone()
    assessment = value["assessment"]
    if prior and (prior["enumeration_id"], prior["assessment"]) == (enum, assessment["assessment"]):
        return 0
    definite = conn.execute(
        "SELECT assessment FROM event_accounting_receipts WHERE source=? AND source_ref=? AND enumeration_id IS ? AND assessment!='unassessed' ORDER BY receipt_id DESC LIMIT 1",
        (source, ref, enum),
    ).fetchone()
    transition = (
        "initial_observation"
        if prior is None
        else "membership_changed"
        if prior["enumeration_id"] != enum
        else "assessment_changed"
    )
    if transition == "assessment_changed" and definite:
        if definite[0] == "locally_accounted" and assessment["assessment"] == "unfinished":
            transition = "reopened"
        elif definite[0] == "unfinished" and assessment["assessment"] == "locally_accounted":
            transition = "availability_restored"
    conn.execute(
        "INSERT INTO event_accounting_receipts(source,source_ref,enumeration_id,previous_receipt_id,assessment,transition,observed_at,earliest_checked_at,valid_until,token_json,summary_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            source,
            ref,
            enum,
            prior["receipt_id"] if prior else None,
            assessment["assessment"],
            transition,
            now.isoformat(),
            assessment["earliest_checked_at"],
            assessment["valid_until"],
            encoded(batch["token"]),
            encoded(assessment),
        ),
    )
    return 1
