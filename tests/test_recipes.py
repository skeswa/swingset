"""Captured policy identity excludes acquisition-only scheduling controls."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from swingset.admission.generations import parse_inputs
from swingset.clock import FakeClock
from swingset.fetch.archive import canonical, digest
from swingset.state import inputs, recipes
from swingset.state.db import open_database


def _fingerprint_package(tmp_path, monkeypatch):
    package = tmp_path / "project/src/swingset"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n")
    (package / "payload.txt").write_text("payload\n")
    (package.parent.parent / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "1"\n'
    )
    (package.parent.parent / "uv.lock").write_text("version = 1\n")
    monkeypatch.setenv("SWINGSET_DEPENDENCY_IMAGE", "sha256:" + digest(str(tmp_path).encode()))
    return package


def _uncached_fingerprint(package):
    return digest(
        canonical({name: digest(body) for name, body in recipes.capture_runtime(package).items()})
    )


def test_runtime_fingerprint_matches_full_capture_and_reuses_derived_work(tmp_path, monkeypatch):
    package = _fingerprint_package(tmp_path, monkeypatch)
    expected_files = recipes.capture_runtime(package)
    expected = digest(canonical({name: digest(body) for name, body in expected_files.items()}))
    first = recipes.runtime_fingerprint(package)
    assert first == expected
    with monkeypatch.context() as context:
        context.setattr(
            recipes.recipe_scope,
            "manifest",
            lambda files: pytest.fail("unchanged fingerprint rebuilt the derived manifest"),
        )
        assert recipes.runtime_fingerprint(package) == first
    assert recipes.capture_runtime(package) == expected_files


@pytest.mark.parametrize("change", ["code", "new_file", "deleted_file", "project", "lock"])
def test_runtime_fingerprint_reads_current_bytes_and_file_inventory(tmp_path, monkeypatch, change):
    package = _fingerprint_package(tmp_path, monkeypatch)
    before = recipes.runtime_fingerprint(package)
    if change == "new_file":
        (package / "new.txt").write_text("new bytes\n")
    elif change == "deleted_file":
        (package / "payload.txt").unlink()
    else:
        path = {
            "code": package / "__init__.py",
            "project": package.parent.parent / "pyproject.toml",
            "lock": package.parent.parent / "uv.lock",
        }[change]
        original = path.stat()
        path.write_bytes(path.read_bytes().replace(b"1", b"2"))
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        assert (path.stat().st_size, path.stat().st_mtime_ns) == (
            original.st_size,
            original.st_mtime_ns,
        )
    after = recipes.runtime_fingerprint(package)
    assert after != before
    assert after == _uncached_fingerprint(package)


def test_runtime_fingerprint_rechecks_dependency_image(tmp_path, monkeypatch):
    package = _fingerprint_package(tmp_path, monkeypatch)
    before = recipes.runtime_fingerprint(package)
    monkeypatch.setenv("SWINGSET_DEPENDENCY_IMAGE", "sha256:" + "b" * 64)
    after = recipes.runtime_fingerprint(package)
    assert after != before
    assert after == _uncached_fingerprint(package)


def test_runtime_fingerprint_rechecks_installed_dependency_records(tmp_path, monkeypatch):
    package = _fingerprint_package(tmp_path, monkeypatch)
    monkeypatch.delenv("SWINGSET_DEPENDENCY_IMAGE")
    record = tmp_path / "RECORD"
    record.write_text("first\n")
    distribution = SimpleNamespace(
        metadata={"Name": "fixture-dependency"},
        version="1",
        read_text=lambda name: record.read_text(),
    )
    monkeypatch.setattr(recipes.importlib.metadata, "distributions", lambda: [distribution])
    before = recipes.runtime_fingerprint(package)
    metadata = record.stat()
    record.write_text("other\n")
    os.utime(record, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    assert (record.stat().st_size, record.stat().st_mtime_ns) == (
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    after = recipes.runtime_fingerprint(package)
    assert after != before
    assert after == _uncached_fingerprint(package)


def test_runtime_fingerprint_rejects_missing_required_code_after_a_hit(tmp_path, monkeypatch):
    package = _fingerprint_package(tmp_path, monkeypatch)
    recipes.runtime_fingerprint(package)
    (package / "__init__.py").unlink()
    with pytest.raises(ValueError, match="missing its source artifact"):
        recipes.runtime_fingerprint(package)


def test_runtime_fingerprint_does_not_cache_a_failed_derived_build(tmp_path, monkeypatch):
    package = _fingerprint_package(tmp_path, monkeypatch)
    manifest = recipes.recipe_scope.manifest
    attempted = []

    def unavailable_once(files):
        if not attempted:
            attempted.append(True)
            raise RuntimeError("manifest temporarily unavailable")
        return manifest(files)

    monkeypatch.setattr(recipes.recipe_scope, "manifest", unavailable_once)
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        recipes.runtime_fingerprint(package)
    assert recipes.runtime_fingerprint(package) == _uncached_fingerprint(package)


def test_only_the_runtime_parts_of_pyproject_and_the_lock_change_the_environment(tmp_path):
    """A `[tool.*]` edit or a lock change (a dev tool bump) would re-parse every
    snapshot if `recipe/env` digested the whole files. It reads the installed
    distributions and pyproject's `[project]` table; both files stay in the
    bundle."""
    project = tmp_path / "project"
    package = project / "src/swingset"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n")
    pyproject = project / "pyproject.toml"
    pyproject.write_text('[project]\nname = "swingset"\ndependencies = ["a"]\n')
    lock = project / "uv.lock"
    lock.write_text("version = 1\n")

    def env() -> str:
        files = recipes.capture_runtime(package)
        assert "runtime/pyproject.toml" in files and "runtime/uv.lock" in files
        return recipes.captured_audit_inputs(files)["recipe/env"]

    first = env()
    pyproject.write_text(
        '[project]\nname = "swingset"\ndependencies = ["a"]\n[tool.ruff]\nline-length = 99\n'
    )
    lock.write_text('version = 1\n[[package]]\nname = "ruff"\nversion = "0.99"\n')
    assert env() == first
    pyproject.write_text('[project]\nname = "swingset"\ndependencies = ["a", "b"]\n')
    assert env() != first


def test_runtime_bytes_are_retained_once_as_scoped_recipes(tmp_path, monkeypatch):
    package = tmp_path / "project/src/swingset"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n")
    artifact = recipes.capture_runtime(package)
    monkeypatch.setattr(inputs, "capture_runtime", lambda: artifact)
    with open_database(tmp_path / "state") as db:
        bundle = inputs.capture(Path("config"), Path("overrides"), db.state_dir, {})
        inputs.accept(db, bundle, FakeClock())
        assert (bundle.path / "runtime/swingset/__init__.py").read_bytes() == b"VALUE = 1\n"
        assert (bundle.path / "recipes/scopes.json").is_file()
        accepted = dict(db.connection.execute("SELECT input_name, digest FROM accepted_inputs"))
        env = {
            name: bundle.file_hashes[name]
            for name in ("runtime/environment.json", "runtime/project.json")
            if name in bundle.file_hashes
        }
        assert accepted["recipe/env"] == digest(canonical(env))
        assert "recipe/runtime" not in accepted
        assert not any(name.startswith("runtime/") for name in accepted)
        assert not any(name.startswith("recipes/") for name in accepted)
        project = recipes.recipe_inputs(db.connection, "project", "event")
        assert "config/sources.toml" not in project
        assert "config/hosts.toml" not in project
        assert project["policy/history_start"] == accepted["policy/history_start"]
        assert "recipe/env" not in project
        assert not inputs.accept(db, bundle, FakeClock())


def test_each_unit_selects_only_its_own_code_scope(tmp_path):
    names = {
        "recipe/env": "env",
        "policy/history_start": "start",
        "recipe/code/project/event": "event",
        "recipe/code/project/dancer": "dancer",
        "recipe/code/link": "link",
        "recipe/code/build": "build",
        "recipe/code/extract/eepro.round": "extract-round",
        "recipe/code/parse/eepro.round": "parse-round",
        "recipe/code/parse/eepro.index": "parse-index",
        "version/extract/eepro.round": "4",
        "version/parser/eepro.round": "7",
        "version/parser/eepro.index": "2",
        "version/repository": "source-sha256:any",
        "overrides/event_aliases.csv": "aliases",
    }
    with open_database(tmp_path) as db:
        db.connection.executemany(
            "INSERT INTO accepted_inputs (consumer, input_name, digest) VALUES ('pipeline', %s, %s)",
            names.items(),
        )
        conn = db.connection
        event = recipes.recipe_inputs(conn, "project", "event")
        assert event["recipe/code/project/event"] == "event"
        assert "recipe/code/project/dancer" not in event
        assert "overrides/event_aliases.csv" in event
        assert set(recipes.recipe_inputs(conn, "link")) == {
            "policy/history_start",
            "recipe/code/link",
        }
        assert "recipe/code/build" in recipes.recipe_inputs(conn, "build")
        parse = recipes.recipe_inputs(conn, "parse", "eepro.round")
        assert set(parse) == {
            "policy/history_start",
            "recipe/code/extract/eepro.round",
            "recipe/code/parse/eepro.round",
        }
        with pytest.raises(ValueError, match="page kind"):
            recipes.recipe_inputs(conn, "parse")
        # Source-generation recipes add the kind's two labels and nothing else.
        assert parse_inputs(conn, "eepro.round") == tuple(
            ("pipeline", name, names[name])
            for name in sorted(
                {*parse, "version/extract/eepro.round", "version/parser/eepro.round"}
            )
        )


def test_a_unit_without_an_accepted_code_scope_fails_closed(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.executemany(
            "INSERT INTO accepted_inputs (consumer, input_name, digest) VALUES ('pipeline', %s, %s)",
            (("recipe/env", "env"), ("recipe/code/project/event", "event")),
        )
        assert "recipe/code/project/event" in recipes.recipe_inputs(conn, "project", "event")
        # A kind with no scope would carry no code identity and never re-run.
        with pytest.raises(ValueError, match="no code scope recipe/code/project/inventory"):
            recipes.recipe_inputs(conn, "project", "inventory")
        with pytest.raises(ValueError, match="no code scope recipe/code/link"):
            recipes.recipe_inputs(conn, "link")
        # A helper bundle names no kind and no code.
        assert "recipe/env" not in recipes.recipe_inputs(conn, "project")


@pytest.mark.parametrize(
    "stage,kind,scope",
    [
        ("project", "event", "recipe/code/project/event"),
        ("project", "year", "recipe/code/project/year"),
        ("link", None, "recipe/code/link"),
        ("build", None, "recipe/code/build"),
    ],
)
def test_scoped_recipe_lookup_uses_one_snapshot_and_preserves_missing_scope_guards(
    tmp_path, stage, kind, scope
):
    with open_database(tmp_path) as db:
        conn = db.connection

        def lookup():
            statements = []
            with conn.observing(lambda sql, params: statements.append(str(sql))):
                try:
                    result = recipes.recipe_inputs(conn, stage, kind)
                finally:
                    assert len(statements) == 1
            return result

        assert lookup() == {}  # No accepted runtime yet.
        conn.execute("INSERT INTO accepted_inputs VALUES('audit','recipe/env','environment')")
        with pytest.raises(recipes.RecipeScopeMissing, match=scope):
            lookup()  # Audit presence matters even without a pipeline row.
        conn.execute("DELETE FROM accepted_inputs")
        conn.execute(
            "INSERT INTO accepted_inputs VALUES('pipeline','RECIPE/CODE/unrelated','other')"
        )
        with pytest.raises(recipes.RecipeScopeMissing, match=scope):
            lookup()  # A global code scope cannot disappear through selection.
        conn.execute("DELETE FROM accepted_inputs")
        conn.execute(
            "INSERT INTO accepted_inputs VALUES('pipeline','policy/history_start','start')"
        )
        assert lookup() == {"policy/history_start": "start"}  # Legacy unscoped runtime.
        conn.execute("INSERT INTO accepted_inputs VALUES('pipeline',%s,'code')", (scope,))
        conn.executemany(
            "INSERT INTO accepted_inputs VALUES('pipeline',%s,'unselected')",
            [(f"unselected/{index}",) for index in range(100)],
        )
        assert lookup() == {"policy/history_start": "start", scope: "code"}
        conn.execute("DELETE FROM accepted_inputs WHERE input_name=%s", (scope,))
        conn.execute("INSERT INTO accepted_inputs VALUES('audit','recipe/env','environment')")
        with pytest.raises(recipes.RecipeScopeMissing, match=scope):
            lookup()


def test_every_project_unit_kind_has_a_projector_scope():
    from swingset.state.derivation_dependencies import BASE
    from swingset.state.recipe_scope import PROJECTORS

    assert set(PROJECTORS) == BASE | {
        "inventory",
        "mapping",
        "resolution",
        "event",
        "source_event",
        "year",
    }


def _reseal(files):
    from swingset.state import recipe_scope

    result = {name: body for name, body in files.items() if not name.startswith("recipes/")}
    result["recipes/runtime.json"] = canonical(
        {
            "format": "swingset-runtime-recipe-v1",
            "files": {name: digest(body) for name, body in sorted(result.items())},
        }
    )
    result["recipes/scopes.json"] = canonical(recipe_scope.manifest(result))
    return result


def test_code_and_runtime_edits_change_audit_but_only_declared_bumps_change_recipes():
    original = recipes.capture_runtime()
    path = "runtime/swingset/project/registry.py"
    changed = _reseal(
        {
            **original,
            path: original[path] + b"\n# implementation edit\n",
            "runtime/environment.json": b'{"new_runtime":true}',
        }
    )
    before = recipes.captured_recipe_inputs(original, history_start="2010-01-01")
    after = recipes.captured_recipe_inputs(changed, history_start="2010-01-01")
    differences = {name for name in before if before[name] != after[name]}
    assert differences == {"recipe/code/build"}
    assert "recipe/env" not in after
    audit = recipes.captured_audit_inputs(changed)
    assert audit["recipe/env"] != recipes.captured_audit_inputs(original)["recipe/env"]
    assert (
        audit["recipe/code/project/dancer"]
        != recipes.captured_audit_inputs(original)["recipe/code/project/dancer"]
    )
    bumped = _reseal({**changed, path: changed[path].replace(b"VERSION = 1", b"VERSION = 2", 1)})
    versioned = recipes.captured_recipe_inputs(bumped, history_start="2010-01-01")
    assert {name for name in after if after[name] != versioned[name]} == {
        "recipe/code/project/dancer",
        "recipe/code/build",
    }


def test_contract_version_bump_changes_only_its_page_kind():
    original = recipes.capture_runtime()
    path = "runtime/swingset/admission/round_contract.py"
    bumped = _reseal(
        {
            **original,
            path: original[path].replace(
                b'"scoringdance.round": "5"', b'"scoringdance.round": "6"'
            ),
        }
    )
    before = recipes.captured_recipe_inputs(original, history_start="2010-01-01")
    after = recipes.captured_recipe_inputs(bumped, history_start="2010-01-01")
    assert {name for name in before if before[name] != after[name]} == {
        "recipe/code/parse/scoringdance.round"
    }


def test_old_scoped_bundle_keeps_its_captured_byte_identity():
    import json

    original = recipes.capture_runtime()
    manifest = json.loads(original["recipes/scopes.json"])
    del manifest["declared"]
    old = {**original, "recipes/scopes.json": canonical(manifest)}
    identities = recipes.captured_recipe_inputs(old, history_start="2010-01-01")
    assert identities["recipe/env"] == recipes.captured_audit_inputs(old)["recipe/env"]
    assert identities["recipe/code/link"] == recipes.captured_audit_inputs(old)["recipe/code/link"]


def test_nonliteral_version_is_rejected_without_executing_source():
    original = recipes.capture_runtime()
    path = "runtime/swingset/project/registry.py"
    with pytest.raises(ValueError, match="literal declared version"):
        _reseal(
            {**original, path: original[path].replace(b"VERSION = 1", b"VERSION = dangerous()", 1)}
        )


def test_captured_legacy_contract_table_keeps_its_exact_declared_versions():
    from swingset.state import recipe_scope

    original = recipes.capture_runtime()
    path = "runtime/swingset/admission/dcn_contract.py"
    legacy = {
        **original,
        path: original[path].replace(
            b'VERSIONS = {"dcn.archive": "1", "dcn.event": "1"}',
            b'VERSIONS = dict.fromkeys(sorted(KINDS), "1")',
        ),
    }
    assert legacy[path] != original[path]
    assert recipe_scope.declared_versions(legacy) == recipe_scope.declared_versions(original)


def test_shared_member_validation_checks_each_group_and_fresh_bytes_on_every_call():
    from swingset.state import recipe_scope

    member = "runtime/swingset/example.py"
    body = b"VALUE = 1\n"
    groups = {
        "format": recipe_scope.FORMAT,
        "env": {},
        "scopes": {
            "recipe/code/project/first": {member: digest(body)},
            "recipe/code/project/second": {member: digest(body)},
        },
    }
    original = {
        member: body,
        "recipes/runtime.json": canonical(
            {"format": "swingset-runtime-recipe-v1", "files": {member: digest(body)}}
        ),
        "recipes/scopes.json": canonical(groups),
    }
    expected = recipes.captured_recipe_inputs(original, history_start="2010-01-01")
    groups["scopes"]["recipe/code/project/second"][member] = "0" * 64
    bad_group = {**original, "recipes/scopes.json": canonical(groups)}
    with pytest.raises(ValueError, match="differs from its scoped recipe"):
        recipes.captured_recipe_inputs(bad_group, history_start="2010-01-01")
    changed = b"VALUE = 2\n"
    changed_member = {
        **original,
        member: changed,
        "recipes/runtime.json": canonical(
            {"format": "swingset-runtime-recipe-v1", "files": {member: digest(changed)}}
        ),
    }
    with pytest.raises(ValueError, match="differs from its scoped recipe"):
        recipes.captured_recipe_inputs(changed_member, history_start="2010-01-01")
    assert recipes.captured_recipe_inputs(original, history_start="2010-01-01") == expected


def test_declaration_reuse_does_not_keep_changed_bytes_or_mutated_results():
    from swingset.state import recipe_scope

    original = recipes.capture_runtime()
    before = recipe_scope.declared_versions(original)
    path = "runtime/swingset/project/registry.py"
    modified = {**original, path: original[path].replace(b"VERSION = 1", b"VERSION = 99", 1)}
    assert modified[path] != original[path]
    after = recipe_scope.declared_versions(modified)
    scope = "recipe/code/project/dancer"
    assert before[scope]["project.registry"] == 1
    assert after[scope]["project.registry"] == 99
    after[scope]["project.registry"] = "caller mutation"
    assert recipe_scope.declared_versions(modified)[scope]["project.registry"] == 99
    assert recipe_scope.declared_versions(original) == before
    malformed = {
        **original,
        path: original[path].replace(b"VERSION = 1", b"VERSION = dangerous()", 1),
    }
    with pytest.raises(ValueError, match="literal declared version"):
        recipe_scope.declared_versions(malformed)


@pytest.mark.parametrize(
    "replacement",
    [
        b"VERSIONS = dict.fromkeys(sorted(KINDS), dangerous())",
        b'sorted = lambda value: ["different"]\nVERSIONS = dict.fromkeys(sorted(KINDS), "1")',
    ],
)
def test_captured_legacy_contract_constructor_refuses_dynamic_or_shadowed_code(replacement):
    from swingset.state import recipe_scope

    original = recipes.capture_runtime()
    path = "runtime/swingset/admission/dcn_contract.py"
    legacy = {
        **original,
        path: original[path].replace(
            b'VERSIONS = {"dcn.archive": "1", "dcn.event": "1"}',
            replacement,
        ),
    }
    with pytest.raises(ValueError, match="literal contract version"):
        recipe_scope.declared_versions(legacy)


def test_audited_runtime_with_all_semantic_scopes_missing_fails_closed(tmp_path):
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO accepted_inputs (consumer, input_name, digest) VALUES ('audit', 'recipe/env', 'runtime')"
        )
        with pytest.raises(recipes.RecipeScopeMissing, match="recipe/code/project/event"):
            recipes.recipe_inputs(db.connection, "project", "event")


def test_runtime_capture_keeps_fresh_files_and_original_order_without_following_directories(
    tmp_path, monkeypatch
):
    import builtins
    import io
    import os

    package = tmp_path / "project/src/swingset"
    package.mkdir(parents=True)
    for name, body in {
        "__init__.py": b"VALUE = 1\n",
        "a/file.txt": b"nested",
        "a.py": b"sibling",
        "directory.pyc/keep.txt": b"keep directory contents",
        ".pyc": b"a filename with no suffix",
        "..pyc": b"excluded bytecode",
        "ignored.pyc": b"excluded bytecode",
        "__pycache__/ignored.txt": b"excluded cache",
        "nested/__pycache__/ignored.py": b"excluded cache",
    }.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"first target")
    (package / "linked.txt").symlink_to(outside)
    external_directory = tmp_path / "external-directory"
    external_directory.mkdir()
    (external_directory / "outside.py").write_bytes(b"not captured")
    (package / "directory-link").symlink_to(external_directory, target_is_directory=True)
    (package / "broken-link").symlink_to(tmp_path / "missing")
    (package / "loop-link").symlink_to("loop-link")
    fifo = package / "named-pipe"
    os.mkfifo(fifo)
    expected = {
        "runtime/swingset/" + path.relative_to(package).as_posix(): path.read_bytes()
        for path in sorted(package.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    for module, opener in ((builtins, builtins.open), (io, io.open)):

        def guarded(path, *args, _opener=opener, **kwargs):
            if isinstance(path, str | bytes | os.PathLike):
                assert os.fsdecode(path) != os.fspath(fifo), "a FIFO must never be opened"
            return _opener(path, *args, **kwargs)

        monkeypatch.setattr(module, "open", guarded)
    first = recipes.capture_runtime(package)
    captured = {key: value for key, value in first.items() if key.startswith("runtime/swingset/")}
    assert captured == expected
    assert list(captured) == list(expected)
    outside.write_bytes(b"changed target")
    second = recipes.capture_runtime(package)
    assert first["runtime/swingset/linked.txt"] == b"first target"
    assert second["runtime/swingset/linked.txt"] == b"changed target"


def test_runtime_capture_prunes_cache_before_scanning_and_keeps_permission_skip(
    tmp_path, monkeypatch
):
    import os

    package = tmp_path / "project/src/swingset"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"VALUE = 1\n")
    for name in ("__pycache__", "denied"):
        (package / name).mkdir()
        (package / name / "not-readable.py").write_bytes(b"not captured")
    original = os.scandir

    def scandir(path):
        assert Path(path).name != "__pycache__", "cache directory was traversed"
        if Path(path).name == "denied":
            raise PermissionError("directory access denied")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", scandir)
        result = recipes.capture_runtime(package)
        assert [key for key in result if key.startswith("runtime/swingset/")] == [
            "runtime/swingset/__init__.py"
        ]
        cached_root = tmp_path / "__pycache__/swingset"
        cached_root.mkdir(parents=True)
        (cached_root / "__init__.py").write_bytes(b"VALUE = 1\n")
        with pytest.raises(ValueError, match="missing its source artifact"):
            recipes.capture_runtime(cached_root)


@pytest.mark.parametrize("error_type", [PermissionError, OSError])
def test_runtime_capture_propagates_real_file_type_errors(tmp_path, monkeypatch, error_type):
    import errno
    import os
    from contextlib import contextmanager

    package = tmp_path / "project/src/swingset"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"VALUE = 1\n")
    original = os.scandir

    class Entry:
        def __init__(self, entry):
            self.name, self.path = entry.name, entry.path

        def is_dir(self, *, follow_symlinks):
            return False

        def is_file(self):
            raise error_type(
                errno.EACCES if error_type is PermissionError else errno.EIO, "file type failed"
            )

    @contextmanager
    def scandir(path):
        with original(path) as stream:
            yield iter([Entry(entry) for entry in stream])

    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(error_type, match="file type failed"):
        recipes.capture_runtime(package)
