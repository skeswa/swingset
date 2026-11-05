"""Private graph verification cannot inherit shared or external credentials."""

import pytest

from swingset.deploy import runtime, utility


@pytest.fixture
def private_credentials(tmp_path, monkeypatch):
    for name in runtime.SECRET_NAMES.values():
        monkeypatch.delenv(name, raising=False)
    for name in ("worker-dsn", "control-dsn", "scratch-admin-dsn"):
        path = tmp_path / name
        path.write_text(
            "host=172.30.240.6 port=5432 dbname=private_fixture user=fixture password=not-a-secret"
        )
        path.chmod(0o400)
    return tmp_path


@pytest.mark.parametrize(
    "name",
    ["HF_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "SWINGSET_OWNER_DATABASE_URL"],
)
def test_verifier_refuses_ambient_shared_or_external_secrets(
    private_credentials, monkeypatch, name
):
    monkeypatch.setenv(name, "fixture")
    with pytest.raises(RuntimeError, match="administration credentials"):
        utility.configure("verifier", private_credentials, {"database": "172.30.240.6"})


def test_verifier_refuses_shared_endpoint_before_database_connection(private_credentials):
    with pytest.raises(RuntimeError, match="endpoint"):
        utility.configure("verifier", private_credentials, {"database": "172.30.240.3"})


def test_operator_refuses_scratch_administrator(private_credentials, monkeypatch):
    monkeypatch.setenv("SWINGSET_SCRATCH_ADMIN_DSN", "fixture")
    with pytest.raises(RuntimeError, match="administration credentials"):
        utility.configure("operator", private_credentials, {"database": "172.30.240.3"})


def test_verifier_checks_stored_dev_identity_and_explicitly_switches_process_mode(
    private_credentials, monkeypatch
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from swingset.state import engine, environment

    @contextmanager
    def connection(dsn, *, read_only):
        assert read_only and "172.30.240.6" in dsn
        yield SimpleNamespace(info=SimpleNamespace(dbname="private_fixture"))

    monkeypatch.setattr(engine, "connect", connection)
    mode = ["dryrun"]
    monkeypatch.setattr(
        environment,
        "identity",
        lambda connection: SimpleNamespace(environment=mode[0], database_id="fixture-copy-id"),
    )
    monkeypatch.setenv("SWINGSET_ENV", "dryrun")
    with pytest.raises(RuntimeError, match="dev copy"):
        utility.configure("verifier", private_credentials, {"database": "172.30.240.6"})
    mode[0] = "dev"
    utility.configure("verifier", private_credentials, {"database": "172.30.240.6"})
    assert __import__("os").environ["SWINGSET_ENV"] == "dev"


def test_verifier_cannot_launch_publication_or_unrestricted_python(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "authorized",
        lambda *args, **kwargs: pytest.fail("no authorization or credentials needed for refusal"),
    )
    for arguments in (
        ["loop", "release"],
        ["-c", "print('arbitrary')"],
        ["restore", "--prod"],
        ["rekey", "--prod"],
    ):
        with pytest.raises(RuntimeError, match="permitted"):
            utility.main(["--role", "verifier", "--", *arguments])
