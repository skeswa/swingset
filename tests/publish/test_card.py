from pathlib import Path

from swingset.build.builder import BuildInput
from swingset.build.schema import PRIMARY_KEYS, SCHEMAS
from swingset.publish.card import render_card


def test_card_lists_every_table_and_one_default() -> None:
    data = BuildInput({}, SCHEMAS, PRIMARY_KEYS, {}, {}, "bundle")
    card = render_card(data).decode()
    assert card.count("config_name:") == len(SCHEMAS)
    assert card.count("default: true") == 1
    assert 'config_name: events\n  default: true' in card
    assert 'config_name: placements\n  default: true' not in card
    assert "ODC-By 1.0" in card
    assert "five-second" in card
    assert "LEFT JOIN" in card
    assert "no claim of complete source or" in card


def test_card_reports_actual_calendar_only_coverage_and_gaps() -> None:
    tables = {
        "events": [
            {"event_id": "one", "year": 2026, "sources": ["wsdc_calendar"]},
            {"event_id": "two", "year": 2026, "sources": ["wsdc_calendar"]},
        ],
        "contests": [],
        "heats": [],
        "dancers": [],
        "registry_placements": [],
    }
    data = BuildInput(tables, SCHEMAS, PRIMARY_KEYS, {}, {}, "bundle")
    card = render_card(data).decode()

    assert "| wsdc_calendar | 2026 | 2 |" in card
    assert "| `events` | 2 |" in card
    assert "2 of 2 events currently have event metadata but no parsed contest results" in card
    assert "`heats` has 0 rows" in card
    assert "registry mirror is incomplete" in card
    assert "calendar-only releases\ndefault to `events`" in card


def test_card_defaults_to_placements_once_results_exist() -> None:
    data = BuildInput({"placements": [{"placement_id": "one"}]}, SCHEMAS, PRIMARY_KEYS, {}, {}, "bundle")
    card = render_card(data).decode()

    assert card.count("default: true") == 1
    assert 'config_name: placements\n  default: true' in card
    assert 'config_name: events\n  default: true' not in card
    assert "`placements` is the default config in this" in card


def test_published_card_counts_match_artifacts_and_repeat_is_unchanged(tmp_path: Path) -> None:
    import json
    from dataclasses import replace
    from datetime import UTC, datetime

    from swingset.build.builder import BuildMetadata, build_candidate

    event: dict[str, object] = {field.name: None for field in SCHEMAS["events"]}
    event.update(event_id="one", year=2026, sources=["wsdc_calendar"], wsdc_status="registry")
    data = BuildInput({"events": [event]}, SCHEMAS, PRIMARY_KEYS, {}, {}, "bundle")
    meta = BuildMetadata(
        "run_a", "code", None, 1, {}, render_card(data), datetime(2026, 9, 9, tzinfo=UTC)
    )
    first = build_candidate(tmp_path, data, meta, card_renderer=render_card)
    manifest = json.loads((first.path / "_meta/manifest.json").read_text())
    card = (first.path / "README.md").read_text()
    assert manifest["row_counts"]["changelog"] > 0
    for table, count in manifest["row_counts"].items():
        assert f"| `{table}` | {count} |" in card
    (first.path / "PUBLISHED").write_text('{"commit":"published-a"}')
    (tmp_path / "baseline").symlink_to(first.path)
    second = build_candidate(
        tmp_path,
        data,
        replace(meta, run_id="run_b", expected_parent="published-a"),
        card_renderer=render_card,
    )
    assert not second.changed
