"""Read-only identity checks over old_TABLE/new_TABLE release views and retained state."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

import duckdb

from swingset.link.decisions import DecisionResolver
from swingset.normalize.names import normalize_name, paired_names
from swingset.state.identity_references import ReferenceReader


def audit_identity(
    conn: duckdb.DuckDBPyConnection,
    *,
    release_policy: dict[str, Any],
    baseline_commit: str,
    state: sqlite3.Connection,
) -> dict[str, Any]:
    """Inspect candidate tables without calling the production build transform.

    The caller supplies verified old/new Parquet views and a consistent read-only
    state transaction. Counts are complete; failure examples are bounded to 20.
    """
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}

    def rows(sql: str, parameters: list[Any] | None = None) -> list[dict[str, Any]]:
        cursor = conn.execute(sql, parameters or [])
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]

    def sql_check(name: str, violations: str, parameters: list[Any] | None = None) -> None:
        result = conn.execute(f"SELECT count(*) FROM ({violations})", parameters or []).fetchone()
        assert result is not None
        count = result[0]
        checks[name] = count == 0
        details[name] = {
            "violations": count,
            "examples": rows(f"SELECT * FROM ({violations}) LIMIT 20", parameters),
        }

    checks["identity_baseline_commit_matches"] = (
        release_policy.get("baseline_commit") == baseline_commit
    )
    checks["identity_expansions_remain_withheld"] = (
        release_policy.get("accuracy_expansions") == "withheld_pending_H17"
    )
    for table, key in (("entries", "entry_id"), ("judges", "judge_id")):
        sql_check(
            f"{table}_no_new_or_replaced_default_ids",
            f"SELECT n.{key},o.wsdc_id AS old_id,n.wsdc_id AS new_id FROM new_{table} n "
            f"LEFT JOIN old_{table} o USING({key}) WHERE n.wsdc_id IS NOT NULL "
            "AND n.wsdc_id IS DISTINCT FROM o.wsdc_id",
        )
    subjects = (
        "SELECT 'entry' AS kind,entry_id AS id,wsdc_id,link_status FROM new_entries UNION ALL "
        "SELECT 'judge',judge_id,wsdc_id,NULL FROM new_judges"
    )
    sql_check(
        "unique_public_assertion_per_subject",
        "SELECT subject_kind,subject_id,count(*) AS count FROM new_identity_links "
        "GROUP BY subject_kind,subject_id HAVING count(*)>1",
    )
    sql_check(
        "default_ids_have_accepted_assertions",
        f"SELECT s.*,a.status,a.acceptance_state,a.wsdc_id AS asserted_id FROM ({subjects}) s "
        "LEFT JOIN new_identity_links a ON a.subject_kind=s.kind AND a.subject_id=s.id "
        "WHERE s.wsdc_id IS NOT NULL AND (a.acceptance_state IS DISTINCT FROM 'accepted' "
        "OR a.status IS DISTINCT FROM 'confirmed' OR a.wsdc_id IS DISTINCT FROM s.wsdc_id "
        "OR (s.kind='entry' AND s.link_status IS DISTINCT FROM 'confirmed'))",
    )
    sql_check(
        "accepted_assertions_have_default_ids",
        f"SELECT a.subject_kind,a.subject_id,a.wsdc_id FROM new_identity_links a LEFT JOIN ({subjects}) s "
        "ON a.subject_kind=s.kind AND a.subject_id=s.id WHERE a.acceptance_state='accepted' "
        "AND (s.id IS NULL OR s.wsdc_id IS NULL OR a.wsdc_id IS DISTINCT FROM s.wsdc_id)",
    )
    sql_check(
        "public_assertions_have_structural_subjects",
        f"SELECT a.subject_kind,a.subject_id FROM new_identity_links a LEFT JOIN ({subjects}) s "
        "ON a.subject_kind=s.kind AND a.subject_id=s.id WHERE s.id IS NULL",
    )
    token = release_policy.get("token", {})
    sql_check(
        "public_assertion_acceptance_fields_match_release",
        "SELECT subject_kind,subject_id,acceptance_state,acceptance_policy,journal_digest,journal_generation "
        "FROM new_identity_links WHERE acceptance_state IS NULL "
        "OR acceptance_state NOT IN ('accepted','revoked','unresolved') "
        "OR acceptance_policy IS DISTINCT FROM ? OR journal_digest IS DISTINCT FROM ? "
        "OR journal_generation IS DISTINCT FROM ?",
        [
            token.get("decision_policy"),
            token.get("journal_digest"),
            token.get("journal_generation"),
        ],
    )
    sql_check(
        "accepted_assertions_have_confirmation_method_and_references",
        "SELECT subject_kind,subject_id,method,status,source_ref_ids FROM new_identity_links "
        "WHERE acceptance_state='accepted' AND (status IS DISTINCT FROM 'confirmed' "
        "OR method IS NULL OR method NOT IN ('source_id','registry_placement','manual') "
        "OR source_ref_ids IS NULL OR len(source_ref_ids)=0)",
    )
    for role in ("leader", "follower"):
        sql_check(
            f"placement_{role}_identity_and_points_supported",
            f"SELECT p.placement_id,p.{role}_entry_id,p.{role}_wsdc_id,p.registry_points_{role} "
            f"FROM new_placements p LEFT JOIN new_entries e ON e.entry_id=p.{role}_entry_id "
            f"WHERE (p.{role}_wsdc_id IS NOT NULL AND (e.entry_id IS NULL "
            f"OR e.role IS DISTINCT FROM '{role}' OR e.wsdc_id IS DISTINCT FROM p.{role}_wsdc_id)) "
            f"OR (p.{role}_wsdc_id IS NULL AND p.registry_points_{role} IS NOT NULL)",
        )
    sql_check(
        "placement_identity_flags_supported",
        "SELECT placement_id,registry_confirmed,points_matches_expected FROM new_placements "
        "WHERE (registry_confirmed AND (registry_points_leader IS NULL OR registry_points_follower IS NULL)) "
        "OR (points_matches_expected IS NOT NULL AND registry_points_leader IS NULL "
        "AND registry_points_follower IS NULL)",
    )
    # Independently assemble support from the candidate's released registry facts.
    names = {
        row["wsdc_id"]: normalize_name(
            " ".join(str(row.get(k) or "") for k in ("first_name", "last_name"))
        ).value
        for row in rows("SELECT wsdc_id,first_name,last_name FROM new_dancers")
    }
    registry: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    for row in rows(
        "SELECT wsdc_id,event_id,role,division,dance_style,result FROM new_registry_placements WHERE event_id IS NOT NULL"
    ):
        registry[
            tuple(row[key] for key in ("event_id", "role", "division", "dance_style", "result"))
        ].add(row["wsdc_id"])
    contests = {
        row["contest_id"]: row
        for row in rows(
            "SELECT contest_id,name_raw,wsdc_points_eligible,division,dance_style FROM new_contests"
        )
    }
    entry_support: dict[str, set[int]] = defaultdict(set)
    for role in ("leader", "follower"):
        for row in rows(
            f"SELECT e.entry_id,e.name_raw,p.event_id,p.contest_id,p.place FROM new_placements p "
            f"JOIN new_entries e ON e.entry_id=p.{role}_entry_id WHERE e.role='{role}'"
        ):
            contest = contests.get(row["contest_id"], {})
            if not contest.get("wsdc_points_eligible"):
                continue
            scope = (row["event_id"], role, contest["division"], contest["dance_style"])
            for identifier in registry.get((*scope, str(row["place"])), set()) | registry.get(
                (*scope, "F"), set()
            ):
                if names.get(identifier) == normalize_name(str(row["name_raw"] or "")).value:
                    entry_support[row["entry_id"]].add(identifier)
    assertions = {
        (row["subject_kind"], row["subject_id"]): row
        for row in rows(
            "SELECT subject_kind,subject_id,wsdc_id,method,source_ref_ids,decision_ids,acceptance_state FROM new_identity_links"
        )
    }
    reader, resolver = ReferenceReader(state), DecisionResolver(state)
    checks["identity_state_journal_matches_release"] = resolver.token.digest == token.get(
        "journal_digest"
    ) and resolver.token.generation == token.get("journal_generation")
    failures = []
    assertion_failures = []
    checked = 0
    checked_assertions = 0
    for kind, table, key in (("entry", "entries", "entry_id"), ("judge", "judges", "judge_id")):
        fields = f"{key},event_id,name_raw,wsdc_id,source,snapshot_id"
        if kind == "entry":
            fields += ",contest_id,bib,role"
        for row in rows(
            f"SELECT {fields} FROM new_{table} WHERE wsdc_id IS NOT NULL OR {key} IN (SELECT subject_id FROM new_identity_links WHERE subject_kind='{kind}' AND wsdc_id IS NOT NULL)"
        ):
            identifier = row["wsdc_id"]
            assertion = assertions.get((kind, row[key]), {})
            bindings = reader.for_record(
                kind, row, contest_name=contests.get(row.get("contest_id"), {}).get("name_raw")
            )
            printed = {
                int(binding.locator["source_wsdc_id"])
                for binding in bindings
                if binding.locator.get("source_wsdc_id") is not None
            }
            supported = (
                frozenset(entry_support.get(row[key], ())) if kind == "entry" else frozenset()
            )
            problem = resolver.binding_problem(bindings, subject_kind=kind, subject_id=row[key])
            if len(printed) > 1:
                problem = "contradictory_printed_source_identities"
            resolution = resolver.resolve(
                tuple(binding.reference for binding in bindings),
                source_wsdc_id=next(iter(printed)) if len(printed) == 1 else None,
                registry_wsdc_ids=supported,
                legacy_subject_id=row[key],
                reference_problem=problem,
            )
            references_match = set(assertion.get("source_ref_ids") or []) == set(
                resolution.ref_ids
            ) and set(assertion.get("decision_ids") or []) == set(resolution.decision_ids)
            if assertion.get("wsdc_id") is not None:
                checked_assertions += 1
                if not references_match or not resolution.allows(assertion["wsdc_id"]):
                    assertion_failures.append(
                        {
                            "subject_kind": kind,
                            "subject_id": row[key],
                            "asserted_wsdc_id": assertion["wsdc_id"],
                            "acceptance_state": assertion.get("acceptance_state"),
                            "reference_problem": problem,
                        }
                    )
            if identifier is None:
                continue
            checked += 1
            valid = (
                bool(bindings)
                and references_match
                and resolution.allows(identifier)
                and row.get("role") != "couple"
                and not paired_names(str(row.get("name_raw") or ""))
                and (
                    (assertion.get("method") == "source_id" and printed == {identifier})
                    or (
                        assertion.get("method") == "registry_placement"
                        and supported == {identifier}
                    )
                    or (
                        assertion.get("method") == "manual"
                        and resolution.positive_wsdc_id == identifier
                    )
                )
            )
            if not valid:
                failures.append(
                    {
                        "subject_kind": kind,
                        "subject_id": row[key],
                        "wsdc_id": identifier,
                        "printed_ids": sorted(printed),
                        "registry_ids": sorted(supported),
                        "reference_problem": problem,
                        "method": assertion.get("method"),
                    }
                )
    checks["accepted_identities_match_retained_support"] = not failures
    details["accepted_identities_match_retained_support"] = {
        "checked": checked,
        "violations": len(failures),
        "examples": failures[:20],
    }
    checks["public_identity_assertions_respect_decisions"] = not assertion_failures
    details["public_identity_assertions_respect_decisions"] = {
        "checked": checked_assertions,
        "violations": len(assertion_failures),
        "examples": assertion_failures[:20],
    }
    # Name continuity is mandatory when the same retained source locator remains.
    judge_fields = "judge_id,event_id,name_raw,wsdc_id,source,snapshot_id"
    current_judges = {
        row["judge_id"]: row for row in rows(f"SELECT {judge_fields} FROM new_judges")
    }
    missing_judges = []
    retained_named = 0
    for row in rows(
        f"SELECT {judge_fields} FROM old_judges WHERE wsdc_id IS NULL AND name_raw IS NOT NULL AND name_raw<>''"
    ):
        current = current_judges.get(row["judge_id"])
        if current and current["name_raw"] == row["name_raw"] and current["wsdc_id"] is None:
            retained_named += 1
        elif reader.for_record("judge", row):
            missing_judges.append(
                {
                    "judge_id": row["judge_id"],
                    "old_name": row["name_raw"],
                    "new_name": current.get("name_raw") if current else None,
                    "snapshot_id": row.get("snapshot_id"),
                }
            )
    checks["named_null_id_judges_preserved_where_source_persists"] = not missing_judges
    details["named_null_id_judges_preserved_where_source_persists"] = {
        "retained": retained_named,
        "violations": len(missing_judges),
        "examples": missing_judges[:20],
        "interpretation": "Missing or changed names with retained source bindings require explicit review of remapping, source correction, or suppression; absence of a registry number alone never permits deletion.",
    }
    return {"checks": checks, "details": details}
