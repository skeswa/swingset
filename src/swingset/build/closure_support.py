"""Admissibility of pinned interpretations, independent of newer watch pointers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

from .closure_manifest import ClosureError, canonical, digest


def _accepted(
    conn: sqlite3.Connection, identifier: str, *, include_result: bool = True
) -> sqlite3.Row | None:
    # Validation only needs the receipt. Fetching every potentially large parse
    # result here would copy it again before support() actually interprets it.
    result = ",g.result_json" if include_result else ""
    row = conn.execute(
        "SELECT g.generation_id,g.input_fingerprint,g.contract_version,g.page_kind,"
        "g.created_at,g.recipe_json,g.report_json" + result + " "
        "FROM source_generations g JOIN admission_policies p ON p.page_kind=g.page_kind "
        "AND p.contract_version=g.contract_version "
        "WHERE g.generation_id=? AND g.state!='revoked' AND EXISTS "
        "(SELECT 1 FROM admission_decisions d WHERE d.generation_id=g.generation_id AND d.state='accepted')",
        (identifier,),
    ).fetchone()
    if row is None:
        return None
    report = json.loads(row["report_json"])
    return None if report.get("failures") else row


def _observations(
    row: sqlite3.Row, *, recipe: Mapping[str, Any] | None = None
) -> Iterator[dict[str, Any]]:
    recipe = json.loads(row["recipe_json"]) if recipe is None else recipe
    snapshot = recipe["context"]["snapshot_id"]
    for seq, observation in enumerate(json.loads(row["result_json"])["observations"]):
        scope = observation["scope"]
        payload = canonical(observation["payload"])
        yield {
            "snapshot_id": snapshot,
            "scope": [scope["kind"], scope["ref"]],
            "observation_kind": observation["kind"],
            "seq": seq,
            "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            "payload_json": payload,
            "parser_version": str(recipe["parser_version"]),
            "extract_version": str(recipe["extract_version"]),
        }


def support(
    conn: sqlite3.Connection,
    observations: Iterable[Mapping[str, Any]],
    sources: Iterable[str],
    *,
    cutoff: str,
) -> tuple[dict[str, Any], ...]:
    from datetime import datetime

    selected = {str(item["key"]): dict(item) for item in observations}
    matches: dict[tuple[Any, ...], list[str]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for identifier in sorted(sources):
        row = _accepted(conn, identifier)
        if row is None or datetime.fromisoformat(row["created_at"]) > datetime.fromisoformat(
            cutoff
        ):
            continue
        recipe = json.loads(row["recipe_json"])
        receipts[identifier] = {
            "generation_id": identifier,
            "input_fingerprint": row["input_fingerprint"],
            "contract_version": row["contract_version"],
            "page_kind": row["page_kind"],
            "recipe": recipe,
        }
        for observation in _observations(row, recipe=recipe):
            key = (
                observation["snapshot_id"],
                tuple(observation["scope"]),
                observation["observation_kind"],
                observation["seq"],
                observation["payload_sha256"],
                observation["parser_version"],
                observation["extract_version"],
            )
            matches.setdefault(key, []).append(identifier)
    revoked = {
        (
            str(json.loads(row[0])["context"]["snapshot_id"]),
            str(json.loads(row[0])["parser_version"]),
        )
        for row in conn.execute("SELECT recipe_json FROM source_generations WHERE state='revoked'")
    }
    result = []
    for identifier, observation in sorted(selected.items()):
        key = (
            observation["snapshot_id"],
            tuple(observation.get("scope", ())),
            observation.get("observation_kind"),
            observation.get("seq"),
            observation["payload_sha256"],
            str(observation["parser_version"]),
            str(observation["extract_version"]),
        )
        accepted = matches.get(key, [])
        state = "accepted" if accepted else "legacy_unassessed"
        if (str(observation["snapshot_id"]), str(observation["parser_version"])) in revoked:
            state = "revoked"
        source_receipts = [receipts[value] for value in accepted]
        result.append(
            {
                **observation,
                "observation_id": identifier,
                "state": state,
                "source_generations": source_receipts,
            }
        )
    return tuple(result)


def validate_support(conn: sqlite3.Connection, evidence: Iterable[Mapping[str, Any]]) -> None:
    revoked = {
        (
            str(json.loads(row[0])["context"]["snapshot_id"]),
            str(json.loads(row[0])["parser_version"]),
        )
        for row in conn.execute("SELECT recipe_json FROM source_generations WHERE state='revoked'")
    }
    checked: set[str] = set()
    for item in evidence:
        key = str(item["snapshot_id"]), str(item["parser_version"])
        if key in revoked and item["state"] != "revoked":
            raise ClosureError("selected_source_support_revoked")
        snapshot = conn.execute(
            "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?", (key[0],)
        ).fetchone()
        if snapshot is None or snapshot[0] != item["body_sha256"]:
            raise ClosureError("selected_source_artifact_changed")
        for receipt in item["source_generations"]:
            identifier = receipt["generation_id"]
            if identifier in checked:
                continue
            checked.add(identifier)
            source = _accepted(conn, identifier, include_result=False)
            if (
                source is None
                or source["input_fingerprint"] != receipt["input_fingerprint"]
                or json.loads(source["recipe_json"]) != receipt["recipe"]
            ):
                raise ClosureError("selected_source_support_changed")


def support_token(manifest: Mapping[str, Any]) -> str:
    """Excludes selection pointers, work state, and evidence arriving later."""
    if manifest.get("format") == "release-closure-public-v1":
        return str(manifest["support_token"])
    return digest(
        {
            "policies": manifest["policies"],
            "source_support": manifest["source_support"],
            "revocations": manifest.get("revocation_digest"),
        }
    )


def interpretation_lookup(manifest: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for item in manifest["source_support"]:
        key = str(item["snapshot_id"]), str(item["parser_version"])
        state = str(item["state"])
        previous = result.get(key)
        if previous is not None and previous["state"] == "revoked":
            continue
        if previous is not None and state == "accepted":
            continue
        result[key] = {
            "state": state,
            "usable": state == "accepted",
            "reason": "pinned_accepted_interpretation"
            if state == "accepted"
            else "baseline_owned_values_required",
            "snapshot_id": key[0],
            "parser_version": key[1],
            "selected_observation": state in {"accepted", "legacy_unassessed"},
        }
    return result


def selected_observations(
    conn: sqlite3.Connection, manifest: Mapping[str, Any]
) -> Iterator[dict[str, Any]]:
    """Exact accepted payloads for a cutoff-bound ReferenceReader overlay."""
    from .closure import hydrate

    manifest = hydrate(conn, manifest)
    selected = {
        item["observation_id"]: item
        for item in manifest["source_support"]
        if item["state"] == "accepted"
    }
    # Legacy evidence may be read only if its exact pinned interpretation still
    # exists. It grants neither admission nor a new default identity.
    for item in manifest["source_support"]:
        if item["state"] != "legacy_unassessed":
            continue
        row = conn.execute(
            "SELECT payload_json,scope_kind,scope_id,kind,seq,parser_version,extract_version,snapshot_id FROM observations WHERE observation_id=?",
            (item["observation_id"],),
        ).fetchone()
        if (
            row is not None
            and (row[1], row[2], row[3], row[4], str(row[5]), str(row[6]), row[7])
            == (
                *item["scope"],
                item["observation_kind"],
                item["seq"],
                str(item["parser_version"]),
                str(item["extract_version"]),
                item["snapshot_id"],
            )
            and hashlib.sha256(row[0].encode()).hexdigest() == item["payload_sha256"]
        ):
            yield {**item, "payload_json": row[0]}
    recipes: dict[str, list[dict[str, Any]]] = {}
    for item in selected.values():
        for receipt in item["source_generations"]:
            identifier = receipt["generation_id"]
            if identifier not in recipes:
                recipes.clear()  # Bound decoded payload retention; the caller owns yielded values.
                row = _accepted(conn, identifier)
                if row is None:
                    raise ClosureError("selected_source_support_changed")
                recipes[identifier] = list(_observations(row))
            for observation in recipes[identifier]:
                if (
                    observation["payload_sha256"] == item["payload_sha256"]
                    and observation["scope"] == item["scope"]
                    and observation["seq"] == item["seq"]
                ):
                    yield {**item, "payload_json": observation["payload_json"]}
                    break
            else:
                continue
            break


def observation_payload_loader(
    conn: sqlite3.Connection, manifest: Mapping[str, Any]
) -> Callable[[str], Iterable[str]]:
    """Resolve exact round payloads on demand, without retaining raw source bodies."""
    from .closure import hydrate

    pinned = hydrate(conn, manifest)
    by_snapshot: dict[str, list[Mapping[str, Any]]] = {}
    for item in pinned["source_support"]:
        if item["observation_kind"] == "round_sheet":
            by_snapshot.setdefault(str(item["snapshot_id"]), []).append(item)

    def payloads(snapshot_id: str) -> Iterator[str]:
        selected = by_snapshot.get(snapshot_id, ())
        if not selected:
            return
        # selected_observations does not revalidate the graph; its exact pinned
        # witnesses still govern every payload, including baseline legacy rows.
        for observation in selected_observations(conn, {"source_support": selected}):
            yield str(observation["payload_json"])

    return payloads
