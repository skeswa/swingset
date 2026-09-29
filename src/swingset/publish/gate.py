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

Measures = Mapping[str, int | None]


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
            if values["measure"] not in MEASURES:
                raise ValueError(f"unknown measure {values['measure']!r}")
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
) -> dict[str, Any]:
    """The verdict: every measure, each decrease, the overrides it used, pass or refused."""
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
