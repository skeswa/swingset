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


def test_a_zstd_wayback_body_stored_undecoded_extracts_its_rows():
    """Wayback replayed this 2026-08-13 capture zstd-compressed and it was stored
    as sent; the extractor decodes it (maintenance item 7)."""
    import pyarrow as pa
    import pytest

    from swingset.sources.base import ExtractError

    fixtures = Path("src/swingset/sources/wsdc_calendar/fixtures")
    body = (fixtures / "calendar-20260813-zstd.body").read_bytes()
    meta = json.loads((fixtures / "calendar-20260813-zstd.json").read_bytes())
    assert json.loads(meta["headers_json"])["content-encoding"] == "zstd"
    assert body.startswith(b"\x28\xb5\x2f\xfd")
    page = EventsPage()
    extract = page.extract(body)
    with pa.input_stream(pa.BufferReader(body), compression="zstd") as stream:
        decoded = stream.read()
    assert extract == page.extract(decoded)
    result = page.parse(
        extract,
        ParseContext(
            "snapshot",
            "watch",
            "https://worldsdc.com/events/",
            "wsdc_calendar",
            page.kind,
            None,
            meta["captured_at"],
        ),
    )
    assert len(result.observations) == 169 and not result.warnings
    first = result.observations[0].payload
    assert (first.name_raw, first.start_date_raw) == ("Grand Party Sofia (GPS)", "2026-08-14")
    # A truncated frame is an extract failure, not an exception.
    with pytest.raises(ExtractError, match="zstd"):
        page.extract(body[:-16])


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
