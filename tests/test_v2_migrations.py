import sqlite3
from pathlib import Path

import pytest

from swingset.state import db


def v1_state(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(db, "SCHEMA_VERSION", 1)
        with db.open_database(path, lock=False) as database:
            conn = database.connection
            conn.execute("INSERT INTO runs VALUES ('run','2026-09-12T00:00:00Z',NULL,1,NULL)")
            conn.execute(
                "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
                "VALUES ('event','series','Example',2026,'2026-09-01','2026-09-02','registry','[]','wsdc_calendar','snapshot','1','2026-09-12T00:00:00Z','2026-09-12T00:00:00Z','run')"
            )
            conn.execute(
                "INSERT INTO contests(contest_id,event_id,name_raw,division,age_division,contest_type,partner_mode,dance_style,wsdc_points_eligible,combined_from,parse_status,source_contest_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
                "VALUES ('contest','event','Novice','novice','none','jack_and_jill','random_partner','wcs',1,'[]','parsed','contest','eepro','snapshot','1','2026-09-12T00:00:00Z','2026-09-12T00:00:00Z','run')"
            )
            conn.execute(
                "INSERT INTO findings(finding_id,owner_kind,owner_id,kind,subject_kind,subject_id,severity,summary,evidence_json,opened_at,run_id) "
                "VALUES ('finding','project','event','unknown_field','event','event','warning','Review field','{}','2026-09-12T00:00:00Z','run')"
            )
            conn.execute(
                "INSERT INTO link_candidates VALUES ('judge','judge',1,0.8,1,1,NULL,0,1,NULL,0,0,0,1,1,'run')"
            )


def test_v1_upgrade_preserves_parent_child_rows_and_review_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v1_state(tmp_path, monkeypatch)
    with db.open_database(tmp_path, lock=False) as database:
        conn = database.connection
        assert database.schema_version == db.SCHEMA_VERSION
        assert tuple(
            conn.execute("SELECT event_id,event_month,date_precision FROM events").fetchone()
        ) == ("event", "2026-09", "day")
        assert conn.execute("SELECT event_id FROM contests").fetchone()[0] == "event"
        assert conn.execute("SELECT subject_id FROM link_candidates").fetchone()[0] == "judge"
        conn.execute("UPDATE link_candidates SET role_ok=NULL")
        assert conn.execute("SELECT state FROM findings").fetchone()[0] == "needs_review"
        assert conn.execute("SELECT finding_id FROM finding_support").fetchone()[0] == "finding"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_failed_evidence_recovery_rolls_back_entire_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swingset.state import verification

    v1_state(tmp_path, monkeypatch)

    def failed_recovery(database: db.Database) -> None:
        database.connection.execute("DELETE FROM contests")
        raise RuntimeError("interrupted recovery")

    with monkeypatch.context() as patch:
        patch.setattr(verification, "recover_registry_verifications", failed_recovery)
        with pytest.raises(RuntimeError, match="interrupted recovery"):
            db.open_database(tmp_path, lock=False)
    with sqlite3.connect(tmp_path / "state.sqlite") as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM contests").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name='registry_verifications'"
            ).fetchone()
            is None
        )
    with db.open_database(tmp_path, lock=False) as restarted:
        assert restarted.schema_version == db.SCHEMA_VERSION


def test_checkpoint_cannot_label_v2_database_as_v1(tmp_path: Path) -> None:
    import json

    from swingset.backup.checkpoint import CheckpointError, create_checkpoint, verify_checkpoint

    with db.open_database(tmp_path / "state", lock=False) as database:
        with pytest.raises(CheckpointError, match="differs from database"):
            create_checkpoint(
                database.state_dir,
                database.connection,
                tmp_path / "bad",
                schema_version=1,
                versions={},
                input_bundle_hash=None,
            )
        checkpoint = create_checkpoint(
            database.state_dir,
            database.connection,
            tmp_path / "checkpoint",
            schema_version=database.schema_version,
            versions={},
            input_bundle_hash=None,
        )
    manifest_path = checkpoint.path / "checkpoint.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 1
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(CheckpointError, match="differs from database"):
        verify_checkpoint(checkpoint.path, maximum_schema_version=1)
