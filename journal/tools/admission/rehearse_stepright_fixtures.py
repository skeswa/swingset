"""Rehearse reviewed Step Right contracts on a new disposable schema-29 state.

Preparation stages the five retained fixture bodies in shadow mode and writes an
unapproved review request. Execution requires a separately supplied review file
bound to that request before it activates or admits anything. Neither phase
imports a network client or opens a production state directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from swingset.admission.contracts import inspect
from swingset.admission.generations import begin_attempt, stage_generation
from swingset.admission.policy import activate_contract, corpus_digest, record_review
from swingset.admission.select import admit_generation
from swingset.clock import FakeClock
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.model.ids import watch_id
from swingset.sources import get_page_kind
from swingset.sources.base import ParseContext
from swingset.state.db import SCHEMA_VERSION, open_database
from swingset.state.work import WorkUnit, enqueue

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests/fixtures/sources/steprightsolutions"
PROVENANCE = "real-provenance-20260917.json"
KINDS = (
    "steprightsolutions.index",
    "steprightsolutions.event",
    "steprightsolutions.round",
)
FILES = (
    "real-srs-index-20260917.html",
    "real-srs-event-20260917.html",
    "real-srs-event2015-20260918.html",
    "real-srs-round507-20260917.html",
    "real-srs-round508-20260917.html",
)
REQUEST_SCHEMA = "stepright-offline-review-request-v1"
REVIEW_SCHEMA = "stepright-offline-external-review-v1"
SCOPE = "offline_disposable_rehearsal_only"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    durable_write(path, canonical(value) + b"\n")


def _past_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("preparation time must be timezone-aware UTC")
    normalized = value.astimezone(UTC)
    if normalized > datetime.now(UTC):
        raise ValueError("preparation time must not be in the future")
    return normalized


def _kind(entry: dict[str, Any]) -> str:
    name = str(entry["file"])
    if "index" in name:
        return KINDS[0]
    if "round" in name:
        return KINDS[2]
    return KINDS[1]


def _source_ref(entry: dict[str, Any]) -> str | None:
    if _kind(entry) == KINDS[0]:
        return None
    supplied = entry.get("source_ref")
    if supplied:
        return str(supplied)
    path = str(entry["original_url"]).rstrip("/").split("/events/", 1)[-1]
    event = path.split("/round/", 1)[0]
    return "steprightsolutions:" + event


def _load_fixtures(directory: Path) -> tuple[list[dict[str, Any]], str]:
    provenance_path = directory / PROVENANCE
    raw = provenance_path.read_bytes()
    records = json.loads(raw)
    selected = {str(record["file"]): record for record in records}
    if set(FILES) - selected.keys():
        raise ValueError("Step Right provenance is missing a required fixture")
    fixtures = []
    for name in FILES:
        record = dict(selected[name])
        body_path = directory / name
        actual = _sha(body_path)
        if actual != record["body_sha256"]:
            raise ValueError(f"fixture digest changed: {name}")
        fixtures.append(
            {
                **record,
                "page_kind": _kind(record),
                "source_ref": _source_ref(record),
                "body_path": str(body_path),
            }
        )
    return fixtures, hashlib.sha256(raw).hexdigest()


def _insert_fixture(
    conn: sqlite3.Connection,
    archive: Archive,
    fixture: dict[str, Any],
    *,
    run_id: str,
    prepared_at: datetime,
) -> ParseContext:
    page_kind = str(fixture["page_kind"])
    watch_kind = page_kind.rsplit(".", 1)[-1]
    url = str(fixture["original_url"])
    identifier = watch_id("steprightsolutions", watch_kind, "GET", url)
    snapshot_id = "fixture-" + str(fixture["id"])
    body = Path(str(fixture["body_path"])).read_bytes()
    body_sha256 = archive.store_body(body)
    captured_at = str(fixture["captured_at"])
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,archive_url,state) "
        "VALUES (?,'steprightsolutions',?,'GET',?,?,?,?, 'sealed')",
        (
            identifier,
            watch_kind,
            url,
            page_kind,
            fixture["source_ref"],
            fixture["requested_archive_url"],
        ),
    )
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,observed_at,captured_at,"
        "http_status,body_sha256,body_bytes,content_changed,run_id,classification,via,archive_url,"
        "requested_archive_url) VALUES (?,?,'GET',?,?,?,?,200,?,?,1,?,'Ok','wayback',?,?)",
        (
            snapshot_id,
            identifier,
            url,
            prepared_at.isoformat(),
            captured_at,
            captured_at,
            body_sha256,
            len(body),
            run_id,
            fixture["archive_url"],
            fixture["requested_archive_url"],
        ),
    )
    enqueue(
        conn,
        (WorkUnit("parse", "snapshot", snapshot_id),),
        enqueued_at=prepared_at.isoformat(),
    )
    return ParseContext(
        snapshot_id,
        identifier,
        url,
        "steprightsolutions",
        page_kind,
        fixture["source_ref"],
        captured_at,
    )


def prepare(
    output: Path,
    *,
    prepared_at: datetime,
    fixture_dir: Path = FIXTURES,
) -> dict[str, Any]:
    """Create a fresh state and stop with every fixture staged in shadow mode."""
    if output.exists():
        raise ValueError("output already exists")
    prepared_at = _past_utc(prepared_at)
    output.mkdir(parents=True)
    state = output / "state"
    fixtures, provenance_sha256 = _load_fixtures(fixture_dir)
    archive = Archive(state)
    clock = FakeClock(prepared_at)
    generations: dict[str, list[dict[str, Any]]] = {kind: [] for kind in KINDS}
    with open_database(state, lock=False) as database:
        if database.schema_version != SCHEMA_VERSION:
            raise ValueError("disposable state did not reach the current schema")
        run_id = database.start_run(clock.now(), dry_run=True)
        for fixture in fixtures:
            context = _insert_fixture(
                database.connection,
                archive,
                fixture,
                run_id=run_id,
                prepared_at=prepared_at,
            )
            page = get_page_kind(context.kind)
            body = Path(str(fixture["body_path"])).read_bytes()
            extract = page.extract(body)
            result = page.parse(extract, context)
            report = inspect(context, body, extract, result)
            attempt = begin_attempt(
                database.connection,
                context,
                page.EXTRACT_VERSION,
                page.PARSER_VERSION,
            )
            with database.transaction():
                generation_id = stage_generation(
                    database.connection,
                    archive,
                    attempt,
                    archive.store_extract(extract),
                    result,
                    report,
                    now=prepared_at.isoformat(),
                    run_id=run_id,
                )
            if (
                admit_generation(
                    database,
                    archive,
                    generation_id,
                    now=prepared_at.isoformat(),
                    run_id=run_id,
                )
                != "shadow"
            ):
                raise ValueError("preparation unexpectedly left shadow mode")
            generations[context.kind].append(
                {
                    "generation_id": generation_id,
                    "fixture": fixture["file"],
                    "body_sha256": fixture["body_sha256"],
                    "interpretation": fixture["interpretation"],
                    "source_receipt": fixture["source_receipt"],
                    "source_receipt_sha256": fixture["source_receipt_sha256"],
                    "original_url": fixture["original_url"],
                    "archive_url": fixture["archive_url"],
                    "captured_at": fixture["captured_at"],
                    "report_digest": report.digest,
                    "failures": list(report.failures),
                    "removal_authority": report.proposed_removal,
                    "observation_count": len(result.observations),
                    "child_watch_count": len(result.watches),
                }
            )
        contracts = {}
        for kind, rows in generations.items():
            identifiers = tuple(str(row["generation_id"]) for row in rows)
            contracts[kind] = {
                "contract_version": database.connection.execute(
                    "SELECT DISTINCT contract_version FROM source_generations WHERE page_kind=?",
                    (kind,),
                ).fetchone()[0],
                "cohort_digest": corpus_digest(database.connection, identifiers),
                "generations": rows,
            }
        foreign_keys = database.connection.execute("PRAGMA foreign_key_check").fetchall()
    request = {
        "schema": REQUEST_SCHEMA,
        "scope": SCOPE,
        "prepared_at": prepared_at.isoformat(),
        "state_schema_version": SCHEMA_VERSION,
        "fixture_provenance": {
            "path": str(fixture_dir / PROVENANCE),
            "sha256": provenance_sha256,
        },
        "contracts": contracts,
        "review_status": "unreviewed",
        "requested_authority": "activate these exact contracts only in this disposable state",
        "production_authority": False,
        "historical_year_acceptance": False,
        "network_requests": 0,
        "production_mutations": 0,
        "foreign_key_violations": len(foreign_keys),
    }
    _write_json(output / "review-request.json", request)
    return request


def _load_review(output: Path, review_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    request_path = output / "review-request.json"
    request = json.loads(request_path.read_bytes())
    review = json.loads(review_path.read_bytes())
    required = {
        "schema": REVIEW_SCHEMA,
        "scope": SCOPE,
        "review_request_sha256": _sha(request_path),
        "disposable_activation_authorized": True,
        "production_activation_authorized": False,
    }
    for key, expected in required.items():
        if review.get(key) != expected:
            raise ValueError(f"external review metadata has invalid {key}")
    for key in ("reviewer", "reviewed_at", "evidence"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            raise ValueError(f"external review metadata requires {key}")
    reviewed_at = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
    if reviewed_at.tzinfo is None:
        raise ValueError("external review time must be timezone-aware")
    contracts = review.get("contracts")
    if not isinstance(contracts, dict) or set(contracts) != set(KINDS):
        raise ValueError("external review must cover the exact Step Right contract set")
    for kind in KINDS:
        if contracts[kind] != request["contracts"][kind]["cohort_digest"]:
            raise ValueError(f"external review digest changed for {kind}")
    return request, review


def execute(output: Path, review_path: Path) -> dict[str, Any]:
    """Activate and admit prepared generations only after exact external review."""
    request, review = _load_review(output, review_path)
    state = output / "state"
    archive = Archive(state)
    reviewed_at = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
    clock = FakeClock(reviewed_at.astimezone(UTC))
    with open_database(state, lock=False) as database:
        if database.schema_version != SCHEMA_VERSION:
            raise ValueError("prepared disposable state schema changed")
        if database.connection.execute("SELECT COUNT(*) FROM admission_decisions").fetchone()[0]:
            raise ValueError("prepared state was already admitted")
        run_id = database.start_run(clock.now(), dry_run=True)
        before_observations = {
            str(row[0])
            for row in database.connection.execute("SELECT observation_id FROM observations")
        }
        for kind in KINDS:
            identifiers = tuple(
                str(row["generation_id"]) for row in request["contracts"][kind]["generations"]
            )
            if corpus_digest(database.connection, identifiers) != review["contracts"][kind]:
                raise ValueError(f"prepared cohort changed for {kind}")
            with database.transaction():
                reviewed_digest = record_review(
                    database.connection,
                    identifiers,
                    reviewer=review["reviewer"],
                    reviewed_at=review["reviewed_at"],
                    evidence=review["evidence"],
                )
                if reviewed_digest != review["contracts"][kind]:
                    raise ValueError(f"recorded review digest changed for {kind}")
                activate_contract(database.connection, kind, reviewed_digest)
        outcomes: dict[str, list[str]] = {kind: [] for kind in KINDS}
        for kind in KINDS:
            for row in request["contracts"][kind]["generations"]:
                outcomes[kind].append(
                    admit_generation(
                        database,
                        archive,
                        str(row["generation_id"]),
                        now=clock.now().isoformat(),
                        run_id=run_id,
                    )
                )
        after_observations = {
            str(row[0])
            for row in database.connection.execute("SELECT observation_id FROM observations")
        }
        kinds = {}
        for kind in KINDS:
            rows = request["contracts"][kind]["generations"]
            failures = Counter(failure for row in rows for failure in row.get("failures", []))
            removals = Counter(str(row["removal_authority"]) for row in rows)
            kinds[kind] = {
                "prepared": len(rows),
                "accepted": outcomes[kind].count("accepted"),
                "guarded": len(outcomes[kind]) - outcomes[kind].count("accepted"),
                "outcomes": dict(sorted(Counter(outcomes[kind]).items())),
                "guard_failures": dict(sorted(failures.items())),
                "removal_authorities": dict(sorted(removals.items())),
            }
        projection_units = [
            {"unit_kind": str(row[0]), "unit_id": str(row[1])}
            for row in database.connection.execute(
                "SELECT unit_kind,unit_id FROM pending_work WHERE stage='project' "
                "ORDER BY unit_kind,unit_id"
            )
        ]
        foreign_keys = [
            tuple(row) for row in database.connection.execute("PRAGMA foreign_key_check")
        ]
        receipt = {
            "schema": "stepright-offline-admission-rehearsal-v1",
            "scope": SCOPE,
            "review_request_sha256": _sha(output / "review-request.json"),
            "external_review_sha256": _sha(review_path),
            "reviewer": review["reviewer"],
            "reviewed_at": review["reviewed_at"],
            "review_evidence": review["evidence"],
            "fixture_provenance": request["fixture_provenance"],
            "source_inputs": [
                {
                    key: generation[key]
                    for key in (
                        "fixture",
                        "body_sha256",
                        "interpretation",
                        "original_url",
                        "archive_url",
                        "captured_at",
                        "source_receipt",
                        "source_receipt_sha256",
                    )
                }
                for contract in request["contracts"].values()
                for generation in contract["generations"]
            ],
            "state_schema_version": database.schema_version,
            "kinds": kinds,
            "observations": {
                "before": len(before_observations),
                "after": len(after_observations),
                "added": len(after_observations - before_observations),
                "removed": len(before_observations - after_observations),
            },
            "queued_projection_units": projection_units,
            "foreign_key_check": {
                "violations": len(foreign_keys),
                "rows": foreign_keys,
            },
            "network_requests": 0,
            "production_mutations": 0,
            "production_activation_authorized": False,
            "historical_year_acceptance": False,
            "publication": False,
        }
    _write_json(output / "receipt.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    prepare_parser.add_argument(
        "--prepared-at",
        type=datetime.fromisoformat,
        required=True,
        help="timezone-aware UTC time at or before the current wall clock",
    )
    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--output", type=Path, required=True)
    execute_parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    result = (
        prepare(args.output, fixture_dir=args.fixtures, prepared_at=args.prepared_at)
        if args.command == "prepare"
        else execute(args.output, args.review)
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
