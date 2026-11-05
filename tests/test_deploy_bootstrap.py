"""Only the dedicated platform job may prepare a pinned empty application DB."""

import json
import os
from contextlib import nullcontext
from types import SimpleNamespace

import psycopg
import pytest

from swingset.deploy import bootstrap as platform


@pytest.fixture
def admin(tmp_path, monkeypatch):
    secret = tmp_path / "admin-dsn"
    secret.write_text(
        "postgresql://administrator:never-print-this@127.0.0.1:5432/swingset_dryrun\n"
    )
    secret.chmod(0o600)
    row = ["swingset_dryrun", 12345, "swingset_owner", "C", "C", True]
    mutations = []
    connects = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def transaction(self):
            return nullcontext()

        def execute(self, query, parameters=None):
            value = (
                (len(platform.postgres_schema.ROLE_NAMES),)
                if query.startswith("SELECT count")
                else row
            )
            return SimpleNamespace(fetchone=lambda: value)

    connection = Connection()

    def connect(dsn, **kwargs):
        connects.append((dsn, kwargs))
        return connection

    def prepare(actual):
        assert actual is connection
        mutations.append("tables")
        return {"database": {"name": "swingset_dryrun", "oid": 12345}, "catalog_sha256": "a" * 64}

    monkeypatch.setattr(platform.psycopg, "connect", connect)
    monkeypatch.setattr(platform.postgres_bootstrap, "prepare", prepare)
    monkeypatch.setattr(
        platform.postgres_schema, "install_roles", lambda actual: mutations.append("roles")
    )
    return SimpleNamespace(
        secret=secret, row=row, mutations=mutations, connects=connects, connection=connection
    )


@pytest.mark.parametrize(
    "mode,database",
    [
        ("roles", "swingset_dryrun"),
        ("tables", "postgres"),
        ("tables", "another_app"),
        ("tables", "swingset;drop database postgres"),
        ("tables", "swingset" + "a" * 48),
    ],
)
def test_wrong_mode_or_target_fails_before_reading_secrets_or_connecting(admin, mode, database):
    admin.secret.unlink()
    with pytest.raises(RuntimeError):
        platform.bootstrap(mode, admin.secret, database)
    assert not admin.connects and not admin.mutations


@pytest.mark.parametrize(
    "damage",
    [
        "mode",
        "symlink",
        "directory",
        "fifo",
        "empty",
        "oversize",
        "foreign_owner",
        "invalid",
        "wrong_db",
        "implicit_db",
    ],
)
def test_private_file_or_explicit_dsn_identity_failure_never_opens_a_connection(
    admin, monkeypatch, damage
):
    secret = admin.secret
    if damage == "mode":
        secret.chmod(0o644)
    elif damage == "symlink":
        link = secret.with_name("linked")
        link.symlink_to(secret)
        secret = link
    elif damage in ("directory", "fifo"):
        secret.unlink()
        if damage == "directory":
            secret.mkdir()
        else:
            os.mkfifo(secret, 0o600)
    elif damage == "foreign_owner":
        original = platform.os.fstat
        target = secret.stat()

        def metadata(descriptor):
            actual = original(descriptor)
            if not os.path.samestat(actual, target):
                return actual
            values = list(actual)
            values[4] = actual.st_uid + 1
            return os.stat_result(values)

        monkeypatch.setattr(platform.os, "fstat", metadata)
    elif damage == "empty":
        secret.write_text(" \n")
    elif damage == "oversize":
        secret.write_bytes(b"x" * 65537)
    elif damage == "invalid":
        secret.write_text("postgresql://broken%invalid")
    elif damage == "wrong_db":
        secret.write_text("dbname=another_app password=never-print-this")
    else:
        secret.write_text("user=administrator password=never-print-this")
    with pytest.raises((RuntimeError, OSError)) as error:
        platform.bootstrap("tables", secret, "swingset_dryrun")
    assert "never-print-this" not in str(error.value)
    assert not admin.connects and not admin.mutations


@pytest.mark.parametrize(
    "index,value",
    [(0, "another_app"), (2, "another_owner"), (3, "en_US.UTF-8"), (4, "en_US.UTF-8"), (5, False)],
)
def test_actual_database_admin_owner_and_both_locale_values_must_match_before_mutation(
    admin, index, value
):
    admin.row[index] = value
    with pytest.raises(RuntimeError):
        platform.bootstrap("tables", admin.secret, "swingset_dryrun")
    assert len(admin.connects) == 1 and not admin.mutations


def test_table_preparation_calls_the_existing_strict_bootstrap_without_migrating_or_exposing_the_dsn(
    admin, capsys, monkeypatch
):
    before = dict(os.environ)
    assert (
        platform.main(
            [
                "--mode",
                "tables",
                "--admin-dsn-file",
                str(admin.secret),
                "--database",
                "swingset_dryrun",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["database"] == {"name": "swingset_dryrun", "oid": 12345}
    assert report["binding"]["catalog_sha256"] == "a" * 64
    assert "never-print-this" not in json.dumps(report)
    assert admin.mutations == ["tables"]
    assert dict(os.environ) == before
    assert admin.connects[0][1] == {"autocommit": True, "connect_timeout": 15}


def test_roles_installation_only_uses_the_admin_database_and_existing_role_api(admin):
    admin.secret.write_text(
        "host=127.0.0.1 port=5432 dbname=postgres user=administrator password=never-print-this"
    )
    admin.row[0] = "postgres"
    report = platform.bootstrap("roles", admin.secret, "postgres")
    assert admin.mutations == ["roles"]
    assert report["role_count"] == len(platform.postgres_schema.ROLE_NAMES)
    assert report["roles_sha256"] == platform.postgres_schema.roles_checksum()


def test_roles_refuse_a_nonsuperuser_before_the_role_api(admin):
    admin.secret.write_text(
        "host=127.0.0.1 port=5432 dbname=postgres user=administrator password=never-print-this"
    )
    admin.row[0], admin.row[5] = "postgres", False
    with pytest.raises(RuntimeError, match="administrator"):
        platform.bootstrap("roles", admin.secret, "postgres")
    assert not admin.mutations


def test_existing_nonempty_database_refusal_is_preserved_from_maintained_prepare(
    admin, monkeypatch
):
    def refuse(connection):
        raise RuntimeError("prepared database contains rows")

    monkeypatch.setattr(platform.postgres_bootstrap, "prepare", refuse)
    with pytest.raises(RuntimeError, match="contains rows"):
        platform.bootstrap("tables", admin.secret, "swingset_dryrun")
    assert not admin.mutations


def test_driver_failure_is_redacted_even_when_it_contains_the_private_dsn(admin, monkeypatch):
    def fail(dsn, **kwargs):
        raise psycopg.OperationalError(dsn)

    monkeypatch.setattr(platform.psycopg, "connect", fail)
    with pytest.raises(RuntimeError, match="database operation failed") as error:
        platform.bootstrap("tables", admin.secret, "swingset_dryrun")
    assert "never-print-this" not in str(error.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "host=database port=5432",
        "host=127.0.0.1",
        "host=127.0.0.1 port=5433",
        "host=127.0.0.1 port=5432 hostaddr=127.0.0.1",
        "host=127.0.0.1 port=5432 service=another_cluster",
    ],
)
def test_dedicated_platform_namespace_forbids_remote_implicit_or_indirect_endpoints(
    admin, endpoint
):
    admin.secret.write_text(
        endpoint + " dbname=swingset_dryrun user=administrator password=never-print-this"
    )
    with pytest.raises(RuntimeError, match="loopback endpoint"):
        platform.bootstrap("tables", admin.secret, "swingset_dryrun")
    assert not admin.connects and not admin.mutations
