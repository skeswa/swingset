"""Per-partition checks: source support, references, and the builder's release checks.

A partition artifact is checked alone (plan phase 3, WP6). A failure withholds
that partition with a stored reason; nothing else waits on it (I1).

- **Support** reads the accepted source generations of each snapshot and
  parser a row names, never a clock (F24), and matches observations as
  `closure_support.support` does (D-0022). A pair is `accepted` only when
  every observation of it the partition's rows came from (the observations
  its generations recorded reading, else the snapshot's live observations)
  is contained, with the same scope, kind, seq, payload and extract version,
  in an accepted generation of that snapshot and parser: an accepted
  admission decision, the current contract and no report failures. A pair
  with a revoked generation is `revoked`; anything else is
  `legacy_unassessed`, which keeps a row only when it equals the published
  baseline, as `closure_rows.reconstruct` does.
- **References** are `closure_rows._REFERENCES` within the partition.
- **Release checks** are `builder.CHECKS`: enums, dates and months, callback
  counts against marks, contiguous unique places, link status, bib to one
  WSDC id. Today each of them fails a whole release.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from swingset.sources.phase_one import stored_scope_ref
from swingset.state import body_packs

from .builder import CHECKS, BuildError
from .closure_manifest import canonical
from .closure_rows import _REFERENCES

#: An observation as support matches it: scope kind, scope ref, kind, seq,
#: payload sha256 and extract version.
Observation = tuple[str, str, str, int, str, str]
#: Accepted generations whose observation keys stay cached in one build.
OBSERVATION_CACHE = 4096

Rows = Mapping[str, Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class Support:
    """One snapshot and parser's standing, with the accepted generations behind it."""

    state: str
    generations: tuple[str, ...] = ()


class SupportIndex:
    """Support of `(snapshot_id, parser_version)` pairs, cached for one build.

    Each pair is resolved through its watch's source units, as
    `admission.support.interpretation_support` does, so a lookup reads a few
    indexed rows. The accepted rule is `closure_support._accepted`'s, without
    its cutoff. `matched` then asks, observation by observation, whether an
    accepted generation of the pair contains what a partition's rows came
    from (`closure_support.support`'s key).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._cache: dict[tuple[str, str], Support] = {}
        self._observations: OrderedDict[str, frozenset[Observation]] = OrderedDict()
        # Report failures of packed bodies by pack digest: one pack read each.
        self._packed_failures: dict[str, bool] = {}
        self._policies = {
            str(kind): str(version)
            for kind, version in conn.execute(
                "SELECT page_kind,contract_version FROM admission_policies"
            )
        }

    def __call__(self, snapshot_id: str, parser_version: str) -> Support:
        key = str(snapshot_id), str(parser_version)
        found = self._cache.get(key)
        if found is None:
            found = self._cache[key] = self._resolve(*key)
        return found

    def _resolve(self, snapshot_id: str, parser_version: str) -> Support:
        accepted: list[str] = []
        revoked = False
        # Only the recipe's snapshot and parser: a registry recipe is about
        # 4 kB of accepted inputs, and a build resolves every pair (F26).
        for (
            generation_id,
            state,
            page_kind,
            contract,
            recipe_snapshot,
            recipe_parser,
            report_json,
        ) in self.conn.execute(
            "SELECT g.generation_id,g.state,g.page_kind,g.contract_version,"
            "json_extract(g.recipe_json,'$.context.snapshot_id'),"
            "json_extract(g.recipe_json,'$.parser_version'),"
            "g.report_json FROM snapshots s JOIN source_units u ON u.watch_id=s.watch_id "
            "JOIN source_generations g ON g.unit_key=u.unit_key WHERE s.snapshot_id=? "
            "ORDER BY g.generation_id",
            (snapshot_id,),
        ):
            if str(recipe_snapshot) != snapshot_id or str(recipe_parser) != parser_version:
                continue
            if state == "revoked":
                revoked = True
                continue
            if (
                self._policies.get(str(page_kind)) == str(contract)
                and self.conn.execute(
                    "SELECT 1 FROM admission_decisions WHERE generation_id=? AND state='accepted' LIMIT 1",
                    (generation_id,),
                ).fetchone()
                and not self._failures(str(generation_id), str(report_json))
            ):
                accepted.append(str(generation_id))
        if revoked:
            return Support("revoked")
        if accepted:
            return Support("accepted", tuple(accepted))
        return Support("legacy_unassessed")

    def _failures(self, generation_id: str, report_json: str) -> bool:
        """Whether the generation's report has failures; a packed one is read from its pack."""
        if not body_packs.is_stub(report_json):
            return bool(json.loads(report_json).get("failures"))
        digest = body_packs.source_pack(self.conn, generation_id)
        if digest is None:
            raise body_packs.BodyUnavailable(f"source generation {generation_id} has no pack row")
        found = self._packed_failures.get(digest)
        if found is None:
            report = body_packs.packed_body(self.conn, generation_id).report_json
            found = self._packed_failures[digest] = bool(json.loads(report).get("failures"))
        return found

    def generation_observations(self, generation_id: str) -> frozenset[Observation]:
        """The observation keys an accepted source generation's result holds (cached)."""
        found = self._observations.get(generation_id)
        if found is not None:
            self._observations.move_to_end(generation_id)
            return found
        row = self.conn.execute(
            "SELECT CASE WHEN result_json=:stub THEN NULL "
            "ELSE json_extract(result_json,'$.observations') END,"
            "json_extract(recipe_json,'$.extract_version'),result_json=:stub,unit_key "
            "FROM source_generations WHERE generation_id=:id",
            {"stub": body_packs.STUB, "id": generation_id},
        ).fetchone()
        if row is not None and row[2]:
            # A packed body: the same extraction over the exact stored string.
            result = body_packs.packed_body(self.conn, generation_id).result_json
            extracted = self.conn.execute(
                "SELECT json_extract(?,'$.observations')", (result,)
            ).fetchone()[0]
            row = (extracted, row[1], 0, row[3])
        keys: set[Observation] = set()
        if row is not None and row[0] is not None:
            extract = str(row[1])
            # An archived listing capture's unit is `watch_id/snapshot_id`;
            # its calendar rows are stored as history (D-0257).
            independent = "/" in str(row[3])
            for seq, item in enumerate(json.loads(row[0])):
                scope = item.get("scope") or {}
                payload = canonical(item.get("payload"))
                keys.add(
                    (
                        str(scope.get("kind")),
                        stored_scope_ref(
                            independent, str(scope.get("kind")), str(scope.get("ref"))
                        ),
                        str(item.get("kind")),
                        seq,
                        hashlib.sha256(payload.encode()).hexdigest(),
                        extract,
                    )
                )
        value = frozenset(keys)
        self._observations[generation_id] = value
        if len(self._observations) > OBSERVATION_CACHE:
            self._observations.popitem(last=False)
        return value

    def matched(
        self, snapshot_id: str, parser_version: str, observations: Iterable[Observation]
    ) -> Support:
        """The pair's support, `accepted` only when an accepted generation holds each observation.

        ``observations`` are what the rows came from. None of them, or one
        that no accepted generation of the pair contains (another extract
        version, a changed payload), leaves the pair `legacy_unassessed`.
        """
        pair = self(snapshot_id, parser_version)
        if pair.state != "accepted":
            return pair
        wanted = set(observations)
        if not wanted:
            return Support("legacy_unassessed")
        holding: list[str] = []
        # The newest generation first: it is usually the one that matches.
        for generation in reversed(pair.generations):
            held = self.generation_observations(generation)
            if wanted & held:
                holding.append(generation)
            wanted -= held
            if not wanted:
                return Support("accepted", tuple(sorted(holding)))
        return Support("legacy_unassessed")


def live_observations(
    conn: sqlite3.Connection, snapshot_id: str, parser_version: str
) -> frozenset[Observation]:
    """A snapshot's live observations at one parser version (through the watch index)."""
    return frozenset(
        (
            str(scope_kind),
            str(scope_id),
            str(kind),
            int(seq),
            hashlib.sha256(str(payload).encode()).hexdigest(),
            str(extract),
        )
        for scope_kind, scope_id, kind, seq, payload, extract in conn.execute(
            "SELECT o.scope_kind,o.scope_id,o.kind,o.seq,o.payload_json,o.extract_version "
            "FROM observations o WHERE o.watch_id=(SELECT watch_id FROM snapshots "
            "WHERE snapshot_id=:s) AND o.snapshot_id=:s AND CAST(o.parser_version AS TEXT)=:p",
            {"s": snapshot_id, "p": parser_version},
        )
    )


def support_inputs(index: SupportIndex, keys: Iterable[tuple[str, str]]) -> list[list[Any]]:
    """`[snapshot, parser, state, accepted generations]` for each pair, sorted."""
    result = []
    for snapshot, parser in sorted({(str(a), str(b)) for a, b in keys}):
        support = index(snapshot, parser)
        result.append([snapshot, parser, support.state, list(support.generations)])
    return result


def dangling(rows: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[tuple[str, str, str]]:
    """Rows naming a parent the partition lacks, as the builder would refuse them.

    ``rows`` maps `(table, record_key)` to its merged payload. Each result is
    `(table, record_key, field)`.
    """
    found = []
    for (table, key), row in sorted(rows.items()):
        for name, field, target, required in _REFERENCES:
            if name != table:
                continue
            value = row.get(field)
            missing = required if value is None else (target, canonical([value])) not in rows
            if missing:
                found.append((table, key, field))
                break
    return found


def release_checks(rows: Rows, history_start: date) -> tuple[str, str] | None:
    """The first builder check the rows fail, as `(reason, message)`, or None."""
    for reason, check in CHECKS:
        try:
            check(rows, history_start)
        except BuildError as error:
            return reason, str(error)
    return None
