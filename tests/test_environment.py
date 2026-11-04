"""A shell flag cannot turn disposable development data into production."""

from uuid import uuid4

import httpx
import pytest

from swingset.state import environment


class Connection:
    def __init__(self, row):
        self.row = row

    def execute(self, statement):
        assert (
            statement
            == "SELECT environment,database_id FROM ops.environment_identity WHERE singleton=1"
        )
        return self

    def fetchone(self):
        return self.row


@pytest.mark.parametrize("operation", ["fetch", "publish", "scheduler"])
@pytest.mark.parametrize("flag", [None, "dev", "production"])
def test_development_identity_refuses_real_effects_even_with_wrong_or_missing_flag(
    monkeypatch, operation, flag
):
    if flag is None:
        monkeypatch.delenv("SWINGSET_ENV", raising=False)
    else:
        monkeypatch.setenv("SWINGSET_ENV", flag)
    with pytest.raises(environment.EnvironmentRefused):
        environment.ensure_external_allowed(Connection(("dev", str(uuid4()))), operation)


def test_development_fixture_transport_is_explicitly_allowed(monkeypatch):
    monkeypatch.delenv("SWINGSET_ENV", raising=False)
    connection = Connection(("dev", str(uuid4())))
    environment.ensure_external_allowed(
        connection, "fetch", httpx.MockTransport(lambda _request: httpx.Response(200))
    )
    environment.ensure_external_allowed(connection, "publish", environment.FixtureIO())
    with httpx.HTTPTransport() as real:
        with pytest.raises(environment.EnvironmentRefused):
            environment.ensure_external_allowed(connection, "fetch", real)


@pytest.mark.parametrize("row", [None, ("unknown", str(uuid4())), ("dev", "missing-id")])
def test_unknown_database_identity_fails_closed(row):
    with pytest.raises(environment.EnvironmentRefused):
        environment.ensure_external_allowed(Connection(row), "fetch", environment.FixtureIO())


def test_production_identity_is_read_from_database_and_conflicting_flag_is_refused(monkeypatch):
    connection = Connection(("production", str(uuid4())))
    monkeypatch.delenv("SWINGSET_ENV", raising=False)
    environment.ensure_external_allowed(connection, "fetch")
    with pytest.raises(environment.EnvironmentRefused):
        environment.ensure_dev(connection)
    monkeypatch.setenv("SWINGSET_ENV", "dev")
    with pytest.raises(environment.EnvironmentRefused):
        environment.ensure_external_allowed(connection, "publish", environment.FixtureIO())
