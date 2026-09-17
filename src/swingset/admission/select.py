"""Atomic generation selection, guarded against stale input and work tokens."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from swingset.fetch.archive import Archive, canonical, digest
from swingset.sources import get_page_kind
from swingset.sources.base import ParseContext
from swingset.state.db import Database
from swingset.state.observations import store_parse_result

from .generations import (
    AttemptInputs,
    accepted_inputs,
    current_snapshot,
    deserialize_result,
    selected_snapshot,
)
from .removal import wdr_preserves_rows


def complete_token(conn: sqlite3.Connection, snapshot_id: str, token: str | None) -> None:
    if token is not None:
        conn.execute(
            "DELETE FROM pending_work WHERE stage='parse' AND unit_kind='snapshot' AND unit_id=? AND enqueued_at=?",
            (snapshot_id, token),
        )


def admit_generation(
    database: Database,
    archive: Archive,
    generation_id: str,
    *,
    now: str,
    run_id: str,
    after_write: Callable[[sqlite3.Connection], None] | None = None,
) -> str:
    """Return accepted, shadow, paused, superseded, or the retained guard state.

    All checks, observation replacement, old/new scope invalidation, pointer
    movement, and token-specific completion share one SQLite transaction.
    The optional callback belongs to the same transaction (for cache/verification
    updates); exceptions roll back selection without losing the staged evidence.
    """
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM source_generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Missing staged generation")
        policy = conn.execute(
            "SELECT * FROM admission_policies WHERE page_kind=?", (row["page_kind"],)
        ).fetchone()
        mode = "shadow" if policy is None else str(policy["mode"])
        if mode != "enforce":
            return mode
        revision = str(policy["policy_revision"])
        recipe, manifest, report = (
            json.loads(row[name]) for name in ("recipe_json", "manifest_json", "report_json")
        )
        ctx = ParseContext(**recipe["context"])
        attempt = AttemptInputs(
            str(row["unit_key"]),
            ctx,
            tuple(tuple(item) for item in recipe["accepted_inputs"]),
            row["previous_generation_id"],
            row["work_token"],
            recipe["extract_version"],
            recipe["parser_version"],
            recipe.get("archive_url"),
        )
        unit = conn.execute(
            "SELECT * FROM source_units WHERE unit_key=?", (row["unit_key"],)
        ).fetchone()
        revoked = conn.execute(
            "SELECT 1 FROM source_generations WHERE unit_key=? AND input_fingerprint=? AND state='revoked' LIMIT 1",
            (row["unit_key"], row["input_fingerprint"]),
        ).fetchone()
        if revoked is not None:
            complete_token(conn, ctx.snapshot_id, attempt.work_token)
            return _decide(
                conn, generation_id, "revoked", "input_generation_revoked", now, revision
            )
        if row["state"] == "accepted" and unit["accepted_generation_id"] == generation_id:
            return "accepted"
        work = conn.execute(
            "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_kind='snapshot' AND unit_id=?",
            (ctx.snapshot_id,),
        ).fetchone()
        current_token = None if work is None else work[0]
        page = get_page_kind(ctx.kind)
        current = (
            unit["desired_fingerprint"] == row["input_fingerprint"]
            and unit["accepted_generation_id"] == attempt.previous_generation_id
            and current_snapshot(conn, attempt)
            and accepted_inputs(conn) == attempt.accepted_inputs
            and current_token == attempt.work_token
            and str(page.EXTRACT_VERSION) == attempt.extract_version
            and str(page.PARSER_VERSION) == attempt.parser_version
        )
        if not current:
            if not current_snapshot(conn, attempt):
                # This snapshot's own old work is obsolete. The newer snapshot
                # has a distinct work key and remains pending.
                complete_token(conn, ctx.snapshot_id, attempt.work_token)
            return _decide(
                conn, generation_id, "superseded", "desired_inputs_changed", now, revision
            )
        if (
            policy["contract_version"] != row["contract_version"]
            or not policy["reviewed_report_digest"]
        ):
            return _decide(
                conn, generation_id, "needs_review", "contract_review_missing", now, revision
            )
        if report["failures"]:
            complete_token(conn, ctx.snapshot_id, attempt.work_token)
            return _decide(
                conn, generation_id, report["state"], ",".join(report["failures"]), now, revision
            )
        slots = [item["slot"] for item in manifest]
        if slots != report["coverage"]["observed_pages"] or len(set(slots)) != len(slots):
            return _decide(
                conn, generation_id, "needs_review", "manifest_coverage_mismatch", now, revision
            )
        if digest(canonical({"manifest": manifest, "recipe": recipe})) != row["input_fingerprint"]:
            return _decide(
                conn, generation_id, "needs_review", "manifest_fingerprint_mismatch", now, revision
            )
        try:
            bodies: set[str] = set()
            for item in manifest:
                snapshot = conn.execute(
                    "SELECT s.body_sha256,s.watch_id,s.url,s.via,w.kind,w.parser,COALESCE(s.observed_at,s.fetched_at) FROM snapshots s JOIN watches w USING(watch_id) WHERE snapshot_id=?",
                    (item["snapshot_id"],),
                ).fetchone()
                if snapshot is None or tuple(snapshot)[:3] != (
                    item["body_sha256"],
                    item["watch_id"],
                    item["url"],
                ):
                    raise ValueError("Manifest snapshot no longer matches")
                child_page = get_page_kind(str(snapshot[5]))
                if str(item.get("extract_version")) != str(child_page.EXTRACT_VERSION) or str(
                    item.get("parser_version")
                ) != str(child_page.PARSER_VERSION):
                    raise ValueError("Manifest input recipe no longer matches")
                if str(item.get("observed_at")) != str(snapshot[6]):
                    raise ValueError("Manifest observation time no longer matches")
                if not selected_snapshot(conn, item["watch_id"], item["snapshot_id"]):
                    return _decide(
                        conn,
                        generation_id,
                        "superseded",
                        "manifest_input_changed",
                        now,
                        revision,
                    )
                if item["body_sha256"] in bodies:
                    raise ValueError("Repeated page body in source unit")
                bodies.add(item["body_sha256"])
                archive.read_body(item["body_sha256"])
                archive.read_extract(item["extract_sha256"])
        except (OSError, ValueError) as exc:
            return _decide(
                conn,
                generation_id,
                "needs_review",
                "manifest_artifact_invalid:" + str(exc),
                now,
                revision,
            )
        result = deserialize_result(row["result_json"])
        if ctx.kind == "wdr.rounds":
            retained = wdr_preserves_rows(conn, ctx.watch_id, result)
            if not retained.passed:
                complete_token(conn, ctx.snapshot_id, attempt.work_token)
                return _decide(conn, generation_id, "needs_review", retained.code, now, revision)
        store_parse_result(
            conn,
            ctx,
            result,
            extract_version=attempt.extract_version,
            parser_version=attempt.parser_version,
            parsed_at=now,
            run_id=run_id,
            legitimate_empty=not report["failures"],
            preserve_history=row["removal_authority"] == "none",
        )
        conn.execute(
            "UPDATE source_units SET accepted_generation_id=?,legacy_state=CASE WHEN legacy_state='legacy_unassessed' AND legacy_snapshot_id=? THEN 'assessed' ELSE legacy_state END WHERE unit_key=?",
            (generation_id, ctx.snapshot_id, row["unit_key"]),
        )
        if after_write is not None:
            after_write(conn)
        complete_token(conn, ctx.snapshot_id, attempt.work_token)
        state = _decide(conn, generation_id, "accepted", "contract_passed", now, revision)
        from swingset.state.event_progress import interpreted

        decision = conn.execute(
            "SELECT decision_id FROM admission_decisions WHERE generation_id=? ORDER BY decision_id DESC LIMIT 1",
            (generation_id,),
        ).fetchone()
        interpreted(
            conn,
            source=ctx.source,
            parser=ctx.kind,
            watch_id=ctx.watch_id,
            generation_id=generation_id,
            decision_id=decision[0],
            occurred_at=now,
            run_id=run_id,
        )
        return state


def _decide(
    conn: sqlite3.Connection, generation: str, state: str, reason: str, now: str, revision: str
) -> str:
    conn.execute("UPDATE source_generations SET state=? WHERE generation_id=?", (state, generation))
    conn.execute(
        "INSERT INTO admission_decisions(generation_id,state,reason,decided_at,policy_revision) VALUES (?,?,?,?,?)",
        (generation, state, reason, now, revision),
    )
    return state


def revoke_generation(
    database: Database, generation_id: str, *, reason: str, now: str, run_id: str
) -> None:
    """Withdraw proven inadmissible claims and restore an assessed predecessor.

    This is an explicit correction operation; a failed fetch or missing lookup
    never calls it. Immutable source evidence and every decision remain stored.
    """
    from swingset.state.work import WorkUnit, bump_revision, enqueue

    if not reason.strip():
        raise ValueError("Revocation requires explicit supporting evidence")
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM source_generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
        if row is None or row["state"] not in {"accepted", "revoked"}:
            raise ValueError("Only an accepted generation can be revoked")
        if row["state"] == "revoked":
            return
        recipe = json.loads(row["recipe_json"])
        ctx = ParseContext(**recipe["context"])
        unit = conn.execute(
            "SELECT * FROM source_units WHERE unit_key=?", (row["unit_key"],)
        ).fetchone()
        scopes = tuple(
            WorkUnit("project", str(scope[0]), str(scope[1]))
            for scope in conn.execute(
                "SELECT DISTINCT scope_kind,scope_id FROM observations WHERE watch_id=? AND snapshot_id=?",
                (ctx.watch_id, ctx.snapshot_id),
            )
        )
        deleted = conn.execute(
            "DELETE FROM observations WHERE watch_id=? AND snapshot_id=?",
            (ctx.watch_id, ctx.snapshot_id),
        ).rowcount
        _decide(conn, generation_id, "revoked", reason, now, "explicit_revocation")
        if unit["accepted_generation_id"] == generation_id:
            predecessor = row["previous_generation_id"]
            prior = None
            while predecessor is not None:
                candidate = conn.execute(
                    "SELECT * FROM source_generations WHERE generation_id=?", (predecessor,)
                ).fetchone()
                if candidate is None:
                    break
                if candidate["state"] == "accepted":
                    prior = candidate
                    break
                predecessor = candidate["previous_generation_id"]
            conn.execute(
                "UPDATE source_units SET accepted_generation_id=? WHERE unit_key=?",
                (None if prior is None else prior["generation_id"], row["unit_key"]),
            )
            conn.execute(
                "UPDATE watches SET current_observation_snapshot_id=NULL WHERE watch_id=? AND current_observation_snapshot_id=?",
                (ctx.watch_id, ctx.snapshot_id),
            )
            if prior is not None:
                prior_recipe = json.loads(prior["recipe_json"])
                store_parse_result(
                    conn,
                    ParseContext(**prior_recipe["context"]),
                    deserialize_result(prior["result_json"]),
                    extract_version=prior_recipe["extract_version"],
                    parser_version=prior_recipe["parser_version"],
                    parsed_at=now,
                    run_id=run_id,
                    legitimate_empty=True,
                    preserve_history=prior["removal_authority"] == "none",
                )
        enqueue(conn, scopes, enqueued_at=now)
        if any(unit.unit_kind in {"calendar", "source_index"} for unit in scopes):
            enqueue(conn, (WorkUnit("project", "map", "all"),), enqueued_at=now)
        if deleted:
            bump_revision(conn, "observations")
