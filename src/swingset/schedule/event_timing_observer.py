"""Bounded cycle observer; request mutations delimit acquisition waiting proofs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from swingset.admission.enumeration_evidence import members
from swingset.admission.evidence_budget import BudgetExceeded
from swingset.admission.page_evidence import Limits, Session
from swingset.admission.parent_evidence import parent_support
from swingset.clock import Clock
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.fetch.eligibility import Assessment, ordinary, union
from swingset.history.archive_timing import ArchiveOfferProof, OfferTiming, epoch
from swingset.state.controls import ActionScope, ControlPaused, operation
from swingset.state.db import Database

from .event_pressure import token
from .event_timing import Recorder, encoded, sample

_active: ContextVar[Observer | None] = ContextVar("event_timing_observer", default=None)


def checkpoint() -> None:
    if observer := _active.get():
        observer.close()


def resume() -> None:
    if observer := _active.get():
        observer.resume()


def archive_offers() -> OfferTiming | None:
    observer = _active.get()
    if observer is None or not observer.subjects:
        return None
    watches = tuple(
        sorted({watch for subject in observer.subjects for watch in subject["watches"]})
    )
    return OfferTiming(
        observer.database.connection,
        observer.client.config,
        observer.clock,
        watches=watches,
        run_id=observer.run_id,
        deadline=observer.deadline,
        accept=observer.accept_archive_proofs,
    )


@contextmanager
def observing(observer: Observer) -> Iterator[None]:
    previous = _active.set(observer)
    try:
        try:
            with operation(
                observer.database,
                action_id="event_timing_" + uuid4().hex,
                action_kind="project",
                clock=observer.clock,
                run_id=observer.run_id,
                scope=ActionScope(
                    all_sources=True,
                    kinds=frozenset(
                        {
                            "work_attempt",
                            "archive_artifact",
                            "admission_blocked",
                            "parse_failure",
                        }
                    ),
                ),
            ):
                observer.begin()
        except ControlPaused:
            # Observation is optional diagnostic work. Do not stop independent
            # acquisition or verify artifacts through a paused observation gate.
            pass
        yield
    finally:
        observer.close(inactive=True)
        _active.reset(previous)


class Observer:
    """At most eight events, 32 members each, one shared evidence budget per phase.

    Persisted rotation is observation coverage, never fleet completion history.
    Gate changes outside the hooks leave explicit gaps; reports never sample.
    """

    def __init__(
        self,
        database: Database,
        client: FetchClient,
        clock: Clock,
        *,
        run_id: str,
        deadline: datetime,
    ) -> None:
        self.database, self.client, self.clock = database, client, clock
        self.run_id, self.deadline = run_id, deadline
        config = client.config.scheduler
        self.policy = {
            "format": "acquisition-waiting-v1",
            "historical_dispatch": "archive-offer-timing-v1",
            "events": 8,
            "members": 32,
            "service_gap": config.event_acquisition_service_alarm_seconds,
            "no_successful_progress": config.event_acquisition_progress_alarm_seconds,
            "objectives": "unmeasured_until_operating_acceptance",
        }
        self.digest = sha256(encoded(self.policy).encode()).hexdigest()
        self.subjects: list[dict[str, Any]] = []
        self.archive_proofs: dict[str, ArchiveOfferProof] = {}

    def _token(self, subject: dict[str, Any]) -> str:
        return encoded(
            token(self.database.connection, subject["source"], subject["source_ref"], self.digest)
        )

    def begin(self) -> None:
        db, conn, now = self.database, self.database.connection, self.clock.now()
        if now >= self.deadline:
            return
        with db.transaction(immediate=False):
            last, high = conn.execute(
                "SELECT last_rowid,high_water FROM event_timing_cursor"
            ).fetchone()
            if last >= high:
                last = 0
                high = conn.execute(
                    "SELECT coalesce(max(rowid),0) FROM source_event_inventory"
                ).fetchone()[0]
            rows = conn.execute(
                "SELECT rowid,source,source_ref,enumeration_id FROM source_event_inventory WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT 8",
                (last, high),
            ).fetchall()
            session = Session(conn, Archive(db.state_dir), cutoff=now, now=now, limits=Limits())
            for row in rows:
                last = row["rowid"]
                subject = {**dict(row), "watches": [], "reason": None}
                subject["token"] = self._token(subject)
                try:
                    if not row["enumeration_id"]:
                        raise ValueError("legacy_enumeration_unknown")
                    pages = members(
                        session,
                        enumeration_id=row["enumeration_id"],
                        source=row["source"],
                        source_ref=row["source_ref"],
                        limit=32,
                    )
                    parents = session.read(
                        "SELECT parent_support_json FROM source_event_enumerations WHERE enumeration_id=?",
                        (row["enumeration_id"],),
                        ("parent_support_json",),
                        cap=1,
                    )
                    support = json.loads(parents[0]["parent_support_json"])
                    if not support or any(
                        parent_support(session, source=row["source"], generation_id=key)["usable"]
                        is not True
                        for key in support
                    ):
                        raise ValueError("enumeration_parent_unverified")
                    for page in pages:
                        proof = session.verify_request(
                            page["request"], classify_unavailability=True
                        )
                        if proof["acquired"] is None:
                            raise ValueError("acquisition_obligation_unknown")
                        if proof["acquired"] or proof["unavailable"]:
                            continue
                        watches = session.read(
                            "SELECT w.watch_id,w.source,w.method,w.url,w.form FROM source_event_member_watches m JOIN watches w USING(watch_id) WHERE m.enumeration_id=? AND m.request_id=? ORDER BY w.watch_id",
                            (row["enumeration_id"], page["request_id"]),
                            ("watch_id", "source", "method", "url", "form"),
                            cap=32,
                        )
                        if not watches:
                            raise ValueError("obligation_watch_unavailable")
                        from .event_evidence import request, request_id

                        for watch in watches:
                            if (
                                request_id(
                                    request(
                                        watch["source"],
                                        watch["method"],
                                        watch["url"],
                                        watch["form"],
                                    )
                                )
                                != page["request_id"]
                            ):
                                raise ValueError("obligation_watch_binding_changed")
                        subject["watches"].extend(watch["watch_id"] for watch in watches)
                        if len(subject["watches"]) > 32:
                            raise ValueError("obligation_watch_budget")
                    if not subject["watches"]:
                        subject["reason"] = "no_verified_missing_acquisition"
                except (BudgetExceeded, ValueError, KeyError, TypeError, RecursionError) as exc:
                    subject["reason"] = str(exc)
                self.subjects.append(subject)
        with db.transaction():
            conn.execute("UPDATE event_timing_cursor SET last_rowid=?,high_water=?", (last, high))
        for subject in self.subjects:
            subject["recorder"] = Recorder(
                db,
                source=subject["source"],
                source_ref=subject["source_ref"],
                run_id=self.run_id,
                token=subject["token"],
                policy=self.policy,
                at=sample(db, self.clock, subject["token"]),
            )
        self.resume()

    def accept_archive_proofs(self, proofs: tuple[ArchiveOfferProof, ...]) -> None:
        self.close()
        self.archive_proofs = {proof.spec.watch_id: proof for proof in proofs}
        self.resume()

    def _interval_token(self, subject: dict[str, Any]) -> str:
        current = self._token(subject)
        if subject.get("archive_epoch") is not None:
            current += ":archive-dispatch:" + str(epoch(self.database.connection))
        return current

    def close(self, *, inactive: bool = False) -> None:
        for subject in self.subjects:
            if recorder := subject.get("recorder"):
                recorder.close(
                    sample(self.database, self.clock, self._interval_token(subject)),
                    inactive=inactive,
                )

    def resume(self) -> None:
        conn, now = self.database.connection, self.clock.now()
        for subject in self.subjects:
            recorder = subject["recorder"]
            if recorder.start is not None:
                continue
            current = self._token(subject)
            before = sample(self.database, self.clock, current)
            before_archive = epoch(conn)
            subject["archive_epoch"] = None
            if now >= self.deadline:
                assessment = Assessment("inactive", "acquisition_deadline", now)
            elif (self.database.state_dir / "operator-hold").exists():
                assessment = Assessment("blocked", "operator_hold", self.deadline)
            elif current != subject["token"]:
                assessment = Assessment("unknown", "obligation_evidence_changed", self.deadline)
            elif subject["reason"]:
                assessment = Assessment("unknown", subject["reason"], self.deadline)
            else:
                assessments = [
                    ordinary(
                        self.client.gate,
                        self.client.robots,
                        watch,
                        deadline=self.deadline,
                        archive_proof=self.archive_proofs.get(watch),
                        run_id=self.run_id,
                    )
                    for watch in subject["watches"]
                ]
                if any(
                    value.state == "eligible" and watch in self.archive_proofs
                    for watch, value in zip(subject["watches"], assessments, strict=True)
                ):
                    subject["archive_epoch"] = before_archive
                assessment = union(assessments, deadline=self.deadline)
            owner = (subject["source"], subject["source_ref"])
            service = conn.execute(
                "SELECT e.rowid,r.action_id,r.issued_at FROM scheduler_event_requests e "
                "JOIN scheduler_requests r USING(action_id) WHERE e.source=? AND e.source_ref=? "
                "ORDER BY e.rowid DESC LIMIT 1",
                owner,
            ).fetchone()
            progress = conn.execute(
                "SELECT p.receipt_id,o.occurred_at FROM event_progress_receipts p JOIN event_stage_operations o USING(operation_id) WHERE p.source=? AND p.source_ref=? ORDER BY p.receipt_id DESC LIMIT 1",
                owner,
            ).fetchone()
            unresolved = (
                conn.execute(
                    "SELECT 1 FROM event_stage_operations o WHERE o.source=? AND o.operation_id>max(?,(SELECT coalesce(max(operation_id),0) FROM event_progress_receipts WHERE source=? AND source_ref=?)) AND (o.stage='interpreted' OR EXISTS(SELECT 1 FROM source_event_enumeration_members m WHERE m.enumeration_id=? AND m.request_id=o.request_id) OR EXISTS(SELECT 1 FROM watches w WHERE w.watch_id=o.watch_id AND w.source_ref=?) OR EXISTS(SELECT 1 FROM source_event_member_watches m WHERE m.enumeration_id=? AND m.watch_id=o.watch_id)) LIMIT 1",
                    (
                        subject["source"],
                        recorder.row["operation_origin"],
                        *owner,
                        subject["enumeration_id"],
                        subject["source_ref"],
                        subject["enumeration_id"],
                    ),
                ).fetchone()
                is not None
            )
            after = sample(self.database, self.clock, self._token(subject))
            if (
                (subject["archive_epoch"] is not None and before_archive != epoch(conn))
                or before.revision != after.revision
                or before.marker != after.marker
                or before.token != after.token
                or abs(
                    (after.wall - before.wall).total_seconds() - (after.elapsed - before.elapsed)
                )
                > 0.25
            ):
                assessment = Assessment(
                    "unknown", "gate_proof_changed_while_assessing", self.deadline
                )
            if subject["archive_epoch"] is not None:
                from dataclasses import replace

                after = replace(
                    after, token=after.token + ":archive-dispatch:" + str(before_archive)
                )
            recorder.open(
                after,
                assessment,
                service=service[0] if service else 0,
                service_action=service[1] if service else None,
                service_at=service[2] if service else None,
                progress=progress[0] if progress else 0,
                progress_at=progress[1] if progress else None,
                unresolved_success=unresolved,
            )
