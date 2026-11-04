"""The offline origin-candidates tool reports the retired fallback's answer and writes nothing."""

import json
import shutil
from pathlib import Path

import pytest
from helpers.state_digest import state_digest
from test_history_origin_dispatch import queries
from test_platform_backfill import (
    CAPTURES,
    URL,
    accept,
    dispatch,
    failed_snapshot,
    recorder,
    review_contract,
)
from test_platform_backfill import fixture as fixture

from journal.tools.collection import origin_candidates as tool
from swingset.state import engine
from swingset.state.postgres_copy import Copies


def exhausted(f):
    """Three failed distinct captures: the archive offer stops at the gap."""
    accept(f)
    review_contract(f)
    queries(f)
    for index in range(3):
        result = dispatch(f, recorder())
        assert result.capture == CAPTURES[index]
        failed_snapshot(f, result.watch_id, CAPTURES[index], index)
    assert dispatch(f, recorder()).reason == "capture_gap_exhausted"
    assert f.conn.execute("SELECT COUNT(*) FROM history_origin_intents").fetchone()[0] == 0


def copy_state(f, target: Path, request) -> Path:
    shutil.copytree(f.db.state_dir, target, ignore=shutil.ignore_patterns("database.json"))
    copies = Copies(f.db.state_dir)
    copies.__enter__()
    request.addfinalizer(lambda: copies.__exit__(None, None, None))
    copies.freeze(f.conn, target)
    return target


def fingerprint(state: Path) -> str:
    with engine.connect(engine.resolve_dsn(state), read_only=True) as conn:
        return state_digest(conn)


def test_exhausted_page_reports_eligible_origin_and_leaves_the_copy_unchanged(
    fixture, tmp_path, request
):
    exhausted(fixture)
    state = copy_state(fixture, tmp_path / "copy", request)
    before = fingerprint(state)
    files = sorted(path.relative_to(state) for path in state.rglob("*"))
    probe = engine.connect(engine.resolve_dsn(state), read_only=True)
    try:
        version = state_digest(probe)
        with tool.open_copy(state) as database:
            result = tool.report(database, fixture.config, fixture.clock)
        assert state_digest(probe) == version
    finally:
        probe.close()
    assert fingerprint(state) == before
    # No lock file or anything else is created in the copy.
    assert sorted(path.relative_to(state) for path in state.rglob("*")) == files
    assert not (state / "state.intent").exists()
    assert result["fallback_pages"] == 1 and result["reasons"] == {"eligible_origin": 1}
    (page,) = result["pages"]
    assert (page["url"], page["archive_reason"], page["origin_reason"]) == (
        URL,
        "capture_gap_exhausted",
        "eligible_origin",
    )
    assert page["source_has_archive_offer"] is False
    assert page["proposal"]["eligible"] is True
    assert page["proposal"]["reason"] == "archive_alternatives_unusable"
    assert page["proposal"]["attempted_capture_urls"] == [c.archive_url for c in CAPTURES[:3]]
    assert result["intents"] == []

    out = tmp_path / "report.json"
    assert tool.main(["--state", str(state), "--out", str(out)]) == 0
    # The command line judges with the system clock, so only the page is pinned.
    assert [row["url"] for row in json.loads(out.read_text())["pages"]] == [URL]
    assert fingerprint(state) == before
    assert sorted(path.relative_to(state) for path in state.rglob("*")) == files


def test_existing_intent_is_listed(fixture, tmp_path, request):
    exhausted(fixture)
    watch_id = fixture.conn.execute("SELECT watch_id FROM watches").fetchone()[0]
    fixture.conn.execute(
        (
            "INSERT INTO history_origin_intents (watch_id, source, source_ref, event_id, page_kind, url, "
            "parent_url, evidence_json, created_at, dispatch_run_id) VALUES (%s, %s, %s, %s, %s, %s, "
            "NULL, '{}', %s, NULL)"
        ),
        (
            watch_id,
            "eepro",
            "eepro:example2019",
            "2019-01-example",
            "eepro.autoindex",
            URL,
            fixture.clock.now().isoformat(),
        ),
    )
    state = copy_state(fixture, tmp_path / "copy", request)
    with tool.open_copy(state) as database:
        result = tool.report(database, fixture.config, fixture.clock, source="eepro")
    assert [row["watch_id"] for row in result["intents"]] == [watch_id]


def test_held_state_lock_and_production_path_are_refused(fixture, tmp_path, capsys, request):
    # The fixture's own handle holds `state.lock` exclusive.
    with pytest.raises(tool.Refused, match="state.lock"), tool.open_copy(fixture.db.state_dir):
        pass
    assert tool.main(["--state", str(fixture.db.state_dir)]) == 2
    assert "state.lock" in capsys.readouterr().err
    with pytest.raises(tool.Refused, match="production"), tool.open_copy(Path("/var/lib/swingset")):
        pass
    state = copy_state(fixture, tmp_path / "copy", request)
    assert tool.main(["--state", str(state), "--out", str(state / "report.json")]) == 2
    assert not (state / "report.json").exists()
