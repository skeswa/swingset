from pathlib import Path

from swingset.build.builder import BuildInput
from swingset.build.schema import PRIMARY_KEYS, SCHEMAS
from swingset.publish.card import render_card


def test_card_lists_every_table_and_one_default() -> None:
    data = BuildInput({}, SCHEMAS, PRIMARY_KEYS, {}, {}, "bundle")
    card = render_card(data).decode()
    assert card.count("config_name:") == len(SCHEMAS)
    assert card.count("default: true") == 1
    assert "config_name: events\n  default: true" in card
    assert "config_name: placements\n  default: true" not in card
    assert "ODC-By 1.0" in card
    assert "traces history from 2010-01-01 onward" in card
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
    data = BuildInput(
        {"placements": [{"placement_id": "one"}]}, SCHEMAS, PRIMARY_KEYS, {}, {}, "bundle"
    )
    card = render_card(data).decode()

    assert card.count("default: true") == 1
    assert "config_name: placements\n  default: true" in card
    assert "config_name: events\n  default: true" not in card
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


def test_card_discloses_phase1_public_finding_counts_without_private_evidence():
    from swingset.publish.card import _gaps

    findings = [
        {
            "kind": "phase1_incomplete",
            "subject_id": "2010",
            "summary": "Event-list catalog has pending captures, unresolved parse findings, or missing discovery",
        },
        {
            "kind": "phase1_incomplete",
            "subject_id": "2011",
            "summary": "Event-list catalog has pending captures, unresolved parse findings, or missing discovery",
        },
        {
            "kind": "series_alias",
            "subject_id": "2010-01:printed-series",
            "summary": "PRIVATE operator note",
            "suggested_override": "PRIVATE proposed alias",
        },
        {
            "kind": "series_alias",
            "subject_id": "2011-01:printed-series",
            "summary": "Historical listing needs a reviewed series alias",
        },
        {
            "kind": "event_alias",
            "subject_id": "2011-09-ambiguous-edition",
            "summary": "Multiple dated editions match one registry occurrence",
        },
        {
            "kind": "parse_warning",
            "subject_id": "watch",
            "summary": "Listed event has no safely parsed date; preserve hiatus or other notice for review",
        },
        {
            "kind": "parse_warning",
            "subject_id": "watch",
            "summary": "unknown calendar date: '20189'",
        },
        {
            "kind": "parse_warning",
            "subject_id": "unrelated-scoring-watch",
            "summary": "Unrelated scoring warning",
        },
        {
            "kind": "parse_failure",
            "subject_id": "same-watch",
            "summary": "captured map marker lacks a name or printed date",
        },
        {
            "kind": "parse_failure",
            "subject_id": "same-watch",
            "summary": "captured map marker lacks a name or printed date",
        },
    ]
    data = BuildInput(
        {
            "review_queue": findings,
            "coverage": [
                {"year": 2010, "events_accepted": False},
                {"year": 2011, "events_accepted": False},
            ],
        },
        SCHEMAS,
        PRIMARY_KEYS,
        {},
        {},
        "bundle",
    )
    text = _gaps(data)
    assert "2 open year-scope findings for 2010, 2011" in text
    assert "2 series-alias and 1 event-alias findings" in text
    assert "2 warning findings" in text
    assert "2 archived-map parse findings" in text
    assert "waits for host budgets" in text
    assert "do not provide a distinct pending-capture count" in text
    assert "acceptance remains open for 2010, 2011" in text
    assert "PRIVATE" not in text
    assert "20189" not in text


def test_correction_card_uses_only_its_baseline_public_findings():
    from swingset.publish.card import _gaps

    data = BuildInput(
        {
            "review_queue": [
                {"kind": "phase1_incomplete", "subject_id": "2020", "summary": "Known baseline gap"}
            ]
        },
        SCHEMAS,
        PRIMARY_KEYS,
        {},
        {},
        "bundle",
        release_policy={"mode": "correction_only", "pending_work": {"parse": 987}},
    )
    text = _gaps(data)
    assert "1 open year-scope findings for 2020" in text
    assert "987" not in text
    assert "archived-map parse findings" not in text


def test_card_discloses_gated_sheet_count_without_private_evidence():
    from swingset.publish.card import _gaps

    data = BuildInput(
        {
            "review_queue": [
                {
                    "kind": "acquisition_gate",
                    "subject_id": "eepro:event2025",
                    "summary": "PRIVATE operator reasoning",
                    "evidence_json": "PRIVATE",
                },
                {"kind": "acquisition_gate", "subject_id": "eepro:event2026"},
            ]
        },
        SCHEMAS,
        PRIMARY_KEYS,
        {},
        {},
        "bundle",
    )
    text = _gaps(data)
    assert "2 newly discovered sheet links await event-year acceptance" in text
    assert "index evidence is retained; acquisition has not started" in text
    assert "PRIVATE" not in text


def test_card_lists_platform_archive_gaps_from_public_findings_only():
    from swingset.publish.card import _gaps

    data = BuildInput(
        {
            "review_queue": [
                {
                    "kind": "history_archive_gap",
                    "subject_id": "2019-01-example",
                    "summary": "PRIVATE",
                    "evidence_json": "https://unreviewed.example/file.pdf",
                },
                {"kind": "history_archive_gap", "subject_id": "2020-02-example"},
                {
                    "kind": "history_unmapped_sheet",
                    "subject_id": "https://unreviewed.example/sheet.html",
                    "suggested_override": "PRIVATE",
                },
            ]
        },
        SCHEMAS,
        PRIMARY_KEYS,
        {},
        {},
        "bundle",
        release_policy={"mode": "correction_only", "pending_work": {"parse": 987}},
    )
    text = _gaps(data)
    assert "2 archive-gap findings and 1 unmapped-sheet findings" in text
    assert "public findings: 2019, 2020" in text
    assert "does not authorize origin acquisition" in text
    assert "PRIVATE" not in text and "unreviewed.example" not in text and "987" not in text


def test_card_discloses_event_site_review_without_echoing_candidate_urls():
    from swingset.publish.card import _gaps

    data = BuildInput(
        {
            "review_queue": [
                {
                    "kind": "history_event_site_review",
                    "subject_id": "2019-01-example",
                    "summary": "PRIVATE",
                    "suggested_override": "https://unreviewed.example/file.pdf",
                }
            ]
        },
        SCHEMAS,
        PRIMARY_KEYS,
        {},
        {},
        "bundle",
    )
    text = _gaps(data)
    assert "1 event-site result findings await human override review for 2019" in text
    assert "do not establish parsed results" in text
    assert "unreviewed.example" not in text and "PRIVATE" not in text
