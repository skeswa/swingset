"""The partitioned release path: stage 1, the manifest, and the assembly's input.

`prepare` builds every partition whose inputs changed (`partitions.build`),
then writes the release manifest (`manifest.write`). `load` reads every built
artifact into public tables for the `whole` assembly and closes them across
partitions, as `closure_rows._close` closes a reconstructed release;
`source_support` turns the artifacts' support into the items coverage reads.
`load` serves the `whole` path; the streamed assembly (`assembly.py`) reads
the artifacts one partition at a time instead (D-0224).
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from swingset.state import release_store
from swingset.state.release_store import PartitionRow

from . import artifact_store
from .manifest import (
    FORMAT,
    ReleaseManifest,
    code_digests,
    correction_token,
    evidence_cutoff,
    overrides_digest,
    support_digest,
    write,
)


def prepare(
    database: Any,
    file_hashes: Mapping[str, str],
    *,
    history_start: Any,
    baseline: Path | None,
    run_id: str,
    now: datetime,
) -> ReleaseManifest:
    """Build stage 1 (every changed partition), then write and record the manifest."""
    from . import partitions
    from .builder import BuildError
    from .closure import _revocations

    conn: sqlite3.Connection = database.connection
    state_dir: Path = database.state_dir

    def damaged() -> list[str]:
        return [
            str(row.artifact_digest)
            for row in release_store.all_rows(conn, PartitionRow)
            if row.state == "built"
            and not artifact_store.intact(state_dir, str(row.artifact_digest))
        ]

    report = partitions.build(
        database, history_start=history_start, baseline=baseline, run_id=run_id, now=now
    )
    broken = damaged()
    if report.get("collected_while_building") or broken:
        # The collector removed an artifact the build meant to name (its row
        # was not written), or an artifact was damaged after stage 1 checked
        # it: building again writes it (and reuses the rest). Once inline: a
        # retry after the build interval would hold the release a whole
        # interval for a race.
        retried = partitions.build(
            database, history_start=history_start, baseline=baseline, run_id=run_id, now=now
        )
        if retried.get("collected_while_building"):
            raise BuildError("partition artifact collected while building; retry")
        again = damaged()
        if again:
            raise BuildError(f"partition artifact damaged after rebuilding: {again[0]}")
        report = {
            **retried,
            "collected_retried": int(report.get("collected_while_building", 0)),
            "damaged_rebuilt": len(broken),
        }
    rows = release_store.all_rows(conn, PartitionRow)
    built = sorted(
        [row.partition, str(row.artifact_digest)] for row in rows if row.state == "built"
    )
    withheld = sorted([row.partition, str(row.reason)] for row in rows if row.state == "withheld")
    sidecars: dict[str, Mapping[str, Any]] = {}
    support: set[tuple[Any, ...]] = set()
    generations: set[str] = set()
    for _, artifact in built:
        sidecar = artifact_store.sidecar(state_dir, artifact)
        sidecars[artifact] = sidecar
        generations.update(str(item) for item in sidecar.get("generations", ()))
        # The observation-level support the build decided (`sidecar["support"]`);
        # an artifact from before it names only the pairs' standing.
        for snapshot, parser, state, accepted in sidecar.get(
            "support", sidecar["inputs"].get("support", ())
        ):
            support.add((str(snapshot), str(parser), str(state), tuple(accepted)))
    supported = support_digest(
        [snapshot, parser, state, list(accepted)] for snapshot, parser, state, accepted in support
    )
    from .generations import baseline_commit

    value = {
        "format": FORMAT,
        "built": built,
        "withheld": withheld,
        "overrides_digest": overrides_digest(file_hashes),
        "code": code_digests(conn),
        "baseline_commit": baseline_commit(database),
        "support_digest": supported,
        "revocations": _revocations(conn),
        "correction_token": correction_token(conn, supported),
        "evidence_cutoff": evidence_cutoff(
            conn,
            generations,
            (generation for *_, accepted in support for generation in accepted),
        ),
    }
    manifest_digest, size = write(database, value, now=now)
    return ReleaseManifest(
        manifest_digest,
        value,
        size,
        report,
        sidecars,
        tuple(sorted(support, key=lambda item: (item[0], item[1]))),
    )


@dataclass
class Fragments:
    """Every built artifact's rows, closed across partitions."""

    tables: dict[str, list[dict[str, Any]]]
    #: `source_event_map` rows of the built event partitions.
    mapping: list[dict[str, Any]]
    #: What the release leaves out, as coverage's omission items.
    omissions: list[dict[str, Any]]
    counts: Counter[str]


def _neutral(table: str) -> dict[str, Any]:
    from .closure_rows import _NEUTRAL

    return dict(_NEUTRAL[table])


def load(state_dir: Path, manifest: ReleaseManifest) -> Fragments:
    """Read every built artifact into public tables and close them across partitions.

    Within a partition the build already closed its rows. Across partitions
    this does what `closure_rows._close` does for a reconstructed release: a
    dancer an artifact withheld (or a withheld dancer batch) leaves every
    entry, judge and identity row that names it, and a registry placement
    whose event is not in the release loses its association.
    """
    from .builder import PUBLISHED_TABLES
    from .partitions import dancer_batch

    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in PUBLISHED_TABLES}
    mapping: list[dict[str, Any]] = []
    omitted: dict[Any, str] = {}
    counts: Counter[str] = Counter()
    for _, artifact in manifest.built:
        sidecar = manifest.sidecars.get(artifact) or artifact_store.sidecar(state_dir, artifact)
        for name in sidecar["tables"]:
            rows = artifact_store.read_table(state_dir, artifact, name)
            if name == "source_event_map":
                mapping.extend(rows)
            else:
                tables[name].extend(rows)
        for dancer, reason in sidecar.get("withheld_subjects", {}).get("dancers", ()):
            omitted.setdefault(dancer, reason)
    omissions: list[dict[str, Any]] = []
    batches: set[str] = set()
    for partition, reason in manifest.withheld:
        kind, _, name = partition.partition("/")
        if kind == "event":
            omissions.extend(
                {
                    "stage": "project",
                    "unit_kind": "event",
                    "unit_id": event,
                    "status": "withheld",
                    "reason": reason,
                }
                for event in name.split("+")
            )
        else:
            if kind == "dancers":
                batches.add(name)
            omissions.append(
                {
                    "stage": "project",
                    "unit_kind": "dancer" if kind == "dancers" else kind,
                    "unit_id": name,
                    "status": "withheld",
                    "reason": reason,
                }
            )

    def gone(wsdc_id: Any) -> bool:
        return wsdc_id is not None and (wsdc_id in omitted or dancer_batch(wsdc_id) in batches)

    events = {row["event_id"] for row in tables["events"]}
    for row in tables["registry_placements"]:
        if row.get("event_id") is not None and row["event_id"] not in events:
            row["event_id"] = None
            counts["registry_association_outside_release"] += 1
    for table in ("entries", "judges"):
        for row in tables[table]:
            if gone(row.get("wsdc_id")):
                row.update(_neutral(table))
                counts["identity_of_withheld_dancer_closed"] += 1
    for table in ("identity_links", "link_candidates"):
        kept = [row for row in tables[table] if not gone(row.get("wsdc_id"))]
        counts["identity_of_withheld_dancer_closed"] += len(tables[table]) - len(kept)
        tables[table] = kept
    entries = {row["entry_id"]: row for row in tables["entries"]}
    for row in tables["placements"]:
        for role in ("leader", "follower"):
            entry = entries.get(row.get(role + "_entry_id"))
            if entry is None or entry.get("wsdc_id") is None:
                row[role + "_wsdc_id"] = None
                row["registry_points_" + role] = None
                row["registry_confirmed"] = False
                row["points_matches_expected"] = None
    return Fragments(tables, mapping, omissions, counts)


class SupportItems:
    """The artifacts' support as the closure's `source_support` items, streamed.

    Each iteration yields one small item per `[snapshot, parser, state,
    generations]` of the manifest's support: its state, the accepted source
    generations with only `recipe.context.source` (all coverage reads of a
    recipe), and the scope its observations have (a source event first),
    which event coverage matches on. Nothing but the scopes and sources is
    kept between iterations: the build's 294,806 recipes of about 4 kB each
    peaked at 3.9 GB when loaded whole (F26).
    """

    def __init__(self, conn: sqlite3.Connection, support: Sequence[Sequence[Any]]) -> None:
        self.conn = conn
        self.support = support
        self._sources: dict[str, str | None] = {}
        self._scopes: dict[tuple[str, str], tuple[str | None, str | None]] = {}

    def __len__(self) -> int:
        return len(self.support)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for snapshot, parser, state, accepted in self.support:
            item: dict[str, Any] = {
                "snapshot_id": snapshot,
                "parser_version": parser,
                "state": state,
                "source_generations": [],
                "scope": [None, None],
            }
            if state == "accepted":
                item["scope"] = list(self._scope(str(snapshot), str(parser)))
                for generation in accepted:
                    source = self._source(str(generation))
                    if source is not None:
                        item["source_generations"].append(
                            {"generation_id": generation, "recipe": {"context": {"source": source}}}
                        )
            yield item

    def _scope(self, snapshot: str, parser: str) -> tuple[str | None, str | None]:
        key = snapshot, parser
        found = self._scopes.get(key)
        if found is None:
            row = self.conn.execute(
                "SELECT scope_kind,scope_id FROM observations WHERE watch_id=(SELECT watch_id "
                "FROM snapshots WHERE snapshot_id=:s) AND snapshot_id=:s "
                "AND CAST(parser_version AS TEXT)=:p "
                "ORDER BY scope_kind<>'source_event',observation_id LIMIT 1",
                {"s": snapshot, "p": parser},
            ).fetchone()
            found = self._scopes[key] = (
                (str(row[0]), str(row[1])) if row is not None else (None, None)
            )
        return found

    def _source(self, generation: str) -> str | None:
        if generation not in self._sources:
            row = self.conn.execute(
                "SELECT json_extract(recipe_json,'$.context.source') FROM source_generations "
                "WHERE generation_id=?",
                (generation,),
            ).fetchone()
            self._sources[generation] = (
                str(row[0]) if row is not None and row[0] is not None else None
            )
        return self._sources[generation]


def source_support(conn: sqlite3.Connection, support: Sequence[Sequence[Any]]) -> SupportItems:
    """The artifacts' support as the closure's `source_support` items coverage reads."""
    return SupportItems(conn, support)
