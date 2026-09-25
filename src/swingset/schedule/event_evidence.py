"""Local evidence shared by source-event enumeration and stage inventory."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from swingset.fetch.archive import canonical, digest


def timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC).isoformat() if parsed.tzinfo else None


def request(source: str, method: str, url: str, form: Any = None) -> dict[str, Any]:
    """Match HTTPX URLs and the form dictionary actually passed by FetchClient."""
    parsed = httpx.URL(url)
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise ValueError("unsupported source request URL")
    parsed = parsed.copy_with(raw_path=parsed.raw_path, fragment=None)
    if isinstance(form, str):
        form = json.loads(form)
    values = dict(form) if form else {}
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()):
        raise ValueError("source forms require string keys and values")
    return {"source": source, "method": method.upper(), "url": str(parsed), "form": values}


def request_id(value: Mapping[str, Any]) -> str:
    return "request_" + digest(canonical(value))


def generation(conn: sqlite3.Connection, identifier: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM source_generations WHERE generation_id=?", (identifier,)
    ).fetchone()
    if row is None:
        raise ValueError("source_generation_missing")
    return decode_generation(dict(row), identifier)


def decode_generation(row: Mapping[str, Any], identifier: str) -> dict[str, Any]:
    """Validate an already loaded generation; callers may bound loading first."""
    result = dict(row)
    raw_result = result["result_json"]
    for name in ("manifest", "recipe", "report", "result"):
        result[name] = json.loads(result.pop(name + "_json"))
    if (
        digest(canonical({"manifest": result["manifest"], "recipe": result["recipe"]}))
        != result["input_fingerprint"]
    ):
        raise ValueError("source_generation_fingerprint_changed")
    evidence = {
        "unit": result["unit_key"],
        "fingerprint": result["input_fingerprint"],
        "report": result["report"],
        "result": raw_result,
        "previous": result["previous_generation_id"],
        "work_token": result["work_token"],
    }
    if "gen_" + digest(canonical(evidence)) != identifier:
        raise ValueError("source_generation_content_changed")
    if result["removal_authority"] != result["report"]["proposed_removal"]:
        raise ValueError("source_generation_removal_authority_changed")
    return result


# Revoked evidence: each revoked generation's own fingerprint and recipe, plus
# the fingerprints a parse cutoff recorded as equivalent to it (D-0205). A
# later parse with any of those inputs repeats the revoked evidence. Readers
# refuse a candidate whose fingerprint, or whose recipe's snapshot and parser
# label, matches a row. The same fingerprint means the same recipe, so an
# equivalence whose label equals its generation's adds nothing and is left
# out; the rest are kept one per label. A cutoff keeps labels byte-equal, so
# today the rows are one per revoked generation however many deploys re-parsed
# it, and the readers' row budgets count revoked generations. An equivalence
# whose recipe is not valid JSON is kept, so readers name it invalid.
_LABEL = (
    "CASE WHEN json_valid({0}.recipe_json) THEN "
    "json_array(json_extract({0}.recipe_json,'$.context.snapshot_id'),"
    "json_extract({0}.recipe_json,'$.parser_version')) END"
)
REVOKED_EVIDENCE = (
    "SELECT unit_key,input_fingerprint,recipe_json FROM ("
    "SELECT generation_id,unit_key,input_fingerprint,recipe_json FROM source_generations "
    "WHERE state='revoked' UNION ALL "
    "SELECT g.generation_id,e.unit_key,min(e.input_fingerprint),e.recipe_json "
    "FROM source_equivalences e JOIN source_generations g "
    "ON g.generation_id=e.generation_id AND g.unit_key=e.unit_key WHERE g.state='revoked' "
    f"AND {_LABEL.format('e')} IS NOT {_LABEL.format('g')} "
    f"GROUP BY g.generation_id,e.unit_key,{_LABEL.format('e')},"
    "CASE WHEN json_valid(e.recipe_json) THEN NULL ELSE e.input_fingerprint END) "
    "ORDER BY generation_id,input_fingerprint"
)
#: The same before schema 33, which has no equivalences.
REVOKED_EVIDENCE_32 = (
    "SELECT unit_key,input_fingerprint,recipe_json FROM source_generations "
    "WHERE state='revoked' ORDER BY generation_id,input_fingerprint"
)


def revoked_evidence(conn: sqlite3.Connection) -> str:
    """The revoked-evidence query this file's schema supports.

    A read-only open skips migration, so a reader can meet schema 32 (D-0206).
    """
    has_equivalences = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_equivalences'"
    ).fetchone()
    return REVOKED_EVIDENCE if has_equivalences else REVOKED_EVIDENCE_32


def admission_reason(
    conn: sqlite3.Connection,
    value: Mapping[str, Any],
    *,
    revoked_rows: Iterable[sqlite3.Row | tuple[Any, ...]] | None = None,
    policy_version: Callable[[str], str | None] | None = None,
) -> str | None:
    if value["state"] == "revoked":
        return "source_generation_revoked"
    context = value["recipe"]["context"]
    if revoked_rows is None:
        revoked_rows = conn.execute(revoked_evidence(conn))
    for revoked in revoked_rows:
        if (revoked[0], revoked[1]) == (value["unit_key"], value["input_fingerprint"]):
            return "source_evidence_revoked"
        try:
            recipe = json.loads(revoked[2])
            if (recipe["context"]["snapshot_id"], str(recipe["parser_version"])) == (
                context["snapshot_id"],
                str(value["recipe"]["parser_version"]),
            ):
                return "source_evidence_revoked"
        except (ValueError, KeyError, TypeError):
            return "revocation_evidence_invalid"
    if value["report"].get("failures"):
        return "source_interpretation_unsupported"
    if not conn.execute(
        "SELECT 1 FROM admission_decisions WHERE generation_id=? AND state='accepted' LIMIT 1",
        (value["generation_id"],),
    ).fetchone():
        return "source_generation_unadmitted"
    if policy_version is None:
        policy = conn.execute(
            "SELECT contract_version FROM admission_policies WHERE page_kind=?",
            (value["page_kind"],),
        ).fetchone()
        contract = policy[0] if policy else None
    else:
        contract = policy_version(value["page_kind"])
    if contract is None or contract != value["contract_version"]:
        return "admission_policy_changed"
    return None


def parent_support(conn: sqlite3.Connection, value: Mapping[str, Any]) -> dict[str, Any]:
    context = value["recipe"]["context"]
    snapshots = []
    for member in value["manifest"]:
        row = conn.execute(
            "SELECT watch_id,url,body_sha256,fetched_at FROM snapshots WHERE snapshot_id=?",
            (member["snapshot_id"],),
        ).fetchone()
        if row is None or tuple(row[:3]) != (
            member["watch_id"],
            member["url"],
            member["body_sha256"],
        ):
            raise ValueError("parent_snapshot_changed")
        snapshots.append({**member, "discovered_at": timestamp(row[3])})
    if not snapshots or context["snapshot_id"] not in {row["snapshot_id"] for row in snapshots}:
        raise ValueError("parent_snapshot_not_in_manifest")
    watch = conn.execute(
        "SELECT source FROM watches WHERE watch_id=?", (context["watch_id"],)
    ).fetchone()
    if watch is None or watch[0] != context["source"]:
        raise ValueError("parent_source_changed")
    return {
        "generation_id": value["generation_id"],
        "unit_key": value["unit_key"],
        "snapshots": snapshots,
    }
