import dataclasses
import json
from pathlib import Path

from swingset.sources.base import ParseContext
from swingset.sources.wsdc_calendar import EventsPage
from swingset.sources.wsdc_registry import DancerPage


def test_archived_calendar_matches_expected_observations():
    fixtures = Path("src/swingset/sources/wsdc_calendar/fixtures")
    page = EventsPage()
    result = page.parse(
        page.extract((fixtures / "calendar-2026-09-09.html").read_bytes()),
        ParseContext(
            "snapshot",
            "watch",
            "https://worldsdc.com/events/",
            "wsdc_calendar",
            page.kind,
            None,
            "2026-09-09T00:00:00Z",
        ),
    )
    observed = json.loads(json.dumps([dataclasses.asdict(o.payload) for o in result.observations]))
    assert observed == json.loads((fixtures / "calendar-2026-09-09.expected.json").read_bytes())
    assert len(observed) == 172


def test_archived_registry_found_and_verified_misses():
    fixtures = Path("src/swingset/sources/wsdc_registry/fixtures")
    page = DancerPage()
    for number, outcome in [(1, "found"), (1000000, "not_found"), (1000001, "not_found")]:
        body = (fixtures / f"lookup-{number}.body").read_bytes()
        context = ParseContext(
            "snapshot",
            "watch",
            "https://points.worldsdc.com/lookup2020/find",
            "wsdc_registry",
            page.kind,
            f"wsdc:{number}",
            "2026-09-09T00:00:00Z",
        )
        result = page.parse(page.extract(body), context)
        assert result.observations[0].payload.outcome == outcome
