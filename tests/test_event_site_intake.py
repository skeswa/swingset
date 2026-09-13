"""Local filtered evidence intake preserves provenance without acquiring pages."""

import json
from dataclasses import replace

import pytest
from test_history_event_sites import PDF, SITE
from test_history_event_sites import sites as sites
from test_platform_backfill import fixture as fixture

from swingset.fetch.archive import Archive, canonical, digest, durable_write
from swingset.history.event_site_intake import FORMAT, ingest
from swingset.history.event_sites import query_url, reconcile, retained_hits


def package(
    f,
    directory,
    *,
    identifier=None,
    pages=1,
    included=None,
    mime="application/pdf",
    urls=(PDF,),
    year=2019,
    request_change=None,
    captures=None,
):
    identifier = identifier or digest((mime + str(year)).encode())
    archive = Archive(directory)
    receipts = []
    included = pages if included is None else included
    for ordinal in range(included + 1):
        page = "probe" if ordinal == 0 else str(ordinal - 1)
        body = canonical(
            pages
            if ordinal == 0
            else [
                ["timestamp", "original", "digest", "mimetype", "length"],
                *(
                    captures
                    if captures is not None
                    else [
                        [f"{year}0601000000", url, f"capture-{index}", mime, "100"]
                        for index, url in enumerate(urls)
                    ]
                ),
            ]
        )
        request = query_url(SITE, year, mime, page=None if ordinal == 0 else ordinal - 1)
        if request_change:
            request = request_change(page, request)
        receipt = {
            "query_id": identifier,
            "page": page,
            "url": request,
            "fetched_at": f.clock.now().isoformat(),
            "http_status": 200,
            "headers": {},
            "body_sha256": archive.store_body(body),
        }
        # Deliberate noncanonical bytes: intake must not rewrite captured provenance.
        raw = json.dumps(receipt, indent=3).encode() + b"\n"
        durable_write(directory / "archive-cdx" / identifier / (page + ".json"), raw)
        receipts.append({"page": page, "sha256": digest(raw)})
    manifest = {
        "format": FORMAT,
        "event_id": SITE.event_id,
        "year": SITE.year,
        "website": SITE.website,
        "queries": [{"query_id": identifier, "receipts": receipts}],
    }
    raw = canonical(manifest)
    durable_write(directory / "event-site-intake.json", raw)
    return digest(raw), identifier


def run(f, directory, sha, **kwargs):
    return ingest(f.db, SITE, directory=directory, manifest_sha256=sha, now=f.clock.now(), **kwargs)


def authority(f):
    return {
        table: [tuple(row) for row in f.conn.execute("SELECT * FROM " + table)]
        for table in (
            "watches",
            "host_budget",
            "history_acceptance",
            "admission_policies",
            "operator_pauses",
            "pending_work",
        )
    }


def test_local_import_preserves_raw_bytes_and_drives_existing_review(sites, tmp_path):
    directory = tmp_path / "package"
    sha, identifier = package(sites, directory)
    before = authority(sites)
    assert run(sites, directory, sha).dry_run
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
    result = run(sites, directory, sha, dry_run=False)
    assert (result.queries, result.pages, result.captures, result.complete_queries) == (1, 1, 1, 1)
    for page in ("probe", "0"):
        assert (directory / "archive-cdx" / identifier / (page + ".json")).read_bytes() == (
            sites.db.state_dir / "archive-cdx" / identifier / (page + ".json")
        ).read_bytes()
    assert authority(sites) == before
    assert retained_hits(sites.conn, Archive(sites.db.state_dir), SITE)[0].url == PDF
    assert reconcile(sites.db, now=sites.clock.now(), run_id=sites.run) == 1
    assert (
        sites.conn.execute(
            "SELECT COUNT(*) FROM findings WHERE kind='history_event_site_review' AND suggested_override IS NOT NULL"
        ).fetchone()[0]
        == 1
    )
    assert authority(sites) == before


def test_partial_resume_idempotence_and_duplicate_capture_provenance(sites, tmp_path):
    directory = tmp_path / "package"
    sha, identifier = package(sites, directory, pages=2, included=1)
    run(sites, directory, sha, dry_run=False)
    assert sites.conn.execute("SELECT completed_at FROM archive_queries").fetchone()[0] is None
    sha, _ = package(sites, directory, pages=2, included=2)
    run(sites, directory, sha, dry_run=False)
    run(sites, directory, sha, dry_run=False)
    assert tuple(
        sites.conn.execute("SELECT next_page,total_pages FROM archive_queries").fetchone()
    ) == (2, 2)
    assert (
        sites.conn.execute(
            "SELECT COUNT(*) FROM archive_captures WHERE source='event_sites'"
        ).fetchone()[0]
        == 1
    )
    refresh = tmp_path / "refresh"
    sha, new_id = package(sites, refresh, identifier=digest(b"refresh"))
    run(sites, refresh, sha, dry_run=False)
    hits = retained_hits(sites.conn, Archive(sites.db.state_dir), SITE)
    assert {hit.query_id for hit in hits} == {identifier, new_id}
    assert len({hit.receipt_sha256 for hit in hits}) == 3
    assert (
        sites.conn.execute(
            "SELECT cdx_query_id FROM archive_captures WHERE source='event_sites'"
        ).fetchone()[0]
        == identifier
    )


def test_zero_page_probe_is_retained_but_never_platform_absence(sites, tmp_path):
    sha, identifier = package(sites, tmp_path / "package", pages=0)
    result = run(sites, tmp_path / "package", sha, dry_run=False)
    assert result.complete_queries == 1 and result.pages == 0
    assert tuple(
        sites.conn.execute("SELECT source,next_page,total_pages FROM archive_queries").fetchone()
    ) == ("event_sites", 0, 0)
    assert (
        sites.conn.execute("SELECT COUNT(*) FROM archive_queries WHERE source='eepro'").fetchone()[
            0
        ]
        == 0
    )
    assert retained_hits(sites.conn, Archive(sites.db.state_dir), SITE) == ()
    assert (sites.db.state_dir / "archive-cdx" / identifier / "probe.json").exists()


@pytest.mark.parametrize(
    "change",
    [
        lambda p, u: u + "&filter=statuscode%3A200",
        lambda p, u: (
            u.replace("mimetype%3Aapplication%2Fpdf", "mimetype%3Atext%2Fhtml") if p == "0" else u
        ),
        lambda p, u: u.replace("web.archive.org", "web.archive.org:9999"),
        lambda p, u: u.replace("from=2019", "from=2018"),
        lambda p, u: u.replace("showNumPages=true", "page=0") if p == "probe" else u,
    ],
)
def test_invalid_request_recipe_never_changes_inventory(sites, tmp_path, change):
    sha, _ = package(sites, tmp_path / "package", request_change=change)
    with pytest.raises(ValueError):
        run(sites, tmp_path / "package", sha, dry_run=False)
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
    assert (
        sites.conn.execute(
            "SELECT COUNT(*) FROM archive_captures WHERE source='event_sites'"
        ).fetchone()[0]
        == 0
    )


def test_query_id_cannot_mix_filters_or_refreshes(sites, tmp_path):
    sha, identifier = package(sites, tmp_path / "first")
    run(sites, tmp_path / "first", sha, dry_run=False)
    sha, _ = package(
        sites,
        tmp_path / "other",
        identifier=identifier,
        mime="text/html",
        urls=("https://dance.example/festival/results2019.html",),
    )
    with pytest.raises(ValueError, match="earlier receipt"):
        run(sites, tmp_path / "other", sha, dry_run=False)
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 1


def test_filtered_review_ignores_platform_namespace(sites, tmp_path):
    sha, _ = package(sites, tmp_path / "package")
    run(sites, tmp_path / "package", sha, dry_run=False)
    sites.conn.execute(
        "INSERT INTO archive_queries VALUES (?,?,?,2019,1,1,?,?)",
        (
            digest(b"platform"),
            "eepro",
            SITE.prefix,
            sites.clock.now().isoformat(),
            sites.clock.now().isoformat(),
        ),
    )
    assert len(retained_hits(sites.conn, Archive(sites.db.state_dir), SITE)) == 1


def test_changed_year_between_inspection_and_commit_rejects_metadata(sites, tmp_path, monkeypatch):
    import swingset.history.event_site_intake as intake

    sha, _ = package(sites, tmp_path / "package")
    original = intake._retain

    def changed(path, body):
        original(path, body)
        sites.conn.execute("DELETE FROM history_acceptance")

    monkeypatch.setattr(intake, "_retain", changed)
    with pytest.raises(ValueError, match="unaccepted"):
        run(sites, tmp_path / "package", sha, dry_run=False)
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0


def test_bad_manifest_body_symlink_and_scope_are_rejected(sites, tmp_path):
    directory = tmp_path / "package"
    sha, identifier = package(sites, directory)
    with pytest.raises(ValueError, match="digest mismatch"):
        run(sites, directory, "0" * 64)
    with pytest.raises(ValueError, match="exact canonical"):
        ingest(
            sites.db,
            replace(SITE, year=2009),
            directory=directory,
            manifest_sha256=sha,
            now=sites.clock.now(),
        )
    receipt = json.loads((directory / "archive-cdx" / identifier / "0.json").read_bytes())
    path = Archive(directory).blob_path(receipt["body_sha256"])
    real = tmp_path / "body"
    path.rename(real)
    path.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        run(sites, directory, sha)


@pytest.mark.parametrize(
    "selector", [("all", "all"), ("kind", "source_event_mapping"), ("source", "eepro")]
)
def test_pause_prevents_even_local_intake_without_budget_debit(sites, tmp_path, selector):
    from swingset.state.controls import Selector, change_control

    sha, _ = package(sites, tmp_path / "package")
    change_control(
        sites.db.state_dir,
        selector=Selector(*selector),
        paused=True,
        actor="test",
        reason="retain pause",
        now=sites.clock.now(),
    )
    before = authority(sites)
    with pytest.raises(ValueError, match="paused"):
        run(sites, tmp_path / "package", sha, dry_run=False)
    assert authority(sites) == before
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0


def test_mixed_filters_have_distinct_queries_and_no_missing_duplicate_provenance(sites, tmp_path):
    first = tmp_path / "pdf"
    sha, first_id = package(sites, first)
    run(sites, first, sha, dry_run=False)
    second = tmp_path / "html"
    sha, second_id = package(
        sites, second, mime="text/html", urls=("https://dance.example/festival/2019-results.html",)
    )
    run(sites, second, sha, dry_run=False)
    assert first_id != second_id
    assert {h.mimetype for h in retained_hits(sites.conn, Archive(sites.db.state_dir), SITE)} == {
        "application/pdf",
        "text/html",
    }


def test_bad_capture_rows_rollback_before_any_inventory_write(sites, tmp_path):
    entries = [
        ["20190601000000", PDF, "one", "application/pdf", "100"],
        ["20190601000000", PDF, "two", "application/pdf", "100"],
    ]
    sha, _ = package(sites, tmp_path / "package", captures=entries)
    with pytest.raises(ValueError, match="conflicting capture"):
        run(sites, tmp_path / "package", sha, dry_run=False)
    assert (
        sites.conn.execute(
            "SELECT COUNT(*) FROM archive_captures WHERE source='event_sites'"
        ).fetchone()[0]
        == 0
    )
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0


def test_sql_failure_leaves_no_partial_query_selection(sites, tmp_path):
    import sqlite3

    sha, _ = package(sites, tmp_path / "package")
    sites.conn.execute(
        "CREATE TEMP TRIGGER fail_intake BEFORE INSERT ON archive_captures WHEN NEW.source='event_sites' BEGIN SELECT RAISE(ABORT,'test interruption'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="interruption"):
        run(sites, tmp_path / "package", sha, dry_run=False)
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
    assert (
        sites.conn.execute(
            "SELECT COUNT(*) FROM archive_captures WHERE source='event_sites'"
        ).fetchone()[0]
        == 0
    )
    sites.conn.execute("DROP TRIGGER fail_intake")
    assert run(sites, tmp_path / "package", sha, dry_run=False).complete_queries == 1


def test_bad_body_and_excessive_probe_are_not_accepted(sites, tmp_path):
    directory = tmp_path / "package"
    sha, identifier = package(sites, directory, pages=65, included=0)
    with pytest.raises(ValueError, match="page count"):
        run(sites, directory, sha, dry_run=False)
    sha, identifier = package(sites, directory)
    receipt = json.loads((directory / "archive-cdx" / identifier / "0.json").read_bytes())
    import gzip

    Archive(directory).blob_path(receipt["body_sha256"]).write_bytes(gzip.compress(b"wrong body"))
    with pytest.raises(ValueError, match="digest verification"):
        run(sites, directory, sha, dry_run=False)
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0


def test_existing_destination_receipt_size_is_checked_before_read(sites, tmp_path, monkeypatch):
    from pathlib import Path

    sha, identifier = package(sites, tmp_path / "package")
    destination = sites.db.state_dir / "archive-cdx" / identifier / "probe.json"
    durable_write(destination, b"x" * (128 * 1024 + 1))
    original = Path.read_bytes

    def guarded(path):
        assert path != destination, "oversized destination must not be read unbounded"
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    with pytest.raises(ValueError, match="bound"):
        run(sites, tmp_path / "package", sha, dry_run=False)


def test_existing_destination_gzip_is_decompressed_with_intake_bound(sites, tmp_path, monkeypatch):
    import gzip

    from swingset.history.event_site_intake import MAX_BODY_BYTES

    directory = tmp_path / "package"
    sha, identifier = package(sites, directory)
    receipt = json.loads((directory / "archive-cdx" / identifier / "0.json").read_bytes())
    destination = Archive(sites.db.state_dir).blob_path(receipt["body_sha256"])
    durable_write(destination, gzip.compress(b"x" * (MAX_BODY_BYTES + 1)))

    def unbounded(*args):
        raise AssertionError("unbounded archive read must not be called")

    monkeypatch.setattr(Archive, "read_body", unbounded)
    with pytest.raises(ValueError, match="exceeds bound"):
        run(sites, directory, sha, dry_run=False)
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0


def test_host_pause_does_not_gate_offline_interpretation(sites, tmp_path):
    from swingset.state.controls import Selector, change_control

    sha, _ = package(sites, tmp_path / "package")
    change_control(
        sites.db.state_dir,
        selector=Selector("host", "web.archive.org"),
        paused=True,
        actor="test",
        reason="no archive HTTP",
        now=sites.clock.now(),
        hosts=("web.archive.org",),
    )
    before = authority(sites)
    assert run(sites, tmp_path / "package", sha, dry_run=False).complete_queries == 1
    assert authority(sites) == before


def test_clock_expiry_leaves_no_query_metadata(sites, tmp_path, monkeypatch):
    import swingset.history.event_site_intake as intake

    sha, _ = package(sites, tmp_path / "package")
    moments = iter([0, 2])
    monkeypatch.setattr(intake.time, "monotonic", lambda: next(moments, 2))
    with pytest.raises(ValueError, match="wall clock"):
        run(sites, tmp_path / "package", sha, dry_run=False, wall_seconds=1)
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0


def test_capture_timestamp_cannot_postdate_its_query_receipt(sites, tmp_path):
    directory = tmp_path / "package"
    sha, identifier = package(sites, directory)
    manifest = json.loads((directory / "event-site-intake.json").read_bytes())
    for record in manifest["queries"][0]["receipts"]:
        path = directory / "archive-cdx" / identifier / (record["page"] + ".json")
        receipt = json.loads(path.read_bytes())
        receipt["fetched_at"] = "2019-01-01T00:00:00+00:00"
        raw = canonical(receipt)
        durable_write(path, raw)
        record["sha256"] = digest(raw)
    raw = canonical(manifest)
    durable_write(directory / "event-site-intake.json", raw)
    with pytest.raises(ValueError, match="later than"):
        run(sites, directory, digest(raw), dry_run=False)
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
