"""Retained UTF-8 manifests stream by bytes with bounded client memory."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from swingset.state.db import open_database


@pytest.mark.parametrize("size", [64 << 20, pytest.param(256 << 20, marks=pytest.mark.extended)])
def test_large_manifest_byte_windows_stay_bounded(tmp_path, size):
    state = tmp_path / "state"
    with open_database(state) as database:
        database.connection.execute(
            "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES('large-value-test',repeat('0123456789abcdef',%s))",
            (size // 16,),
        )
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "helpers/large_values_client.py"),
            str(state),
            "15",
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    Path(f"/tmp/swingset-large-values-{size}.json").write_text(
        json.dumps(measured, indent=2) + "\n"
    )
    assert measured["complete"], measured
    expected = hashlib.sha256()
    for _ in range(size // 65536):
        expected.update(b"0123456789abcdef" * 4096)
    assert measured["sha256"] == expected.hexdigest()
    assert measured["peak_rss_bytes"] < 128 << 20, measured
    assert measured["seconds"] < 15, measured


def test_utf8_windows_can_split_characters_without_changing_stored_bytes(tmp_path):
    from swingset.state.large_values import column_reader

    text = '雪"\\n"\n\\u03b1' * 65536
    expected = text.encode()
    with open_database(tmp_path / "state") as database:
        row = database.connection.execute(
            "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES('unicode-window',%s) RETURNING rowid",
            (text,),
        ).fetchone()
        with column_reader(
            database.connection, "derivation_dependency_sets", "manifest_json", row[0]
        ) as value:
            assert len(value) == len(expected)
            assert value.read(1) == expected[:1]
            assert value.read(2) == expected[1:3]
            value.seek(0)
            assert b"".join(iter(lambda: value.read(65536), b"")) == expected
            value.seek(-7, 2)
            assert value.read() == expected[-7:]
