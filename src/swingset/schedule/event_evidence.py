"""Local evidence shared by source-event enumeration and stage inventory."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from swingset.fetch.archive import canonical, digest
from swingset.state import engine


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


def generation(conn: engine.Connection, identifier: str) -> dict[str, Any]:
    row = conn.execute(
        (
            "SELECT source_generations.generation_id, source_generations.unit_key, "
            "source_generations.page_kind, source_generations.contract_version, "
            "source_generations.input_fingerprint, source_generations.manifest_json, "
            "source_generations.recipe_json, source_generations.result_json, "
            "source_generations.report_json, source_generations.previous_generation_id, "
            "source_generations.work_token, source_generations.created_at, source_generations.run_id, "
            "source_generations.state, source_generations.removal_authority FROM source_generations "
            "WHERE generation_id = %s"
        ),
        (identifier,),
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
    "CASE WHEN pg_input_is_valid({0}.recipe_json,'json') THEN "
    "ops.compact_json(json_build_array("
    "json_extract_path({0}.recipe_json::json,'context','snapshot_id'),"
    "json_extract_path({0}.recipe_json::json,'parser_version'))) END"
)
REVOKED_EVIDENCE = (
    "WITH candidate_equivalences AS ("
    "SELECT g.generation_id,e.unit_key,e.input_fingerprint,e.recipe_json,"
    f"{_LABEL.format('e')} AS source_label,"
    "CASE WHEN pg_input_is_valid(e.recipe_json,'json') THEN NULL ELSE e.input_fingerprint END AS invalid_label "
    "FROM source_equivalences e JOIN source_generations g "
    "ON g.generation_id=e.generation_id AND g.unit_key=e.unit_key "
    f"WHERE g.state='revoked' AND {_LABEL.format('e')} IS DISTINCT FROM {_LABEL.format('g')}"
    "), distinct_equivalences AS ("
    "SELECT DISTINCT ON (generation_id,unit_key,source_label,invalid_label) "
    "generation_id,unit_key,input_fingerprint,recipe_json FROM candidate_equivalences "
    "ORDER BY generation_id,unit_key,source_label NULLS FIRST,invalid_label NULLS FIRST,input_fingerprint"
    ") SELECT unit_key,input_fingerprint,recipe_json FROM ("
    "SELECT generation_id,unit_key,input_fingerprint,recipe_json FROM source_generations WHERE state='revoked' "
    "UNION ALL SELECT generation_id,unit_key,input_fingerprint,recipe_json FROM distinct_equivalences"
    ") AS revoked ORDER BY generation_id,input_fingerprint"
)

#: The same before schema 33, which has no equivalences.
REVOKED_EVIDENCE_32 = (
    "SELECT unit_key, input_fingerprint, recipe_json FROM source_generations WHERE state = "
    "'revoked' ORDER BY generation_id NULLS FIRST, input_fingerprint NULLS FIRST"
)


def revoked_evidence(conn: engine.Connection) -> str:
    """The revoked-evidence query this file's schema supports.

    A read-only open skips migration, so a reader can meet schema 32 (D-0206).
    """
    has_equivalences = conn.execute(
        "SELECT 1 AS _value_0 FROM pg_catalog.pg_class WHERE (relkind = 'r' AND relname = 'source_equivalences') AND (PG_TABLE_IS_VISIBLE(oid) AND NOT relnamespace IN (CAST('pg_catalog' AS REGNAMESPACE), CAST('information_schema' AS REGNAMESPACE)))"
    ).fetchone()
    return REVOKED_EVIDENCE if has_equivalences else REVOKED_EVIDENCE_32


def admission_reason(
    conn: engine.Connection,
    value: Mapping[str, Any],
    *,
    revoked_rows: Iterable[engine.Row | tuple[Any, ...]] | None = None,
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
        "SELECT 1 AS _value_0 FROM admission_decisions WHERE generation_id = %s AND state = 'accepted' LIMIT 1",
        (value["generation_id"],),
    ).fetchone():
        return "source_generation_unadmitted"
    if policy_version is None:
        policy = conn.execute(
            "SELECT contract_version FROM admission_policies WHERE page_kind = %s",
            (value["page_kind"],),
        ).fetchone()
        contract = policy[0] if policy else None
    else:
        contract = policy_version(value["page_kind"])
    if contract is None or contract != value["contract_version"]:
        return "admission_policy_changed"
    return None


def parent_support(conn: engine.Connection, value: Mapping[str, Any]) -> dict[str, Any]:
    context = value["recipe"]["context"]
    snapshots = []
    for member in value["manifest"]:
        row = conn.execute(
            "SELECT watch_id, url, body_sha256, fetched_at FROM snapshots WHERE snapshot_id = %s",
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
        "SELECT source FROM watches WHERE watch_id = %s", (context["watch_id"],)
    ).fetchone()
    if watch is None or watch[0] != context["source"]:
        raise ValueError("parent_source_changed")
    return {
        "generation_id": value["generation_id"],
        "unit_key": value["unit_key"],
        "snapshots": snapshots,
    }
