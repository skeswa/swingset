"""Repeated platform staging keeps administrator values in fresh private files."""

import copy
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

from swingset.deploy import bootstrap_credentials as credentials


@pytest.fixture
def host(tmp_path, monkeypatch):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    container = {
        "Id": "1" * 64,
        "Image": "sha256:" + "2" * 64,
        "State": {"Running": True, "Status": "running", "OOMKilled": False},
        "Config": {"Env": ["POSTGRES_USER=administrator", "POSTGRES_PASSWORD=p@ss:'word /é"]},
    }
    calls = []

    def inspect(argv, **kwargs):
        calls.append(argv)
        assert argv == ["docker", "container", "inspect", "1" * 64]
        return subprocess.CompletedProcess(argv, 0, json.dumps([container]), "")

    monkeypatch.setattr(credentials.subprocess, "run", inspect)
    return SimpleNamespace(parent=parent, target=parent / "fresh", container=container, calls=calls)


def _stage(host):
    return credentials.stage(
        "1" * 64, "sha256:" + "2" * 64, host.target, ["postgres", "swingset_dryrun"]
    )


def test_two_database_files_are_created_once_without_values_in_receipts_arguments_or_environment(
    host, capsys
):
    before = dict(os.environ)
    assert (
        credentials.main(
            [
                "--postgres-container",
                "1" * 64,
                "--postgres-image",
                "sha256:" + "2" * 64,
                "--directory",
                str(host.target),
                "--database",
                "postgres",
                "--database",
                "swingset_dryrun",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    record = json.loads(output)
    password = "p@ss:'word /é"
    assert password not in output and hashlib.sha256(password.encode()).hexdigest() not in output
    assert "Env" not in output and "PASSWORD" not in output
    assert record["complete"] and len(record["files"]) == 2
    for database in ("postgres", "swingset_dryrun"):
        path = host.target / (database + ".dsn")
        parsed = urlsplit(path.read_text().strip())
        assert unquote(parsed.username) == "administrator" and unquote(parsed.password) == password
        assert (
            parsed.hostname == "127.0.0.1" and parsed.port == 5432 and parsed.path == "/" + database
        )
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_uid == os.getuid()
    assert stat.S_IMODE(host.target.stat().st_mode) == 0o700
    assert json.loads((host.target / "result.json").read_bytes()) == record
    assert dict(os.environ) == before
    assert len(host.calls) == 1


@pytest.mark.parametrize(
    "damage",
    ["directory_exists", "dangling_link", "parent_link", "parent_mode", "root_user", "relative"],
)
def test_unsafe_or_reused_directory_is_refused_before_docker_or_secret_creation(
    host, monkeypatch, damage
):
    if damage == "directory_exists":
        host.target.mkdir(mode=0o700)
        (host.target / "keep").write_text("original")
    elif damage == "dangling_link":
        host.target.symlink_to(host.parent / "absent")
    elif damage == "parent_link":
        link = host.parent.with_name("parent-link")
        link.symlink_to(host.parent, target_is_directory=True)
        host.target = link / "fresh"
    elif damage == "parent_mode":
        host.parent.chmod(0o755)
    elif damage == "root_user":
        monkeypatch.setattr(credentials.os, "getuid", lambda: 0)
    else:
        host.target = Path("relative")
    with pytest.raises(RuntimeError):
        _stage(host)
    assert not host.calls
    if damage == "directory_exists":
        assert (host.target / "keep").read_text() == "original"


@pytest.mark.parametrize(
    "field,value",
    [
        ("Id", "3" * 64),
        ("Image", "sha256:" + "3" * 64),
        ("Running", False),
        ("Status", "paused"),
        ("OOMKilled", True),
        ("Paused", True),
        ("Restarting", True),
    ],
)
def test_pinned_postgres_identity_and_health_must_match_before_extracting_values(
    host, field, value
):
    if field in {"Id", "Image"}:
        host.container[field] = value
    else:
        host.container["State"][field] = value
    with pytest.raises(RuntimeError, match="identity or running state"):
        _stage(host)
    assert not host.target.exists()


@pytest.mark.parametrize(
    "environment",
    [
        [],
        ["POSTGRES_USER=administrator"],
        ["POSTGRES_USER=administrator", "POSTGRES_PASSWORD="],
        ["POSTGRES_USER=administrator", "POSTGRES_PASSWORD=secret", "POSTGRES_PASSWORD=other"],
        [
            "POSTGRES_USER=administrator",
            "POSTGRES_PASSWORD=secret",
            "POSTGRES_PASSWORD_FILE=/secrets/other",
        ],
        ["POSTGRES_USER=administrator", "POSTGRES_PASSWORD=" + "x" * 4097],
    ],
)
def test_missing_ambiguous_or_indirect_secret_metadata_is_refused_without_values_in_errors(
    host, environment
):
    host.container["Config"]["Env"] = environment
    with pytest.raises(RuntimeError, match="credential metadata") as error:
        _stage(host)
    assert "secret" not in str(error.value) and "other" not in str(error.value)
    assert not host.target.exists()


@pytest.mark.parametrize(
    "databases",
    [[], ["postgres", "postgres"], ["another_app"], ["../postgres"], ["swingset" + "a" * 48]],
)
def test_staging_cannot_target_unrelated_databases_or_filename_traversal(host, databases):
    with pytest.raises(RuntimeError, match="targets"):
        credentials.stage("1" * 64, "sha256:" + "2" * 64, host.target, databases)
    assert not host.calls and not host.target.exists()


def test_inspection_error_cannot_echo_an_administrator_value(host, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1, args[0], output="POSTGRES_PASSWORD=private", stderr="private"
        )

    monkeypatch.setattr(credentials.subprocess, "run", fail)
    with pytest.raises(RuntimeError) as error:
        _stage(host)
    assert "private" not in str(error.value) and not host.target.exists()


def test_partial_write_preserves_owned_files_and_refuses_adoption_on_retry(host, monkeypatch):
    original = credentials._write

    def fail(path, body):
        if path.name == "swingset_dryrun.dsn":
            raise OSError("simulated disk failure")
        original(path, body)

    monkeypatch.setattr(credentials, "_write", fail)
    with pytest.raises(OSError):
        _stage(host)
    prior = copy.deepcopy((host.target / "postgres.dsn").read_bytes())
    assert (host.target / "owner.json").is_file() and not (host.target / "result.json").exists()
    with pytest.raises(RuntimeError, match="fresh private"):
        _stage(host)
    assert (host.target / "postgres.dsn").read_bytes() == prior
