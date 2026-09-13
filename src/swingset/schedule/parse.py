"""One archived snapshot attempt, with handled failures and durable completion."""

from dataclasses import dataclass
from datetime import timedelta

from swingset.admission.contracts import contract_version, inspect
from swingset.admission.generations import begin_attempt, stage_generation
from swingset.admission.report import Coverage, Field, Guard, evaluate
from swingset.admission.select import admit_generation, complete_token
from swingset.admission.support import generation_failure_codes
from swingset.clock import Clock
from swingset.fetch.archive import Archive
from swingset.history.acquisition import is_phase_one_index
from swingset.sources import get_page_kind
from swingset.sources.base import ExtractError, ParseContext, ParseError, ParseResult
from swingset.state.db import Database
from swingset.state.findings import Finding, replace_findings
from swingset.state.observations import store_parse_result
from swingset.state.verification import finish_interpretation
from swingset.state.work import WorkUnit, bump_revision


@dataclass(frozen=True)
class ParseAttempt:
    source: str
    failed: bool
    selection: str | None = None


def parse_snapshot(
    database: Database, archive: Archive, unit: WorkUnit, clock: Clock, run_id: str
) -> ParseAttempt:
    conn = database.connection
    row = conn.execute(
        """SELECT s.*,w.source,w.parser,w.source_ref,w.current_observation_snapshot_id,w.kind AS watch_kind
        FROM snapshots s JOIN watches w USING(watch_id) WHERE snapshot_id=?""",
        (unit.unit_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"pending snapshot is missing: {unit.unit_id}")
    page = get_page_kind(str(row["parser"]))
    ctx = ParseContext(
        str(row["snapshot_id"]),
        str(row["watch_id"]),
        str(row["url"]),
        str(row["source"]),
        str(row["parser"]),
        row["source_ref"],
        str(row["observed_at"] or row["fetched_at"]),
    )
    archive_index = row["via"] == "wayback" and is_phase_one_index(
        ctx.source, ctx.kind, str(row["watch_kind"])
    )
    finding_owner = ctx.snapshot_id if archive_index else ctx.watch_id
    now = clock.now().isoformat()
    inputs = begin_attempt(conn, ctx, page.EXTRACT_VERSION, page.PARSER_VERSION)
    runtime_recipe = next(
        (
            value
            for consumer, name, value in inputs.accepted_inputs
            if consumer == "pipeline" and name == "recipe/runtime"
        ),
        None,
    )
    has_recipe_cache = "extract_recipe_sha256" in row.keys()
    recipe_changed = runtime_recipe is not None and (
        not has_recipe_cache or row["extract_recipe_sha256"] != runtime_recipe
    )
    sha = row["extract_sha256"]
    result = ParseResult()
    try:
        if not sha or str(row["extract_version"]) != str(page.EXTRACT_VERSION) or recipe_changed:
            extract = page.extract(archive.read_body(str(row["body_sha256"])))
            sha = archive.store_extract(extract)
        else:
            extract = archive.read_extract(str(sha))
        result = page.parse(extract, ctx)
        invalid = any(
            getattr(observation.payload, "outcome", "") == "invalid"
            for observation in result.observations
        )
        if invalid:
            raise ParseError(
                "; ".join(w.message for w in result.warnings) or "invalid registry response"
            )
        report = inspect(ctx, archive.read_body(str(row["body_sha256"])), extract, result)
        # Commit evidence first: a failed or interrupted admission must leave its
        # complete proposed action available without exposing half its output.
        with database.transaction() as tx:
            generation = stage_generation(
                tx, archive, inputs, str(sha), result, report, now=now, run_id=run_id
            )
        with database.transaction() as tx:
            before = tx.execute(
                "SELECT parse_status,parser_version FROM snapshots WHERE snapshot_id=?",
                (unit.unit_id,),
            ).fetchone()
            try:
                selection = admit_generation(database, archive, generation, now=now, run_id=run_id)
                if selection not in {"shadow", "accepted"}:
                    if selection != "superseded":
                        complete_token(tx, ctx.snapshot_id, inputs.work_token)
                    failures = generation_failure_codes(tx, generation)
                    replace_findings(
                        tx,
                        owner_kind="admission",
                        owner_id=inputs.unit_key,
                        findings=(
                            Finding(
                                "admission_blocked",
                                "watch",
                                ctx.watch_id,
                                "warning",
                                "Source generation retained without promotion: "
                                + selection
                                + (" (" + ", ".join(failures) + ")" if failures else ""),
                                {"generation_id": generation, "failures": failures},
                                watch_id=ctx.watch_id,
                                snapshot_id=ctx.snapshot_id,
                            ),
                        ),
                        opened_at=now,
                        run_id=run_id,
                    )
                    return ParseAttempt(ctx.source, False, selection)
                if selection == "shadow":
                    store_parse_result(
                        tx,
                        ctx,
                        result,
                        extract_version=page.EXTRACT_VERSION,
                        parser_version=page.PARSER_VERSION,
                        parsed_at=now,
                        run_id=run_id,
                        legitimate_empty=any(
                            getattr(o.payload, "outcome", "") == "not_found"
                            for o in result.observations
                        )
                        or result.legitimate_empty,
                    )
            except ValueError as exc:
                raise ParseError(str(exc)) from exc
            tx.execute(
                "UPDATE snapshots SET extract_status='ok',extract_sha256=? WHERE snapshot_id=?",
                (sha, unit.unit_id),
            )
            if has_recipe_cache:
                tx.execute(
                    "UPDATE snapshots SET extract_recipe_sha256=? WHERE snapshot_id=?",
                    (runtime_recipe, unit.unit_id),
                )
            after = tx.execute(
                "SELECT parse_status,parser_version FROM snapshots WHERE snapshot_id=?",
                (unit.unit_id,),
            ).fetchone()
            if tuple(before) != tuple(after):
                bump_revision(tx, "snapshots")
            # Only the newest successful snapshot may own the watch's cache.
            latest = tx.execute(
                "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?",
                (ctx.watch_id,),
            ).fetchone()[0]
            if latest == ctx.snapshot_id:
                tx.execute(
                    "UPDATE watches SET fingerprint=?,extract_version=? WHERE watch_id=?",
                    (sha, str(page.EXTRACT_VERSION), ctx.watch_id),
                )
                replace_findings(
                    tx,
                    owner_kind="parse_failure",
                    owner_id=finding_owner,
                    findings=(),
                    opened_at=now,
                    run_id=run_id,
                )
            if archive_index:
                replace_findings(
                    tx,
                    owner_kind="parse_failure",
                    owner_id=finding_owner,
                    findings=(),
                    opened_at=now,
                    run_id=run_id,
                )
            if row["via"] == "wayback":
                replace_findings(
                    tx,
                    owner_kind="archive_gap",
                    owner_id=ctx.watch_id,
                    findings=(),
                    opened_at=now,
                    run_id=run_id,
                )
                tx.execute(
                    "UPDATE watches SET state='sealed',next_check_at=NULL WHERE watch_id=?",
                    (ctx.watch_id,),
                )
            # Parent content changes refresh child round pages as well as slow clocks.
            from swingset.history.origin_dispatch import managed

            if (
                row["via"] != "wayback"
                and not managed(tx, ctx.watch_id)
                and row["parser"]
                in (
                    "scoringdance.event",
                    "eepro.autoindex",
                )
            ):
                tx.execute(
                    "UPDATE watches SET next_check_at=? WHERE parent_watch_id=? AND state!='gone'",
                    (now, ctx.watch_id),
                )
            if ctx.source == "wsdc_registry":
                finish_interpretation(tx, archive, ctx.snapshot_id)
            complete_token(tx, ctx.snapshot_id, inputs.work_token)
        return ParseAttempt(ctx.source, False)
    except (ExtractError, ParseError) as exc:
        failed_report = evaluate(
            ctx.kind,
            contract_version(ctx.kind),
            (Field("$", "unknown", str(exc)),),
            Coverage((ctx.snapshot_id,), (ctx.snapshot_id,), None, None, len(result.observations)),
            guards=(
                Guard(
                    "extract_failed" if isinstance(exc, ExtractError) else "parse_failed",
                    False,
                    str(exc),
                ),
            ),
        )
        with database.transaction() as tx:
            stage_generation(
                tx,
                archive,
                inputs,
                None if isinstance(exc, ExtractError) else sha,
                result,
                failed_report,
                now=now,
                run_id=run_id,
            )
        with database.transaction() as tx:
            before = tx.execute(
                "SELECT parse_status,parser_version FROM snapshots WHERE snapshot_id=?",
                (unit.unit_id,),
            ).fetchone()
            tx.execute(
                "UPDATE snapshots SET parse_status='failed',parsed_at=?,"
                "extract_status=CASE WHEN ? THEN 'failed' ELSE extract_status END,"
                "extract_sha256=CASE WHEN ? THEN NULL ELSE ? END,"
                "extract_version=?,parser_version=? WHERE snapshot_id=?",
                (
                    now,
                    isinstance(exc, ExtractError),
                    isinstance(exc, ExtractError),
                    sha,
                    str(page.EXTRACT_VERSION),
                    str(page.PARSER_VERSION),
                    unit.unit_id,
                ),
            )
            if has_recipe_cache:
                tx.execute(
                    "UPDATE snapshots SET extract_recipe_sha256=? WHERE snapshot_id=?",
                    (None if isinstance(exc, ExtractError) else runtime_recipe, unit.unit_id),
                )
            after = tx.execute(
                "SELECT parse_status,parser_version FROM snapshots WHERE snapshot_id=?",
                (unit.unit_id,),
            ).fetchone()
            if tuple(before) != tuple(after):
                bump_revision(tx, "snapshots")
            replace_findings(
                tx,
                owner_kind="parse_failure",
                owner_id=finding_owner,
                findings=(
                    Finding(
                        "invalid_response" if ctx.source == "wsdc_registry" else "parse_failure",
                        "watch",
                        ctx.watch_id,
                        "error",
                        str(exc),
                        {"snapshot_id": ctx.snapshot_id},
                        watch_id=ctx.watch_id,
                        snapshot_id=ctx.snapshot_id,
                    ),
                ),
                opened_at=now,
                run_id=run_id,
            )
            if row["via"] == "wayback" and not archive_index:
                from swingset.fetch.wayback import retry_capture

                if not retry_capture(tx, ctx.watch_id, now=clock.now()):
                    tx.execute(
                        "UPDATE watches SET next_check_at=NULL WHERE watch_id=?", (ctx.watch_id,)
                    )
                    replace_findings(
                        tx,
                        owner_kind="archive_gap",
                        owner_id=ctx.watch_id,
                        findings=(
                            Finding(
                                "archive_gap",
                                "source_event",
                                str(row["source_ref"] or ctx.watch_id),
                                "warning",
                                "No further eligible archive capture after bounded parse attempts",
                                {"snapshot_id": ctx.snapshot_id},
                                watch_id=ctx.watch_id,
                                snapshot_id=ctx.snapshot_id,
                            ),
                        ),
                        opened_at=now,
                        run_id=run_id,
                    )
            if ctx.source == "wsdc_registry":
                failures = tx.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE watch_id=? AND parse_status='failed'",
                    (ctx.watch_id,),
                ).fetchone()[0]
                retry_at = clock.now() + timedelta(
                    seconds=min(86400, 900 * 2 ** min(int(failures) - 1, 7))
                )
                tx.execute(
                    "UPDATE watches SET body_sha256=NULL,fingerprint=NULL,etag=NULL,last_modified=NULL,"
                    "next_check_at=? WHERE watch_id=?",
                    (retry_at.isoformat(), ctx.watch_id),
                )
            if ctx.source == "wsdc_registry":
                finish_interpretation(tx, archive, ctx.snapshot_id)
            complete_token(tx, ctx.snapshot_id, inputs.work_token)
        return ParseAttempt(ctx.source, True)
