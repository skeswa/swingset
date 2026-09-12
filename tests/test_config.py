from datetime import date
from pathlib import Path

import pytest

from swingset.config import Config, load_config, parse_history_start
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
