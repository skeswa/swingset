import shutil
from pathlib import Path

import httpx

from swingset.clock import FakeClock
from swingset.schedule.cycle import run_cycle
from swingset.state.db import open_database


def test_archived_scoringdance_event_runs_end_to_end(tmp_path: Path) -> None:
    fixtures = Path("src/swingset/sources/scoringdance/fixtures")
    event = (fixtures / "event-2026-09-09.body").read_bytes()
    bodies = {
        "/sitemap.xml": b"<urlset><url><loc>https://scoring.dance/enUS/events/418/results/</loc></url></urlset>",
        "/enUS/recent": (fixtures / "recent-2026-09-09.body").read_bytes(),
        "/enUS/events/418/results/": event,
    }
    for path in fixtures.glob("round-*-2026-09-09.body"):
        round_id = path.name.split("-")[1]
        bodies[f"/enUS/events/418/results/{round_id}.html"] = path.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = bodies.get(request.url.path)
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    config = tmp_path / "config"
    config.mkdir()
    shutil.copy("config/hosts.toml", config / "hosts.toml")
    (config / "sources.toml").write_text(
        "[sources.scoringdance]\nenabled=true\nindex_urls=[]\n", encoding="utf-8"
    )
    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    clock = FakeClock()
    with open_database(tmp_path / "state") as database:
        for _ in range(30):
            result = run_cycle(
                database,
                config_dir=config,
                overrides_dir=overrides,
                clock=clock,
                transport=httpx.MockTransport(handler),
            )
            if (
                result.get("candidate_id")
                and database.connection.execute("SELECT count(*) FROM entries").fetchone()[0]
                == 202
            ):
                break
        conn = database.connection
        assert conn.execute("SELECT count(*) FROM contests").fetchone()[0] == 6
        assert conn.execute("SELECT count(*) FROM rounds").fetchone()[0] == 12
        assert conn.execute("SELECT count(*) FROM entries").fetchone()[0] == 202
        assert conn.execute("SELECT count(*) FROM callbacks").fetchone()[0] == 153
        assert conn.execute("SELECT count(*) FROM callback_marks").fetchone()[0] == 769
        assert conn.execute("SELECT count(*) FROM placements").fetchone()[0] == 62
        assert conn.execute("SELECT count(*) FROM final_marks").fetchone()[0] == 420
        assert conn.execute(
            "SELECT count(*) FROM identity_links WHERE method='source_id' AND status='confirmed'"
        ).fetchone()[0] == 151
        assert result.get("candidate_id")
