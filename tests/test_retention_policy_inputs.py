"""The two retention limits are captured as values, and changing one recomputes nothing.

Section 9 of [the bounded state plan](../docs/plans/bounded-state-and-archive.md)
says the limits are policy values captured in the input bundle. They are read
from an optional `[retention]` table, so the captured file bytes alone cannot
say what the size cap was: an absent table means the defaults. The bundle
therefore carries the effective values as well
([D-0156](../journal/decisions/0156-capture-the-retention-limits-as-values.md)).

The second test is the promise an operator needs before raising the cap: the
limits are operating policy, not interpretation policy, so changing one never
invalidates work and never makes the pipeline recompute the database the cap
defends.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

from swingset.clock import FakeClock
from swingset.config import parse_retention
from swingset.state.db import open_database
from swingset.state.derivations import desired
from swingset.state.inputs import accept, capture
from swingset.state.recipes import recipe_inputs
from swingset.state.work import WorkUnit, affected_work

RAISED_CAP = '\n[retention]\nmax_database_bytes = "12GB"\nrecent_window = 5\n'


def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A writable copy of the repository's config and overrides, plus a state directory."""
    config = tmp_path / "config"
    overrides = tmp_path / "overrides"
    shutil.copytree("config", config)
    shutil.copytree("overrides", overrides)
    return config, overrides, tmp_path / "state"


def raise_the_cap(config: Path) -> None:
    """Exactly what the operation guide tells an operator to do when the cap is hit."""
    sources = config / "sources.toml"
    # The shipped configuration already carries a [retention] table (D-0186);
    # replace it rather than append a second one.
    body = sources.read_text()
    assert "[retention]" in body
    body = body[: body.index("[retention]")] + RAISED_CAP.lstrip("\n")
    sources.write_text(body)


def test_the_bundle_records_the_retention_limits_as_values(tmp_path: Path) -> None:
    config, overrides, state = workspace(tmp_path)
    first = capture(config, overrides, state, {})
    # The shipped [retention] table is the oracle: its cap may differ from the
    # dataclass default between decisions (D-0196), and the bundle must
    # record the shipped values.
    shipped = parse_retention((config / "sources.toml").read_bytes())
    assert json.loads(first.files["policy/retention.json"]) == asdict(shipped)
    assert (first.path / "policy/retention.json").is_file()

    raise_the_cap(config)
    second = capture(config, overrides, state, {})
    captured = json.loads(second.files["policy/retention.json"])
    assert captured["max_database_bytes"] == 12_000_000_000
    assert captured["recent_window"] == 5
    assert second.config.retention.max_database_bytes == 12_000_000_000
    # Two bundles, two digests: the change is recorded, not silent.
    assert second.digest != first.digest


def test_changing_a_retention_limit_recomputes_nothing(tmp_path: Path) -> None:
    """Raising the cap must not recompute the history the cap is there to bound."""
    config, overrides, state = workspace(tmp_path)
    clock = FakeClock()
    unit = WorkUnit("project", "event", "2026-09-example")
    with open_database(state) as database:
        conn = database.connection
        accept(database, capture(config, overrides, state, {}), clock)
        conn.execute("DELETE FROM pending_work")
        before = desired(conn, unit).fingerprint

        raise_the_cap(config)
        changed = accept(database, capture(config, overrides, state, {}), clock)

        assert changed == {"config/sources.toml", "policy/retention.json"}
        assert desired(conn, unit).fingerprint == before
        assert conn.execute("SELECT count(*) FROM pending_work").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT digest FROM accepted_inputs WHERE input_name='policy/retention.json'"
            ).fetchone()
            is not None
        )


def test_a_retention_limit_is_no_stage_recipe_input(tmp_path: Path) -> None:
    """Why nothing is recomputed: no stage selects the limits, and nothing depends on them."""
    config, overrides, state = workspace(tmp_path)
    clock = FakeClock()
    with open_database(state) as database:
        conn = database.connection
        accept(database, capture(config, overrides, state, {}), clock)
        for stage in ("project", "link", "build", "parse"):
            kind = "wsdc_calendar.events" if stage == "parse" else "event"
            assert "policy/retention.json" not in recipe_inputs(conn, stage, kind)
        assert tuple(affected_work(conn, "policy/retention.json")) == ()
