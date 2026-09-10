"""One archived snapshot attempt, with handled failures and durable completion."""

from dataclasses import dataclass
from datetime import timedelta

from swingset.clock import Clock
from swingset.fetch.archive import Archive
from swingset.sources import get_page_kind
from swingset.sources.base import ExtractError, ParseContext, ParseError
from swingset.state.db import Database
from swingset.state.findings import Finding, replace_findings
from swingset.state.observations import store_parse_result
from swingset.state.work import WorkUnit, bump_revision, complete


@dataclass(frozen=True)
class ParseAttempt:
    source: str
    failed: bool


def parse_snapshot(
    database: Database, archive: Archive, unit: WorkUnit, clock: Clock, run_id: str
) -> ParseAttempt:
    conn = database.connection
    row = conn.execute(
        """SELECT s.*,w.source,w.parser,w.source_ref,w.current_observation_snapshot_id
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
        str(row["fetched_at"]),
    )
    now = clock.now().isoformat()
    sha = row["extract_sha256"]
    try:
        if not sha or str(row["extract_version"]) != str(page.EXTRACT_VERSION):
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
        with database.transaction() as tx:
            before = tx.execute(
                "SELECT parse_status,parser_version FROM snapshots WHERE snapshot_id=?",
                (unit.unit_id,),
            ).fetchone()
            try:
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
                    owner_id=ctx.watch_id,
                    findings=(),
                    opened_at=now,
                    run_id=run_id,
                )
            # Parent content changes refresh child round pages as well as slow clocks.
            if row["parser"] in ("scoringdance.event", "eepro.autoindex"):
                tx.execute(
                    "UPDATE watches SET next_check_at=? WHERE parent_watch_id=? AND state!='gone'",
                    (now, ctx.watch_id),
                )
            complete(database, unit, lambda _conn: None)
        return ParseAttempt(ctx.source, False)
    except (ExtractError, ParseError) as exc:
        with database.transaction() as tx:
            before = tx.execute(
                "SELECT parse_status,parser_version FROM snapshots WHERE snapshot_id=?",
                (unit.unit_id,),
            ).fetchone()
            tx.execute(
                "UPDATE snapshots SET parse_status='failed',parsed_at=?,"
                "extract_status=CASE WHEN ? THEN 'failed' ELSE extract_status END,"
                "extract_version=?,parser_version=? WHERE snapshot_id=?",
                (
                    now,
                    isinstance(exc, ExtractError),
                    str(page.EXTRACT_VERSION),
                    str(page.PARSER_VERSION),
                    unit.unit_id,
                ),
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
                owner_id=ctx.watch_id,
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
            complete(database, unit, lambda _conn: None)
        return ParseAttempt(ctx.source, True)
