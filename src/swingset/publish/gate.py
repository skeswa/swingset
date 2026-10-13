"""The strictly-better publication gate (D-0225).

A candidate is compared with the published baseline on fixed measures: the
row counts of the fact tables, confirmed identity links with a WSDC id,
entries and judges with a WSDC id, and events with placements. A measure that
goes down needs a row in `overrides/release_decreases.csv` naming the
measure, the baseline value, the lowest candidate value it approves, a reason
and who approved it; otherwise the verdict refuses the release with
`decrease:<measure> <from>-><to>`. The row is a floor: it approves any
candidate value from the one it names up to the baseline, so a build a day
later with a few more rows still passes, and a larger decrease does not. A
malformed file refuses every release with `overrides_invalid:<line>:
<error>`; no loop reads it but the build.

A growing total can hide a lost event, so results are also checked event by
event (D-0251): every event with placements or entries in the baseline must
still have one in the candidate, or its results must have moved. They moved
when each of its contests with results is in the candidate under another
event, still with results; a contest is known by its id without the event
prefix together with its `source_contest_ref` or any of its rounds'
`source_round_ref`, since a re-projection may take the contest's own ref from
another of its pages. A merge of a placeholder into its WSDC event (D-0253)
moves results this way, and the verdict lists it under
`moved_result_events`. Each lost event refuses with
`lost_result_events:<count> <ids>` unless the file approves it by name, with
the measure `result_event:<event_id>`, the baseline's placements plus entries
of that event as `baseline_value`, and `0` as `candidate_value`.

The builder writes the verdict into the candidate as `_meta/verdict.json`,
named and hashed in its manifest like every other file. It is left out of the
content hash, so an identical rebuild is still unchanged. Publication reads
it and refuses before submitting anything; a refusal is not a build failure.
A correction-only candidate is exempt: its decreases are the override rows
that asked for it, and its verdict says so.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from swingset.build.files import canonical_json

FILE = "release_decreases.csv"
#: The candidate file the verdict is written to.
VERDICT = "_meta/verdict.json"
FORMAT = "release-verdict-v1"
HEADER = ("measure", "baseline_value", "candidate_value", "reason", "approved_by", "approved_at")

#: Tables measured by row count.
COUNTED = (
    "events",
    "contests",
    "rounds",
    "entries",
    "judges",
    "callback_marks",
    "callbacks",
    "final_marks",
    "placements",
    "dancers",
    "registry_placements",
    "heats",
)
MEASURES = (
    *COUNTED,
    "confirmed_links",
    "linked_entries",
    "linked_judges",
    "events_with_placements",
)

#: The override measure that approves losing one event's results (D-0251).
RESULT_EVENT = "result_event:"
#: Lost event ids named in a refusal reason; the verdict lists them all.
REASON_EVENTS = 20

Measures = Mapping[str, int | None]
#: Each contest's event, source refs (its own and its rounds') and placements
#: plus entries.
ContestResults = Mapping[str, tuple[str, tuple[str, ...], int]]


@dataclass(frozen=True)
class Override:
    measure: str
    baseline_value: int
    candidate_value: int
    reason: str
    approved_by: str
    approved_at: str


def parse_overrides(body: bytes | Iterable[Mapping[str, str]]) -> tuple[Override, ...]:
    """Validated override rows; a malformed file raises `ValueError`."""
    if isinstance(body, bytes):
        reader = csv.DictReader(io.StringIO(body.decode("utf-8")))
        headers = tuple(reader.fieldnames or ())
        if body.strip() and headers != HEADER:
            raise ValueError(f"{FILE}: header must be {','.join(HEADER)}")
        rows: Iterable[Mapping[str, str]] = reader
    else:
        rows = body
    result: list[Override] = []
    seen: set[tuple[str, int, int]] = set()
    for number, row in enumerate(rows, 2):
        try:
            if None in row or any(row.get(name) is None for name in HEADER):
                raise ValueError("malformed CSV row")
            values = {name: str(row[name]).strip() for name in HEADER}
            for name in HEADER:
                if not values[name]:
                    raise ValueError(f"empty {name}")
            measure_name = values["measure"]
            if measure_name not in MEASURES and not (
                measure_name.startswith(RESULT_EVENT) and measure_name[len(RESULT_EVENT) :]
            ):
                raise ValueError(f"unknown measure {measure_name!r}")
            baseline, candidate = int(values["baseline_value"]), int(values["candidate_value"])
            if candidate < 0 or candidate >= baseline:
                raise ValueError("an override names a decrease: candidate_value < baseline_value")
            try:
                date.fromisoformat(values["approved_at"])
            except ValueError:
                datetime.fromisoformat(values["approved_at"])
        except ValueError as error:
            raise ValueError(f"{FILE}:{number}: {error}") from error
        key = (values["measure"], baseline, candidate)
        if key in seen:
            raise ValueError(f"{FILE}:{number}: duplicate override")
        seen.add(key)
        result.append(
            Override(
                values["measure"],
                baseline,
                candidate,
                values["reason"],
                values["approved_by"],
                values["approved_at"],
            )
        )
    return tuple(result)


def _files(directory: Path, table: str) -> list[Path]:
    return sorted((directory / "data" / table).glob("*.parquet"))


def measure(directory: Path, row_counts: Mapping[str, Any] | None = None) -> dict[str, int | None]:
    """Every measure of one candidate directory; None for a table it lacks.

    Row counts come from the manifest's `row_counts` when given, else from
    the Parquet footers. The others read one or two columns per file.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    counts = row_counts or {}
    result: dict[str, int | None] = {}
    present = {table: (directory / "data" / table).is_dir() for table in COUNTED}
    present["identity_links"] = (directory / "data" / "identity_links").is_dir()
    for table in COUNTED:
        if table in counts:
            result[table] = int(counts[table])
        elif present[table]:
            result[table] = sum(
                pq.ParquetFile(f).metadata.num_rows for f in _files(directory, table)
            )
        else:
            result[table] = None

    def non_null(table: str, column: str) -> int | None:
        if not present[table]:
            return None
        total = 0
        for path in _files(directory, table):
            values = pq.read_table(path, columns=[column]).column(column)
            total += len(values) - values.null_count
        return total

    result["linked_entries"] = non_null("entries", "wsdc_id")
    result["linked_judges"] = non_null("judges", "wsdc_id")
    if present["identity_links"]:
        confirmed = 0
        for path in _files(directory, "identity_links"):
            links = pq.read_table(path, columns=["wsdc_id", "status"])
            mask = pc.and_(pc.is_valid(links["wsdc_id"]), pc.equal(links["status"], "confirmed"))
            confirmed += links.filter(mask).num_rows
        result["confirmed_links"] = confirmed
    else:
        result["confirmed_links"] = None
    if present["placements"]:
        events: set[object] = set()
        for path in _files(directory, "placements"):
            column = pq.read_table(path, columns=["event_id"]).column("event_id")
            events.update(value for value in pc.unique(column).to_pylist() if value is not None)
        result["events_with_placements"] = len(events)
    else:
        result["events_with_placements"] = None
    return result


def contest_results(directory: Path) -> dict[str, tuple[str, tuple[str, ...], int]]:
    """Each contest's event, source refs and placements plus entries.

    The refs are its `source_contest_ref` and its rounds' `source_round_ref`,
    sorted. Placements and entries are counted by their `contest_id`; a
    contest with neither counts 0.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    counts: dict[str, int] = {}
    for table in ("placements", "entries"):
        for path in _files(directory, table):
            column = pq.read_table(path, columns=["contest_id"]).column("contest_id")
            values = pc.value_counts(column.combine_chunks() if column.num_chunks else column)
            for item in values.to_pylist():
                if item["values"] is not None:
                    key = str(item["values"])
                    counts[key] = counts.get(key, 0) + int(item["counts"])
    refs: dict[str, set[str]] = {}
    for path in _files(directory, "rounds"):
        table = pq.read_table(path, columns=["contest_id", "source_round_ref"])
        for contest, ref in zip(
            table.column("contest_id").to_pylist(),
            table.column("source_round_ref").to_pylist(),
            strict=True,
        ):
            if ref is not None:
                refs.setdefault(str(contest), set()).add(str(ref))
    result: dict[str, tuple[str, tuple[str, ...], int]] = {}
    for path in _files(directory, "contests"):
        table = pq.read_table(path, columns=["contest_id", "event_id", "source_contest_ref"])
        for contest, event, ref in zip(
            table.column("contest_id").to_pylist(),
            table.column("event_id").to_pylist(),
            table.column("source_contest_ref").to_pylist(),
            strict=True,
        ):
            own = refs.get(str(contest), set()) | ({str(ref)} if ref is not None else set())
            result[str(contest)] = (str(event), tuple(sorted(own)), counts.get(str(contest), 0))
    return result


def event_results(contests: ContestResults) -> dict[str, int]:
    """Placements plus entries of each event that has any."""
    result: dict[str, int] = {}
    for event, _ref, count in contests.values():
        if count > 0:
            result[event] = result.get(event, 0) + count
    return result


def _contest_keys(contest: str, event: str, refs: tuple[str, ...]) -> set[tuple[str, str | None]]:
    """A contest as a move keeps it: its id without the event prefix, with each source ref."""
    prefix = event + "/"
    name = contest[len(prefix) :] if contest.startswith(prefix) else contest
    return {(name, ref) for ref in refs} or {(name, None)}


def _moved_to(
    event: str,
    baseline: ContestResults,
    index: Mapping[tuple[str, str | None], list[tuple[str, int]]],
) -> list[str] | None:
    """The events that now hold all of ``event``'s results, or None if any are gone.

    ``index`` maps each candidate contest's keys (`_contest_keys`) to its
    event and results.
    """
    targets: set[str] = set()
    for contest, (holder, refs, count) in baseline.items():
        if holder != event or count <= 0:
            continue
        found = {
            other
            for key in _contest_keys(contest, holder, refs)
            for other, results in index.get(key, ())
            if other != event and results > 0
        }
        if not found:
            return None
        targets |= found
    return sorted(targets) or None


def baseline_measures(baseline: Path) -> dict[str, int | None]:
    """The measures of a published candidate, from its manifest and files."""
    manifest = json.loads((baseline / "_meta" / "manifest.json").read_bytes())
    return measure(baseline, manifest.get("row_counts"))


def evaluate(
    candidate: Measures,
    baseline: Measures | None,
    overrides: Sequence[Override] = (),
    *,
    baseline_commit: str | None = None,
    baseline_candidate: str | None = None,
    exempt: str | None = None,
    candidate_contests: ContestResults | None = None,
    baseline_contests: ContestResults | None = None,
) -> dict[str, Any]:
    """The verdict: every measure, each decrease, the overrides it used, pass or refused.

    With both contest maps, each baseline event with results that the
    candidate lacks is checked: one whose results all moved to other events
    is listed in `moved_result_events`; any other is listed in
    `lost_result_events` and refuses unless a `result_event:<id>` row
    approves it.
    """
    measures = {
        name: {
            "baseline": None if baseline is None else baseline.get(name),
            "candidate": candidate.get(name),
        }
        for name in MEASURES
    }
    decreases: list[dict[str, Any]] = []
    used: list[dict[str, Any]] = []
    reasons: list[str] = []
    for name in MEASURES:
        before = measures[name]["baseline"]
        after = measures[name]["candidate"]
        if before is None:
            continue
        # A table the candidate lacks has no rows; a baseline without one is unknown.
        after = 0 if after is None else after
        if after >= before:
            continue
        # A floor: the baseline must match, and the candidate may lose no more
        # than the row approved.
        match = next(
            (
                row
                for row in overrides
                if row.measure == name
                and row.baseline_value == before
                and after >= row.candidate_value
            ),
            None,
        )
        decreases.append(
            {"measure": name, "baseline": before, "candidate": after, "override": bool(match)}
        )
        if match is not None:
            used.append(asdict(match))
        elif exempt is None:
            reasons.append(f"decrease:{name} {before}->{after}")
    lost: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    if candidate_contests is not None and baseline_contests is not None:
        after_events = event_results(candidate_contests)
        index: dict[tuple[str, str | None], list[tuple[str, int]]] = {}
        for contest, (holder, refs, count) in candidate_contests.items():
            for key in _contest_keys(contest, holder, refs):
                index.setdefault(key, []).append((holder, count))
        for event, before in sorted(event_results(baseline_contests).items()):
            if after_events.get(event, 0) > 0:
                continue
            targets = _moved_to(event, baseline_contests, index)
            if targets is not None:
                moved.append({"event_id": event, "baseline": before, "to": targets})
                continue
            match = next(
                (
                    row
                    for row in overrides
                    if row.measure == RESULT_EVENT + event and row.baseline_value == before
                ),
                None,
            )
            lost.append({"event_id": event, "baseline": before, "override": bool(match)})
            if match is not None:
                used.append(asdict(match))
        refused = [item["event_id"] for item in lost if not item["override"]]
        if refused and exempt is None:
            named = " ".join(refused[:REASON_EVENTS])
            more = f" +{len(refused) - REASON_EVENTS}" if len(refused) > REASON_EVENTS else ""
            reasons.append(f"lost_result_events:{len(refused)} {named}{more}")
    return {
        "format": FORMAT,
        "state": "refused" if reasons else "pass",
        "baseline": None
        if baseline is None
        else {"commit": baseline_commit, "candidate_id": baseline_candidate},
        "exempt": exempt,
        "measures": measures,
        "decreases": decreases,
        "overrides_used": used,
        "lost_result_events": lost,
        "moved_result_events": moved,
        "reasons": reasons,
    }


def write(
    directory: Path,
    *,
    row_counts: Mapping[str, Any],
    baseline: Path | None,
    baseline_commit: str | None,
    overrides: bytes | Iterable[Mapping[str, str]],
    exempt: str | None = None,
) -> str:
    """Evaluate a candidate being built and write its verdict; returns the file's sha256.

    A malformed override file approves nothing and refuses the release with
    `overrides_invalid:<line>: <error>`, whatever the measures say.
    """
    try:
        parsed = parse_overrides(overrides)
        invalid = None
    except (ValueError, csv.Error) as error:
        parsed, invalid = (), str(error)
    verdict = evaluate(
        measure(directory, row_counts),
        None if baseline is None else baseline_measures(baseline),
        parsed,
        baseline_commit=baseline_commit,
        baseline_candidate=None if baseline is None else baseline.name,
        exempt=exempt,
        candidate_contests=contest_results(directory),
        baseline_contests=None if baseline is None else contest_results(baseline),
    )
    if invalid is not None:
        verdict = {
            **verdict,
            "state": "refused",
            "overrides_invalid": invalid,
            "reasons": [f"overrides_invalid:{invalid}", *verdict["reasons"]],
        }
    path = directory / VERDICT
    path.parent.mkdir(exist_ok=True)
    body = canonical_json(verdict)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def read(state_dir: Path, candidate: Path) -> dict[str, Any]:
    """A candidate's verdict, checked against its manifest.

    A candidate built before the gate has no verdict file: it is evaluated
    now against the baseline, with no overrides. A verdict whose bytes do not
    match the manifest refuses.
    """
    manifest = json.loads((candidate / "_meta" / "manifest.json").read_bytes())
    expected = manifest.get("files", {}).get(VERDICT)
    if expected is None:
        link = state_dir / "baseline"
        baseline = link.resolve() if link.is_symlink() else None
        verdict = evaluate(
            measure(candidate, manifest.get("row_counts")),
            None if baseline is None else baseline_measures(baseline),
            baseline_commit=manifest.get("baseline_commit"),
            baseline_candidate=None if baseline is None else baseline.name,
            candidate_contests=contest_results(candidate),
            baseline_contests=None if baseline is None else contest_results(baseline),
        )
        return {**verdict, "evaluated": "at_publication"}
    body = (candidate / VERDICT).read_bytes()
    if hashlib.sha256(body).hexdigest() != expected:
        return {"format": FORMAT, "state": "refused", "reasons": ["verdict_changed_after_build"]}
    return dict(json.loads(body))


def refusal(verdict: Mapping[str, Any]) -> str | None:
    """The refusal reason, or None when the verdict passes."""
    if verdict.get("state") == "pass":
        return None
    return "; ".join(verdict.get("reasons") or ["refused"])


def summary(verdict: Mapping[str, Any]) -> dict[str, Any]:
    """The loop summary's `verdict`: state, reasons, decreases and each measure as [from, to]."""
    return {
        "state": verdict.get("state"),
        "reasons": list(verdict.get("reasons") or ()),
        "exempt": verdict.get("exempt"),
        "baseline_commit": (verdict.get("baseline") or {}).get("commit"),
        "decreases": list(verdict.get("decreases") or ()),
        "overrides_used": len(verdict.get("overrides_used") or ()),
        "lost_result_events": len(verdict.get("lost_result_events") or ()),
        "moved_result_events": len(verdict.get("moved_result_events") or ()),
        "measures": {
            name: [value.get("baseline"), value.get("candidate")]
            for name, value in (verdict.get("measures") or {}).items()
        },
    }


def record(conn: sqlite3.Connection, candidate: Path, verdict: Mapping[str, Any]) -> bool:
    """Store the verdict on the candidate's `release_manifests` row, when it has one."""
    from swingset.state import release_store

    manifest = json.loads((candidate / "_meta" / "manifest.json").read_bytes())
    digest = (manifest.get("release_policy") or {}).get("manifest_digest")
    if not digest:
        return False
    row = release_store.get(conn, release_store.ManifestRow, str(digest))
    if row is None:
        return False
    release_store.upsert(conn, replace(row, verdict_json=canonical_json(verdict).decode()))
    return True
