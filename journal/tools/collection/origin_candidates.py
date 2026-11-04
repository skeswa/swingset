"""Report origin-fallback candidates from a state copy, offline (D-0236).

The loops make no origin requests from the history plan. This tool asks the
retired fallback's question on a copy instead: it builds the retained platform
plan as the offers pass does (facts-only year gate, one projection answer),
and for each page whose archive offer stops at `capture_gap_exhausted` or
`operator_pause` it reports `origin_dispatch.candidate(...)`'s reason and
proposal. It also lists every existing `history_origin_intents` row, which the
request gate still refuses (fail closed).

Safety: it refuses `/var/lib/swingset` and a state whose native advisory state
lock another process holds. It takes that lock for the run and reads the
database inside one read-only repeatable snapshot. No transport exists, so no
request can be sent; `candidate` runs without a run id, so the per-cycle
cadence is not judged.

    python -m journal.tools.collection.origin_candidates --state <copy> \\
        [--source eepro] [--config config] [--out candidates.json]

A reviewer checks an `eligible_origin` page's proposal (the archive queries and
attempted captures that prove the gap) and the origin URL, then adds a
reviewed row to `overrides/source_urls.csv`. The tool writes nothing else.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from swingset.clock import Clock, SystemClock
from swingset.config import Config, load_config
from swingset.history.backfill import _options, page_watch_id
from swingset.history.origin_dispatch import available, candidate
from swingset.history.platform import retained_plan
from swingset.project.history import facts_only_year_gate, projection_answer_cached
from swingset.state import engine
from swingset.state.db import Database, DatabaseLockedError, open_database
from swingset.state.environment import EnvironmentRefused, ensure_dev

#: Archive stop reasons after which the retired fallback asked the origin.
FALLBACK_REASONS = frozenset({"capture_gap_exhausted", "operator_pause"})
PRODUCTION = Path("/var/lib/swingset")


class Refused(RuntimeError):
    """The state path is not a copy this tool may read."""


def _is_production(path: Path) -> bool:
    resolved = path.resolve()
    return any(
        resolved == root or root in resolved.parents for root in {PRODUCTION, PRODUCTION.resolve()}
    )


@contextmanager
def open_copy(state: Path) -> Iterator[Database]:
    """Hold the copy's native state lock and one read-only repeatable snapshot."""
    directory = engine.state_directory(state)
    if _is_production(directory):
        raise Refused(f"refusing the production state: {directory}")
    if not (directory / "database.json").is_file():
        raise FileNotFoundError(f"no configured state database at {directory}")
    try:
        database = open_database(directory, read_only=True, lock_timeout=0)
    except DatabaseLockedError as error:
        raise Refused(f"state.lock is held by another process: {directory}") from error
    with database:
        try:
            ensure_dev(database.connection)
        except EnvironmentRefused as error:
            raise Refused(str(error)) from error
        with database.connection.read_snapshot():
            yield database


def _proposal(proposal: Any) -> dict[str, Any] | None:
    if proposal is None:
        return None
    return {
        "eligible": proposal.eligible,
        "reason": proposal.reason,
        "archive_query_ids": list(proposal.archive_query_ids),
        "attempted_capture_urls": list(proposal.attempted_capture_urls),
    }


def report(
    database: Database, config: Config, clock: Clock, *, source: str | None = None
) -> dict[str, Any]:
    """Candidate rows for every fallback page, plus the existing intents."""
    conn = database.connection
    plan = retained_plan(conn, history_start=config.history_start)
    if source is not None:
        plan = type(plan)(tuple(p for p in plan.pages if p.page.source == source), plan.findings)
    pages: list[dict[str, Any]] = []
    with facts_only_year_gate(), projection_answer_cached():
        options, _ = _options(database, config, clock, plan, deadline=None)
        archive_sources = {item.page.source for item, (spec, _, _) in options if spec is not None}
        for item, (_, _, archive_reason) in options:
            if archive_reason not in FALLBACK_REASONS:
                continue
            _, proposal, reason = candidate(database, config, clock, item, plan, run_id=None)
            page = item.page
            pages.append(
                {
                    "event_id": item.event.event_id,
                    "year": item.event.year,
                    "source": page.source,
                    "source_ref": page.source_ref,
                    "url": page.url,
                    "page_kind": page.page_kind,
                    "kind": page.kind,
                    "parent_url": page.parent_url,
                    "watch_id": page_watch_id(item),
                    "archive_reason": archive_reason,
                    "origin_reason": reason,
                    "source_has_archive_offer": page.source in archive_sources,
                    "proposal": _proposal(proposal),
                }
            )
    intents = (
        [
            dict(row)
            for row in conn.execute("SELECT * FROM history_origin_intents ORDER BY watch_id")
        ]
        if available(conn)
        else []
    )
    return {
        "checked_at": clock.now().isoformat(),
        "source": source,
        "plan_pages": len(plan.pages),
        "fallback_pages": len(pages),
        "reasons": dict(sorted(Counter(row["origin_reason"] for row in pages).items())),
        "pages": pages,
        "intents": intents,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--state", type=Path, required=True, help="a state copy directory or file")
    parser.add_argument("--source", help="only this source's plan pages")
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--out", type=Path, help="write the JSON report here, outside the state")
    args = parser.parse_args(argv)
    state_dir = engine.state_directory(args.state).resolve()
    if args.out is not None and (
        args.out.resolve() == state_dir or state_dir in args.out.resolve().parents
    ):
        print("--out must be outside the state directory", file=sys.stderr)
        return 2
    config = load_config(args.config)
    try:
        with open_copy(args.state) as database:
            result = report(database, config, SystemClock(), source=args.source)
    except (Refused, FileNotFoundError) as error:
        print(str(error), file=sys.stderr)
        return 2
    text = json.dumps(result, indent=1, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
