"""Captured-map file closure cannot silently omit historical raw evidence."""

from __future__ import annotations

import gzip
import hashlib
import json

import pytest
from test_postgres_schema import fresh_database  # noqa: F401

from swingset.fetch.archive import Archive, canonical
from swingset.state import history_catalog as history
from swingset.state import postgres_backup as backup
from swingset.state import postgres_files
from swingset.state import postgres_schema as schema


def test_source_pack_manifest_reader_streams_escaped_unicode(tmp_path):
    captures = [{"body_sha256": "a" * 64, "note": '雪/😀\\"\n'}] * 1000
    encoded = json.dumps(
        {
            "manifest": json.dumps(captures, ensure_ascii=True),
            "report": "large report" * 100_000,
            "result": "{}",
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    path = tmp_path / "pack.gz"
    path.write_bytes(gzip.compress(encoded))
    assert list(postgres_files._manifest_entries(postgres_files._packed_manifest(path))) == captures
    for malformed in ("[{},]", "[{}{}]", "{}", "[1]", "[{}", "[] garbage"):
        with pytest.raises(ValueError):
            list(postgres_files._manifest_entries(iter(malformed)))


def test_selected_raw_file_must_be_present_in_captured_mapping(fresh_database, tmp_path):  # noqa: F811
    _, _, conn = fresh_database
    schema.migrate(conn)
    archive = Archive(tmp_path / "state")
    digest = archive.store_body(b"fixture robots")
    conn.execute(
        'INSERT INTO "fetch".hosts(host,robots_sha256) VALUES(%s,%s)', ("example.test", digest)
    )
    backup.prepare(conn)
    name = archive.blob_path(digest).relative_to(archive.state_dir).as_posix()
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with pytest.raises(ValueError, match="missing captured file"):
            backup.export(conn, tmp_path / "missing", files={}, runtime_identity="fixture")
        result = backup.export(
            conn,
            tmp_path / "complete",
            files={name: archive.blob_path(digest)},
            runtime_identity="fixture",
        )
    assert result["file_closure"]["required_files"] == 1


def test_input_bundle_member_missing_or_corrupt_rejects_export(fresh_database, tmp_path):  # noqa: F811
    _, _, conn = fresh_database
    schema.migrate(conn)
    member = tmp_path / "config.toml"
    member.write_bytes(b"fixture captured input")
    manifest = {"config.toml": hashlib.sha256(member.read_bytes()).hexdigest()}
    digest = hashlib.sha256(canonical(manifest)).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(canonical(manifest))
    conn.execute("INSERT INTO ops.meta(key,value) VALUES('input_bundle_hash',%s)", (digest,))
    history.prepare(conn)
    files = {f"inputs/{digest}/manifest.json": manifest_path}
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        history.select_history(conn)
        with pytest.raises(ValueError, match="missing captured file"):
            postgres_files.verify(conn, files, {})
        files[f"inputs/{digest}/config.toml"] = member
        assert postgres_files.verify(conn, files, {})["input_bundles"] == 1
        member.write_bytes(b"corrupt")
        with pytest.raises(ValueError, match="member digest"):
            postgres_files.verify(conn, files, {})


def test_legacy_equivalence_code_witness_is_required_by_live_and_offline_closure(
    fresh_database,  # noqa: F811
    tmp_path,
):
    from test_history_catalog import seed_history
    from test_legacy_recipes import SCOPE, bundle

    from swingset.state.legacy_recipes import MissingLegacyRecipes

    _, _, conn = fresh_database
    seed_history(conn)
    identity, group, files = bundle(tmp_path / "captured")
    recipe = canonical(
        {
            "accepted_inputs": [
                ["pipeline", "recipe/env", "b" * 64],
                ["pipeline", SCOPE, hashlib.sha256(canonical(group)).hexdigest()],
            ]
        }
    ).decode()
    conn.execute(
        "INSERT INTO evidence.source_equivalences(unit_key,input_fingerprint,generation_id,recipe_json,work_token,created_at,run_id) VALUES('history-unit','legacy-scope-input','source-previous',%s,'original-token','2026-11-04T00:00:00Z','history-run')",
        (recipe,),
    )
    backup.prepare(conn)
    target = tmp_path / "history"
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with pytest.raises(MissingLegacyRecipes):
            backup.export(conn, tmp_path / "missing-code", files={}, runtime_identity="fixture")
        result = backup.export(conn, target, files=files, runtime_identity="fixture")
    assert result["file_closure"]["legacy_code_scopes"] == 1
    assert result["file_closure"]["input_bundle_hashes"] == [identity]
    assert backup.verify(target)["complete"]

    # Removing the witness and rewriting every declared file entry still leaves
    # the independent COPY recipe reader with an unresolved code reference.
    import shutil

    shutil.rmtree(target / "files" / "inputs")
    manifest = json.loads((target / "history.json").read_bytes())
    manifest["files"] = {
        name: value for name, value in manifest["files"].items() if not name.startswith("inputs/")
    }
    manifest["file_closure"]["input_bundle_hashes"] = []
    (target / "history.json").write_bytes(canonical(manifest))
    with pytest.raises(MissingLegacyRecipes):
        backup.verify(target)
