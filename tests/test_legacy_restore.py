"""Legacy compressed backups become exact offline SQLite import artifacts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tarfile
from pathlib import Path

import pyarrow as pa
import pytest

from swingset.backup.transport import LocalDirectoryTransport
from swingset.fetch.archive import Archive
from swingset.state.legacy_restore import unpack

SET_ID = "set_20261105T040000Z"
CANDIDATE = "cand_0123456789abcdef"


def sha(body):
    return hashlib.sha256(body).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def make_set(tmp_path, *, database_sql=None, omit_body=False, pending=False):
    state, transport = tmp_path / "source", tmp_path / "transport"
    state.mkdir()
    database = state / "state.sqlite"
    archive = Archive(state)
    raw_body = b"User-agent: *\nDisallow: /private\n"
    body = archive.store_body(raw_body)
    with sqlite3.connect(database) as conn:
        conn.executescript(Path("tests/fixtures/postgres/schema37.sql").read_text())
        conn.execute("PRAGMA user_version=37")
        conn.execute("INSERT INTO hosts(host,robots_sha256) VALUES('fixture.test',?)", (body,))
        if database_sql:
            conn.executescript(database_sql)
    candidate = state / "candidates" / CANDIDATE
    write_json(candidate / "_meta/manifest.json", {"files": {}})
    write_json(
        candidate / "BUILT",
        {"manifest_hash": sha((candidate / "_meta/manifest.json").read_bytes())},
    )
    write_json(candidate / "PUBLISHED", {"candidate_id": CANDIDATE, "commit": "baseline-A"})
    if pending:
        write_json(candidate / "PUBLISHING", {"candidate_id": CANDIDATE})
    members = [
        ("candidate", p, sha(p.read_bytes())) for p in sorted(candidate.rglob("*")) if p.is_file()
    ]
    if not omit_body:
        members.append(("body", archive.blob_path(body), body))
    temporary_tar = tmp_path / "segment.tar"
    with tarfile.open(temporary_tar, "w") as tar:
        for _, path, _ in members:
            tar.add(path, arcname=path.relative_to(state).as_posix(), recursive=False)
    segment = sha(temporary_tar.read_bytes())
    (transport / "segments").mkdir(parents=True)
    temporary_tar.rename(transport / "segments" / (segment + ".tar"))
    rows = [
        [kind, p.relative_to(state).as_posix(), digest, p.stat().st_size, segment]
        for kind, p, digest in members
    ]
    directory = transport / "sets" / SET_ID
    (directory / "db").mkdir(parents=True)
    compressed = pa.Codec("zstd").compress(database.read_bytes()).to_pybytes()
    halfway = len(compressed) // 2
    parts = []
    for number, block in enumerate((compressed[:halfway], compressed[halfway:])):
        path = f"db/part-{number:04}.zst"
        (directory / path).write_bytes(block)
        parts.append({"path": path, "bytes": len(block), "sha256": sha(block)})
    index = directory / "index.jsonl.zst"
    with pa.output_stream(str(index), compression="zstd") as stream:
        stream.write(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    classes = {}
    for kind, path, _ in members:
        values = classes.setdefault(kind, {"items": 0, "bytes": 0})
        values["items"] += 1
        values["bytes"] += path.stat().st_size
    manifest = {
        "format": "swingset-backup-set-v1",
        "set_id": SET_ID,
        "schema_version": 37,
        "database": {
            "parts": parts,
            "raw_bytes": database.stat().st_size,
            "raw_sha256": sha(database.read_bytes()),
        },
        "index": {
            "path": index.name,
            "bytes": index.stat().st_size,
            "sha256": sha(index.read_bytes()),
            "items": len(rows),
            "item_bytes": sum(row[3] for row in rows),
        },
        "segments": [segment],
        "classes": classes,
        "baseline_candidate": CANDIDATE,
        "pending_candidate": CANDIDATE if pending else None,
        "input_bundle_hash": None,
        "missing": [],
    }
    write_json(directory / "backup.json", manifest)
    return transport, directory, manifest


def rewrite_index(directory, manifest, change):
    index = directory / "index.jsonl.zst"
    with pa.input_stream(str(index), compression="zstd") as stream:
        rows = [json.loads(line) for line in stream.read().splitlines()]
    change(rows)
    with pa.output_stream(str(index), compression="zstd") as stream:
        stream.write(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    manifest["index"].update(bytes=index.stat().st_size, sha256=sha(index.read_bytes()))


@pytest.mark.parametrize("pending", [False, True])
def test_unpack_keeps_exact_database_files_and_publication_without_activation(tmp_path, pending):
    source, directory, manifest = make_set(tmp_path, pending=pending)
    original = {
        p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    target = tmp_path / "unpacked"
    report = unpack(
        LocalDirectoryTransport(source),
        target,
        set_id=SET_ID,
        expected_manifest_sha256=sha((directory / "backup.json").read_bytes()),
    )
    assert report["complete"] and report["closure"]["complete"]
    assert report["schema_version"] == 37
    assert report["state_sqlite"] == str(target / "state.sqlite")
    assert sha((target / "state.sqlite").read_bytes()) == manifest["database"]["raw_sha256"]
    assert (target / "baseline").is_symlink()
    assert (target / "baseline").resolve() == target / "candidates" / CANDIDATE
    assert json.loads((target / "baseline/PUBLISHED").read_text())["commit"] == "baseline-A"
    assert not (target / "database.json").exists()
    assert not (target / "RESTORE_PENDING").exists()
    assert report["closure"]["candidates"] == 1
    assert report["indexed_files"] == manifest["index"]["items"]
    assert original == {
        p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("existing", ["file", "empty", "populated", "symlink"])
def test_existing_targets_are_never_adopted_or_overwritten(tmp_path, existing):
    target = tmp_path / "target"
    if existing == "file":
        target.write_bytes(b"keep")
    elif existing == "symlink":
        real = tmp_path / "real"
        real.mkdir()
        (real / "keep").write_bytes(b"keep")
        target.symlink_to(real, target_is_directory=True)
    else:
        target.mkdir()
        if existing == "populated":
            (target / "keep").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        unpack(LocalDirectoryTransport(tmp_path / "absent"), target, set_id=SET_ID)
    assert target.exists()
    if existing == "file":
        assert target.read_bytes() == b"keep"
    elif existing != "empty":
        assert (target / "keep").read_bytes() == b"keep"


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda m: m.update(schema_version=38), "schema 37"),
        (lambda m: m["database"].update(format="postgres-history"), "native Postgres"),
        (lambda m: m["database"].update(raw_sha256="0" * 64), "joined database"),
        (lambda m: m["index"].update(items=m["index"]["items"] + 1), "counts or byte"),
        (lambda m: m["index"].update(item_bytes=m["index"]["item_bytes"] + 1), "counts or byte"),
        (lambda m: m["classes"]["body"].update(items=2), "classes differ"),
        (lambda m: m.update(missing=["blobs/gone"]), "required files"),
        (lambda m: m.update(baseline_candidate="../outside"), "relative|unsafe"),
        (lambda m: m.update(pending_candidate=CANDIDATE), "publication intent"),
    ],
)
def test_bad_manifest_is_rejected_without_placing_state(tmp_path, change, reason):
    source, directory, manifest = make_set(tmp_path)
    change(manifest)
    write_json(directory / "backup.json", manifest)
    target = tmp_path / "target"
    with pytest.raises(ValueError, match=reason):
        unpack(LocalDirectoryTransport(source), target, set_id=SET_ID)
    assert not target.exists()


@pytest.mark.parametrize("kind", ["part", "segment", "index", "manifest"])
def test_all_transport_hashes_are_checked(tmp_path, kind):
    source, directory, manifest = make_set(tmp_path)
    expected = None
    if kind == "manifest":
        expected = "0" * 64
    else:
        path = directory / ("db/part-0000.zst" if kind == "part" else "index.jsonl.zst")
        if kind == "segment":
            path = source / "segments" / (manifest["segments"][0] + ".tar")
        path.write_bytes(path.read_bytes() + b"changed")
    target = tmp_path / "target"
    with pytest.raises(ValueError, match="digest"):
        unpack(
            LocalDirectoryTransport(source),
            target,
            set_id=SET_ID,
            expected_manifest_sha256=expected,
        )
    assert not target.exists()


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda rows: rows[0].__setitem__(1, "../outside"), "relative|unsafe"),
        (lambda rows: rows[0].__setitem__(1, "state.sqlite"), "reserved"),
        (lambda rows: rows.append(rows[0]), "file twice"),
        (lambda rows: rows[0].__setitem__(4, "f" * 64), "unlisted segment"),
        (lambda rows: rows[0].__setitem__(2, "0" * 64), "digest"),
    ],
)
def test_checked_index_still_requires_safe_unique_exact_members(tmp_path, change, reason):
    source, directory, manifest = make_set(tmp_path)
    rewrite_index(directory, manifest, change)
    write_json(directory / "backup.json", manifest)
    target = tmp_path / "target"
    with pytest.raises(ValueError, match=reason):
        unpack(LocalDirectoryTransport(source), target, set_id=SET_ID)
    assert not target.exists()
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize(
    "database_sql,omit_body,reason",
    [
        ("DROP TRIGGER admission_review_immutable", False, "fingerprint"),
        (
            "INSERT INTO source_units(unit_key,watch_id,page_kind) VALUES('fixture','absent','fixture')",
            False,
            "foreign-key",
        ),
        (None, True, "referenced raw artifact"),
    ],
)
def test_database_constraints_and_referenced_file_closure_are_not_inferred_from_hashes(
    tmp_path, database_sql, omit_body, reason
):
    source, _, _ = make_set(tmp_path, database_sql=database_sql, omit_body=omit_body)
    target = tmp_path / "target"
    with pytest.raises(ValueError, match=reason):
        unpack(LocalDirectoryTransport(source), target, set_id=SET_ID)
    assert not target.exists()


def test_keep_failed_retains_fenced_raw_workspace_and_original_error(tmp_path, monkeypatch):
    from swingset.state import legacy_restore

    source, _, manifest = make_set(tmp_path)
    target = tmp_path / "failed-target"
    error = ValueError("153 legacy code scopes have no verified retained bundle")

    def reject_closure(*args, **kwargs):
        raise error

    monkeypatch.setattr(legacy_restore, "verify_sqlite_files", reject_closure)
    with pytest.raises(ValueError) as caught:
        unpack(LocalDirectoryTransport(source), target, set_id=SET_ID, keep_failed=True)
    assert caught.value is error
    failure = json.loads((target / "UNPACK_FAILED.json").read_text())
    workspace = Path(failure["workspace"])
    assert failure["complete"] is False
    assert (target / "RESTORE_PENDING").is_file()
    assert (workspace / "state/RESTORE_PENDING").is_file()
    assert not (target / "state.sqlite").exists()
    assert (
        sha((workspace / "state/state.sqlite").read_bytes()) == manifest["database"]["raw_sha256"]
    )
    assert (workspace / "fetch").is_dir()
    assert (workspace.stat().st_mode & 0o777) == 0o700
    with pytest.raises(FileExistsError):
        unpack(LocalDirectoryTransport(source), target, set_id=SET_ID, keep_failed=True)
    assert (workspace / "state/state.sqlite").is_file()


def test_keep_failed_success_removes_fence_and_private_workspace(tmp_path):
    source, _, _ = make_set(tmp_path)
    target = tmp_path / "complete-target"
    assert unpack(LocalDirectoryTransport(source), target, set_id=SET_ID, keep_failed=True)[
        "complete"
    ]
    assert not (target / "RESTORE_PENDING").exists()
    assert not list(tmp_path.glob(".sqlite37-*"))


def supplemental_bundle(root, *, payload=b"archived code, never executed\n"):
    from swingset.fetch.archive import canonical

    scope = {"code/module.py": sha(payload)}
    descriptor = canonical(
        {"format": "swingset-scoped-recipe-v1", "env": {}, "scopes": {"recipe/code/build": scope}}
    )
    members = {"code/module.py": payload, "recipes/scopes.json": descriptor}
    manifest = canonical({name: sha(body) for name, body in members.items()})
    bundle = sha(manifest)
    directory = root / bundle
    for name, body in {**members, "manifest.json": manifest}.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return bundle, sha(canonical(scope))


def test_separate_verified_bundle_completes_original_legacy_scope_closure(tmp_path):
    supplements = tmp_path / "supplements"
    bundle, digest = supplemental_bundle(supplements)
    recipe = json.dumps({"inputs": {"recipe/env": "a" * 64, "recipe/code/build": digest}})
    source, directory, manifest = make_set(
        tmp_path, database_sql="INSERT INTO derivation_recipes VALUES('recipe', '" + recipe + "');"
    )
    original_manifest = (directory / "backup.json").read_bytes()
    with pytest.raises(ValueError, match="no verified retained bundle"):
        unpack(LocalDirectoryTransport(source), tmp_path / "without", set_id=SET_ID)
    report = unpack(
        LocalDirectoryTransport(source),
        tmp_path / "with",
        set_id=SET_ID,
        supplemental_inputs=supplements,
    )
    assert report["closure"]["legacy_code_scopes"] == 1
    assert report["closure"]["input_bundles"] == 1
    assert report["indexed_files"] == manifest["index"]["items"]
    assert report["supplemental_input_bundles"][0]["bundle"] == bundle
    assert report["supplemental_input_bundles"][0]["added"] is True
    assert report["source_sha256"] == manifest["database"]["raw_sha256"]
    assert (directory / "backup.json").read_bytes() == original_manifest
    assert (
        tmp_path / "with/inputs" / bundle / "code/module.py"
    ).read_bytes() == b"archived code, never executed\n"


@pytest.mark.parametrize("damage", ["member", "extra", "symlink", "directory-link", "special"])
def test_supplemental_bundles_reject_corruption_and_unlisted_or_indirect_files(tmp_path, damage):
    import os

    from swingset.state.legacy_restore import _supplement_inputs

    root, stage = tmp_path / "supplements", tmp_path / "stage"
    bundle, _ = supplemental_bundle(root)
    directory = root / bundle
    if damage == "member":
        (directory / "code/module.py").write_bytes(b"changed")
    elif damage == "extra":
        (directory / "extra").write_bytes(b"unlisted")
    elif damage == "symlink":
        path = directory / "code/module.py"
        body = path.read_bytes()
        path.unlink()
        (tmp_path / "external").write_bytes(body)
        path.symlink_to(tmp_path / "external")
    elif damage == "directory-link":
        (directory / "alias").symlink_to(directory / "code", target_is_directory=True)
    else:
        os.mkfifo(directory / "pipe")
    stage.mkdir()
    with pytest.raises(ValueError):
        _supplement_inputs(root, stage)
    assert not (stage / "inputs").exists()


def test_supplement_never_replaces_checkpoint_bundle_or_its_manifest_bytes(tmp_path):
    from swingset.state.legacy_restore import _supplement_inputs

    root, stage = tmp_path / "supplements", tmp_path / "stage"
    bundle, _ = supplemental_bundle(root)
    supplemental_bundle(stage / "inputs")
    report = _supplement_inputs(root, stage)
    assert report[0]["added"] is False
    manifest = stage / "inputs" / bundle / "manifest.json"
    # Same canonical bundle identity is insufficient to overwrite frozen bytes.
    changed = json.dumps(json.loads(manifest.read_bytes()), indent=2).encode()
    manifest.write_bytes(changed)
    with pytest.raises(ValueError, match="checkpoint bytes"):
        _supplement_inputs(root, stage)
    assert manifest.read_bytes() == changed


def test_changed_supplement_during_copy_cannot_pass_verification(tmp_path, monkeypatch):
    from swingset.state import legacy_restore

    root, stage = tmp_path / "supplements", tmp_path / "stage"
    supplemental_bundle(root)
    stage.mkdir()
    original = legacy_restore.shutil.copyfileobj

    def corrupt(incoming, outgoing, **kwargs):
        original(incoming, outgoing, **kwargs)
        if incoming.name.endswith("module.py"):
            outgoing.write(b"changed while copying")

    monkeypatch.setattr(legacy_restore.shutil, "copyfileobj", corrupt)
    with pytest.raises(ValueError, match="missing or corrupt"):
        legacy_restore._supplement_inputs(root, stage)
