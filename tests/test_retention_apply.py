"""One planned, locked apply and one in-place reclaim: bounded-state step 3b.

Each test is named after a row of the plan's section 10 table.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from test_retention_plan import NOW, candidate_tree, history

from swingset.state import retention, retention_apply
from swingset.state.db import open_database
from swingset.state.retention import RetentionError, add_hold, plan, usage
from swingset.state.retention_apply import apply_plan, eligible_payloads, receipt_directory, reclaim

KNOBS = {"max_database_bytes": 1 << 40, "recent_window": 1, "collect_older_than": 86400.0}


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


def notes(database: Any) -> list[sqlite3.Row]:
    return list(database.connection.execute("SELECT * FROM retention_applies ORDER BY plan_digest"))


def payload_count(database: Any) -> int:
    return int(
        database.connection.execute("SELECT count(*) FROM derivation_payloads").fetchone()[0]
    )


def fat_payloads(database: Any, *, count: int = 300, size: int = 4096) -> tuple[str, ...]:
    """Bytes on an archivable generation, big enough that giving them back shows."""
    import hashlib

    conn = database.connection
    digests = []
    with database.transaction():
        for index in range(count):
            text = json.dumps({"filler": f"{index:06d}" + "x" * size})
            digest = hashlib.sha256(text.encode()).hexdigest()
            conn.execute("INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (digest, text))
            conn.execute(
                "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
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
            conn.execute("CREATE TABLE archived_generations(generation_id TEXT PRIMARY KEY)")
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
            conn.execute("CREATE TABLE archived_generations(generation_id TEXT PRIMARY KEY)")
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
            reader = sqlite3.connect(state / "state.sqlite")
            reader.row_factory = sqlite3.Row
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
            reader = sqlite3.connect(state / "state.sqlite")
            reader.row_factory = sqlite3.Row
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
            conn.execute("CREATE TABLE archived_generations(generation_id TEXT PRIMARY KEY)")
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
                "SELECT count(*) FROM derivation_payload_removal_authority"
            ).fetchone()[0]
            == 0
        )
        with pytest.raises(sqlite3.Error, match="requires authority"):
            with database.transaction() as conn:
                conn.execute("DELETE FROM derivation_payloads")


def test_a_shared_payload_stays_while_one_local_generation_names_it(tmp_path: Path) -> None:
    """A payload goes only when every generation naming it is eligible."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        shared = str(
            database.connection.execute(
                "SELECT payload_sha256 FROM derivation_row_refs WHERE generation_id='dg_b1'"
            ).fetchone()[0]
        )
        with database.transaction() as conn:
            conn.execute("CREATE TABLE archived_generations(generation_id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO archived_generations VALUES ('dg_b1')")
            # dg_a2 is a current pointer, so it is local and never eligible.
            conn.execute(
                "INSERT INTO derivation_row_refs VALUES ('dg_a2',99,'entries','shared',?)",
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
            with pytest.raises(RetentionError, match="free bytes on the state volume"):
                reclaim(database, now=NOW)
        assert not receipt_directory(state).exists()
        assert usage(database.connection, state) == before


def test_row_data_is_removed_and_reclaim_has_not_run(tmp_path: Path) -> None:
    """File size unchanged; doctor shows the bytes as free-list."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        digests = fat_payloads(database)
        database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = usage(database.connection, state)
        with database.transaction() as conn:
            retention_apply._remove_payloads(conn, digests, plan_digest="test", now=NOW)
        database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after = usage(database.connection, state)
        assert payload_count(database) < 10
        assert after["file_bytes"] == before["file_bytes"]
        assert after["free_list_bytes"] > before["free_list_bytes"]
        assert after["bytes_in_use"] is not None
        assert after["bytes_in_use"] < before["bytes_in_use"]
        measured = retention.report(database.connection, state, **KNOBS)
        assert measured["usage"]["free_list_bytes"] == after["free_list_bytes"]


def test_reclaim_completes(tmp_path: Path) -> None:
    """File and write-ahead log shrink to bytes in use; next backup too; checks pass."""
    from swingset.backup.checkpoint import create_checkpoint
    from swingset.state.db import SCHEMA_VERSION

    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        digests = fat_payloads(database)
        fat_backup = tmp_path / "before"
        create_checkpoint(
            state,
            database.connection,
            fat_backup,
            schema_version=SCHEMA_VERSION,
            versions={},
            input_bundle_hash=None,
        )
        with database.transaction() as conn:
            retention_apply._remove_payloads(conn, digests, plan_digest="test", now=NOW)
        before = usage(database.connection, state)
        receipt = reclaim(database, now=NOW)
        after = usage(database.connection, state)
        assert receipt["before"]["file_bytes"] == before["file_bytes"]
        assert receipt["after"]["file_bytes"] == after["file_bytes"]
        assert receipt["file_bytes_freed"] > 0
        assert after["file_bytes"] < before["file_bytes"]
        assert after["free_list_bytes"] == 0
        # The file is now about what the pages in it hold, and the log is empty.
        assert after["bytes_in_use"] is not None
        assert after["file_bytes"] - after["bytes_in_use"] < after["bytes_in_use"]
        assert after["wal_bytes"] == 0
        assert database.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        thin_backup = tmp_path / "after"
        create_checkpoint(
            state,
            database.connection,
            thin_backup,
            schema_version=SCHEMA_VERSION,
            versions={},
            input_bundle_hash=None,
        )
        assert (thin_backup / "state.sqlite").stat().st_size < (
            fat_backup / "state.sqlite"
        ).stat().st_size
        written = sorted(receipt_directory(state).glob("reclaim-*.json"))
        assert len(written) == 1
        assert json.loads(written[0].read_text()) == receipt


def test_reclaim_runs_while_doctor_holds_a_read_connection(tmp_path: Path) -> None:
    """Doctor reads a consistent view; reclaim completes."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        digests = fat_payloads(database)
        with database.transaction() as conn:
            retention_apply._remove_payloads(conn, digests, plan_digest="test", now=NOW)
        with open_database(state, lock=False, read_only=True) as reader:
            expected = sorted(
                str(row[0])
                for row in reader.connection.execute(
                    "SELECT generation_id FROM derivation_generations"
                )
            )
            assert expected
            receipt = reclaim(database, now=NOW)
            assert receipt["after"]["file_bytes"] < receipt["before"]["file_bytes"]
            # The same reader, across the rewrite, still sees the whole database.
            assert (
                sorted(
                    str(row[0])
                    for row in reader.connection.execute(
                        "SELECT generation_id FROM derivation_generations"
                    )
                )
                == expected
            )
            assert reader.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_reclaim_runs_while_another_connection_has_a_pending_write(tmp_path: Path) -> None:
    """Reclaim reports it and stops; nothing changes."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = usage(database.connection, state)
        database.connection.execute("PRAGMA busy_timeout=200")
        other = sqlite3.connect(state / "state.sqlite", isolation_level=None)
        try:
            other.execute("PRAGMA busy_timeout=200")
            other.execute("BEGIN IMMEDIATE")
            other.execute("INSERT INTO meta(key,value) VALUES ('pending','1')")
            with pytest.raises(RetentionError, match="pending write"):
                reclaim(database, now=NOW)
        finally:
            other.rollback()
            other.close()
        database.connection.execute("PRAGMA busy_timeout=5000")
        assert not receipt_directory(state).exists()
        assert usage(database.connection, state)["file_bytes"] == before["file_bytes"]
        # And the file is still sound, so the next reclaim works.
        assert reclaim(database, now=NOW)["after"]["file_bytes"] > 0


def test_reclaim_is_killed_partway_through_the_rewrite(tmp_path: Path) -> None:
    """Next open rolls back; integrity passes; bytes unchanged; next reclaim succeeds."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        digests = fat_payloads(database)
        with database.transaction() as conn:
            retention_apply._remove_payloads(conn, digests, plan_digest="test", now=NOW)
        database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = usage(database.connection, state)
    # A real kill, in its own process, part way into the rewrite. How far in is
    # measured first on a throwaway copy, so the kill lands in the middle of
    # this database's rewrite rather than at a guessed offset.
    script = tmp_path / "vacuum.py"
    script.write_text(
        "import os, sqlite3, sys\n"
        "limit = int(sys.argv[2])\n"
        "conn = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
        "seen = [0]\n"
        "def handler():\n"
        "    seen[0] += 1\n"
        "    if limit and seen[0] >= limit:\n"
        "        os._exit(91)\n"
        "    return 0\n"
        "conn.set_progress_handler(handler, 50)\n"
        "conn.execute('VACUUM')\n"
        "conn.set_progress_handler(None, 0)\n"
        "print(seen[0])\n"
    )

    def vacuum(database_path: Path, limit: int) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(script), str(database_path), str(limit)], capture_output=True
        )

    probe = tmp_path / "probe"
    shutil.copytree(state, probe)
    measured = vacuum(probe / "state.sqlite", 0)
    assert measured.returncode == 0, measured.stderr
    steps = int(measured.stdout)
    assert steps > 4, "the rewrite has to be long enough to be killed part way through"

    killed = vacuum(state / "state.sqlite", steps // 2)
    assert killed.returncode == 91, killed.stderr
    with open_database(state) as database:
        assert database.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert database.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        rolled_back = usage(database.connection, state)
        assert rolled_back["file_bytes"] == before["file_bytes"]
        assert rolled_back["free_list_bytes"] == before["free_list_bytes"]
        receipt = reclaim(database, now=NOW)
        assert receipt["after"]["file_bytes"] < before["file_bytes"]
        assert database.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_reclaim_is_interrupted_by_an_aborting_progress_handler(tmp_path: Path) -> None:
    """It reports the rollback and changes nothing; the next reclaim succeeds."""
    state = tmp_path / "state"
    with open_database(state) as database:
        history(database)
        digests = fat_payloads(database)
        with database.transaction() as conn:
            retention_apply._remove_payloads(conn, digests, plan_digest="test", now=NOW)
        database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = usage(database.connection, state)
        real_usage = retention_apply.usage

        def arm(conn: sqlite3.Connection, state_dir: Path) -> dict[str, Any]:
            measured = real_usage(conn, state_dir)
            # Armed only once the "before" reading is taken, so the abort lands
            # inside the locked region and not in the measurement.
            conn.set_progress_handler(lambda: 1, 20)
            return measured

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(retention_apply, "usage", arm)
            with pytest.raises(RetentionError, match="rolled back"):
                reclaim(database, now=NOW)
        database.connection.set_progress_handler(None, 0)
        assert not receipt_directory(state).exists()
        after = usage(database.connection, state)
        assert after["file_bytes"] == before["file_bytes"]
        assert database.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert reclaim(database, now=NOW)["after"]["file_bytes"] < before["file_bytes"]


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
