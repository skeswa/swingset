"""Pin compatible immutable scope generations independently of live work queues."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
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
        return {**value, "digest": digest(value)}


class _Selection:
    def __init__(self, conn: sqlite3.Connection, cutoff: str):
        self.conn, self.cutoff = conn, _time(cutoff)
        self.selected: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.continuity_sets: set[str] = set()
        self.observations: dict[str, dict[str, Any]] = {}
        self.sources: set[str] = set()
        self.identifiers: set[str] = set()
        self.traversed: set[str] = set()
        self.active: set[str] = set()

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
        if _time(item["created_at"]) > self.cutoff:
            raise ClosureError("selected_generation_after_cutoff")
        self.selected[key] = item
        self.identifiers.add(identifier)
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
                self.observations[str(dependency["key"])] = dependency
            elif kind == "source_selection" and dependency.get("generation_id"):
                self.sources.add(str(dependency["generation_id"]))
        continuity = item["recipe"].get("continuity", {}).get("dependency_set_id")
        if continuity:
            # Historical support is retained, but is not another selected scope.
            for _ in members(self.conn, continuity):
                pass
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


def select(
    conn: sqlite3.Connection, *, cutoff: datetime, baseline: Path | None = None
) -> ReleaseClosure:
    """Select one coherent structural anchor, then only compatible identity joins."""
    if cutoff.tzinfo is None:
        raise ClosureError("closure_cutoff_requires_timezone")
    at = cutoff.astimezone(UTC).isoformat()
    chosen = _Selection(conn, at)
    inventory: list[dict[str, Any]] = []
    anchors = conn.execute(
        "SELECT generation_id FROM derivation_generations WHERE stage='project' AND unit_kind='history' AND unit_id='all' AND julianday(created_at)<=julianday(?) ORDER BY julianday(created_at) DESC,rowid DESC",
        (at,),
    )
    for (identifier,) in anchors:
        proposed = _Selection(conn, at)
        try:
            proposed.add(identifier)
        except ClosureError:
            continue
        chosen = proposed
        break
    # The history anchor names both sides of any atomic alias move. A link may
    # not replace its pinned event, registry or historical association inputs.
    events = [key[2] for key in chosen.selected if key[:2] == ("project", "event")]
    for event in sorted(events):
        linked = False
        for (identifier,) in conn.execute(
            "SELECT generation_id FROM derivation_generations WHERE stage='link' AND unit_kind='event' AND unit_id=? AND julianday(created_at)<=julianday(?) ORDER BY julianday(created_at) DESC,rowid DESC",
            (event, at),
        ):
            candidate = _Selection(conn, at)
            candidate.selected = dict(chosen.selected)
            candidate.identifiers = set(chosen.identifiers)
            candidate.traversed = set(chosen.traversed)
            candidate.continuity_sets = set(chosen.continuity_sets)
            candidate.observations = dict(chosen.observations)
            candidate.sources = set(chosen.sources)
            try:
                candidate.add(identifier)
            except ClosureError:
                continue
            chosen = candidate
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
    for row in conn.execute(
        "SELECT stage,unit_kind,unit_id,materialized_generation_id FROM derivation_scopes WHERE stage IN ('project','link') ORDER BY stage,unit_kind,unit_id"
    ):
        key = str(row[0]), str(row[1]), str(row[2])
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
    result = ReleaseClosure(
        at,
        tuple(sorted(chosen.selected.values(), key=_scope)),
        tuple(inventory),
        evidence,
        _policy(conn),
        tuple(sorted(chosen.dependency_sets())),
        _baseline(baseline),
        _revocations(conn),
    )
    assert len(selected_ids) == len(result.selected)
    return result


def validate(conn: sqlite3.Connection, value: ReleaseClosure | Mapping[str, Any]) -> None:
    """Check the pinned graph; newer work and source selections are irrelevant."""
    manifest = value.manifest() if isinstance(value, ReleaseClosure) else hydrate(conn, value)
    if manifest.get("format") != "release-closure-v1" or digest(
        {k: v for k, v in manifest.items() if k != "digest"}
    ) != manifest.get("digest"):
        raise ClosureError("closure_manifest_digest_mismatch")
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
    return {**public, "digest": digest(public)}


def retain(conn: sqlite3.Connection, value: ReleaseClosure | Mapping[str, Any]) -> None:
    """Retain immutable input proof; this does not complete a build generation."""
    if not conn.in_transaction:
        raise ClosureError("closure_retention_requires_output_transaction")
    manifest = value.manifest() if isinstance(value, ReleaseClosure) else hydrate(conn, value)
    private = {key: item for key, item in manifest.items() if key != "digest"}
    if digest(private) != manifest["digest"]:
        raise ClosureError("closure_manifest_digest_mismatch")
    conn.execute(
        "INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)",
        (manifest["digest"], canonical(private)),
    )


def hydrate(conn: sqlite3.Connection, value: Mapping[str, Any]) -> dict[str, Any]:
    manifest = dict(value)
    if manifest.get("format") != "release-closure-public-v1":
        return manifest
    if digest({key: item for key, item in manifest.items() if key != "digest"}) != manifest.get(
        "digest"
    ):
        raise ClosureError("public_closure_digest_mismatch")
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
