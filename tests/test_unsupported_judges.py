import hashlib
import json
from pathlib import Path

import pytest

from swingset.model.canonical import Contest, Judge, Round
from swingset.model.observations import encode_payload
from swingset.project.contests import project_event
from swingset.sources.base import ParseContext
from swingset.sources.wdr.adapter import RoundsPage
from swingset.state.db import open_database
from swingset.state.identity_references import ReferenceReader

FIXTURES = Path(__file__).parent / "fixtures/sources/wdr-unsupported-judges"


@pytest.mark.parametrize("metadata_path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_retained_wdr_named_judges_survive_unsupported_scoring(tmp_path, metadata_path):
    metadata = json.loads(metadata_path.read_bytes())
    body = metadata_path.with_suffix(".body").read_bytes()
    assert hashlib.sha256(body).hexdigest() == metadata["body_sha256"]
    assert len(body) == metadata["body_bytes"]
    page = RoundsPage()
    ctx = ParseContext(
        metadata["snapshot_id"],
        "watch",
        metadata["url"],
        "wdr",
        page.kind,
        metadata["source_ref"],
        metadata["fetched_at"],
    )
    parsed = page.parse(page.extract(body), ctx)
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,1)",
            (metadata["fetched_at"],),
        )
        conn.execute(
            "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,state) VALUES ('watch','wdr','event','GET',?,?,?,'paused')",
            (ctx.url, page.kind, ctx.source_ref),
        )
        conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) VALUES (?,'watch','GET',?,?,200,?,?,1,'test','Ok')",
            (ctx.snapshot_id, ctx.url, ctx.fetched_at, metadata["body_sha256"], len(body)),
        )
        conn.execute(
            "INSERT INTO source_event_map VALUES ('wdr',?,?,'override',1)",
            (ctx.source_ref, metadata["event_id"]),
        )
        for seq, observation in enumerate(parsed.observations):
            conn.execute(
                "INSERT INTO observations VALUES (?,'watch',?,?,'source_event',?,?,?, ?,?)",
                (
                    str(seq),
                    ctx.snapshot_id,
                    observation.kind,
                    ctx.source_ref,
                    seq,
                    str(page.EXTRACT_VERSION),
                    str(page.PARSER_VERSION),
                    encode_payload(observation.payload),
                ),
            )
        projection = project_event(conn, metadata["event_id"], ctx.fetched_at, "test")
        judges = {row.judge_id: row for row in projection.rows if isinstance(row, Judge)}
        reader = ReferenceReader(conn)
        for expected in metadata["expected_preserved_judges"]:
            judge = judges[expected["judge_id"]]
            assert judge.name_raw == expected["name_raw"]
            assert not judge.anonymous and judge.wsdc_id is None
            assert reader.for_record(
                "judge",
                {
                    "judge_id": judge.judge_id,
                    "event_id": judge.event_id,
                    "source": judge.source,
                    "snapshot_id": judge.snapshot_id,
                    "name_raw": judge.name_raw,
                },
            )
        unsupported = {
            row.contest_id
            for row in projection.rows
            if isinstance(row, Contest) and row.parse_status == "unsupported"
        }
        assert unsupported
        assert all(
            getattr(row, "contest_id", None) not in unsupported
            for row in projection.rows
            if not isinstance(row, Contest)
        )
        # Supported ordinal rounds still project beside unsupported numeric contests.
        assert any(isinstance(row, Round) for row in projection.rows)


def test_retained_fixture_set_covers_all_eleven_reported_losses():
    metadata = [json.loads(path.read_bytes()) for path in FIXTURES.glob("*.json")]
    assert len(metadata) == 6
    assert sum(len(row["expected_preserved_judges"]) for row in metadata) == 11
