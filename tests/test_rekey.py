"""Re-keying carries only proven clean outputs across recipe changes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from swingset.fetch.archive import canonical, digest
from swingset.state import derivations, rekey, verify_graph
from swingset.state.db import open_database
from swingset.state.work import WorkUnit, enqueue

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def seed(database, units, recipe=None):
    run = database.start_run(NOW)
    with database.transaction() as conn:
        for unit in units:
            selection = derivations.capture(
                conn, unit, now=NOW, recipe={"recipe/test": "old"} if recipe is None else recipe
            )
            derivations.complete(conn, selection, rows=iter(()), now=NOW, run_id=run)
            enqueue(conn, [unit], enqueued_at=NOW.isoformat())


def receipt(database, *, mismatch=(), bumped=()):
    conn = database.connection
    expected = verify_graph.enumerate_units(conn)
    results = []
    for name in expected:
        unit = WorkUnit(*name.split("/", 2))
        results.append(
            {
                "unit": name,
                "state": "mismatch" if name in mismatch else "match",
                "generation_id": derivations.selected_generation(conn, unit),
            }
        )
    return {
        "format": verify_graph.FORMAT,
        **verify_graph.context(conn),
        "checkpoint": verify_graph.checkpoint_identity(conn),
        "expected_units": expected,
        "results": results,
        "unit_set_digest": digest(canonical(expected)),
        "complete": True,
        "clean": not mismatch,
        "errors": [],
        "bumped_transforms": list(bumped),
    }


def test_clean_rekey_is_current_and_restart_is_idempotent(tmp_path: Path):
    units = [WorkUnit("project", "dancer", str(i)) for i in range(3)]
    with open_database(tmp_path / "state") as database:
        seed(database, units)
        proof = receipt(database)
        interrupted = rekey.run(
            database, proof, now=NOW, batch_size=1, stop=iter([False, True]).__next__
        )
        assert not interrupted["complete"]
        assert interrupted["processed"] == 1
        resumed = rekey.run(database, proof, now=NOW, batch_size=1)
        assert resumed["complete"]
        assert resumed["already_current"] == 1
        assert all(derivations.current(database.connection, unit) for unit in units)
        again = rekey.run(database, proof, now=NOW)
        assert again["equivalences"] == {}
        assert again["already_current"] == 3
        assert (
            database.connection.execute("SELECT COUNT(*) FROM derivation_equivalences").fetchone()[
                0
            ]
            == 3
        )
        assert database.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0


@pytest.mark.parametrize("bump", [False, True])
def test_bumped_producer_blocks_its_consumer_but_clean_unit_rekeys(tmp_path: Path, bump: bool):
    producer = WorkUnit("project", "dancer", "person")
    consumer = WorkUnit("project", "inventory", "all")
    clean = WorkUnit("project", "event", "clean")
    with open_database(tmp_path / "state") as database:
        seed(database, [producer, consumer, clean])
        proof = receipt(
            database,
            bumped=["project/dancer"] if bump else [],
            mismatch=[] if bump else ["project/dancer/person"],
        )
        result = rekey.run(database, proof, now=NOW)
        assert result["complete"]
        assert result["excluded"] == ["project/dancer/person", "project/inventory/all"]
        assert not derivations.current(database.connection, producer)
        assert not derivations.current(database.connection, consumer)
        assert derivations.current(database.connection, clean)
        assert (
            database.connection.execute("SELECT unit_kind FROM derivation_equivalences").fetchall()[
                0
            ][0]
            == "event"
        )


@pytest.mark.parametrize(
    "corrupt", ["missing", "duplicate", "failed", "interrupted", "inputs", "checkpoint"]
)
def test_invalid_or_stale_proof_never_writes_equivalences(tmp_path: Path, corrupt: str):
    with open_database(tmp_path / "state") as database:
        seed(database, [WorkUnit("project", "dancer", "p")])
        proof = receipt(database)
        if corrupt == "missing":
            proof["results"] = []
        elif corrupt == "duplicate":
            proof["results"] *= 2
        elif corrupt == "failed":
            proof["results"][0]["state"] = "failed"
        elif corrupt == "interrupted":
            proof["complete"] = False
        elif corrupt == "inputs":
            proof["inputs"]["unproven"] = "edit"
        else:
            proof["checkpoint"] = "different"
        with pytest.raises(ValueError):
            rekey.run(database, proof, now=NOW)
        assert (
            database.connection.execute("SELECT COUNT(*) FROM derivation_equivalences").fetchone()[
                0
            ]
            == 0
        )


def test_requires_state_exclusive_lock(tmp_path: Path):
    with open_database(tmp_path / "state", lock=False) as database:
        with pytest.raises(ValueError, match="exclusive"):
            rekey.run(database, {}, now=NOW)


def test_source_rekey_needs_both_extract_and_parse_proven_clean(tmp_path: Path):
    import json

    with open_database(tmp_path / "state") as database:
        run_id = database.start_run(NOW)
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO watches (watch_id, source, kind, method, url, parser, state) VALUES ('w', "
                "'wsdc', 'dancer', 'GET', 'https://example.test/', 'wsdc.dancer', 'active')"
            )
            conn.execute(
                "INSERT INTO source_units (unit_key, watch_id, page_kind) VALUES ('w', 'w', 'wsdc.dancer')"
            )
            recipe = {
                "context": {"kind": "wsdc.dancer", "snapshot_id": "snap"},
                "accepted_inputs": [],
            }
            conn.execute(
                (
                    "INSERT INTO source_generations (generation_id, unit_key, page_kind, contract_version, "
                    "input_fingerprint, manifest_json, recipe_json, result_json, report_json, created_at, "
                    "run_id, state, removal_authority) VALUES ('sg', 'w', 'wsdc.dancer', 'v1', 'old', '[]', %s, "
                    "'{}', '{}', %s, %s, 'staged', 'none')"
                ),
                (json.dumps(recipe), NOW.isoformat(), run_id),
            )
            conn.execute(
                "UPDATE source_units SET accepted_generation_id = 'sg' WHERE unit_key = 'w'"
            )
        expected = verify_graph.enumerate_units(conn)
        proof = {
            "format": verify_graph.FORMAT,
            **verify_graph.context(conn),
            "checkpoint": verify_graph.checkpoint_identity(conn),
            "expected_units": expected,
            "results": [
                {"unit": name, "state": "match", "generation_id": "sg"} for name in expected
            ],
            "unit_set_digest": digest(canonical(expected)),
            "complete": True,
            "clean": True,
            "errors": [],
            "bumped_transforms": ["parse/wsdc.dancer"],
        }
        blocked = rekey.run(database, proof, now=NOW)
        assert blocked["excluded"] == expected
        assert conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 0
        proof["bumped_transforms"] = []
        clean = rekey.run(database, proof, now=NOW)
        assert clean["equivalences"] == {"parse": 1}
        assert conn.execute("SELECT desired_fingerprint FROM source_units").fetchone()[0] != "old"
        assert rekey.run(database, proof, now=NOW)["already_current"] == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_same_output_version_bump_is_detected_without_manual_flag(tmp_path: Path, legacy: bool):
    unit = WorkUnit("project", "dancer", "p")
    scope = "recipe/code/project/dancer"
    with open_database(tmp_path / "state") as database:
        old = {scope: "old-version"}
        if legacy:
            old["recipe/env"] = "old-runtime"
        seed(database, [unit], recipe=old)
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO accepted_inputs (consumer, input_name, digest) VALUES ('pipeline', %s, %s)",
                (scope, "new-version"),
            )
        proof = receipt(database)
        assert proof["clean"]
        result = rekey.run(database, proof, now=NOW)
        assert result["excluded"] == ["project/dancer/p"]
        assert result["bumped_transforms"] == ["project/dancer"]
        assert conn.execute("SELECT COUNT(*) FROM derivation_equivalences").fetchone()[0] == 0


def test_legacy_closure_resolves_retained_declarations_for_clean_rekey(tmp_path: Path):
    import json

    from swingset.state.recipes import capture_runtime

    scope = "recipe/code/project/dancer"
    files = capture_runtime()
    manifest = json.loads(files["recipes/scopes.json"])
    old_closure = digest(canonical(manifest["scopes"][scope]))
    declared = digest(canonical(manifest["declared"][scope]))
    with open_database(tmp_path / "state") as database:
        root = database.state_dir / "inputs" / "legacy"
        for name, body in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        seed(
            database,
            [WorkUnit("project", "dancer", "p")],
            recipe={scope: old_closure, "recipe/env": "legacy-runtime"},
        )
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO accepted_inputs (consumer, input_name, digest) VALUES ('pipeline', %s, %s)",
                (scope, declared),
            )
        result = rekey.run(database, receipt(database), now=NOW)
        assert result["excluded"] == []
        assert result["equivalences"] == {"project/dancer": 1}


def _legacy_parse_source(database, monkeypatch, *, recorded="4", change=None):
    import json
    from dataclasses import replace

    from test_admission import Corpus

    from swingset.admission import generations
    from swingset.state import recipe_scope
    from swingset.state.recipes import capture_runtime

    scope = "recipe/code/parse/eepro.round"
    files = capture_runtime()
    current = json.loads(files["recipes/scopes.json"])["declared"][scope]
    dispatch = "runtime/swingset/admission/kinds.py"
    if change != "present_declaration":
        files[dispatch] = files[dispatch].replace(b"CONTRACTS", b"LEGACY_CONTRACTS")
    if change == "adapter":
        name = "runtime/swingset/sources/eepro/adapter.py"
        files[name] = files[name].replace(b"PARSER_VERSION = 7", b"PARSER_VERSION = 6")
    if change == "present_declaration":
        name = "runtime/swingset/admission/round_contract.py"
        files[name] = files[name].replace(b'"eepro.round": "4"', b'"eepro.round": "3"')
    manifest = recipe_scope.manifest(files)
    old_code = digest(canonical(manifest["scopes"][scope]))
    files["recipes/scopes.json"] = canonical(manifest)
    runtime = json.loads(files["recipes/runtime.json"])
    runtime["files"] = {
        name: digest(body) for name, body in files.items() if name.startswith("runtime/")
    }
    files["recipes/runtime.json"] = canonical(runtime)
    root = database.state_dir / "inputs" / "legacy"
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    if change == "missing_archive":
        (root / "recipes/scopes.json").unlink()
    conn = database.connection
    conn.executemany(
        "INSERT INTO accepted_inputs(consumer,input_name,digest) VALUES('pipeline',%s,%s)",
        [(scope, old_code), ("recipe/env", "legacy-runtime")],
    )
    corpus = Corpus(database)
    context = corpus.snapshot("legacy-source")
    names = generations.parse_input_names
    with monkeypatch.context() as patch:
        patch.setattr(generations, "parse_input_names", lambda kind: names(kind) | {"recipe/env"})
        generation, report = corpus.stage(
            context, report_change=lambda value: replace(value, contract_version=recorded)
        )
        corpus.review(generation)
        assert corpus.admit(generation) == "accepted"
    conn.execute(
        "UPDATE accepted_inputs SET digest=%s WHERE consumer='pipeline' AND input_name=%s",
        (digest(canonical(current)), scope),
    )
    return corpus, generation, report, manifest


def _source_verify_receipt(database):
    expected = verify_graph.enumerate_units(database.connection)
    result = {
        "format": verify_graph.FORMAT,
        **verify_graph.context(database.connection),
        "checkpoint": verify_graph.checkpoint_identity(database.connection),
        "expected_units": expected,
        "results": [
            {
                "unit": name,
                "state": "match",
                "generation_id": database.connection.execute(
                    "SELECT accepted_generation_id FROM source_units WHERE unit_key=%s",
                    (name.split("/", 2)[2],),
                ).fetchone()[0],
            }
            for name in expected
        ],
        "unit_set_digest": digest(canonical(expected)),
        "complete": True,
        "clean": True,
        "errors": [],
    }
    return result


def test_legacy_parse_missing_contract_declaration_uses_recorded_version(tmp_path, monkeypatch):
    with open_database(tmp_path / "state") as database:
        _, generation, report, manifest = _legacy_parse_source(database, monkeypatch)
        assert (
            "admission.round_contract" not in manifest["declared"]["recipe/code/parse/eepro.round"]
        )
        before = database.connection.execute(
            "SELECT recipe_json,report_json FROM source_generations WHERE generation_id=%s",
            (generation,),
        ).fetchone()
        assert before["report_json"] == canonical(report.as_dict()).decode()
        result = rekey.run(database, _source_verify_receipt(database), now=NOW)
        assert result["bumped_transforms"] == [] and result["excluded"] == []
        assert result["equivalences"] == {"parse": 1}
        assert (
            database.connection.execute(
                "SELECT accepted_generation_id FROM source_units"
            ).fetchone()[0]
            == generation
        )
        assert tuple(
            database.connection.execute(
                "SELECT recipe_json,report_json FROM source_generations WHERE generation_id=%s",
                (generation,),
            ).fetchone()
        ) == tuple(before)
        assert (
            database.connection.execute("SELECT count(*) FROM source_generations").fetchone()[0]
            == 1
        )
        assert (
            database.connection.execute("SELECT count(*) FROM admission_decisions").fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "reason", ["recorded_contract", "present_declaration", "adapter", "missing_archive"]
)
def test_legacy_parse_contract_bridge_refuses_declared_changes_or_missing_code(
    tmp_path, monkeypatch, reason
):
    with open_database(tmp_path / "state") as database:
        _, generation, _, _ = _legacy_parse_source(
            database,
            monkeypatch,
            recorded="3" if reason == "recorded_contract" else "4",
            change=reason,
        )
        result = rekey.run(database, _source_verify_receipt(database), now=NOW)
        assert result["bumped_transforms"] == ["parse/eepro.round"]
        assert result["excluded"] and result["equivalences"] == {}
        assert (
            database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[0]
            == 0
        )
        assert (
            database.connection.execute(
                "SELECT accepted_generation_id FROM source_units"
            ).fetchone()[0]
            == generation
        )


def test_legacy_second_scope_checks_retained_member_hash_even_with_cached_versions(tmp_path):
    import json

    from swingset.state.recipes import capture_runtime

    files = capture_runtime()
    manifest = json.loads(files["recipes/scopes.json"])
    dancer, event = "recipe/code/project/dancer", "recipe/code/project/event"
    member = next(iter(manifest["scopes"][event]))
    manifest["scopes"][event][member] = "0" * 64
    files["recipes/scopes.json"] = canonical(manifest)
    with open_database(tmp_path / "state") as database:
        root = database.state_dir / "inputs" / "legacy"
        for name, body in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        seed(
            database,
            [WorkUnit("project", "dancer", "p"), WorkUnit("project", "event", "e")],
            recipe={
                dancer: digest(canonical(manifest["scopes"][dancer])),
                event: digest(canonical(manifest["scopes"][event])),
                "recipe/env": "legacy-runtime",
            },
        )
        database.connection.executemany(
            "INSERT INTO accepted_inputs(consumer,input_name,digest) VALUES('pipeline',%s,%s)",
            [(scope, digest(canonical(manifest["declared"][scope]))) for scope in (dancer, event)],
        )
        with pytest.raises(ValueError, match="source differs from its scope"):
            rekey.run(database, receipt(database), now=NOW)
        assert (
            database.connection.execute("SELECT count(*) FROM derivation_equivalences").fetchone()[
                0
            ]
            == 0
        )
