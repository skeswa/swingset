from datetime import date
from pathlib import Path

import pytest

from swingset.config import (
    Config,
    DerivationConfig,
    HostConfig,
    StageConfig,
    load_config,
    parse_derivation,
    parse_history_start,
    parse_hosts,
    parse_retention,
    parse_stages,
)
from swingset.model.history import HISTORY_START, in_history


def test_history_start_is_enshrined_as_2010_01_01() -> None:
    assert HISTORY_START == date(2010, 1, 1)
    assert Config({}, {}).history_start == date(2010, 1, 1)
    assert load_config(Path("config")).history_start == date(2010, 1, 1)


def test_history_start_defaults_when_absent() -> None:
    assert parse_history_start(b"[sources.wdr]\nenabled = true\n") == HISTORY_START


def test_history_start_reads_toml_date_or_iso_string() -> None:
    assert parse_history_start(b"history_start = 2010-01-01\n") == date(2010, 1, 1)
    assert parse_history_start(b'history_start = "2005-06-01"\n') == date(2005, 6, 1)


@pytest.mark.parametrize(
    "body",
    [
        b'history_start = "2010"\n',
        b"history_start = 2010-01-01T00:00:00Z\n",
        b"history_start = 2010\n",
        b"history_start = true\n",
    ],
)
def test_history_start_rejects_non_dates(body: bytes) -> None:
    with pytest.raises(ValueError, match="history_start"):
        parse_history_start(body)


def test_in_history_uses_end_date_then_start_date_then_year() -> None:
    start = date(2010, 1, 1)
    assert in_history(start, end_date=date(2010, 1, 1))
    assert not in_history(start, end_date=date(2009, 12, 31))
    assert in_history(start, end_date="2010-01-01", start_date="2009-12-30")
    assert not in_history(start, end_date="2009-12-31T00:00:00")
    assert in_history(start, start_date="2010-03-05")
    assert not in_history(start, start_date=date(2009, 3, 5))
    assert in_history(start, year=2010)
    assert not in_history(start, year=2009)
    assert in_history(start)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"[retention]\ncollect_older_than = 0\n", "collect_older_than"),
        (b"[retention]\ncollect_older_than = -1\n", "negative"),
        (b'[retention]\ncollect_older_than = "0s"\n', "collect_older_than"),
        (b"[retention]\ncheckpoint_max_age = -1\n", "negative"),
        (b"[retention]\ncheckpoint_incomplete_max_age = -1\n", "negative"),
    ],
)
def test_retention_refuses_an_age_floor_that_protects_nothing(body: bytes, message: str) -> None:
    """The collector's age floor is the only thing a build in flight has (D-0143).

    Zero would let an apply remove a candidate directory the moment it appeared,
    so zero is not a setting. Only `max_database_bytes` and `recent_window` were
    checked before, so every age parsed unvalidated.
    """
    with pytest.raises(ValueError, match=message):
        parse_retention(body)


def test_retention_accepts_the_smallest_age_floor_that_still_protects() -> None:
    assert parse_retention(b'[retention]\ncollect_older_than = "1s"\n').collect_older_than == 1
    assert parse_retention(b"[retention]\ncheckpoint_max_age = 0\n").checkpoint_max_age == 0


def test_production_retention_keeps_one_local_checkpoint() -> None:
    """Local-only backups keep one timer checkpoint until phase 4 (D-0203)."""
    config = Path(__file__).resolve().parents[1] / "config"
    retention = load_config(config).retention
    assert retention.checkpoint_keep_recent == 1
    assert retention.checkpoint_max_age == 0
    assert retention == parse_retention((config / "sources.toml").read_bytes())


@pytest.mark.parametrize("host", ["scoring.dance", "points.worldsdc.com", "example.test"])
def test_hosts_accept_two_seconds_and_reject_less(host: str) -> None:
    """Every host shares the two-second floor (D-0247)."""
    body = f'[hosts."{host}"]\nmin_gap_seconds = 2\n'.encode()
    assert parse_hosts(body)[host].min_gap_seconds == 2
    with pytest.raises(ValueError, match="unsafe host limits"):
        parse_hosts(body.replace(b"= 2", b"= 1.9"))


def test_production_hosts_space_wcs_sites_at_two_seconds_and_the_archive_at_ten() -> None:
    hosts = parse_hosts((Path(__file__).resolve().parents[1] / "config/hosts.toml").read_bytes())
    gaps = {name: host.min_gap_seconds for name, host in hosts.items()}
    assert gaps.pop("web.archive.org") == 10
    assert set(gaps.values()) == {2}
    assert HostConfig().min_gap_seconds == 2


STAGES = (
    "event_pressure",
    "event_progress_refresh",
    "event_blocker_observations",
)


def test_stages_default_on_when_the_table_is_absent() -> None:
    stages = parse_stages(b"[sources.wdr]\nenabled = true\n")
    assert stages == StageConfig() == Config({}, {}).stages
    assert all(getattr(stages, name) for name in STAGES)
    assert stages.disabled() == []
    assert parse_stages(b"[stages]\n") == StageConfig()


@pytest.mark.parametrize("name", STAGES)
def test_each_stage_switch_parses(name: str) -> None:
    stages = parse_stages(f"[stages]\n{name} = false\n".encode())
    assert not getattr(stages, name)
    assert all(getattr(stages, other) for other in STAGES if other != name)
    assert stages.disabled() == [name]
    assert parse_stages(f"[stages]\n{name} = true\n".encode()) == StageConfig()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"[stages]\nevent_timing = false\n", "unknown stages"),
        (b"[stages]\nevent_pressure = 0\n", "true or false"),
        (b'[stages]\nevent_progress_refresh = "false"\n', "true or false"),
        # The event-site review left the loops for an offline tool (phase 5).
        (b"[stages]\nevent_site_review = false\n", "unknown stages"),
        (b"stages = false\n", "table"),
    ],
)
def test_stages_reject_unknown_keys_and_non_bools(body: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_stages(body)


def test_load_config_reads_stages(tmp_path: Path) -> None:
    config = Path(__file__).resolve().parents[1] / "config"
    # Production turns all three bookkeeping stages off (D-0204).
    assert load_config(config).stages.disabled() == sorted(STAGES)
    copy = tmp_path / "config"
    copy.mkdir()
    (copy / "hosts.toml").write_bytes((config / "hosts.toml").read_bytes())
    (copy / "sources.toml").write_bytes(b"[stages]\nevent_pressure = false\n")
    assert load_config(copy).stages.disabled() == ["event_pressure"]


def test_derivation_cutoff_defaults_on_and_can_be_turned_off(tmp_path: Path) -> None:
    assert parse_derivation(b"") == DerivationConfig() == Config({}, {}).derivation
    assert DerivationConfig().cutoff
    assert not parse_derivation(b"[derivation]\ncutoff = false\n").cutoff
    for body, message in (
        (b"[derivation]\ncutoff = 0\n", "true or false"),
        (b"[derivation]\nreplay = false\n", "unknown derivation"),
        (b"derivation = false\n", "table"),
    ):
        with pytest.raises(ValueError, match=message):
            parse_derivation(body)
    config = Path(__file__).resolve().parents[1] / "config"
    # Production runs with cutoff on and paces the inventory (D-0205, D-0210).
    loaded = load_config(config)
    assert loaded.derivation.cutoff
    # 12 hours (D-0210); D-0227's 48 ended with the phase 3 switch.
    assert loaded.scheduler.inventory_interval_seconds == 43200
    assert loaded.scheduler.history_interval_seconds is None


def test_the_streamed_build_keeps_the_heavy_lock_until_turned_off() -> None:
    from swingset.config import ReleaseConfig, parse_release

    assert parse_release(b"") == ReleaseConfig() == Config({}, {}).release
    assert ReleaseConfig().streamed_lock and ReleaseConfig().path == "partitioned"
    assert not parse_release(b"[release]\nstreamed_lock = false\n").streamed_lock
    with pytest.raises(ValueError, match="true or false"):
        parse_release(b"[release]\nstreamed_lock = 1\n")
    # Production keeps it on until the worker measures the build (D-0224).
    loaded = load_config(Path(__file__).resolve().parents[1] / "config")
    assert loaded.release.streamed_lock and loaded.release.path == "partitioned"


def test_derive_one_applies_the_bundles_cutoff_switch(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace

    import swingset.project
    from swingset import cutoff
    from swingset.clock import FakeClock
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one
    from swingset.state.db import open_database
    from swingset.state.inputs import InputBundle
    from swingset.state.work import WorkUnit, enqueue

    seen = []

    def project(*_args, selection=None):
        seen.append(cutoff.enabled())
        raise RuntimeError("stop after reading the switch")

    monkeypatch.setattr(swingset.project, "process_unit", project)
    clock = FakeClock()
    on = InputBundle("test", tmp_path, {}, Config({}, {}))
    off = replace(on, config=replace(on.config, derivation=DerivationConfig(cutoff=False)))
    with open_database(tmp_path / "state") as db:
        run = db.start_run(clock.now())
        for index, bundle in enumerate((off, on)):
            unit = WorkUnit("project", "dancer", str(index))
            enqueue(db.connection, [unit], enqueued_at=clock.now().isoformat())
            derive_one(db, Archive(tmp_path), unit, bundle, clock, run)
    assert seen == [False, True]
    assert cutoff.enabled()


def test_series_alias_rows_may_carry_a_month_range(tmp_path):
    import pytest

    from swingset.state.override_validation import validate_override

    path = tmp_path / "series_aliases.csv"
    good = (
        "printed_name,series_id,source,note,from_month,to_month\n"
        "Name,wsdc-1,calendar,,,\n"
        "Name,wsdc-2,calendar,,2014-01,2017-12\n"
    )
    validate_override(path, good.encode())
    with pytest.raises(ValueError, match="yyyy-mm"):
        validate_override(path, good.replace("2014-01", "2014").encode())
    with pytest.raises(ValueError, match="after"):
        validate_override(path, good.replace("2014-01", "2018-01").encode())
    with pytest.raises(ValueError, match="duplicate"):
        validate_override(path, (good + "Name,wsdc-3,calendar,,2014-01,2015-12\n").encode())


def test_finding_reviews_override_accepts_only_dismissals(tmp_path):
    import pytest

    from swingset.state.override_validation import validate_override

    path = tmp_path / "finding_reviews.csv"
    validate_override(
        path, b"kind,subject_id,decision,reviewer,note\nevent_alias,x,dismiss,claude,why\n"
    )
    with pytest.raises(ValueError, match="dismiss"):
        validate_override(
            path, b"kind,subject_id,decision,reviewer,note\nevent_alias,x,accept,claude,why\n"
        )
    with pytest.raises(ValueError, match="duplicate"):
        validate_override(
            path,
            b"kind,subject_id,decision,reviewer,note\nevent_alias,x,dismiss,a,\nevent_alias,x,dismiss,b,\n",
        )
