"""Bounded cutoff-local evidence shared by release and scheduling policies.

The caller supplies a read-only SQLite snapshot. Artifact availability is checked
now, separately from the source cutoff. Limits bound returned data and file reads;
SQL/filesystem calls themselves do not have a hard execution deadline. No result
is cached across sessions and no recovery, request, or database write is performed.
Consumers pin exact support and apply their own release or progress authority.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from swingset.admission.evidence_budget import Budget as _Probe
from swingset.admission.evidence_budget import Limits as ProbeLimits
from swingset.admission.evidence_budget import _Unknown
from swingset.fetch.archive import Archive, canonical, digest
from swingset.schedule.event_evidence import (
    admission_reason,
    decode_generation,
    request_id,
    timestamp,
)
from swingset.schedule.event_evidence import request as normalize_request

FORMAT = "cutoff-request-evidence-v1"


@dataclass(frozen=True)
class Limits:
    candidates: int = 64
    rows: int = 1024
    manifest_members: int = 128
    json_bytes: int = 1024 * 1024
    total_json_bytes: int = 8 * 1024 * 1024
    compressed_bytes: int = 8 * 1024 * 1024
    decoded_bytes: int = 32 * 1024 * 1024
    seconds: float = 2.0

    def __post_init__(self) -> None:
        self.budget()

    def budget(self) -> ProbeLimits:
        return ProbeLimits(members=1, **asdict(self))


_DEFAULT_LIMITS = Limits()

SNAPSHOT = (
    "snapshot_id",
    "watch_id",
    "method",
    "url",
    "form",
    "fetched_at",
    "classification",
    "body_sha256",
    "via",
)
GENERATION = (
    "generation_id",
    "unit_key",
    "input_fingerprint",
    "page_kind",
    "contract_version",
    "manifest_json",
    "recipe_json",
    "report_json",
    "result_json",
    "previous_generation_id",
    "work_token",
    "removal_authority",
    "state",
    "created_at",
)
DECISION = ("decision_id", "generation_id", "state", "reason", "decided_at", "policy_revision")
POLICY = (
    "page_kind",
    "contract_version",
    "mode",
    "policy_revision",
    "reviewed_report_digest",
    "reviewed_by",
    "reviewed_at",
)


def _time(value: str) -> datetime:
    normalized = timestamp(value)
    if normalized is None:
        raise _Unknown("evidence_time_unknown")
    return datetime.fromisoformat(normalized)


class _Evidence:
    def __init__(
        self,
        conn: sqlite3.Connection,
        archive: Archive,
        limits: Limits,
        cutoff: datetime,
        request: Mapping[str, Any],
        reader: _Probe | None = None,
    ):
        self.conn, self.cutoff, self.request = conn, cutoff, request
        # Reuse bounded loaders and streaming digest/JSON checks, not the
        # scheduling probe's verdicts (which have no release cutoff).
        self.reader = reader or _Probe(conn, archive, limits.budget())
        self.used_artifacts: set[tuple[str, str]] = set()
        self.matching: dict[str, dict[str, Any]] = {}
        self.acquired: dict[str, dict[str, Any]] = {}
        self.unknown = False
        self.checked_generations: set[str] = set()
        self.generation_unknown = False
        self.revoked: list[tuple[Any, ...]] | None = None

    def artifact(self, kind: str, sha: str) -> bool:
        self.used_artifacts.add((kind, sha))
        cached = (kind, sha) in self.reader.artifacts
        valid = self.reader.artifact(kind, sha)
        if cached and not valid:
            self.reader.reasons[kind + "_artifact_unavailable"] += 1
        return valid

    def matches(self, row: Mapping[str, Any]) -> bool:
        return (
            normalize_request(self.request["source"], row["method"], row["url"], row["form"])
            == self.request
        )

    def snapshots(self, *, exact: bool) -> bool:
        cap = self.reader.limits.candidates
        # Exact URL first; a second bounded source pass covers alternate URL
        # spellings and watch kinds. Only both exhausted passes justify absence.
        url_predicate = "s.url=?" if exact else "s.url<>?"
        rows = self.reader.read(
            "SELECT s.* FROM snapshots s JOIN watches w USING(watch_id) WHERE w.source=? "
            "AND upper(s.method)=? AND (julianday(s.fetched_at)<=julianday(?) OR julianday(s.fetched_at) IS NULL) "
            f"AND {url_predicate} ORDER BY s.fetched_at DESC,s.snapshot_id DESC LIMIT ?",
            (
                self.request["source"],
                self.request["method"],
                self.cutoff.isoformat(),
                self.request["url"],
                cap,
            ),
            SNAPSHOT,
            cap=cap,
        )
        exhausted = len(rows) < cap
        if not exhausted:
            self.reader.reasons["snapshot_candidate_budget"] += 1
        for row in rows:
            try:
                if _time(row["fetched_at"]) > self.cutoff or not self.matches(row):
                    continue
                self.matching[row["snapshot_id"]] = row
                if row["classification"] in {"Ok", "NotModified"} and self.artifact(
                    "body", row["body_sha256"]
                ):
                    self.acquired[row["snapshot_id"]] = row
            except (ValueError, KeyError, TypeError, _Unknown) as exc:
                self.unknown = True
                self.reader.reasons[
                    str(exc) if isinstance(exc, _Unknown) else "snapshot_evidence_invalid"
                ] += 1
                if isinstance(exc, _Unknown):
                    raise
        return exhausted

    def interpreted(
        self, identifier: str, decision_id: int | None = None, *, unsupported: bool = False
    ) -> dict[str, Any] | None:
        reader = self.reader
        raw = reader.read(
            "SELECT * FROM source_generations WHERE generation_id=?",
            (identifier,),
            GENERATION,
            cap=1,
        )
        if not raw:
            raise _Unknown("source_generation_missing")
        value = decode_generation(raw[0], identifier)
        if _time(value["created_at"]) > self.cutoff:
            return None
        decision_clause = " AND decision_id=?" if decision_id is not None else ""
        decision_parameters = (decision_id,) if decision_id is not None else ()
        decisions = reader.read(
            "SELECT * FROM admission_decisions WHERE generation_id=? AND state='accepted' "
            "AND (julianday(decided_at)<=julianday(?) OR julianday(decided_at) IS NULL)"
            + decision_clause
            + " ORDER BY decision_id",
            (identifier, self.cutoff.isoformat(), *decision_parameters),
            DECISION,
            cap=reader.limits.candidates,
        )
        eligible = [row for row in decisions if _time(row["decided_at"]) <= self.cutoff]
        if not eligible and not unsupported:
            reader.reasons["not_accepted_at_cutoff"] += 1
            return None
        policies = reader.read(
            "SELECT * FROM admission_policies WHERE page_kind=?",
            (value["page_kind"],),
            POLICY,
            cap=1,
        )
        if self.revoked is None:
            revoked = reader.read(
                "SELECT unit_key,input_fingerprint,recipe_json FROM source_generations WHERE state='revoked' ORDER BY generation_id",
                (),
                ("unit_key", "input_fingerprint", "recipe_json"),
                cap=reader.limits.candidates,
            )
            self.revoked = [
                (r["unit_key"], r["input_fingerprint"], r["recipe_json"]) for r in revoked
            ]
        reason = admission_reason(
            self.conn,
            value,
            revoked_rows=self.revoked,
            policy_version=lambda _: policies[0]["contract_version"] if policies else None,
        )
        if unsupported:
            from .unsupported_evidence import explicit_unknown

            if reason != "source_interpretation_unsupported" or not explicit_unknown(value):
                return None
            if (
                not policies
                or policies[0]["contract_version"] != value["contract_version"]
                or not policies[0]["reviewed_report_digest"]
                or not policies[0]["reviewed_by"]
                or not policies[0]["reviewed_at"]
                or _time(policies[0]["reviewed_at"]) > self.cutoff
            ):
                reader.reasons["unsupported_contract_unassessed"] += 1
                return None
        elif reason:
            reader.reasons[reason] += 1
            return None
        if value["recipe"]["context"]["source"] != self.request["source"]:
            reader.reasons["generation_source_mismatch"] += 1
            return None
        manifest = value["manifest"]
        if not isinstance(manifest, list) or not manifest:
            raise ValueError("generation manifest is empty or malformed")
        if unsupported and len(manifest) != 1:
            reader.reasons["unsupported_request_scope_unassessed"] += 1
            return None
        if len(manifest) > reader.limits.manifest_members:
            raise _Unknown("manifest_member_budget")
        support = []
        matched = False
        for member in manifest:
            rows = reader.read(
                "SELECT s.*,w.source AS source FROM snapshots s JOIN watches w USING(watch_id) "
                "WHERE s.snapshot_id=?",
                (member["snapshot_id"],),
                (*SNAPSHOT, "source"),
                cap=1,
            )
            if not rows or any(
                rows[0][key] != member[key] for key in ("watch_id", "url", "body_sha256")
            ):
                reader.reasons["snapshot_evidence_changed"] += 1
                return None
            row = rows[0]
            if row["source"] != self.request["source"]:
                reader.reasons["manifest_source_mismatch"] += 1
                return None
            context = value["recipe"]["context"]
            if row["snapshot_id"] == context["snapshot_id"] and any(
                row[key] != context[key] for key in ("watch_id", "url")
            ):
                reader.reasons["generation_anchor_mismatch"] += 1
                return None
            if _time(row["fetched_at"]) > self.cutoff:
                reader.reasons["manifest_snapshot_after_cutoff"] += 1
                return None
            if row["classification"] not in {"Ok", "NotModified"}:
                reader.reasons["manifest_response_unsuccessful"] += 1
                return None
            if not self.artifact("body", member["body_sha256"]) or not self.artifact(
                "extract", member["extract_sha256"]
            ):
                return None
            support.append({**row, "extract_sha256": member["extract_sha256"]})
            matched = matched or self.matches(row)
        if not matched or value["recipe"]["context"]["snapshot_id"] not in {
            row["snapshot_id"] for row in support
        }:
            reader.reasons["generation_request_support_missing"] += 1
            return None
        return {
            "generation_id": identifier,
            "content_digest": digest(
                canonical(
                    {key: item for key, item in raw[0].items() if key not in {"state", "_bytes"}}
                )
            ),
            "input_fingerprint": value["input_fingerprint"],
            "accepted_decision": None if unsupported else eligible[0],
            "policy": policies[0],
            "snapshots": support,
        }

    def generations(self, *, other_anchors: bool = False) -> tuple[dict[str, Any] | None, bool]:
        watches = sorted({row["watch_id"] for row in self.matching.values()})
        if not watches:
            return None, True
        cap = self.reader.limits.candidates
        # The ordinary indexed watch lookup comes first. Aggregate units may
        # interpret this request while anchored on another watch of the source.
        membership = "NOT IN" if other_anchors else "IN"
        rows = self.reader.read(
            "SELECT g.generation_id FROM source_generations g JOIN source_units u USING(unit_key) "
            "JOIN watches w ON w.watch_id=u.watch_id WHERE w.source=? "
            f"AND u.watch_id {membership} (SELECT value FROM json_each(?)) "
            "AND (julianday(g.created_at)<=julianday(?) OR julianday(g.created_at) IS NULL) "
            "ORDER BY julianday(g.created_at) DESC,g.generation_id DESC LIMIT ?",
            (self.request["source"], json.dumps(watches), self.cutoff.isoformat(), cap),
            ("generation_id",),
            cap=cap,
        )
        exhausted = len(rows) < cap
        if not exhausted:
            self.reader.reasons[
                "source_generation_candidate_budget"
                if other_anchors
                else "generation_candidate_budget"
            ] += 1
        for row in rows:
            if row["generation_id"] in self.checked_generations:
                continue
            try:
                receipt = self.interpreted(row["generation_id"])
                self.checked_generations.add(row["generation_id"])
                if receipt:
                    return receipt, exhausted
            except (ValueError, KeyError, TypeError, RecursionError):
                self.checked_generations.add(row["generation_id"])
                self.generation_unknown = True
                self.reader.reasons["generation_evidence_invalid"] += 1
        return None, exhausted


class Session:
    """One bounded verification session inside one caller-owned read snapshot.

    Budgets and digest checks are shared across requests. Do not retain a session
    across transaction boundaries. Every later validation operation needs a new
    session; this object is not a proof cache or an eligible-progress clock.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        archive: Archive,
        *,
        cutoff: datetime,
        now: datetime,
        limits: Limits = _DEFAULT_LIMITS,
    ):
        if archive.recovery is not None:
            raise ValueError("cutoff verification requires Archive(recovery=None)")
        self.conn, self.archive, self.limits = conn, archive, limits
        self._check_snapshot()
        try:
            self.cutoff, self.now = _time(cutoff.isoformat()), _time(now.isoformat())
        except _Unknown as exc:
            raise ValueError("cutoff and verification time require timezones") from exc
        if self.now < self.cutoff:
            raise ValueError("verification time precedes evidence cutoff")
        self.reader = _Probe(conn, archive, limits.budget())

    def _check_snapshot(self) -> None:
        if (
            not self.conn.in_transaction
            or self.conn.execute("PRAGMA query_only").fetchone()[0] != 1
        ):
            raise ValueError("caller must own a query-only read transaction")

    def read(
        self, sql: str, parameters: tuple[Any, ...], columns: tuple[str, ...], *, cap: int
    ) -> list[dict[str, Any]]:
        """Load caller metadata against this session's cumulative budget.

        SQL and columns are trusted program text, not user input. Exhaustion
        raises evidence_budget.BudgetExceeded; no connection settings change.
        """
        self._check_snapshot()
        if type(cap) is not int or not 1 <= cap <= self.limits.rows:
            raise ValueError("cap must be positive and within the session row limit")
        return self.reader.read(sql, parameters, columns, cap=cap)

    def exhausted(self) -> bool:
        """Cumulative budget exhaustion; local candidate overflow is distinct."""
        self._check_snapshot()
        return self.reader.exhausted()

    def _checker(self, request: Mapping[str, Any]) -> _Evidence:
        self._check_snapshot()
        normalized = normalize_request(
            request["source"], request["method"], request["url"], request.get("form")
        )
        if len(json.dumps(normalized).encode()) > 4 * 1024 * 1024:
            raise ValueError("request exceeds the JSON input bound")
        if dict(request) != normalized:
            raise ValueError("request must use the normalized request identity")
        return _Evidence(self.conn, self.archive, self.limits, self.cutoff, normalized, self.reader)

    def _result(
        self,
        checker: _Evidence,
        receipt: dict[str, Any] | None,
        snapshots_exhausted: bool,
        generations_exhausted: bool,
        reasons_before: Mapping[str, int],
    ) -> dict[str, Any]:
        known = snapshots_exhausted and not checker.unknown
        return {
            "format": FORMAT,
            "request_id": request_id(checker.request),
            "request": checker.request,
            "cutoff": self.cutoff.isoformat(),
            "artifacts_verified_at": self.now.isoformat(),
            "acquired": True if checker.acquired else False if known else None,
            "interpreted": True if receipt else False if known and generations_exhausted else None,
            "snapshots_exhausted": snapshots_exhausted,
            "generations_exhausted": generations_exhausted,
            "acquisition_support": next(iter(checker.acquired.values()), None),
            "interpretation_support": receipt,
            "artifacts": [
                {"kind": kind, "sha256": sha, "valid": valid, "verified_at": self.now.isoformat()}
                for (kind, sha), valid in sorted(self.reader.artifacts.items())
                if (kind, sha) in checker.used_artifacts
            ],
            "reasons": {
                key: value - reasons_before.get(key, 0)
                for key, value in sorted(self.reader.reasons.items())
                if value > reasons_before.get(key, 0)
            },
            "limits": asdict(self.limits),
            "authority": "local verification observations; no release, publication, or scheduling authority",
        }

    def verify_request(
        self,
        request: Mapping[str, Any],
        *,
        classify_unavailability: bool = False,
        classify_unsupported: bool = False,
    ) -> dict[str, Any]:
        """Prove existence; false requires exhausting every relevant candidate domain."""
        checker = self._checker(request)
        reasons_before = dict(self.reader.reasons)
        snapshots_exhausted = generations_exhausted = False
        receipt = None
        try:
            exact_exhausted = checker.snapshots(exact=True)
            if checker.acquired:
                receipt, generations_exhausted = checker.generations()
            if receipt is None:
                aliases_exhausted = checker.snapshots(exact=False)
                snapshots_exhausted = exact_exhausted and aliases_exhausted
                if checker.acquired:
                    receipt, generations_exhausted = checker.generations()
                    if receipt is None:
                        receipt, other_exhausted = checker.generations(other_anchors=True)
                        generations_exhausted = generations_exhausted and other_exhausted
                else:
                    generations_exhausted = snapshots_exhausted
        except _Unknown as exc:
            generations_exhausted = False
            self.reader.reasons[str(exc)] += 1
        unavailable: bool | None = None
        support: dict[str, Any] | None = None
        if classify_unavailability:
            from .unavailable_evidence import observe

            unavailable, support = observe(checker, snapshots_exhausted=snapshots_exhausted)
        unsupported_value, unsupported_support = None, None
        if classify_unsupported:
            from .unsupported_evidence import observe as observe_unsupported

            unsupported_value, unsupported_support = observe_unsupported(
                checker,
                interpreted=receipt is not None,
                exhausted=snapshots_exhausted and generations_exhausted,
            )
        result = self._result(
            checker, receipt, snapshots_exhausted, generations_exhausted, reasons_before
        )
        if classify_unavailability:
            result.update(unavailable=unavailable, unavailability_support=support)
        if classify_unsupported:
            result.update(unsupported=unsupported_value, unsupported_support=unsupported_support)
        return result

    def verify_operation(
        self,
        request: Mapping[str, Any],
        *,
        snapshot_id: str | None = None,
        generation_id: str | None = None,
        decision_id: int | None = None,
    ) -> dict[str, Any]:
        """Check only a supplied snapshot OR generation and accepted decision.

        Verdicts describe the supplied operation, not all retained evidence.
        Progress/frontier rules belong to the caller. Aggregate manifests may
        support a request other than their anchor, but all members must verify.
        """
        if (snapshot_id is not None) == (generation_id is not None) or (
            generation_id is not None
        ) != (decision_id is not None):
            raise ValueError("supply snapshot_id or generation_id with decision_id")
        checker = self._checker(request)
        reasons_before = dict(self.reader.reasons)
        receipt = None
        assessed = False
        try:
            if snapshot_id is not None:
                rows = self.reader.read(
                    "SELECT s.* FROM snapshots s JOIN watches w USING(watch_id) WHERE s.snapshot_id=? AND w.source=?",
                    (snapshot_id, request["source"]),
                    SNAPSHOT,
                    cap=1,
                )
                if rows:
                    row = rows[0]
                    if (
                        _time(row["fetched_at"]) <= self.cutoff
                        and checker.matches(row)
                        and row["classification"] in {"Ok", "NotModified"}
                        and checker.artifact("body", row["body_sha256"])
                    ):
                        checker.acquired[snapshot_id] = row
                assessed = True
            else:
                assert generation_id is not None
                receipt = checker.interpreted(generation_id, decision_id)
                if receipt:
                    for row in receipt["snapshots"]:
                        if checker.matches(row):
                            checker.acquired[row["snapshot_id"]] = {
                                key: row[key] for key in SNAPSHOT
                            }
                assessed = True
        except _Unknown as exc:
            self.reader.reasons[str(exc)] += 1
        except (ValueError, KeyError, TypeError, RecursionError):
            assessed = True
            self.reader.reasons["operation_evidence_invalid"] += 1
        result = self._result(
            checker, receipt, assessed if snapshot_id else False, False, reasons_before
        )
        if generation_id is not None:
            result["interpreted"] = True if receipt else False if assessed else None
        result["selection"] = {
            "snapshot_id": snapshot_id,
            "generation_id": generation_id,
            "decision_id": decision_id,
        }
        return result


def verify_request(
    conn: sqlite3.Connection,
    archive: Archive,
    *,
    request: Mapping[str, Any],
    cutoff: datetime,
    now: datetime,
    limits: Limits = _DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Compatibility convenience: one request with a fresh bounded session."""
    return Session(conn, archive, cutoff=cutoff, now=now, limits=limits).verify_request(request)


def revalidate_positive(
    conn: sqlite3.Connection, archive: Archive, observation: Mapping[str, Any], *, budget: _Probe
) -> bool:
    """Recheck pinned positive support without searching for a replacement.

    Unlike discovery, a completion caller may own a write transaction. This
    function only reads exact supplied evidence. The budget belongs to this
    validation invocation and must never be reused at a later boundary.
    """
    if archive.recovery is not None or budget.conn is not conn or budget.archive is not archive:
        raise ValueError("pinned validation requires its own nonrecovering artifact budget")
    identity = observation["request"]
    normalized = normalize_request(
        identity["source"], identity["method"], identity["url"], identity.get("form")
    )
    if normalized != identity or request_id(identity) != observation["request_id"]:
        return False
    checker = _Evidence(conn, archive, Limits(), _time(observation["cutoff"]), identity, budget)
    if observation["acquired"] is True:
        expected = observation["acquisition_support"]
        if not isinstance(expected, Mapping):
            return False
        rows = budget.read(
            "SELECT s.* FROM snapshots s JOIN watches w USING(watch_id) WHERE s.snapshot_id=? AND w.source=?",
            (expected["snapshot_id"], identity["source"]),
            SNAPSHOT,
            cap=1,
        )
        if not rows or any(rows[0][key] != expected[key] for key in SNAPSHOT):
            return False
        row = rows[0]
        if (
            _time(row["fetched_at"]) > checker.cutoff
            or not checker.matches(row)
            or row["classification"] not in {"Ok", "NotModified"}
            or not checker.artifact("body", row["body_sha256"])
        ):
            return False
    elif observation.get("acquisition_support") is not None:
        return False
    if observation["interpreted"] is True:
        expected = observation["interpretation_support"]
        if not isinstance(expected, Mapping) or observation["acquired"] is not True:
            return False
        actual = checker.interpreted(
            expected["generation_id"], expected["accepted_decision"]["decision_id"]
        )
        if actual != expected:
            return False
    elif observation.get("interpretation_support") is not None:
        return False
    return True
