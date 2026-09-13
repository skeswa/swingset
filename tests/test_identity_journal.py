import json
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import pytest
from test_project_event import EVENT, add, seed, sheet

from swingset.clock import FakeClock
from swingset.config import Config
from swingset.project.contests import project_event
from swingset.project.writer import replace_scope
from swingset.state.db import open_database
from swingset.state.identity_journal import (
    Decision,
    accept_journal,
    active_decisions,
    convert_legacy,
    encode_journal,
    parse_journal,
    token,
)
from swingset.state.identity_references import (
    record_migration,
    references_for_subject,
    retain_binding,
)
from swingset.state.inputs import InputBundle, accept

NOW = "2026-09-13T06:00:00+00:00"


def prepared(conn):
    seed(conn)
    add(conn, "watch", "snapshot", sheet("prelim", name="Alex Lee"), "2026-09-08T00:00:00Z")
    replace_scope(
        conn,
        scope_kind="event",
        scope_id=EVENT,
        projection=project_event(conn, EVENT, NOW, "run_a"),
        run_id="run_a",
        projected_at=NOW,
    )
    identifier = conn.execute("SELECT entry_id FROM entries").fetchone()[0]
    bindings = references_for_subject(conn, "entry", identifier)
    assert len(bindings) == 1
    return identifier, bindings[0]


def decision(binding, kind="different_person", number="100", identifier="review-1", supersedes=""):
    return Decision(
        identifier,
        **asdict(binding.reference),
        wsdc_id=number,
        decision=kind,
        evidence="fixture:reviewed-source",
        reason="Reviewed identity",
        author="Reviewer",
        date="2026-09-13",
        supersedes=supersedes,
    )


def bundle(body):
    return InputBundle(
        "bundle",
        Path("unused"),
        MappingProxyType({"overrides/identity_overrides.csv": body, "versions.json": b"{}"}),
        Config({}, {}),
    )


def test_legacy_conversion_preserves_positive_none_and_unmapped_provenance(tmp_path):
    with open_database(tmp_path) as db:
        identifier, binding = prepared(db.connection)
        body = f"entry_id,wsdc_id,reason,author,date\n{identifier},100,Prior reviewed match,Owner,2026-09-01\nmissing-entry,NONE,Keep withheld,Owner,2026-09-02\n".encode()
        converted = convert_legacy(db.connection, body)
        rows = parse_journal(converted)
        assert rows[0].decision == "same_person" and rows[0].reference == binding.reference
        assert (
            rows[1].decision == "insufficient_evidence"
            and rows[1].participant == "unmapped-entry:missing-entry"
        )
        assert json.loads(rows[0].evidence)["legacy_row"]["entry_id"] == identifier
        assert json.loads(rows[0].evidence)["references"][0]["snapshot"] == "snapshot"
        assert convert_legacy(db.connection, body) == converted
        assert convert_legacy(db.connection, converted) == converted
        held = parse_journal(convert_legacy(db.connection, body.replace(b",100,", b",NONE,")))[0]
        assert held.decision == "hold_unlinked"
        assert db.connection.execute("SELECT count(*) FROM identity_decisions").fetchone()[0] == 0


def test_ambiguous_conversion_never_creates_a_positive(tmp_path):
    with open_database(tmp_path) as db:
        identifier, binding = prepared(db.connection)
        other = replace(binding, reference=replace(binding.reference, round="other"))
        body = f"entry_id,wsdc_id,reason,author,date\n{identifier},100,Prior match,Owner,2026-09-01\n".encode()
        with patch(
            "swingset.state.identity_journal.references_for_subject", return_value=(binding, other)
        ):
            converted = parse_journal(convert_legacy(db.connection, body))[0]
        assert converted.decision == "insufficient_evidence"
        assert json.loads(converted.evidence)["mapping"] == "ambiguous"
        assert len(json.loads(converted.evidence)["references"]) == 2


def test_acceptance_rejects_removed_or_altered_history_and_requires_explicit_supersession(tmp_path):
    with open_database(tmp_path) as db:
        _, binding = prepared(db.connection)
        first = decision(binding)
        clock = FakeClock(datetime(2026, 9, 13, tzinfo=UTC))
        accept(db, bundle(encode_journal((first,))), clock)
        accepted = token(db.connection)
        for body in (encode_journal(()), encode_journal((replace(first, reason="edited"),))):
            with pytest.raises(ValueError, match="removes or alters"):
                accept(db, bundle(body), clock)
            assert token(db.connection) == accepted
        replacement = decision(
            binding, "same_person", identifier="review-2", supersedes=first.decision_id
        )
        accept(db, bundle(encode_journal((first, replacement))), clock)
        assert active_decisions(db.connection) == (replacement,)
        assert db.connection.execute("SELECT count(*) FROM identity_decisions").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.connection.execute("DELETE FROM identity_decisions")


def test_crash_during_acceptance_rolls_back_digest_decisions_and_invalidation(tmp_path):
    with open_database(tmp_path) as db:
        _, binding = prepared(db.connection)
        body = encode_journal((decision(binding),))
        db.connection.execute("DELETE FROM pending_work")
        initial = token(db.connection)
        clock = FakeClock(datetime(2026, 9, 13, tzinfo=UTC))
        with patch("swingset.state.identity_journal.enqueue", side_effect=RuntimeError("crash")):
            with pytest.raises(RuntimeError, match="crash"):
                accept(db, bundle(body), clock)
        assert token(db.connection) == initial
        for table in (
            "identity_decisions",
            "identity_journal_acceptances",
            "pending_work",
            "accepted_inputs",
        ):
            assert db.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        accept(db, bundle(body), clock)
        accepted = token(db.connection)
        assert (
            db.connection.execute("SELECT unit_id FROM pending_work WHERE stage='link'").fetchone()[
                0
            ]
            == EVENT
        )
        accept(db, bundle(body), clock)
        assert token(db.connection) == accepted
        assert (
            db.connection.execute("SELECT count(*) FROM identity_journal_acceptances").fetchone()[0]
            == 1
        )


def test_sqlite_recovery_retains_journal_provenance_migrations_and_unaccepted_digest(tmp_path):
    state = tmp_path / "state"
    with open_database(state) as db:
        _, binding = prepared(db.connection)
        first = decision(binding)
        body = encode_journal((first,))
        with db.transaction() as conn:
            accept_journal(conn, body, bundle_digest="accepted-bundle", now=NOW)
            retain_binding(conn, binding, now=NOW)
            target = replace(binding, reference=replace(binding.reference, contest="renumbered"))
            retain_binding(conn, target, now=NOW)
            record_migration(
                conn,
                migration_id="migration-1",
                from_ref_id=binding.reference.ref_id,
                to_ref_ids=(target.reference.ref_id,),
                status="approved",
                evidence="fixture:reviewed-renumbering",
                reason="Source renumbered",
                author="Reviewer",
                date="2026-09-13",
                now=NOW,
            )
        unaccepted = state / "inputs" / "unaccepted" / "overrides"
        unaccepted.mkdir(parents=True)
        (unaccepted / "identity_overrides.csv").write_bytes(
            encode_journal((first, replace(first, decision_id="not-accepted")))
        )
        expected = {
            name: [tuple(r) for r in db.connection.execute(f"SELECT * FROM {name}")]
            for name in (
                "identity_decisions",
                "identity_journal_acceptances",
                "identity_reference_migrations",
                "identity_reference_bindings",
            )
        }
        original_token = token(db.connection)
        with sqlite3.connect(tmp_path / "restored.sqlite") as destination:
            db.connection.backup(destination)
    for _ in range(2):
        with open_database(tmp_path / "restored.sqlite") as restored:
            assert token(restored.connection) == original_token
            for name, rows in expected.items():
                assert [
                    tuple(r) for r in restored.connection.execute(f"SELECT * FROM {name}")
                ] == rows
            assert [r.decision_id for r in active_decisions(restored.connection)] == ["review-1"]


def test_checkpoint_restore_preserves_accepted_journal_and_excludes_unaccepted_capture(tmp_path):
    from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint
    from swingset.fetch.archive import Archive
    from swingset.state.db import SCHEMA_VERSION
    from swingset.state.inputs import capture

    state = tmp_path / "source"
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    clock = FakeClock(datetime(2026, 9, 13, tzinfo=UTC))
    with open_database(state) as db:
        _, binding = prepared(db.connection)
        first = decision(binding)
        (overrides / "identity_overrides.csv").write_bytes(encode_journal((first,)))
        captured = capture(Path("config"), overrides, state, {"linker": "9"})
        accept(db, captured, clock)
        archive = Archive(state)
        body_sha = archive.store_body(b"<html>Explicit synthetic retained source evidence</html>")
        extract_sha = archive.store_extract({"fixture": "source extract"})
        db.connection.execute(
            "UPDATE snapshots SET body_sha256=?,extract_sha256=?", (body_sha, extract_sha)
        )
        target = replace(binding, reference=replace(binding.reference, contest="new-round"))
        with db.transaction() as conn:
            retain_binding(conn, binding, now=NOW)
            retain_binding(conn, target, now=NOW)
            record_migration(
                conn,
                migration_id="move",
                from_ref_id=binding.reference.ref_id,
                to_ref_ids=(target.reference.ref_id,),
                status="approved",
                evidence="fixture:reviewed-continuity",
                reason="Source renumbered",
                author="Reviewer",
                date="2026-09-13",
                now=NOW,
            )
        accepted_token = token(db.connection)
        expected = {
            table: [tuple(r) for r in db.connection.execute(f"SELECT * FROM {table}")]
            for table in (
                "identity_decisions",
                "identity_journal_acceptances",
                "identity_reference_bindings",
                "identity_reference_migrations",
                "accepted_inputs",
                "pending_work",
            )
        }
        (overrides / "identity_overrides.csv").write_bytes(
            encode_journal(
                (first, decision(binding, "same_person", "100", "unaccepted", first.decision_id))
            )
        )
        unaccepted = capture(Path("config"), overrides, state, {"linker": "9"})
        assert unaccepted.digest != captured.digest
        checkpoint = create_checkpoint(
            state,
            db.connection,
            tmp_path / "checkpoint",
            schema_version=SCHEMA_VERSION,
            versions={"linker": "9"},
            input_bundle_hash=captured.digest,
        )
        assert f"inputs/{captured.digest}/overrides/identity_overrides.csv" in checkpoint.files
        assert not any(path.startswith(f"inputs/{unaccepted.digest}/") for path in checkpoint.files)
    for attempt in range(2):
        restored = tmp_path / f"restored-{attempt}"
        restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
        assert (restored / "RESTORE_PENDING").is_file()
        assert (
            Archive(restored).read_body(body_sha)
            == b"<html>Explicit synthetic retained source evidence</html>"
        )
        assert Archive(restored).read_extract(extract_sha) == {"fixture": "source extract"}
        with open_database(restored, allow_restore_pending=True) as db:
            assert token(db.connection) == accepted_token
            for table, rows in expected.items():
                assert [tuple(r) for r in db.connection.execute(f"SELECT * FROM {table}")] == rows
            accept(db, replace(captured, path=restored / "inputs" / captured.digest), clock)
            assert token(db.connection) == accepted_token
            assert [d.decision_id for d in active_decisions(db.connection)] == ["review-1"]
            assert not (restored / "inputs" / unaccepted.digest).exists()
