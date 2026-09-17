"""Bounded observations cannot convert incomplete verification into completion."""

import gzip
from unittest.mock import Mock

import pytest
from test_event_enumerations import admit_parent, child, finish_bootstrap
from test_event_enumerations import event as event

from swingset.fetch.archive import Archive
from swingset.schedule.event_pressure_probe import Limits, probe


def check(f, **kwargs):
    enumeration = f.conn.execute(
        "SELECT enumeration_id FROM source_event_inventory WHERE source_ref='eepro:test'"
    ).fetchone()[0]
    return probe(f.conn, f.archive, enumeration_id=enumeration, now=f.corpus.clock.now(), **kwargs)


def test_admitted_pages_and_aliases_are_read_only(event, monkeypatch):
    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    child(f, "one.htm")
    finish_bootstrap(f)
    before = f.conn.total_changes
    monkeypatch.setattr(Archive, "read_body", Mock(side_effect=AssertionError("whole body read")))
    monkeypatch.setattr(
        Archive, "read_extract", Mock(side_effect=AssertionError("whole extract read"))
    )
    result = check(f)
    assert result["parent_valid"] is True
    assert (result["checked"], result["acquired"], result["interpreted"], result["unknown"]) == (
        2,
        1,
        1,
        0,
    )
    assert result["end_of_enumeration"] and result["pagination"] == "unknown"
    assert f.conn.total_changes == before
    f.conn.execute(
        "INSERT INTO source_event_map VALUES ('eepro','eepro:test','different-alias','alias',1)"
    )
    assert check(f) == result


def test_batches_advance_without_rechecking_finished_members(event):
    f = event
    admit_parent(f, [f"{i}.htm" for i in range(7)])
    finish_bootstrap(f)
    cursor = None
    totals = 0
    for i in range(4):
        result = check(f, after_request_id=cursor, limits=Limits(members=2))
        assert result["next_cursor"] > (cursor or "")
        cursor = result["next_cursor"]
        totals += result["checked"]
        assert result["end_of_enumeration"] is (i == 3)
    assert totals == 7
    last = check(f, after_request_id=cursor)
    assert last["checked"] == 0 and last["end_of_enumeration"]


@pytest.mark.parametrize(
    "limit",
    [
        Limits(json_bytes=20),
        Limits(rows=1),
        Limits(decoded_bytes=1),
        Limits(compressed_bytes=1),
        Limits(total_json_bytes=1),
    ],
)
def test_exhaustion_keeps_unknown_and_advances_even_when_parent_unknown(event, limit):
    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    child(f, "one.htm")
    finish_bootstrap(f)
    result = check(f, limits=limit)
    assert result["parent_valid"] is None
    assert result["checked"] == 2 and 1 <= result["unknown"] <= 2
    assert result["next_cursor"] and result["end_of_enumeration"]
    assert result["reasons"]


def test_missing_artifact_and_related_revocation_never_clear_pressure(event):
    f = event
    parent = admit_parent(f, ["one.htm"])
    context, gen = child(f, "one.htm")
    finish_bootstrap(f)
    assert check(f)["interpreted"] == 1
    f.archive.blob_path(
        f.conn.execute(
            "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?", (context.snapshot_id,)
        ).fetchone()[0]
    ).write_bytes(gzip.compress(b"incorrect"))
    damaged = check(f)
    assert damaged["acquired"] == damaged["interpreted"] == 0
    assert damaged["reasons"]["body_artifact_unavailable"]
    f.conn.execute("UPDATE source_generations SET state='revoked' WHERE generation_id=?", (parent,))
    assert check(f)["parent_valid"] is False


def test_generation_json_is_capped_before_decoder(event, monkeypatch):
    f = event
    parent = admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    f.conn.execute("DROP TRIGGER source_generation_immutable")
    f.conn.execute(
        "UPDATE source_generations SET result_json=? WHERE generation_id=?", ("x" * 10000, parent)
    )
    import swingset.schedule.event_pressure_probe as module

    decoder = Mock(side_effect=AssertionError("oversized generation decoded"))
    monkeypatch.setattr(module, "decode_generation", decoder)
    result = check(f, limits=Limits(json_bytes=4000))
    assert result["parent_valid"] is None
    assert result["reasons"]["json_byte_budget"]
    decoder.assert_not_called()


def test_candidate_history_cap_is_unknown_not_missing(event):
    f = event
    admit_parent(f, ["one.htm"])
    child(f, "one.htm")
    snapshot = dict(
        f.conn.execute("SELECT * FROM snapshots WHERE snapshot_id='child-one.htm'").fetchone()
    )
    snapshot["snapshot_id"] = "second-child-response"
    f.conn.execute(
        f"INSERT INTO snapshots ({','.join(snapshot)}) VALUES ({','.join('?' for _ in snapshot)})",
        tuple(snapshot.values()),
    )
    finish_bootstrap(f)
    result = check(f, limits=Limits(candidates=1))
    assert result["unknown"] == 1 and result["reasons"]["candidate_budget"]


def test_recovery_is_rejected_before_any_access(event):
    f = event
    recovery = Mock()
    with pytest.raises(ValueError, match="without recovery"):
        probe(
            f.conn,
            Archive(f.archive.state_dir, recovery=recovery),
            enumeration_id="x",
            now=f.corpus.clock.now(),
        )
    recovery.recover.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"members": 0},
        {"members": 33},
        {"rows": -1},
        {"seconds": float("inf")},
        {"seconds": True},
        {"json_bytes": 1 << 30},
    ],
)
def test_limits_are_positive_and_bounded(kwargs):
    with pytest.raises(ValueError):
        Limits(**kwargs)


def test_cooperative_deadline_keeps_cursor_and_unknown(event, monkeypatch):
    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    finish_bootstrap(f)
    import swingset.schedule.event_pressure_probe as module

    ticks = iter([0.0, 3.0, 4.0, 5.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    result = check(f)
    assert result["parent_valid"] is None and result["unknown"] == 2
    assert result["checked"] == 2 and result["end_of_enumeration"]
    assert result["reasons"]["time_budget"] == 3


def test_old_schema_and_readonly_settings_are_untouched(event, tmp_path):
    import sqlite3

    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    f.conn.execute("PRAGMA query_only=ON")
    before = f.conn.total_changes
    length_limit = f.conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    result = check(f)
    assert result["parent_valid"] is True
    assert f.conn.total_changes == before
    assert f.conn.execute("PRAGMA query_only").fetchone()[0] == 1
    assert f.conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == length_limit
    with sqlite3.connect(":memory:") as legacy:
        result = probe(
            legacy, Archive(tmp_path), enumeration_id="missing", now=f.corpus.clock.now()
        )
        assert not result["supported"] and result["parent_valid"] is None
        assert legacy.total_changes == 0


def test_direct_result_and_archive_redirect_identity(event):
    from test_admission import BODY

    f = event
    context = f.corpus.snapshot("direct-result", BODY)
    generation, _ = f.corpus.stage(context)
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    f.conn.execute(
        "UPDATE snapshots SET via='wayback',archive_url='https://web.archive.org/web/20200101000000id_/https://example.org/redirected' WHERE snapshot_id=?",
        (context.snapshot_id,),
    )
    finish_bootstrap(f)
    enumeration = f.conn.execute(
        "SELECT enumeration_id FROM source_event_inventory WHERE enumeration_id IS NOT NULL"
    ).fetchone()[0]
    result = probe(f.conn, f.archive, enumeration_id=enumeration, now=f.corpus.clock.now())
    assert result["parent_valid"] is True
    assert (result["checked"], result["acquired"], result["interpreted"], result["unknown"]) == (
        1,
        1,
        1,
        0,
    )


def test_related_revoked_copy_is_not_usable(event):
    f = event
    parent = admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    row = dict(
        f.conn.execute(
            "SELECT * FROM source_generations WHERE generation_id=?", (parent,)
        ).fetchone()
    )
    row.update(generation_id="revoked-evidence-copy", state="revoked")
    f.conn.execute(
        f"INSERT INTO source_generations ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
        tuple(row.values()),
    )
    result = check(f)
    assert result["parent_valid"] is False and result["reasons"]["source_evidence_revoked"]


@pytest.mark.parametrize("damage", ["truncated", "wrong_digest", "invalid_extract", "result_blob"])
def test_parent_corruption_is_explicit_and_never_healthy(event, damage):
    f = event
    parent = admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    import json

    row = f.conn.execute(
        "SELECT manifest_json FROM source_generations WHERE generation_id=?", (parent,)
    ).fetchone()
    member = json.loads(row[0])[0]
    if damage == "truncated":
        path = f.archive.blob_path(member["body_sha256"])
        path.write_bytes(path.read_bytes()[:-4])
    elif damage == "wrong_digest":
        f.archive.blob_path(member["body_sha256"]).write_bytes(gzip.compress(b"wrong"))
    elif damage == "invalid_extract":
        f.archive.extract_path(member["extract_sha256"]).write_bytes(b"{broken")
    else:
        f.conn.execute("DROP TRIGGER source_generation_immutable")
        f.conn.execute(
            "UPDATE source_generations SET result_json=? WHERE generation_id=?", (b"{}", parent)
        )
    result = check(f)
    assert result["parent_valid"] is False
    assert result["reasons"]


def test_many_revocations_are_unknown_even_when_first_one_is_unrelated(event):
    f = event
    parent = admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    row = dict(
        f.conn.execute(
            "SELECT * FROM source_generations WHERE generation_id=?", (parent,)
        ).fetchone()
    )
    for i in range(3):
        row.update(generation_id=f"copy-{i}", state="revoked")
        f.conn.execute(
            f"INSERT INTO source_generations ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
            tuple(row.values()),
        )
    result = check(f, limits=Limits(candidates=2))
    assert result["parent_valid"] is None and result["reasons"]["candidate_budget"]


def test_policy_change_reopens_parent(event):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    f.conn.execute(
        "UPDATE admission_policies SET contract_version='changed' WHERE page_kind='eepro.autoindex'"
    )
    result = check(f)
    assert result["parent_valid"] is False and result["reasons"]["admission_policy_changed"]


def test_bounded_reads_include_gzip_members_and_extract_json(event, monkeypatch):
    from pathlib import Path

    f = event
    parent = admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    import json

    row = f.conn.execute(
        "SELECT manifest_json FROM source_generations WHERE generation_id=?", (parent,)
    ).fetchone()
    member = json.loads(row[0])[0]
    path = f.archive.blob_path(member["body_sha256"])
    body = gzip.decompress(path.read_bytes())
    path.write_bytes(gzip.compress(body[:7]) + gzip.compress(body[7:]))
    original = Path.open
    calls = []

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            assert 0 < size <= 65536
            chunk = self.stream.read(size)
            calls.append(len(chunk))
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

    def opened(path, mode="r", *args, **kwargs):
        stream = original(path, mode, *args, **kwargs)
        return Reader(stream) if mode == "rb" else stream

    monkeypatch.setattr(Path, "open", opened)
    assert check(f)["parent_valid"] is True
    calls.clear()
    capped = check(f, limits=Limits(compressed_bytes=10))
    assert capped["parent_valid"] is None
    assert sum(calls) <= 10


def test_gzip_expansion_stops_at_total_decoded_limit(event, monkeypatch):
    from pathlib import Path

    f = event
    parent = admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    import json

    row = f.conn.execute(
        "SELECT manifest_json FROM source_generations WHERE generation_id=?", (parent,)
    ).fetchone()
    member = json.loads(row[0])[0]
    # Deliberately wrong digest, but it must stop as unknown BEFORE consuming
    # this entire highly compressed body merely to discover the mismatch.
    f.archive.blob_path(member["body_sha256"]).write_bytes(gzip.compress(b"x" * (4 * 1024 * 1024)))
    original = gzip.GzipFile.read
    decoded = []

    def read(stream, size=-1):
        assert 0 < size <= 65536
        chunk = original(stream, size)
        decoded.append(len(chunk))
        return chunk

    monkeypatch.setattr(gzip.GzipFile, "read", read)
    monkeypatch.setattr(Path, "read_bytes", Mock(side_effect=AssertionError("unbounded read")))
    result = check(f, limits=Limits(decoded_bytes=10000))
    assert result["parent_valid"] is None and result["reasons"]["decoded_byte_budget"]
    assert sum(decoded) == 10000


def test_oversized_member_and_unsupported_member_are_unknown_and_do_not_trap_cursor(event):
    f = event
    admit_parent(f, ["one.htm", "two.htm", "unsupported.pdf"])
    finish_bootstrap(f)
    f.conn.execute("DROP TRIGGER event_enumeration_member_no_update")
    key = f.conn.execute(
        "SELECT request_id FROM source_event_enumeration_members ORDER BY request_id LIMIT 1"
    ).fetchone()[0]
    f.conn.execute(
        "UPDATE source_event_enumeration_members SET request_json=? WHERE request_id=?",
        ("x" * 10000, key),
    )
    result = check(f, limits=Limits(json_bytes=8000))
    assert result["checked"] == 3 and result["unknown"] >= 1
    assert result["end_of_enumeration"] and result["next_cursor"] >= key
    assert result["reasons"]["json_byte_budget"]


def test_manifest_member_limit_applies_to_content_valid_admitted_generation(event):
    import json

    from swingset.fetch.archive import canonical, digest

    f = event
    old = admit_parent(f, ["one.htm"])
    # Retain a second immutable recipe with two references to the same exact
    # support. Its content address and accepted decision are real, independent
    # of the probe's configurable traversal limit.
    row = dict(
        f.conn.execute("SELECT * FROM source_generations WHERE generation_id=?", (old,)).fetchone()
    )
    manifest = json.loads(row["manifest_json"]) * 2
    row["manifest_json"] = canonical(manifest).decode()
    row["input_fingerprint"] = digest(
        canonical({"manifest": manifest, "recipe": json.loads(row["recipe_json"])})
    )
    row["work_token"] = "two-retained-support-slots"
    row["generation_id"] = "gen_" + digest(
        canonical(
            {
                "unit": row["unit_key"],
                "fingerprint": row["input_fingerprint"],
                "report": json.loads(row["report_json"]),
                "result": row["result_json"],
                "previous": row["previous_generation_id"],
                "work_token": row["work_token"],
            }
        )
    )
    f.conn.execute(
        f"INSERT INTO source_generations ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
        tuple(row.values()),
    )
    decision = dict(
        f.conn.execute("SELECT * FROM admission_decisions WHERE generation_id=?", (old,)).fetchone()
    )
    del decision["decision_id"]
    decision["generation_id"] = row["generation_id"]
    f.conn.execute(
        f"INSERT INTO admission_decisions ({','.join(decision)}) VALUES ({','.join('?' for _ in decision)})",
        tuple(decision.values()),
    )
    finish_bootstrap(f)
    assert check(f)["parent_valid"] is True
    result = check(f, limits=Limits(manifest_members=1))
    assert result["parent_valid"] is None
    assert result["reasons"]["manifest_member_budget"]
    assert result["checked"] == 1 and result["end_of_enumeration"]
