"""One planned, locked apply and one in-place reclaim: bounded-state step 3b.

Each test is named after a row of the plan's section 10 table.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from test_retention_plan import NOW, candidate_tree, history

from swingset.state import engine, retention, retention_apply
from swingset.state.db import open_database
from swingset.state.retention import RetentionError, add_hold, plan, usage
from swingset.state.retention_apply import apply_plan, eligible_payloads, receipt_directory, reclaim

KNOBS = {"max_database_bytes": 1 << 40, "recent_window": 1, "collect_older_than": 86400.0}


def archive_table(conn):
    """A prospective archive gate installed explicitly by the fixture owner."""
    from swingset.dev.services import ensure_service

    with ensure_service().connect(conn.info.dbname) as admin:
        admin.execute("CREATE TABLE ops.archived_generations(generation_id TEXT PRIMARY KEY)")
        admin.execute(
            "GRANT SELECT,INSERT,UPDATE,DELETE ON ops.archived_generations TO swingset_worker"
        )


class DiskUsage(NamedTuple):
    """What `shutil.disk_usage` returns; a stub of it makes the refusal testable."""

    total: int
    used: int
    free: int


#: Six built candidates: the collector's rule keeps the five newest, so the
#: oldest is the one a plan calls removable.
CANDIDATES = ("cand_0", "cand_1", "cand_2", "cand_3", "cand_4", "cand_5")


def aged_candidates(state: Path, names: Sequence[str] = CANDIDATES) -> list[Path]:
    """Built candidates nothing points at, oldest first, all past the age floor."""
    made = []
    for index, name in enumerate(names):
        path = candidate_tree(state, name)
        stamp = NOW.timestamp() - 2 * 86400 + index
        os.utime(path, (stamp, stamp))
        made.append(path)
    return made


def planned(database: Any) -> Any:
    return plan(database.connection, database.state_dir, **KNOBS)


def applied(database: Any, digest: str, *, now: Any = NOW, **extra: Any) -> dict[str, Any]:
    return apply_plan(database, plan_digest=digest, now=now, **KNOBS, **extra)


def notes(database: Any) -> list[engine.Row]:
    return list(
        database.connection.execute(
            "SELECT retention_applies.plan_digest, retention_applies.planned_files_json, "
            "retention_applies.planned_payloads_json, retention_applies.removed_payload_bytes, "
            "retention_applies.started_at, retention_applies.files_completed_at, "
            "retention_applies.receipt_json FROM retention_applies ORDER BY plan_digest NULLS FIRST"
        )
    )


def payload_count(database: Any) -> int:
    return int(
        database.connection.execute("SELECT COUNT(*) FROM derivation_payloads").fetchone()[0]
    )


def fat_payloads(database: Any, *, count: int = 300, size: int = 16384) -> tuple[str, ...]:
    """Bytes on an archivable generation, big enough that giving them back shows."""
    import hashlib
    import random

    random_bytes = random.Random(0)
    conn = database.connection
    from swingset.dev.services import ensure_service

    with ensure_service().connect(conn.info.dbname) as admin:
        admin.execute(
            "ALTER TABLE derive.derivation_payloads SET "
            "(autovacuum_enabled=false, toast.autovacuum_enabled=false)"
        )
    digests = []
    with database.transaction():
        for index in range(count):
            text = json.dumps({"filler": f"{index:06d}" + random_bytes.randbytes(size).hex()})
            digest = hashlib.sha256(text.encode()).hexdigest()
            conn.execute(
                "INSERT INTO derivation_payloads (payload_sha256, payload_json) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (digest, text),
            )
            conn.execute(
                (
                    "INSERT INTO derivation_row_refs (generation_id, ordinal, table_name, record_key, "
                    "payload_sha256) VALUES (%s, %s, %s, %s, %s)"
                ),
                ("dg_b1", 100 + index, "entries", f"fat-{index}", digest),
            )
            digests.append(digest)
    return tuple(digests)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def test_apply_is_rerun_with_an_old_fingerprint(tmp_path: Path) -> None:
    """It stops."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        stale = planned(database).digest
        # The state moves on: one more file nothing declares.
        (state / "blobs" / "sha256" / "ab" / "cd").mkdir(parents=True, exist_ok=True)
        (state / "blobs" / "sha256" / "ab" / "cd" / f"{1:064x}").write_bytes(b"new")
        assert planned(database).digest != stale
        with pytest.raises(RetentionError, match="is not the plan of this state"):
            applied(database, stale)
        assert notes(database) == []
        assert not receipt_directory(state).exists()
        assert (state / "candidates" / "cand_0").is_dir()


def test_a_root_is_added_between_plan_and_apply(tmp_path: Path) -> None:
    """Apply stops; nothing changed."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        digest = planned(database).digest
        add_hold(
            state,
            database.connection,
            who="operator",
            why="investigating a link",
            now=NOW,
            generations=["dg_b1"],
        )
        with pytest.raises(RetentionError, match="is not the plan of this state"):
            applied(database, digest)
        assert notes(database) == []
        assert (state / "candidates" / "cand_0").is_dir()
        # And the new plan has the hold in it, so the operator can apply that.
        fresh = planned(database)
        assert fresh.digest != digest
        assert any(root["kind"] == "hold" for root in fresh.content["roots"])


def test_apply_removes_what_the_plan_named_under_both_locks(tmp_path: Path) -> None:
    """The oldest candidate goes; every file the plan kept stays."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        value = planned(database)
        removable = [row["path"] for row in value.content["files"] if row["list"] == "removable"]
        assert removable == ["candidates/cand_0"]
        receipt = applied(database, value.digest)
        assert receipt["removed_files"] == ["candidates/cand_0"]
        assert not (state / "candidates" / "cand_0").exists()
        assert all((state / "candidates" / name).is_dir() for name in CANDIDATES[1:])
        note = notes(database)[0]
        assert note["plan_digest"] == value.digest
        assert note["files_completed_at"] is not None
        assert json.loads(note["planned_files_json"]) == ["candidates/cand_0"]
        written = receipt_directory(state) / f"{value.digest}.json"
        assert json.loads(written.read_text()) == receipt


def test_apply_removes_nothing_without_the_writer_lock(tmp_path: Path) -> None:
    """Removal is the one data writer's job, so an unlocked handle refuses."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        digest = planned(database).digest
    with open_database(state, lock=False) as unlocked:
        with pytest.raises(RetentionError, match="needs the writer lock"):
            applied(unlocked, digest)
        assert (state / "candidates" / "cand_0").is_dir()


def test_apply_is_rerun_with_the_applied_plan_fingerprint(tmp_path: Path) -> None:
    """The recorded receipt is returned; nothing changes."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        digest = planned(database).digest
        first = applied(database, digest)
        written = receipt_directory(state) / f"{digest}.json"
        before = written.stat().st_mtime_ns
        remaining = sorted(path.name for path in (state / "candidates").iterdir())
        # The plan has moved on, because the candidate is gone. The rerun must
        # not replan and must not object: it returns what it recorded.
        assert planned(database).digest != digest
        second = applied(database, digest, now=NOW + timedelta(hours=3))
        assert second == first
        assert written.stat().st_mtime_ns == before
        assert sorted(path.name for path in (state / "candidates").iterdir()) == remaining
        assert len(notes(database)) == 1


def test_apply_crashes_before_the_transaction_commits(tmp_path: Path) -> None:
    """No row data removed, no file removed, no note written."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        digest = planned(database).digest
        payloads = payload_count(database)

        def die(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("killed inside the transaction")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(retention_apply, "_remove_payloads", die)
            with pytest.raises(RuntimeError, match="killed inside the transaction"):
                applied(database, digest)
        assert notes(database) == []
        assert payload_count(database) == payloads
        assert (state / "candidates" / "cand_0").is_dir()
        assert not receipt_directory(state).exists()
        # The plan is unchanged, so the same digest still applies cleanly.
        assert planned(database).digest == digest
        assert applied(database, digest)["removed_files"] == ["candidates/cand_0"]


def test_apply_crashes_after_commit_before_file_removal(tmp_path: Path) -> None:
    """Receipt available; the next apply removes the files."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        digest = planned(database).digest

        def die(*_args: Any, **_kwargs: Any) -> list[str]:
            raise RuntimeError("killed before the first unlink")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(retention_apply, "_remove_planned_files", die)
            with pytest.raises(RuntimeError, match="killed before the first unlink"):
                applied(database, digest)
        note = notes(database)[0]
        assert note["files_completed_at"] is None and note["receipt_json"] is None
        assert json.loads(note["planned_files_json"]) == ["candidates/cand_0"]
        assert (state / "candidates" / "cand_0").is_dir()
        # Nothing was removed, so a fresh plan is the same plan, and applying it
        # finishes what the note promised and records the receipt.
        assert planned(database).digest == digest
        receipt = applied(database, digest, now=NOW + timedelta(hours=1))
        assert receipt["removed_files"] == ["candidates/cand_0"]
        assert not (state / "candidates" / "cand_0").exists()
        assert json.loads(notes(database)[0]["receipt_json"]) == receipt
        assert applied(database, digest) == receipt


def test_a_crashed_apply_is_finished_by_the_next_fresh_plan(tmp_path: Path) -> None:
    """The fresh plan decides: what it still calls removable goes, the rest stays.

    A stale note is not a plan of the state it is replayed against. Between the
    crash and the next apply a publication starts on one of the two candidates
    the note named, so that one is a root now and must survive; the other is
    still removable and must go.
    """
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        # Seven built candidates, so the collector's rule leaves the two oldest
        # removable and one note names both.
        aged_candidates(state, [*CANDIDATES, "cand_6"])
        stale_plan = planned(database)
        stale = stale_plan.digest
        assert [
            row["path"] for row in stale_plan.content["files"] if row["list"] == "removable"
        ] == [
            "candidates/cand_0",
            "candidates/cand_1",
        ]

        def die(*_args: Any, **_kwargs: Any) -> list[str]:
            raise RuntimeError("killed before the first unlink")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(retention_apply, "_remove_planned_files", die)
            with pytest.raises(RuntimeError):
                applied(database, stale)
        assert json.loads(notes(database)[0]["planned_files_json"]) == [
            "candidates/cand_0",
            "candidates/cand_1",
        ]
        # An operator starts publishing cand_0 before anyone reruns. It is a
        # pending candidate now, so the fresh plan keeps it locally.
        (state / "candidates" / "cand_0" / "PUBLISHING").write_text("{}")
        fresh = planned(database)
        assert fresh.digest != stale
        assert {row["path"]: row["list"] for row in fresh.content["files"]}[
            "candidates/cand_0"
        ] == "local"
        receipt = applied(database, fresh.digest, now=NOW + timedelta(hours=2))
        # The in-progress publication is untouched; the other planned file went.
        assert (state / "candidates" / "cand_0").is_dir()
        assert not (state / "candidates" / "cand_1").exists()
        assert receipt["removed_files"] == ["candidates/cand_1"]
        assert receipt["resumed"] == [
            {
                "plan_digest": stale,
                "removed_files": ["candidates/cand_1"],
                "skipped_files": ["candidates/cand_0"],
            }
        ]
        # The stale note is closed with its own receipt naming what finished it
        # and what that apply refused to remove.
        stale_note = next(row for row in notes(database) if row["plan_digest"] == stale)
        stale_receipt = json.loads(str(stale_note["receipt_json"]))
        assert stale_receipt["finished_by"] == fresh.digest
        assert stale_receipt["removed_files"] == ["candidates/cand_1"]
        assert stale_receipt["skipped_files"] == ["candidates/cand_0"]
        assert stale_note["files_completed_at"] is not None
        # Closing it is not a licence: the next plan names cand_0 again only if
        # it becomes removable again.
        assert retention_apply._unfinished_notes(database.connection, exclude="") == []


def test_a_crashed_apply_never_removes_a_file_the_fresh_plan_does_not_name(
    tmp_path: Path,
) -> None:
    """A rollback between the crash and the next apply keeps its own candidate.

    The published baseline is pointed back at the candidate the stale note
    planned to remove. Nothing in the fresh plan calls anything removable, so the
    apply of that plan removes nothing at all.
    """
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        stale = planned(database).digest

        def die(*_args: Any, **_kwargs: Any) -> list[str]:
            raise RuntimeError("killed before the first unlink")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(retention_apply, "_remove_planned_files", die)
            with pytest.raises(RuntimeError):
                applied(database, stale)
        baseline = state / "baseline"
        if baseline.is_symlink():
            baseline.unlink()
        baseline.symlink_to(state / "candidates" / "cand_0")
        fresh = planned(database)
        assert fresh.digest != stale
        assert not any(row["list"] == "removable" for row in fresh.content["files"])
        receipt = applied(database, fresh.digest, now=NOW + timedelta(hours=2))
        assert receipt["removed_files"] == []
        assert receipt["resumed"] == [
            {"plan_digest": stale, "removed_files": [], "skipped_files": ["candidates/cand_0"]}
        ]
        assert (state / "candidates" / "cand_0" / "PUBLISHED").is_file()


def test_a_resumed_receipt_names_the_files_the_crashed_apply_already_removed(
    tmp_path: Path,
) -> None:
    """Every planned file is in exactly one list of the note's receipt.

    The crashed apply unlinked one of the two candidates its note named before
    it died. That file is gone from the disk and gone from the fresh plan, so
    counting only this apply's own unlinks would leave it in `planned_files` and
    in no other list at all.
    """
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state, [*CANDIDATES, "cand_6"])
        stale_plan = planned(database)
        stale = stale_plan.digest

        def die_after_one(state_dir: Path, planned_files: tuple[str, ...]) -> list[str]:
            shutil.rmtree(state_dir / planned_files[0])
            raise RuntimeError("killed after the first unlink")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(retention_apply, "_remove_planned_files", die_after_one)
            with pytest.raises(RuntimeError):
                applied(database, stale)
        assert not (state / "candidates" / "cand_0").exists()
        # cand_1 is still there and still removable, so the fresh plan names it.
        fresh = planned(database)
        assert fresh.digest != stale
        receipt = applied(database, fresh.digest, now=NOW + timedelta(hours=2))
        assert receipt["removed_files"] == ["candidates/cand_1"]
        assert receipt["already_gone_files"] == []
        stale_receipt = json.loads((receipt_directory(state) / f"{stale}.json").read_text())
        assert stale_receipt["planned_files"] == ["candidates/cand_0", "candidates/cand_1"]
        assert stale_receipt["removed_files"] == ["candidates/cand_0", "candidates/cand_1"]
        assert stale_receipt["skipped_files"] == []


def test_the_same_apply_rerun_reports_what_it_found_already_gone(tmp_path: Path) -> None:
    """A rerun of the same digest accounts for the file its crashed run removed."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        digest = planned(database).digest

        def die_after_one(state_dir: Path, planned_files: tuple[str, ...]) -> list[str]:
            shutil.rmtree(state_dir / planned_files[0])
            raise RuntimeError("killed after the first unlink")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(retention_apply, "_remove_planned_files", die_after_one)
            with pytest.raises(RuntimeError):
                applied(database, digest)
        # Removing cand_0 promoted cand_5 out of the collector's five newest, so
        # the fresh plan of this state is a different plan. Its own note is the
        # one that reports the file that already went.
        fresh = planned(database)
        receipt = applied(database, fresh.digest, now=NOW + timedelta(hours=1))
        assert receipt["already_gone_files"] == []
        stale_receipt = json.loads((receipt_directory(state) / f"{digest}.json").read_text())
        assert stale_receipt["removed_files"] == ["candidates/cand_0"]
        assert stale_receipt["skipped_files"] == []


def test_a_generation_archived_between_plan_and_apply_stops_the_apply(tmp_path: Path) -> None:
    """The reviewed plan says what is archived, so widening it moves the digest.

    Payload removal is gated on a generation being archived. If apply read that
    from the database instead of from the plan, a row written after the review
    would make bytes eligible that the operator never saw named (D-0155).
    """
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        before = planned(database)
        assert not any(row["archived"] for row in before.content["generations"])
        assert before.content["totals"]["archived_generations"] == 0
        assert eligible_payloads(database.connection, before) == ()
        with database.transaction() as conn:
            archive_table(conn)
            conn.execute("INSERT INTO archived_generations VALUES ('dg_b1')")
        # An empty table changes nothing; the row does.
        after = planned(database)
        assert after.digest != before.digest
        assert after.content["totals"]["archived_generations"] == 1
        assert eligible_payloads(database.connection, after)
        with pytest.raises(RetentionError, match="is not the plan of this state"):
            applied(database, before.digest)
        assert notes(database) == []
        assert payload_count(database) == 5


def test_a_crashed_apply_reports_the_payload_bytes_its_note_recorded(tmp_path: Path) -> None:
    """The receipt of a resumed note says what its own transaction removed.

    Nothing can count those bytes a second time once they are gone, so the note
    carries the number and the receipt is rebuilt from the note, not from state
    that has moved on.
    """
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        # Step 4's table, stood up here only to open the payload gate.
        with database.transaction() as conn:
            archive_table(conn)
            conn.execute("INSERT INTO archived_generations VALUES ('dg_b1')")
        stale_plan = planned(database)
        stale = stale_plan.digest
        removed_payloads = eligible_payloads(database.connection, stale_plan)
        assert removed_payloads

        def die(*_args: Any, **_kwargs: Any) -> list[str]:
            raise RuntimeError("killed before the first unlink")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(retention_apply, "_remove_planned_files", die)
            with pytest.raises(RuntimeError):
                applied(database, stale)
        note = notes(database)[0]
        assert json.loads(str(note["planned_payloads_json"])) == list(removed_payloads)
        assert int(note["removed_payload_bytes"]) > 0
        # The removal changed the bytes the plan measures, so only a fresh plan
        # applies now, and it is the one that closes the stale note.
        fresh = planned(database)
        assert fresh.digest != stale
        applied(database, fresh.digest, now=NOW + timedelta(hours=2))
        stale_receipt = json.loads((receipt_directory(state) / f"{stale}.json").read_text())
        assert stale_receipt["planned_payloads"] == list(removed_payloads)
        assert stale_receipt["removed_payload_bytes"] == int(note["removed_payload_bytes"])


def test_a_receipt_lost_after_its_note_was_written_is_written_again(tmp_path: Path) -> None:
    """The note is the record; the receipt file is the operator's copy of it.

    A process that dies between finishing the note and writing the file, or a
    restore from a backup, which leaves `gc/` out, leaves the note without its
    copy. The next run of that digest writes it from the note and removes
    nothing.
    """
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        digest = planned(database).digest
        first = applied(database, digest)
        written = receipt_directory(state) / f"{digest}.json"
        written.unlink()
        remaining = sorted(path.name for path in (state / "candidates").iterdir())
        assert applied(database, digest, now=NOW + timedelta(hours=4)) == first
        assert json.loads(written.read_text()) == first
        assert sorted(path.name for path in (state / "candidates").iterdir()) == remaining
        assert len(notes(database)) == 1


def test_a_hold_arrives_while_apply_holds_the_locks(tmp_path: Path) -> None:
    """It waits until apply finishes; the next plan includes it."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        digest = planned(database).digest
        marks: dict[str, float] = {}
        real = retention_apply._remove_planned_files

        def slow(state_dir: Path, planned_files: tuple[str, ...]) -> list[str]:
            time.sleep(0.4)
            removed = real(state_dir, planned_files)
            marks["files_removed"] = time.monotonic()
            return removed

        def place_hold() -> None:
            time.sleep(0.1)
            reader = engine.connect(engine.resolve_dsn(state))
            try:
                add_hold(
                    state,
                    reader,
                    who="operator",
                    why="arrived while apply held the locks",
                    now=NOW,
                    generations=["dg_b1"],
                    timeout=30,
                )
            finally:
                reader.close()
            marks["hold_written"] = time.monotonic()

        waiter = threading.Thread(target=place_hold)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(retention_apply, "_remove_planned_files", slow)
            waiter.start()
            applied(database, digest)
        waiter.join(30)
        assert not waiter.is_alive()
        # The hold asked at 0.1s and could not be written until apply let go of
        # the control lock, which is after the last unlink.
        assert marks["hold_written"] > marks["files_removed"]
        assert not (state / "candidates" / "cand_0").exists()
        fresh = planned(database)
        assert any(root["kind"] == "hold" for root in fresh.content["roots"])
        assert "dg_b1" in {
            row["generation_id"] for row in fresh.content["generations"] if row["list"] == "local"
        }


def test_a_hold_arrives_after_the_commit_before_file_removal(tmp_path: Path) -> None:
    """It waits; apply finishes its files; the hold then sees a fresh plan."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        digest = planned(database).digest
        marks: dict[str, float] = {}
        real = retention_apply._remove_planned_files
        started: list[threading.Thread] = []

        def place_hold() -> None:
            reader = engine.connect(engine.resolve_dsn(state))
            try:
                add_hold(
                    state,
                    reader,
                    who="operator",
                    why="arrived after the commit",
                    now=NOW,
                    generations=["dg_b1"],
                    timeout=30,
                )
            finally:
                reader.close()
            marks["hold_written"] = time.monotonic()

        def slow(state_dir: Path, planned_files: tuple[str, ...]) -> list[str]:
            # The note has committed and no file has gone yet. This is exactly
            # the window the plan says a hold creator must wait out.
            assert notes(database)[0]["files_completed_at"] is None
            started[0].start()
            time.sleep(0.4)
            removed = real(state_dir, planned_files)
            marks["files_removed"] = time.monotonic()
            return removed

        started.append(threading.Thread(target=place_hold))
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(retention_apply, "_remove_planned_files", slow)
            applied(database, digest)
        started[0].join(30)
        assert not started[0].is_alive()
        assert marks["hold_written"] > marks["files_removed"]
        assert not (state / "candidates" / "cand_0").exists()
        assert [record["why"] for record in retention.holds(state)] == ["arrived after the commit"]
        fresh = planned(database)
        assert fresh.digest != digest
        assert any(root["kind"] == "hold" for root in fresh.content["roots"])


def test_apply_never_removes_row_data_in_this_step(tmp_path: Path) -> None:
    """No payload is eligible until a table says a generation is archived."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        before = payload_count(database)
        assert before > 0
        value = planned(database)
        # There are archivable generations, and still nothing is eligible.
        assert any(row["list"] == "archivable" for row in value.content["generations"])
        assert eligible_payloads(database.connection, value) == ()
        receipt = applied(database, value.digest)
        assert receipt["planned_payloads"] == [] and receipt["removed_payload_bytes"] == 0
        assert payload_count(database) == before
        assert json.loads(notes(database)[0]["planned_payloads_json"]) == []


def test_the_archive_gate_opens_only_when_a_table_names_the_generation(tmp_path: Path) -> None:
    """The removal path exists and is gated on the fact step 4 will record."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        # Step 4's table, stood up here only to prove the gate is what opens it.
        with database.transaction() as conn:
            archive_table(conn)
        value = planned(database)
        assert eligible_payloads(database.connection, value) == ()
        with database.transaction() as conn:
            conn.execute("INSERT INTO archived_generations VALUES ('dg_b1')")
        value = planned(database)
        eligible = eligible_payloads(database.connection, value)
        assert eligible, "an archived archivable generation makes its own payloads eligible"
        before = payload_count(database)
        receipt = applied(database, value.digest)
        assert receipt["planned_payloads"] == list(eligible)
        assert receipt["removed_payload_bytes"] > 0
        assert payload_count(database) == before - len(eligible)
        # The gate closed again: the grant row can never be left switched on.
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM derivation_payload_removal_authority"
            ).fetchone()[0]
            == 0
        )
        with pytest.raises(engine.Error, match="requires authority"):
            with database.transaction() as conn:
                conn.execute("DELETE FROM derivation_payloads")


def test_a_shared_payload_stays_while_one_local_generation_names_it(tmp_path: Path) -> None:
    """A payload goes only when every generation naming it is eligible."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        shared = str(
            database.connection.execute(
                "SELECT payload_sha256 FROM derivation_row_refs WHERE generation_id = 'dg_b1'"
            ).fetchone()[0]
        )
        with database.transaction() as conn:
            archive_table(conn)
            conn.execute("INSERT INTO archived_generations VALUES ('dg_b1')")
            # dg_a2 is a current pointer, so it is local and never eligible.
            conn.execute(
                (
                    "INSERT INTO derivation_row_refs (generation_id, ordinal, table_name, record_key, "
                    "payload_sha256) VALUES ('dg_a2', 99, 'entries', 'shared', %s)"
                ),
                (shared,),
            )
        assert shared not in eligible_payloads(database.connection, planned(database))


# ---------------------------------------------------------------------------
# Reclaim
# ---------------------------------------------------------------------------


def test_reclaim_starts_with_too_little_free_disk(tmp_path: Path) -> None:
    """It refuses before writing anything."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        before = usage(database.connection, state)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(shutil, "disk_usage", lambda _path: DiskUsage(100, 99, 1024))
            with pytest.raises(RetentionError, match="free bytes on the state.s disk"):
                reclaim(database, now=NOW)
        assert not receipt_directory(state).exists()
        assert usage(database.connection, state) == before


def test_reclaim_refuses_below_the_new_estimate(tmp_path: Path) -> None:
    """Two and a half times the bytes in use plus a margin, not twice the file
    (F36, W3), against the smaller of statfs and the disk quota."""
    from swingset.state import disk

    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        digests = fat_payloads(database)
        with database.transaction() as conn:
            retention_apply._remove_payloads(conn, digests, plan_digest="test", now=NOW)
        before = usage(database.connection, state)
        quota = tmp_path / "swingset-disk"
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(disk, "QUOTA_FILE", tmp_path / "absent")
            # Without the margin the two estimates differ by the free pages.
            patch.setattr(retention_apply, "RECLAIM_FREE_MARGIN", 0)
            estimate = retention_apply.reclaim_estimate(
                database.connection, int(before["file_bytes"])
            )
            needed = estimate["needed_bytes"]
            in_use = int(before["bytes_in_use"])
            assert needed == 5 * in_use // 2
            assert estimate["file_size_estimate_bytes"] == 2 * int(before["file_bytes"])
            patch.setattr(shutil, "disk_usage", lambda _path: DiskUsage(1, 0, needed - 1))
            with pytest.raises(RetentionError, match=f"about {needed} free bytes"):
                reclaim(database, now=NOW)
            assert not receipt_directory(state).exists()
            # statfs shows the host volume; the quota has less: refused.
            patch.setattr(shutil, "disk_usage", lambda _path: DiskUsage(1, 0, 10 * needed))
            patch.setattr(disk, "QUOTA_FILE", quota)
            stamp = datetime.now(UTC).isoformat()
            quota.write_text(f"{1000 - needed + 1} 1000 {stamp}\n")
            with pytest.raises(RetentionError, match=f"there are {needed - 1}"):
                reclaim(database, now=NOW)
            # A stale quota reading is not trusted.
            quota.write_text(f"0 {10 * needed} 2026-01-01T00:00:00Z\n")
            with pytest.raises(RetentionError, match="sudo swingset-disk"):
                reclaim(database, now=NOW)
            # Enough on both.
            quota.write_text(f"{1000 - needed} 1000 {stamp}\n")
            receipt = reclaim(database, now=NOW)
        assert receipt["free_space"]["free_bytes"] == needed
        assert receipt["free_space"]["quota_free_bytes"] == needed
        assert receipt["free_space"]["quota_read_at"] == stamp
        assert receipt["after"]["file_bytes"] > 0
        # Other databases may pin a cluster visibility horizon; the budget
        # refusal test does not assume that this run can reclaim every tuple.
        # The margin is twelve gigabytes.
        assert (
            retention_apply.reclaim_estimate(database.connection, 0)["needed_bytes"] >= 12 * 10**9
        )


def test_reclaim_uses_server_managed_temporary_storage(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        receipt = reclaim(database, now=NOW)
        assert receipt["temp_directory"] == "postgres-server-managed"
        assert not (state / "state.sqlite").exists()
        assert receipt["completed_tables"]
        assert receipt["uncertain_table"] is None


def test_row_data_is_removed_and_reclaim_has_not_run(tmp_path: Path) -> None:
    """Deletion preserves allocated relation storage until server maintenance."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        digests = fat_payloads(database)
        before = usage(database.connection, state)
        with database.transaction() as conn:
            retention_apply._remove_payloads(conn, digests, plan_digest="test", now=NOW)
        after = usage(database.connection, state)
        assert payload_count(database) < 10
        assert after["bytes_in_use"] >= before["bytes_in_use"]
        assert after["free_list_bytes"] is None and after["wal_bytes"] is None
        assert retention.report(database.connection, state, **KNOBS)["usage"] == after


def test_reclaim_completes(tmp_path: Path, monkeypatch) -> None:
    """Isolate the physical-space assertion from other tests' global xmin pins."""
    from swingset.dev import services
    from swingset.state import engine, postgres_schema
    from swingset.state.postgres_copy import Copies

    state = tmp_path / "state"
    with services.lifecycle_service() as service, Copies.for_admin(service.dsn()) as copies:
        # A configured CI cluster may serve several physical cases. Each one
        # still owns a fresh C-locale database and the normal exact cleanup.
        with copies.empty() as admin:
            postgres_schema.migrate(admin, environment="dev")
            name = admin.info.dbname
            engine.write_settings(
                state,
                service.dsn(name, role="worker"),
                control_dsn=service.dsn(name, role="control"),
            )
            with monkeypatch.context() as physical:
                physical.setattr(services, "ensure_service", lambda *args, **kwargs: service)
                _reclaim_completion(state)


def _reclaim_completion(state: Path) -> None:
    """Physical storage shrinks while logical rows and immutable guards survive."""
    from swingset.state.postgres_schema import validate_schema

    with open_database(state) as database:
        history(database)
        digests = fat_payloads(database)
        with database.transaction() as conn:
            retention_apply._remove_payloads(conn, digests, plan_digest="test", now=NOW)
        before = usage(database.connection, state)
        expected = list(
            database.connection.execute(
                "SELECT generation_id FROM derivation_generations ORDER BY 1"
            )
        )
        receipt = reclaim(database, now=NOW)
        after = usage(database.connection, state)
        assert receipt["file_bytes_freed"] > 0
        assert after["file_bytes"] < before["file_bytes"]
        assert after["bytes_in_use"] < before["bytes_in_use"]
        assert after["free_list_bytes"] is None and after["wal_bytes"] is None
        assert (
            list(
                database.connection.execute(
                    "SELECT generation_id FROM derivation_generations ORDER BY 1"
                )
            )
            == expected
        )
        validate_schema(database.connection, full=True)
        written = sorted(receipt_directory(state).glob("reclaim-*.json"))
        assert len(written) == 1
        assert json.loads(written[0].read_text()) == receipt


def test_reclaim_runs_while_doctor_holds_an_idle_read_connection(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        with open_database(state, lock=False, read_only=True) as reader:
            expected = list(
                reader.connection.execute(
                    "SELECT generation_id FROM derivation_generations ORDER BY 1"
                )
            )
            reclaim(database, now=NOW)
            assert (
                list(
                    reader.connection.execute(
                        "SELECT generation_id FROM derivation_generations ORDER BY 1"
                    )
                )
                == expected
            )


def test_reclaim_reports_a_pending_write_and_can_resume(tmp_path: Path) -> None:
    """Postgres relation locks bound the rewrite and retain a partial receipt."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        conn = database.connection
        other = engine.connect(engine.resolve_dsn(state))
        try:
            other.execute("BEGIN")
            other.execute("INSERT INTO meta(key,value) VALUES('pending','1')")
            conn.execute("SET lock_timeout='100ms'")
            with pytest.raises(engine.LockTimeout):
                reclaim(database, now=NOW)
        finally:
            other.rollback()
            other.close()
            conn.execute("SET lock_timeout='70s'")
        written = sorted(receipt_directory(state).glob("reclaim-*.json"))
        receipt = json.loads(written[0].read_text())
        assert receipt["completed_tables"]
        assert receipt["uncertain_table"] in {"ops.meta", "fetch.history_dispatch_fence"}
        assert receipt["error"]
        assert conn.execute("SELECT count(*) FROM meta WHERE key='pending'").fetchone()[0] == 0
        assert reclaim(database, now=NOW)["uncertain_table"] is None


def test_reclaim_is_killed_partway_through_the_rewrite(tmp_path: Path) -> None:
    """Terminate a real VACUUM backend while it waits; committed rows survive."""
    from swingset.dev.services import ensure_service

    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        expected = list(
            database.connection.execute(
                "SELECT generation_id FROM derivation_generations ORDER BY 1"
            )
        )
        reader = engine.connect(engine.resolve_dsn(state))
        vacuum = engine.connect(engine.resolve_dsn(state))
        pid = vacuum.info.backend_pid
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM derivation_generations")
        errors = []

        def rewrite():
            try:
                vacuum.execute("VACUUM FULL derive.derivation_generations")
            except engine.Error as exc:
                errors.append(exc)

        worker = threading.Thread(target=rewrite)
        worker.start()
        with ensure_service().connect(database.connection.info.dbname) as admin:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if (
                    admin.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (pid,)
                    ).fetchone()[0]
                    == "Lock"
                ):
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("VACUUM backend never reached its rewrite lock")
            assert admin.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()[0]
        reader.rollback()
        reader.close()
        worker.join(5)
        vacuum.close()
        assert errors and not worker.is_alive()
        assert (
            list(
                database.connection.execute(
                    "SELECT generation_id FROM derivation_generations ORDER BY 1"
                )
            )
            == expected
        )
        assert reclaim(database, now=NOW)["uncertain_table"] is None


def test_reclaim_interruption_records_completed_tables(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        original = engine.Connection.execute
        count = 0

        def interrupted(self, query, *args, **kwargs):
            nonlocal count
            text = query.as_string(self) if hasattr(query, "as_string") else str(query)
            if self is database.connection and text.startswith("VACUUM"):
                count += 1
                if count == 3:
                    raise KeyboardInterrupt("injected rewrite interruption")
            return original(self, query, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(engine.Connection, "execute", interrupted)
            with pytest.raises(KeyboardInterrupt):
                reclaim(database, now=NOW)
        receipt = json.loads(next(receipt_directory(state).glob("reclaim-*.json")).read_text())
        assert len(receipt["completed_tables"]) == 2
        assert receipt["uncertain_table"]
        assert receipt["error"].startswith("KeyboardInterrupt")
        assert reclaim(database, now=NOW)["uncertain_table"] is None


def test_reclaim_refuses_without_the_writer_lock(tmp_path: Path) -> None:
    """Rewriting the file is the one data writer's job."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
    with open_database(state, lock=False) as unlocked:
        with pytest.raises(RetentionError, match="needs the writer lock"):
            reclaim(unlocked, now=NOW)
        assert not receipt_directory(state).exists()


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def removable_candidates(content: dict[str, Any]) -> list[str]:
    return [
        str(row["path"])
        for row in content["files"]
        if row["list"] == "removable" and str(row["path"]).startswith("candidates/")
    ]


def test_a_candidate_plan_walks_no_graph_and_names_what_the_full_plan_does(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The worker cannot finish the full plan's graph walk (I10), so candidate
    pruning has a plan and an apply that never start one."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        full = planned(database)
        monkeypatch.setattr(
            retention, "reachable", lambda *_a, **_k: pytest.fail("candidate plan walked the graph")
        )
        pruning = retention.candidate_plan(
            database.connection, state, collect_older_than=KNOBS["collect_older_than"]
        )
        assert removable_candidates(pruning.content) == ["candidates/cand_0"]
        assert removable_candidates(pruning.content) == removable_candidates(full.content)
        assert pruning.content["totals"]["removable_candidates"] == 1
        assert pruning.content["totals"]["removable_candidate_bytes"] > 0
        # A stale digest removes nothing.
        with pytest.raises(RetentionError, match="--candidates-only"):
            applied(database, full.digest, candidates_only=True)
        assert (state / "candidates" / "cand_0").is_dir()
        receipt = applied(database, pruning.digest, candidates_only=True)
        assert receipt["removed_files"] == ["candidates/cand_0"]
        assert receipt["planned_payloads"] == []
        assert not (state / "candidates" / "cand_0").exists()
        assert all((state / "candidates" / name).is_dir() for name in CANDIDATES[1:])


def test_neither_plan_removes_a_candidate_a_selected_build_names(tmp_path: Path) -> None:
    """A build reuses the candidate its generation names, however old."""
    from test_retention_plan import generation, point_at

    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
        run = database.start_run(NOW)
        with database.transaction() as conn:
            scope = ("build", "correction", "all")
            generation(conn, run, "dg_build", scope, rows=(("artifact", "cand_0", {"n": 1}),))
            point_at(conn, scope, "dg_build")
        pruning = retention.candidate_plan(
            database.connection, state, collect_older_than=KNOBS["collect_older_than"]
        )
        assert removable_candidates(pruning.content) == []
        assert removable_candidates(planned(database).content) == []
        assert {"path": "candidates/cand_0", "list": "local", "why": "built_candidate"} in (
            pruning.content["files"]
        )


def test_gc_candidates_only_plans_and_applies(tmp_path: Path, capsys: Any) -> None:
    from swingset.cli import main

    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)
    assert main(["gc", "--plan", "--candidates-only", "--state", str(state)]) == 0
    written = json.loads(capsys.readouterr().out)
    assert Path(written["plan"]).is_file()
    assert written["totals"]["removable_candidates"] == 1
    assert written["equivalences"]["derivation_equivalences"] == 0
    assert (
        main(["gc", "--apply", written["digest"], "--candidates-only", "--state", str(state)]) == 0
    )
    assert json.loads(capsys.readouterr().out)["removed_files"] == ["candidates/cand_0"]
    assert main(["gc", "--candidates-only", "--state", str(state)]) == 1


def test_an_apply_before_the_age_floor_says_why_nothing_went(tmp_path: Path, capsys: Any) -> None:
    """A copied state directory has fresh modification times: the plan names
    removable candidates, but none is past its floor. The apply must say so
    instead of reading as a successful empty run (phase 1 review)."""
    from swingset.cli import main

    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        for path in aged_candidates(state):
            os.utime(path, None)
    assert main(["gc", "--plan", "--candidates-only", "--state", str(state)]) == 0
    written = json.loads(capsys.readouterr().out)
    assert written["totals"]["removable_candidates"] == 1
    assert written["eligibility"]["eligible_now"] == 0
    assert written["eligibility"]["not_yet_eligible"] == 1
    assert written["eligibility"]["earliest_eligible_after"]
    command = ["gc", "--apply", written["digest"], "--candidates-only", "--state", str(state)]
    assert main(command) == 0
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert receipt["removed_files"] == []
    assert receipt["eligibility"]["eligible_now"] == 0
    assert receipt["eligibility"]["not_yet_eligible"] == 1
    assert "none has passed its age floor" in captured.err


def test_the_gc_command_plans_applies_and_reclaims(tmp_path: Path, capsys: Any) -> None:
    """Bare gc removes nothing and points at the three commands that act."""
    from swingset.cli import main

    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        aged_candidates(state)

    assert main(["gc", "--state", str(state)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert "plan" not in summary and not (state / "gc" / "plans").exists()
    assert any("gc --reclaim" in line for line in summary["next"])
    # Nothing goes without a written plan, so the bare command hands out no
    # digest to apply: it points at gc --plan, which is what prints that line.
    assert not any("--apply" in line for line in summary["next"])
    assert (state / "candidates" / "cand_0").is_dir()

    assert main(["gc", "--plan", "--state", str(state)]) == 0
    written = json.loads(capsys.readouterr().out)
    assert written["digest"] == summary["digest"]
    assert Path(written["plan"]).is_file()
    assert any(f"gc --apply {written['digest']}" in line for line in written["next"])

    assert main(["gc", "--apply", written["digest"], "--state", str(state)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["removed_files"] == ["candidates/cand_0"]
    assert not (state / "candidates" / "cand_0").exists()

    assert main(["gc", "--reclaim", "--state", str(state)]) == 0
    assert json.loads(capsys.readouterr().out)["format"] == retention_apply.RECLAIM_RECEIPT_FORMAT

    # An old digest after a successful apply stops, and the command says so.
    assert main(["gc", "--apply", written["digest"], "--state", str(state)]) == 0
    assert json.loads(capsys.readouterr().out)["removed_files"] == ["candidates/cand_0"]
