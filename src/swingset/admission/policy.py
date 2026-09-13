"""Per-contract activation requires an externally reviewed frozen corpus."""

import json
import sqlite3
from pathlib import Path

from swingset.fetch.archive import canonical, digest


def corpus_digest(conn: sqlite3.Connection, generation_ids: tuple[str, ...]) -> str:
    if not generation_ids or len(set(generation_ids)) != len(generation_ids):
        raise ValueError("Review requires a nonempty, unique generation cohort")
    rows = []
    for identifier in sorted(generation_ids):
        row = conn.execute(
            "SELECT generation_id,page_kind,contract_version,input_fingerprint,report_json FROM source_generations WHERE generation_id=?",
            (identifier,),
        ).fetchone()
        if row is None:
            raise ValueError("Review references a missing generation")
        rows.append(tuple(row))
    return digest(canonical(rows))


def record_review(
    conn: sqlite3.Connection,
    generation_ids: tuple[str, ...],
    *,
    reviewer: str,
    reviewed_at: str,
    evidence: str,
) -> str:
    """Record supplied adjudication, never manufacture a review from a green run."""
    if not reviewer.strip() or not reviewed_at.strip() or not evidence.strip():
        raise ValueError("Reviewer, review time, and external review evidence are required")
    report_digest = corpus_digest(conn, generation_ids)
    contracts = set()
    passing = False
    for identifier in generation_ids:
        row = conn.execute(
            "SELECT page_kind,contract_version,report_json FROM source_generations WHERE generation_id=?",
            (identifier,),
        ).fetchone()
        contracts.add((row[0], row[1]))
        passing |= not json.loads(row[2])["failures"]
    if len(contracts) != 1 or not passing:
        raise ValueError("A review covers one contract version and includes a passing control")
    page_kind, version = contracts.pop()
    if version == "unassessed":
        raise ValueError("Unassessed contracts cannot be activated")
    conn.execute(
        "INSERT INTO admission_reviews(report_digest,page_kind,contract_version,cohort_json,reviewer,reviewed_at,evidence) VALUES (?,?,?,?,?,?,?) ON CONFLICT(report_digest) DO NOTHING",
        (
            report_digest,
            page_kind,
            version,
            canonical(sorted(generation_ids)).decode(),
            reviewer,
            reviewed_at,
            evidence,
        ),
    )
    return report_digest


def record_corpus_review(
    conn: sqlite3.Connection,
    directory: Path,
    page_kind: str,
    *,
    expected_digest: str,
    reviewer: str,
    reviewed_at: str,
    evidence: str,
) -> str:
    """Import an explicitly supplied review of the portable read-only corpus."""
    import hashlib

    if not reviewer.strip() or not reviewed_at.strip() or not evidence.strip():
        raise ValueError("Reviewer, review time, and external review evidence are required")
    receipt = json.loads((directory / "receipt.json").read_bytes())
    calculated = hashlib.sha256()
    versions = set()
    passing, count = 0, 0
    with (directory / "reports.jsonl").open("rb") as stream:
        for line in stream:
            calculated.update(line)
            row = json.loads(line)
            if row["page_kind"] == page_kind:
                count += 1
                report = row.get("report")
                if report is not None:
                    versions.add(report["contract_version"])
                    passing += not report["failures"]
    if calculated.hexdigest() != expected_digest or receipt["reports_sha256"] != expected_digest:
        raise ValueError("Reviewed corpus digest changed")
    if len(versions) != 1 or "unassessed" in versions or not passing:
        raise ValueError("Reviewed contract needs a versioned passing control")
    version = versions.pop()
    cohort = {
        "external_corpus": expected_digest,
        "page_kind": page_kind,
        "contract_version": version,
        "count": count,
        "passing": passing,
        "directory": str(directory),
        "cutoff": receipt["cutoff"],
    }
    report_digest = digest(canonical(cohort))
    conn.execute(
        "INSERT INTO admission_reviews(report_digest,page_kind,contract_version,cohort_json,reviewer,reviewed_at,evidence) VALUES (?,?,?,?,?,?,?) ON CONFLICT(report_digest) DO NOTHING",
        (
            report_digest,
            page_kind,
            version,
            canonical(cohort).decode(),
            reviewer,
            reviewed_at,
            evidence,
        ),
    )
    return report_digest


def activate_contract(conn: sqlite3.Connection, page_kind: str, reviewed_digest: str) -> None:
    review = conn.execute(
        "SELECT * FROM admission_reviews WHERE report_digest=? AND page_kind=?",
        (reviewed_digest, page_kind),
    ).fetchone()
    if review is None:
        raise ValueError("An exact externally reviewed corpus receipt is required")
    cohort = json.loads(review["cohort_json"])
    actual = (
        digest(canonical(cohort))
        if isinstance(cohort, dict)
        else corpus_digest(conn, tuple(cohort))
    )
    if actual != reviewed_digest:
        raise ValueError("Reviewed corpus changed")
    conn.execute(
        "INSERT INTO admission_policies(page_kind,contract_version,mode,policy_revision,reviewed_report_digest,reviewed_by,reviewed_at) VALUES (?,?,'enforce',?,?,?,?) ON CONFLICT(page_kind) DO UPDATE SET contract_version=excluded.contract_version,mode='enforce',policy_revision=excluded.policy_revision,reviewed_report_digest=excluded.reviewed_report_digest,reviewed_by=excluded.reviewed_by,reviewed_at=excluded.reviewed_at",
        (
            page_kind,
            review["contract_version"],
            "review:" + reviewed_digest,
            reviewed_digest,
            review["reviewer"],
            review["reviewed_at"],
        ),
    )
    # Activation replays retained evidence only. Existing newer work tokens
    # survive, and no watch/fetch schedule is changed.
    conn.execute(
        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) SELECT 'parse','snapshot',s.snapshot_id,? FROM snapshots s JOIN watches w USING(watch_id) WHERE w.parser=? AND s.body_sha256 IS NOT NULL ON CONFLICT(stage,unit_kind,unit_id) DO NOTHING",
        (review["reviewed_at"], page_kind),
    )


def pause_contract(conn: sqlite3.Connection, page_kind: str) -> None:
    conn.execute("UPDATE admission_policies SET mode='paused' WHERE page_kind=?", (page_kind,))
