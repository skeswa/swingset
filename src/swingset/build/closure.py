"""Pin compatible immutable scope generations independently of live work queues."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .closure_manifest import ClosureError, canonical, digest, generation, leaves, members

__all__ = ["ClosureError", "ReleaseClosure", "select", "validate"]


def _scope(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return str(item["stage"]), str(item["unit_kind"]), str(item["unit_id"])


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ClosureError("closure_cutoff_requires_timezone")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class ReleaseClosure:
    cutoff: str
    selected: tuple[dict[str, Any], ...]
    inventory: tuple[dict[str, Any], ...]
    source_support: tuple[dict[str, Any], ...]
    policies: tuple[dict[str, Any], ...]
    dependency_sets: tuple[str, ...]
    baseline: Mapping[str, Any] | None = None
    revocation_digest: str = ""
    event_coverage: Mapping[str, Any] | None = None

    @property
    def semantic_fingerprint(self) -> str:
        return digest(
            {
                "selected": [row["generation_id"] for row in self.selected],
                "support": self.support_token,
                "baseline": self.baseline,
            }
        )

    @property
    def support_token(self) -> str:
        from .closure_support import support_token

        return support_token(self.manifest())

    def manifest(self) -> dict[str, Any]:
        value = {
            "format": "release-closure-v1",
            "cutoff": self.cutoff,
            "selected": list(self.selected),
            "inventory": list(self.inventory),
            "source_support": list(self.source_support),
            "policies": list(self.policies),
            "dependency_sets": list(self.dependency_sets),
            "baseline": self.baseline,
            "revocation_digest": self.revocation_digest,
        }
        if self.event_coverage is not None:
            value["event_coverage"] = dict(self.event_coverage)
        return {**value, "digest": digest(value)}


class _Traversed(set[str]):
    """The dependency sets a selection has walked; `attempt` can undo additions."""

    log: list[tuple[str, Any]] | None = None

    def add(self, item: str) -> None:
        if self.log is not None and item not in self:
            self.log.append(("traversed", item))
        super().add(item)


class _Selection:
    def __init__(
        self,
        conn: sqlite3.Connection,
        cutoff: str,
        current: Mapping[tuple[str, str, str], str] | None = None,
    ):
        self.conn, self.cutoff = conn, _time(cutoff)
        # Each project scope's anchor generation at the cutoff. A walk that
        # reaches another generation of such a scope reaches a superseded one:
        # since output cutoff no longer checks the walk, a kept generation can
        # name one (D-0224). That anchor is withheld, never published stale.
        self.current = dict(current or {})
        self.selected: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.continuity_sets: set[str] = set()
        self.observations: dict[str, dict[str, Any]] = {}
        self.sources: set[str] = set()
        self.identifiers: set[str] = set()
        self.traversed: _Traversed = _Traversed()
        self.active: set[str] = set()
        self._log: list[tuple[str, Any]] | None = None

    def _note(self, change: str, value: Any) -> None:
        if self._log is not None:
            self._log.append((change, value))

    def attempt(self, identifier: str) -> bool:
        """Add one generation and its closure, or leave the selection as it was.

        A conflict (`ClosureError`) undoes every addition the attempt made,
        which is what copying the selection before each try did, without
        copying a registry-sized selection per anchor or link.
        """
        log: list[tuple[str, Any]] = []
        self._log = self.traversed.log = log
        try:
            self.add(identifier)
        except ClosureError:
            for change, value in reversed(log):
                if change == "selected":
                    del self.selected[value]
                elif change == "identifier":
                    self.identifiers.discard(value)
                elif change == "observation":
                    del self.observations[value]
                elif change == "source":
                    self.sources.discard(value)
                elif change == "continuity":
                    self.continuity_sets.discard(value)
                else:
                    self.traversed.discard(value)
            self.active.clear()
            return False
        finally:
            self._log = self.traversed.log = None
        return True

    def add(self, identifier: str) -> None:
        if identifier in self.active:
            raise ClosureError("cyclic_selected_generations")
        if identifier in self.identifiers:
            return
        item = generation(self.conn, identifier)
        key = _scope(item)
        previous = self.selected.get(key)
        if previous is not None:
            if previous["generation_id"] != identifier:
                raise ClosureError("mixed_dependency_generations")
            return
        if self.current.get(key, identifier) != identifier:
            raise ClosureError("superseded_dependency_generation")
        if _time(item["created_at"]) > self.cutoff:
            raise ClosureError("selected_generation_after_cutoff")
        self.selected[key] = item
        self._note("selected", key)
        self.identifiers.add(identifier)
        self._note("identifier", identifier)
        self.active.add(identifier)
        # One immutable set always names the same dependencies. Already applied
        # members need not be decoded again for every link sharing the registry
        # pool; every distinct generation still passes the scope conflict fence.
        for dependency in leaves(self.conn, item["dependency_set_id"], visited=self.traversed):
            kind = dependency.get("kind")
            if kind == "derivation":
                if dependency.get("generation_id") is None:
                    raise ClosureError("unmaterialized_selected_dependency")
                self.add(str(dependency["generation_id"]))
                selected_dependency = self.selected.get(tuple(dependency.get("key", ())))
                if (
                    selected_dependency is None
                    or selected_dependency["generation_id"] != dependency["generation_id"]
                ):
                    raise ClosureError("selected_dependency_scope_mismatch")
            elif kind == "observation":
                previous_observation = self.observations.get(str(dependency["key"]))
                if previous_observation is not None and previous_observation != dependency:
                    raise ClosureError("mixed_source_interpretations")
                if previous_observation is None:
                    self._note("observation", str(dependency["key"]))
                self.observations[str(dependency["key"])] = dependency
            elif kind == "source_selection" and dependency.get("generation_id"):
                source = str(dependency["generation_id"])
                if source not in self.sources:
                    self._note("source", source)
                self.sources.add(source)
        continuity = item["recipe"].get("continuity", {}).get("dependency_set_id")
        if continuity:
            # Historical support is retained, but is not another selected scope.
            for _ in members(self.conn, continuity):
                pass
            if continuity not in self.continuity_sets:
                self._note("continuity", continuity)
            self.continuity_sets.add(continuity)
        self.active.remove(identifier)

    def dependency_sets(self) -> set[str]:
        # Traversal already accumulates every ordinary set. Combining this on
        # each generation would repeatedly copy the entire growing graph.
        return set(self.traversed) | self.continuity_sets


def _policy(conn: sqlite3.Connection) -> tuple[dict[str, Any], ...]:
    return tuple(
        dict(row) for row in conn.execute("SELECT * FROM admission_policies ORDER BY page_kind")
    )


def _baseline(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    from .files import sha256_file

    published = json.loads((path / "PUBLISHED").read_bytes())
    return {
        "commit": published["commit"],
        "manifest_hash": sha256_file(path / "_meta/manifest.json"),
    }


def _anchors(conn: sqlite3.Connection, kind: str, at: str, building: str) -> list[tuple[str, str]]:
    """Each registered scope of a project kind, with the generation it anchors.

    Its pointer when that was made by the cutoff, otherwise its newest
    generation made by then; never an unfinished one. A scope with neither is
    left out.
    """
    rows = conn.execute(
        "SELECT unit_id,generation_id FROM (SELECT g.unit_id,g.generation_id,row_number() OVER ("
        "PARTITION BY g.unit_id ORDER BY coalesce(g.generation_id=s.materialized_generation_id,0) "
        "DESC,julianday(g.created_at) DESC,g.rowid DESC) AS n "
        "FROM derivation_scopes s JOIN derivation_generations g ON g.stage=s.stage "
        "AND g.unit_kind=s.unit_kind AND g.unit_id=s.unit_id "
        "WHERE s.stage='project' AND s.unit_kind=? AND julianday(g.created_at)<=julianday(?)"
        + building
        + ") WHERE n=1",
        (kind, at),
    ).fetchall()
    return [(str(unit), str(generation)) for unit, generation in rows if generation is not None]


def select(
    conn: sqlite3.Connection, *, cutoff: datetime, baseline: Path | None = None
) -> ReleaseClosure:
    """Anchor on every year, source event and dancer, then add compatible identity joins.

    Years anchor in year order; each brings its events, calendars, inventory
    and map. A year whose walk conflicts with what earlier anchors selected
    (`mixed_dependency_generations`) or reaches a project scope's generation
    other than that scope's own anchor (`superseded_dependency_generation`:
    since output cutoff no longer checks the walk, a kept generation may name
    a superseded upstream one, D-0224) is skipped, and its events are
    withheld with `year_anchor_conflict`, never published stale. Then each
    source event's and each dancer's pointer, where it agrees with what the
    years selected. Without a year anchor nothing is selected (plan F22).
    """
    if cutoff.tzinfo is None:
        raise ClosureError("closure_cutoff_requires_timezone")
    at = cutoff.astimezone(UTC).isoformat()
    inventory: list[dict[str, Any]] = []
    # A generation still being stored in chunks, or one a staged run stopped
    # storing, is not output (WP4).
    from swingset.state.derivations import unfinished_excluded

    building = unfinished_excluded(conn, "g")
    current = {
        ("project", str(kind), unit): generation
        for (kind,) in conn.execute(
            "SELECT DISTINCT unit_kind FROM derivation_scopes WHERE stage='project'"
        ).fetchall()
        for unit, generation in _anchors(conn, str(kind), at, building)
    }
    chosen = _Selection(conn, at, current)
    conflicts: list[str] = []
    years = sorted(_anchors(conn, "year", at, building), key=lambda item: (int(item[0]), item[0]))
    for year, identifier in years:
        if not chosen.attempt(identifier):
            conflicts.append(year)
    if years:
        # Source events carry their listings' support, which history named
        # as prerequisites; a year does not.
        for kind in ("source_event", "dancer"):
            for _, identifier in sorted(_anchors(conn, kind, at, building)):
                chosen.attempt(identifier)
    for year in conflicts:
        inventory.append(
            {
                "stage": "project",
                "unit_kind": "year",
                "unit_id": year,
                "status": "withheld",
                "reason": "year_anchor_conflict",
            }
        )
        for (event,) in conn.execute(
            "SELECT event_id FROM events WHERE year=? ORDER BY event_id", (int(year),)
        ):
            if ("project", "event", str(event)) not in chosen.selected:
                inventory.append(
                    {
                        "stage": "project",
                        "unit_kind": "event",
                        "unit_id": str(event),
                        "status": "withheld",
                        "reason": "year_anchor_conflict",
                    }
                )
    # The year anchors name both sides of any atomic alias move. A link may
    # not replace its pinned event, registry or association inputs.
    events = [key[2] for key in chosen.selected if key[:2] == ("project", "event")]
    for event in sorted(events):
        linked = False
        for (identifier,) in conn.execute(
            "SELECT generation_id FROM derivation_generations WHERE stage='link' AND unit_kind='event' AND unit_id=? AND julianday(created_at)<=julianday(?)"
            + unfinished_excluded(conn)
            + " ORDER BY julianday(created_at) DESC,rowid DESC",
            (event, at),
        ):
            if chosen.attempt(identifier):
                linked = True
                break
        if not linked:
            inventory.append(
                {
                    "stage": "link",
                    "unit_kind": "event",
                    "unit_id": event,
                    "status": "withheld",
                    "reason": "compatible_link_generation_unavailable",
                }
            )
    selected_ids = {row["generation_id"] for row in chosen.selected.values()}
    inventory_keys = {_scope(item) for item in inventory}
    from swingset.state.derivation_dependencies import RETIRED_KINDS

    for row in conn.execute(
        "SELECT stage,unit_kind,unit_id,materialized_generation_id FROM derivation_scopes WHERE stage IN ('project','link') ORDER BY stage,unit_kind,unit_id"
    ):
        key = str(row[0]), str(row[1]), str(row[2])
        if key[:2] in RETIRED_KINDS:
            # A retired scope (history/all) is neither selected nor withheld.
            continue
        selected = chosen.selected.get(key)
        if selected:
            inventory.append(
                {
                    "stage": key[0],
                    "unit_kind": key[1],
                    "unit_id": key[2],
                    "generation_id": selected["generation_id"],
                    "status": "selected" if row[3] == selected["generation_id"] else "retained",
                    "reason": "compatible_selected_generation",
                }
            )
        elif key not in inventory_keys:
            inventory.append(
                {
                    "stage": key[0],
                    "unit_kind": key[1],
                    "unit_id": key[2],
                    "status": "unavailable" if row[3] is None else "withheld",
                    "reason": "unmaterialized_scope"
                    if row[3] is None
                    else "outside_compatible_closure",
                }
            )
    from .closure_support import support

    evidence = support(conn, chosen.observations.values(), chosen.sources, cutoff=at)
    from .event_coverage import capture as capture_events

    result = ReleaseClosure(
        at,
        tuple(sorted(chosen.selected.values(), key=_scope)),
        tuple(inventory),
        evidence,
        _policy(conn),
        tuple(sorted(chosen.dependency_sets())),
        _baseline(baseline),
        _revocations(conn),
        capture_events(conn, cutoff=at, selected_support=evidence),
    )
    assert len(selected_ids) == len(result.selected)
    return result


def validate(conn: sqlite3.Connection, value: ReleaseClosure | Mapping[str, Any]) -> None:
    """Check the pinned graph; newer work and source selections are irrelevant."""
    from .closure_validation import remember, unchanged

    manifest = value.manifest() if isinstance(value, ReleaseClosure) else hydrate(conn, value)
    if manifest.get("format") != "release-closure-v1" or (
        not _verified(manifest)
        and digest({k: v for k, v in manifest.items() if k != "digest"}) != manifest.get("digest")
    ):
        raise ClosureError("closure_manifest_digest_mismatch")
    from .event_local_coverage import check_artifacts

    local_budget = (
        check_artifacts(conn, manifest["event_coverage"])
        if manifest.get("event_coverage") is not None
        else None
    )
    if unchanged(conn, str(manifest["digest"])):
        return
    if manifest.get("revocation_digest") != _revocations(conn):
        raise ClosureError("selected_source_support_revoked")
    selected = manifest["selected"]
    if len({_scope(row) for row in selected}) != len(selected) or len(
        {row["generation_id"] for row in selected}
    ) != len(selected):
        raise ClosureError("duplicate_selected_generation")
    graph = _Selection(conn, manifest["cutoff"])
    for row in selected:
        graph.add(row["generation_id"])
        # add() loads the complete immutable receipt, including generations
        # reached recursively. Compare that same verified row without a second
        # SQLite read and recipe decode for every selected generation.
        if graph.selected.get(_scope(row)) != row:
            raise ClosureError("selected_generation_receipt_mismatch")
    if {row["generation_id"] for row in graph.selected.values()} != {
        row["generation_id"] for row in selected
    }:
        raise ClosureError("selected_dependency_omitted")
    if sorted(graph.dependency_sets()) != manifest["dependency_sets"]:
        raise ClosureError("selected_dependency_manifest_omitted")
    if list(_policy(conn)) != manifest["policies"]:
        raise ClosureError("selected_acceptance_policy_changed")
    from .closure_support import support, validate_support

    expected_observations = {key: item for key, item in graph.observations.items()}
    supported = manifest["source_support"]
    if len(supported) != len(expected_observations) or {
        item["observation_id"] for item in supported
    } != set(expected_observations):
        raise ClosureError("selected_source_support_omitted")
    for item in supported:
        original = expected_observations[item["observation_id"]]
        if any(item.get(key) != content for key, content in original.items()):
            raise ClosureError("selected_source_support_mismatch")
        if any(
            receipt["generation_id"] not in graph.sources for receipt in item["source_generations"]
        ):
            raise ClosureError("source_support_outside_selected_closure")
    validate_support(conn, supported)
    expected_support = support(
        conn, graph.observations.values(), graph.sources, cutoff=manifest["cutoff"]
    )
    if list(expected_support) != supported:
        raise ClosureError("selected_source_admission_witness_mismatch")
    if manifest.get("event_coverage") is not None:
        from .event_coverage import validate as validate_events

        if manifest["event_coverage"]["cutoff"] != manifest["cutoff"]:
            raise ClosureError("event_coverage_cutoff_mismatch")
        validate_events(
            conn, manifest["event_coverage"], selected_support=supported, local_budget=local_budget
        )
    remember(conn, manifest, graph.sources)


def _revocations(conn: sqlite3.Connection) -> str:
    return digest(
        [
            tuple(row)
            for row in conn.execute(
                "SELECT generation_id,input_fingerprint FROM source_generations WHERE state='revoked' ORDER BY generation_id"
            )
        ]
    )


def public_manifest(value: ReleaseClosure | Mapping[str, Any]) -> dict[str, Any]:
    """Public commitment to private provenance; never publish raw recipes/scopes."""
    from .closure_support import support_token

    manifest = value.manifest() if isinstance(value, ReleaseClosure) else dict(value)
    if manifest.get("format") == "release-closure-public-v1":
        return manifest
    public = {
        "format": "release-closure-public-v1",
        "private_digest": manifest["digest"],
        "cutoff": manifest["cutoff"],
        "selected_generations": [item["generation_id"] for item in manifest["selected"]],
        "support_token": support_token(manifest),
        "inventory_digest": digest(manifest["inventory"]),
        "baseline": manifest["baseline"],
    }
    if manifest.get("event_coverage") is not None:
        public["event_coverage"] = {
            "digest": manifest["event_coverage"]["digest"],
            "omitted_subjects": manifest["event_coverage"]["omitted_subjects"],
        }
    return {**public, "digest": digest(public)}


def retain(conn: sqlite3.Connection, value: ReleaseClosure | Mapping[str, Any]) -> None:
    """Retain immutable input proof; this does not complete a build generation."""
    if not conn.in_transaction:
        raise ClosureError("closure_retention_requires_output_transaction")
    manifest = value.manifest() if isinstance(value, ReleaseClosure) else hydrate(conn, value)
    if conn.execute(
        "SELECT 1 FROM derivation_dependency_sets WHERE dependency_set_id=?",
        (str(manifest["digest"]),),
    ).fetchone():
        # Content-addressed and immutable: the stored proof is these bytes.
        return
    private = {key: item for key, item in manifest.items() if key != "digest"}
    if digest(private) != manifest["digest"]:
        raise ClosureError("closure_manifest_digest_mismatch")
    conn.execute(
        "INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)",
        (manifest["digest"], canonical(private)),
    )


_known_proofs: ContextVar[dict[str, tuple[Mapping[str, Any], str]] | None] = ContextVar(
    "known_closure_proofs", default=None
)


@contextmanager
def known_proof(private: Mapping[str, Any]) -> Iterator[None]:
    """Verify one private proof once, then reuse it for a single operation.

    Build completion names its proof by digest and resolves and validates it at
    four boundaries inside one bounded write. Recomputing digests over its
    235 MB each time cost seconds and ratcheted memory toward the worker's
    8 GB limit (D-0199). The digest and the public commitment are checked here,
    once; later uses of the same objects skip only those two recomputations.
    """
    if digest({key: item for key, item in private.items() if key != "digest"}) != private.get(
        "digest"
    ):
        raise ClosureError("closure_manifest_digest_mismatch")
    public = public_manifest(private)
    token = _known_proofs.set({str(private["digest"]): (private, str(public["digest"]))})
    try:
        yield
    finally:
        _known_proofs.reset(token)


def _verified(manifest: Mapping[str, Any]) -> bool:
    """Whether these are the exact objects known_proof already verified."""
    entry = (_known_proofs.get() or {}).get(str(manifest.get("digest")))
    if entry is None:
        return False
    known = entry[0]
    return manifest.keys() == known.keys() and all(manifest[key] is known[key] for key in known)


def hydrate(conn: sqlite3.Connection, value: Mapping[str, Any]) -> dict[str, Any]:
    manifest = dict(value)
    if manifest.get("format") != "release-closure-public-v1":
        return manifest
    if digest({key: item for key, item in manifest.items() if key != "digest"}) != manifest.get(
        "digest"
    ):
        raise ClosureError("public_closure_digest_mismatch")
    entry = (_known_proofs.get() or {}).get(str(manifest["private_digest"]))
    if entry is not None:
        # The public digest covers every public field, private_digest included.
        if manifest["digest"] != entry[1]:
            raise ClosureError("public_closure_commitment_mismatch")
        return dict(entry[0])
    row = conn.execute(
        "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
        (manifest["private_digest"],),
    ).fetchone()
    if row is None:
        raise ClosureError("private_closure_proof_unavailable")
    private: dict[str, Any] = json.loads(row[0])
    if digest(private) != manifest["private_digest"]:
        raise ClosureError("private_closure_proof_corrupt")
    private["digest"] = manifest["private_digest"]
    if public_manifest(private) != manifest:
        raise ClosureError("public_closure_commitment_mismatch")
    return private
