"""Run the loops' ticks in order with a mock transport and a fake clock.

The harness the cycle's tests moved to (architecture phase 5, WP7): one
round runs a plan, an acquire, a derive and a release tick, in that order,
each its own open of the state through `loops.runtime.tick`. Derive is the
only loop that accepts the input bundle, so on a fresh state the first
round's plan tick skips with `inputs_not_accepted`.

A tick opens the state with the shared lock, so a round cannot run while a
test holds `open_database` (the exclusive lock). A round given that open
`Database` instead of a path lends it to the loops (`released`): its locks
are released for the round and taken again after; its connection stays open.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from swingset.clock import Clock
from swingset.loops import acquire, derive, maintain, plan, release
from swingset.loops.runtime import tick
from swingset.state import rwlock
from swingset.state.db import Database

ORDER = ("plan", "acquire", "derive", "release")
#: A round whose derive tick accepts the inputs first, so its plan tick runs
#: the bookkeeping stages on a fresh state too.
ACCEPTED_FIRST = ("derive", *ORDER)
BODIES = {
    "plan": plan.run,
    "acquire": acquire.run,
    "derive": derive.run,
    "maintain": maintain.run,
}
#: Seconds each tick gets unless a round names its own budget.
BUDGET = 720.0


class Round(dict[str, dict[str, Any]]):
    """The records of one round: the last record of each loop by name, and
    `sequence`, every record in tick order, which the cycle-summary views read."""

    def __init__(self) -> None:
        super().__init__()
        self.sequence: list[dict[str, Any]] = []

    def add(self, record: dict[str, Any]) -> None:
        self.sequence.append(record)
        self[record["loop"]] = record

    @property
    def failed(self) -> bool:
        return any(record["failed"] for record in self.sequence)

    @property
    def stopped(self) -> bool:
        return any(record["stopped"] for record in self.sequence)

    @property
    def stages(self) -> list[str]:
        """Derive's stages, then release's (`build`), in tick order."""
        return [stage for record in self.sequence for stage in record.get("stages") or []]

    @property
    def held_operations(self) -> list[dict[str, Any]]:
        return [item for record in self.sequence for item in record.get("held_operations") or []]

    @property
    def stage_errors(self) -> list[dict[str, Any]]:
        return [item for record in self.sequence for item in record.get("stage_errors") or []]

    @property
    def checked(self) -> int:
        return sum(int(record.get("checked") or 0) for record in self.sequence)

    @property
    def accepted_inputs(self) -> list[str]:
        return sorted(
            {name for record in self.sequence for name in record.get("accepted_inputs") or []}
        )


@contextmanager
def released(database: Database) -> Iterator[Path]:
    """Lend an exclusive open to the loops; yields its state directory."""
    assert not database.connection.in_transaction
    held = database._locks
    if held is not None:
        held.release()
    try:
        yield database.state_dir
    finally:
        if held is not None:
            database._locks = rwlock.exclusive(database.state_dir)


def run_tick(
    loop: str,
    state: Path,
    *,
    config_dir: Path,
    overrides_dir: Path,
    clock: Clock,
    transport: httpx.BaseTransport | None = None,
    budget: float = BUDGET,
    hub: Any = None,
    dry_run: bool = True,
    should_stop: Callable[[], bool] = lambda: False,
    disk_file: Path | None = None,
) -> dict[str, Any]:
    """One tick of `loop`; its run record."""
    with tick(
        loop,
        state=state,
        config_dir=config_dir,
        overrides_dir=overrides_dir,
        clock=clock,
        budget=budget,
        should_stop=should_stop,
        transport=transport,
        hub=hub,
        dry_run=dry_run,
    ) as current:
        if loop == "release":
            # No quota reading: the disk check neither refuses nor trusts one.
            release.run(current, disk_file=disk_file or state.parent / "no-disk-quota")
        else:
            BODIES[loop](current)
    assert current.record is not None
    return current.record


def run_round(
    state: Path | Database,
    *,
    config_dir: Path,
    overrides_dir: Path,
    clock: Clock,
    transport: httpx.BaseTransport | None = None,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    budget: float = BUDGET,
    hub: Any = None,
    dry_run: bool = True,
    should_stop: Callable[[], bool] = lambda: False,
    loops: Iterable[str] = ORDER,
    disk_file: Path | None = None,
) -> Round:
    """Plan, acquire, derive and release ticks in order; their records by loop."""
    if isinstance(state, Database):
        with released(state) as state_dir:
            return run_round(
                state_dir,
                config_dir=config_dir,
                overrides_dir=overrides_dir,
                clock=clock,
                transport=transport,
                handler=handler,
                budget=budget,
                hub=hub,
                dry_run=dry_run,
                should_stop=should_stop,
                loops=loops,
                disk_file=disk_file,
            )
    if handler is not None:
        assert transport is None
        transport = httpx.MockTransport(handler)
    records = Round()
    for loop in loops:
        records.add(
            run_tick(
                loop,
                state,
                config_dir=config_dir,
                overrides_dir=overrides_dir,
                clock=clock,
                transport=transport,
                budget=budget,
                hub=hub,
                dry_run=dry_run,
                should_stop=should_stop,
                disk_file=disk_file,
            )
        )
    return records
