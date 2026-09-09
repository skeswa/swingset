from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from swingset.project.map import project_map
from swingset.state.db import open_database


@dataclass(frozen=True)
class Bundle:
    files: MappingProxyType[str, bytes]


def test_reprojecting_unchanged_overrides_keeps_placeholder_events(tmp_path: Path) -> None:
    csv = b"event_id,source,kind,url,parser\n2026-08-desert-city-swing,worlddanceregistry,event,https://scores.worlddanceregistry.com/98011277-01cd-11f1-9a29-0aa72bbce9ea/rounds/routeInfo.json,wdr.rounds\n"
    bundle = Bundle(
        MappingProxyType(
            {
                "overrides/source_urls.csv": csv,
                "overrides/event_aliases.csv": b"event_id,source,source_ref\n",
            }
        )
    )
    with open_database(tmp_path, lock=False) as database:
        database.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-08T00:00:00Z',0)"
        )
        with database.transaction():
            assert project_map(database.connection, bundle, "2026-09-08T00:00:00Z", "run_a", 2)
        assert (
            database.connection.execute("SELECT event_id FROM events").fetchone()[0]
            == "2026-08-desert-city-swing"
        )
        with database.transaction():
            assert not project_map(database.connection, bundle, "2026-09-09T00:00:00Z", "run_a", 2)
        assert (
            database.connection.execute("SELECT event_id FROM events").fetchone()[0]
            == "2026-08-desert-city-swing"
        )
