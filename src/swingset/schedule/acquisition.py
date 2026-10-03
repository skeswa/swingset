"""The acquisition pass and the event bookkeeping around it.

Run by the plan and acquire loops. `Acquisition.run` is one pass: offered history pages compete with due watches under the fair
policy, each dispatch or fetch is admitted by the client's gates, the
archive index gets the pass's spare time, and the pass's guards are flushed
(D-0219). Where the offered pages come from is the caller's: acquire reads
eligible `acquisition_plan` rows (D-0218).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from swingset.clock import Clock
from swingset.config import Config
from swingset.fetch.archive import Archive
from swingset.fetch.client import CONTROL_TIMEOUT, FetchClient
from swingset.log import log
from swingset.schedule.fairness import (
    WatchChoice,
    backpressure,
    next_delay,
    next_watch,
    prepare_event_turns,
    servicing,
)
from swingset.schedule.registry import advance_sweep
from swingset.schedule.watches import refresh_policy
from swingset.sources import get_page_kind
from swingset.sources.base import WatchSpec
from swingset.state import guards
from swingset.state.control_lock import ControlTimeout
from swingset.state.control_scopes import for_watch
from swingset.state.controls import ActionScope, ControlPaused, operation
from swingset.state.db import Database
from swingset.state.write_deadline import WriteDeadlineExceeded

if TYPE_CHECKING:
    from swingset.state.inputs import InputBundle

#: Offered history pages for a pass that ends at the given time.
Offers = Callable[[datetime], Iterable[WatchSpec]]
#: What an operator pause held: the action and the pause.
Held = Callable[[str, ControlPaused], None]


def paused_hosts(database: Database, now: datetime) -> list[dict[str, Any]]:
    """Hosts paused past ``now``; a `Blocked` one fails the run."""
    return [
        dict(row)
        for row in database.connection.execute(
            "SELECT host,paused_until,pause_reason FROM hosts WHERE paused_until IS NOT NULL"
        )
        if datetime.fromisoformat(row["paused_until"]) > now
    ]


@contextmanager
def contained(summary: dict[str, Any], stage: str) -> Iterator[None]:
    """A bookkeeping stage that overruns its write is skipped, not fatal.

    Its write rolled back whole. Letting it escape aborted every cycle before
    acquisition for eleven hours on 2026-09-25 (D-0201); the run still
    reports failed=true so the watchdog sees it.
    """
    try:
        yield
    except WriteDeadlineExceeded as exc:
        summary["stage_errors"].append({"stage": stage, "error": str(exc)})
        log("stage_failed", stage=stage, error=str(exc)[:300])


class Acquisition:
    """One run's acquisition passes: its fetch client and the watches it visited.

    Visits are unique per run. ``prepare``, ``select`` and ``delay`` are the
    fair policy's functions; tests replace them through these defaults.
    """

    def __init__(
        self,
        database: Database,
        config: Config,
        clock: Clock,
        run_id: str,
        summary: dict[str, Any],
        *,
        archive: Archive,
        stop: Callable[[], bool],
        should_stop: Callable[[], bool],
        transport: httpx.BaseTransport | None = None,
        loop: str = "acquire",
        prepare: Callable[..., Any] = prepare_event_turns,
        select: Callable[..., WatchChoice | None] = next_watch,
        delay: Callable[..., float | None] = next_delay,
    ) -> None:
        self.database, self.config, self.clock = database, config, clock
        self.run_id, self.summary, self.archive = run_id, summary, archive
        self.stop, self.should_stop, self.transport, self.loop = (
            stop,
            should_stop,
            transport,
            loop,
        )
        self.prepare, self.select, self.delay = prepare, select, delay
        self.visited: set[str] = set()
        self.fetcher: FetchClient | None = None

    def client(self) -> FetchClient:
        if self.fetcher is None:
            self.fetcher = FetchClient(
                self.database.connection,
                self.config,
                self.clock,
                self.archive,
                transport=self.transport,
                should_stop=self.should_stop,
                loop=self.loop,
                run_id=self.run_id,
            )
        return self.fetcher

    def close(self) -> None:
        if self.fetcher is not None:
            self.fetcher.close()

    def run(self, until: datetime, offers: Offers) -> tuple[bool, float]:
        """One pass until ``until``; whether it issued a request, and its seconds."""
        from swingset.schedule.event_timing_observer import Observer, observing

        began = self.clock.now()
        try:
            with observing(
                Observer(
                    self.database, self.client(), self.clock, run_id=self.run_id, deadline=until
                )
            ):
                issued = self._pass(until, offers)
        finally:
            # Each pass writes why its fetches were held, in one transaction
            # (D-0219), even a pass that raised. An overrun loses this pass's
            # guards, not the run.
            with contained(self.summary, "guards"):
                flushed = self.client().guards.flush(self.database, self.clock.now())
                guarded = self.summary["guards"]
                guarded["passes"] += 1
                guarded["recorded"] += flushed["recorded"]
                guarded["cleared"] += flushed["cleared"]
        seconds = (self.clock.now() - began).total_seconds()
        return issued, seconds

    def _choices(self, offered: Iterable[WatchSpec]) -> list[WatchChoice]:
        extra: list[WatchChoice] = []
        for spec in offered:
            if spec.watch_id in self.visited:
                continue
            host = urlsplit(spec.archive_url or spec.url).hostname or ""
            extra.append(
                WatchChoice(
                    key=spec.watch_id,
                    watch_id=None,
                    host=host,
                    category="old",
                    scope=for_watch(
                        self.database.connection,
                        source=spec.source,
                        watch_id=spec.watch_id,
                        page_kind=spec.parser,
                        watch_kind=spec.kind,
                        host=host,
                    ),
                    due_at=self.clock.now(),
                    archive=bool(spec.archive_url),
                    history=True,
                )
            )
        return extra

    def _pass(self, until: datetime, offers: Offers) -> bool:
        from swingset.schedule.event_timing_observer import checkpoint, resume

        database, config, clock, run_id = self.database, self.config, self.clock, self.run_id
        summary, stop = self.summary, self.stop
        issued = False
        # A watch whose request raised waits on its clock (D-0219): it is
        # visited already, so neither the fair policy nor the offers pick it.
        errored = guards.requests_errored(database.connection, now=clock.now())
        self.visited.update(key for key, holding in errored.items() if holding)
        # Under backpressure no historical request can be admitted, so the
        # offers are not asked for; otherwise the caller's offers stop at the
        # pass deadline instead of consuming the whole run (D-0181).
        pressure = backpressure(database.connection, config, now=clock.now())
        extra = self._choices(offers(until) if not stop() and not pressure["active"] else ())
        while not stop() and clock.now() < until:
            checkpoint()
            with database.transaction() as conn:
                self.prepare(
                    conn,
                    config,
                    now=clock.now(),
                    exclude=self.visited,
                    extra_choices=extra,
                    run_id=run_id,
                )
            resume()
            choice = self.select(
                database.connection,
                config,
                now=clock.now(),
                exclude=self.visited,
                extra_choices=extra,
                run_id=run_id,
            )
            if choice is None:
                delay = self.delay(
                    database.connection,
                    config,
                    now=clock.now(),
                    exclude=self.visited,
                    extra_choices=extra,
                    run_id=run_id,
                )
                if delay is None or delay <= 0:
                    break
                clock.sleep(min(delay, (until - clock.now()).total_seconds(), 5))
                continue
            self.visited.add(choice.key)
            # One choice's exception stops only that choice (I1): the rest of
            # the pass, the archive index and the guard flush still run.
            try:
                served = self._serve(choice, until)
            except Exception as exc:
                self._request_failed(choice, exc)
                continue
            if served:
                issued = True
                if choice.key in errored:
                    self._request_cleared(choice.key)
        index_seconds = config.scheduler.archive_index_seconds
        if (
            index_seconds is not None
            and not stop()
            and not pressure["active"]
            and clock.now() < until
        ):
            from swingset.history.platform import index_platform_archives

            # Step 1 of every platform backfill: index the archive's captures
            # with the acquisition time left after the pages already offered
            # had their turn, so indexing never takes a dispatch's host slot
            # (D-0184). Newly indexed captures are offered from the next
            # pass on.
            with contained(self.summary, "archive_index"):
                summary["archive_index"] = index_platform_archives(
                    self.client(),
                    config,
                    clock,
                    history_start=config.history_start,
                    deadline=min(until, clock.now() + timedelta(seconds=index_seconds)),
                )
        return issued

    def _serve(self, choice: WatchChoice, until: datetime) -> bool:
        """Dispatch or fetch one choice; whether it issued a request."""
        from swingset.history.backfill import dispatch_one

        database, config, clock, run_id = self.database, self.config, self.clock, self.run_id
        summary = self.summary
        with servicing(choice, run_id=run_id):
            if choice.archive or choice.history:
                # Dispatch re-checks its target from facts before any
                # mutation: year, parent, capture and source gates.
                historical = dispatch_one(
                    database,
                    config,
                    clock,
                    run_id,
                    deadline=until,
                    fetcher=self.client(),
                    allocated=True,
                    target_watch_id=choice.key,
                )
                summary["history_dispatch"] = {
                    "reason": historical.reason,
                    "watch_id": historical.watch_id,
                }
                result = historical.result
                if result is None:
                    return False
            else:
                assert choice.watch_id is not None
                row = database.connection.execute(
                    "SELECT parser,notes,url,archive_url FROM watches WHERE watch_id=?",
                    (choice.watch_id,),
                ).fetchone()
                result = self.client().fetch(
                    choice.watch_id,
                    get_page_kind(row["parser"]),
                    run_id,
                    deadline=until,
                    sweep=row["notes"] == "sweep",
                )
                if not result.skipped:
                    with database.transaction() as conn:
                        refresh_policy(
                            conn,
                            config,
                            choice.watch_id,
                            clock.now(),
                            outcome=result.classification.outcome,
                        )
                        if row["parser"] == "wsdc_registry.dancer":
                            advance_sweep(database, now=clock.now())
            if result.skipped:
                if result.skipped == CONTROL_TIMEOUT:
                    summary["control_timeouts"] = summary.get("control_timeouts", 0) + 1
                return False
            summary["checked"] += 1
            summary["changed"] += int(result.changed)
            summary["not_modified"] += int(result.classification.outcome == "NotModified")
            summary["nonce_unchanged"] += int(result.nonce_unchanged)
            host = choice.host
            summary["bytes_by_host"][host] = (
                summary["bytes_by_host"].get(host, 0) + result.body_bytes
            )
            return True

    def _request_failed(self, choice: WatchChoice, exc: Exception) -> None:
        """Note a choice that raised; hold its watch on a clock unless it only waited.

        A busy writer, an overrun write, a publication fence, an operator
        pause or a control-lock timeout is not the watch's fault: the request
        is skipped, nothing is held (F7).
        Anything else is a defect: the watch waits an hour, doubling, and a
        week from the third in a row (`guards.request_failed`).
        """
        entry: dict[str, Any] = {
            "watch": choice.key,
            "host": choice.host,
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }
        if isinstance(exc, (WriteDeadlineExceeded, ControlPaused, ControlTimeout)) or _contended(
            exc
        ):
            entry["skipped"] = True
        else:
            try:
                with self.database.transaction() as conn:
                    held = guards.request_failed(
                        conn, choice.key, loop=self.loop, now=self.clock.now()
                    )
                entry["held_until"] = held.expires_at
                entry["failures"] = held.seen
            except (sqlite3.Error, WriteDeadlineExceeded) as recorded:
                entry["guard_error"] = f"{type(recorded).__name__}: {recorded}"[:200]
        self.summary.setdefault("request_errors", []).append(entry)
        log("request_failed", **entry)

    def _request_cleared(self, key: str) -> None:
        """A request went through: its watch's `request_error` row goes."""
        try:
            with self.database.transaction() as conn:
                guards.delete(conn, f"watch:{key}", guards.REQUEST_ERROR)
        except (sqlite3.Error, WriteDeadlineExceeded):
            pass


def request_failures(summary: dict[str, Any]) -> bool:
    """Whether a request raised for a reason other than contention."""
    return any(not entry.get("skipped") for entry in summary.get("request_errors", ()))


def _contended(exc: BaseException) -> bool:
    """A busy timeout or a publication fence: the request waits, uncounted."""
    message = str(exc).lower()
    return isinstance(exc, sqlite3.DatabaseError) and (
        "locked" in message or "busy" in message or "active publication fences" in message
    )


class Bookkeeping:
    """The event bookkeeping stages the plan loop runs, each switchable in `[stages]`.

    Each writes one summary key and is skipped whole when its switch is off.
    A write that overruns its deadline is a stage error, not fatal (D-0201);
    an operator pause is a held operation.
    """

    def __init__(
        self,
        database: Database,
        bundle: InputBundle,
        clock: Clock,
        run_id: str,
        summary: dict[str, Any],
        *,
        deadline: datetime,
        held: Held,
    ) -> None:
        self.database, self.bundle, self.clock = database, bundle, clock
        self.run_id, self.summary, self.deadline, self.held = run_id, summary, deadline, held
        self.stages = bundle.config.stages

    def _left(self, cap: float) -> float:
        return min(cap, max(0.0, (self.deadline - self.clock.now()).total_seconds()))

    def enumerations(self) -> None:
        from swingset.schedule.event_enumerations import bootstrap

        with contained(self.summary, "event_enumerations"):
            self.summary["event_enumerations"] = bootstrap(self.database, now=self.clock.now())

    def pressure_bootstrap(self) -> None:
        if not self.stages.event_pressure:
            return
        from swingset.schedule.event_pressure import bootstrap

        with contained(self.summary, "event_pressure_bootstrap"):
            self.summary["event_pressure_bootstrap"] = bootstrap(
                self.database, self.bundle.config, now=self.clock.now()
            )

    def blocker_observations(self) -> None:
        if not self.stages.event_blocker_observations:
            return
        from swingset.schedule.event_blocker_history import refresh

        # Diagnostic bookkeeping may observe a pause without starting paused
        # acquisition, artifact verification, or derivation work.
        with contained(self.summary, "event_blocker_observations"):
            self.summary["event_blocker_observations"] = refresh(
                self.database,
                self.bundle.config,
                now=self.clock.now(),
                run_id=self.run_id,
                operator_hold=(self.database.state_dir / "operator-hold").is_file(),
            )

    def requirements_scan(self) -> None:
        from swingset.state.requirements import scan

        with contained(self.summary, "requirements_scan"):
            self.summary["requirements_scanned"] = scan(
                self.database,
                self.clock.now(),
                self.run_id,
                history_start=self.bundle.config.history_start,
            )

    def _refresh(self, stage: str, key: str, call: Callable[[], Any]) -> None:
        try:
            with operation(
                self.database,
                action_id=f"{stage}_" + uuid4().hex,
                action_kind="project",
                scope=ActionScope(
                    all_sources=True,
                    kinds=frozenset(
                        {"work_attempt", "archive_artifact", "admission_blocked", "parse_failure"}
                    ),
                ),
                clock=self.clock,
                run_id=self.run_id,
            ):
                self.summary[key] = call()
        except ControlPaused as exc:
            self.held(key, exc)
        except WriteDeadlineExceeded as exc:
            self.summary["stage_errors"].append({"stage": key, "error": str(exc)})
            log("stage_failed", stage=key, error=str(exc)[:300])

    def pressure_refresh(self) -> None:
        if not self.stages.event_pressure:
            return
        from swingset.schedule.event_pressure import refresh

        self._refresh(
            "event_pressure",
            "event_pressure_refresh",
            lambda: refresh(
                self.database,
                Archive(self.database.state_dir),
                self.bundle.config,
                now=self.clock.now(),
                wall_seconds=self._left(5.0),
            ),
        )

    def progress_refresh(self) -> None:
        if not self.stages.event_progress_refresh:
            return
        from swingset.schedule.event_progress import refresh

        self._refresh(
            "event_progress",
            "event_progress_refresh",
            lambda: refresh(
                self.database,
                Archive(self.database.state_dir),
                self.bundle.config,
                now=self.clock.now(),
                run_id=self.run_id,
                wall_seconds=self._left(5.0),
            ),
        )
