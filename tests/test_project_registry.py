from datetime import UTC, datetime
from pathlib import Path

from swingset.model.canonical import Dancer, RegistryPlacement
from swingset.model.ids import observation_id
from swingset.model.observations import encode_payload
from swingset.project.registry import project_dancer
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import ParseContext
from swingset.sources.records import DancerLookup
from swingset.sources.records import RegistryPlacement as RawPlacement
from swingset.sources.wsdc_registry import DancerPage
from swingset.sources.wsdc_registry.adapter import SOURCE
from swingset.state.db import open_database


def project(tmp_path: Path, payload: DancerLookup):
    page = DancerPage()
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        spec = SOURCE.watch(payload.requested_wsdc_id)
        upsert_watch(conn, spec, datetime(2026, 9, 9, tzinfo=UTC))
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run','2026-09-09T00:00:00Z',1)"
        )
        conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES ('snap',?,'POST',?,'2026-09-09T00:00:00Z',200,1,1,'run','Ok')",
            (spec.watch_id, spec.url),
        )
        conn.execute(
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                observation_id(spec.watch_id, "snap", payload.kind, 0),
                spec.watch_id,
                "snap",
                payload.kind,
                "dancer",
                str(payload.requested_wsdc_id),
                0,
                str(page.EXTRACT_VERSION),
                str(page.PARSER_VERSION),
                encode_payload(payload),
            ),
        )
        return project_dancer(
            conn, str(payload.requested_wsdc_id), "2026-09-09T00:00:00Z", "run"
        )


def test_real_registry_vocabulary_projects_to_closed_enums(tmp_path: Path) -> None:
    page = DancerPage()
    body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body").read_bytes()
    parsed = page.parse(
        page.extract(body),
        ParseContext(
            "snap",
            "registry-1",
            "https://points.worldsdc.com/lookup2020/find",
            "wsdc_registry",
            page.kind,
            "wsdc:1",
            "2026-09-09T00:00:00Z",
        ),
    ).observations[0].payload
    assert isinstance(parsed, DancerLookup)
    projection = project(tmp_path, parsed)

    dancer = projection.rows[0]
    placements = projection.rows[1:]
    assert isinstance(dancer, Dancer)
    assert dancer.primary_role == "follower"
    assert dancer.follower_required_level == "intermediate"
    assert dancer.follower_highest_level == "intermediate"
    assert {row.dance_style for row in placements if isinstance(row, RegistryPlacement)} == {
        "wcs"
    }
    assert {row.division for row in placements if isinstance(row, RegistryPlacement)} == {
        "newcomer",
        "novice",
        "intermediate",
    }
    assert projection.findings == ()


def test_registry_age_divisions_remain_distinct_and_unknown_is_omitted(tmp_path: Path) -> None:
    placements = tuple(
        RawPlacement("leader", code, "7", "Same Event", "August 2026", "1", 1, style)
        for code, style in (
            ("JRS", "West Coast Swing"),
            ("SPH", "wcs"),
            ("MSTR", "West Coast Swing"),
            ("UNVERIFIED", "Unverified Style"),
            ("NOV", "Unverified Style"),
        )
    )
    projection = project(
        tmp_path,
        DancerLookup(
            "dancer_lookup",
            "found",
            7,
            7,
            first_name="Test",
            last_name="Dancer",
            primary_role_raw="L",
            placements=placements,
        ),
    )
    rows = [row for row in projection.rows if isinstance(row, RegistryPlacement)]
    assert [row.division for row in rows] == [
        "juniors",
        "sophisticated",
        "masters",
        "novice",
    ]
    assert rows[-1].dance_style == "other"
    assert len({row.key() for row in rows}) == 4
    assert {(finding.evidence["field"], finding.evidence["raw"]) for finding in projection.findings} == {
        ("division", "UNVERIFIED"),
        ("dance_style", "Unverified Style"),
    }
