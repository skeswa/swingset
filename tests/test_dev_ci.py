"""External physical fixtures stay durable, isolated and owned by the host."""

import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from swingset.dev import ci, services


def configured(monkeypatch, *, settings=None, grant=True, closed=None):
    prefix = "SWINGSET_TEST_LIFECYCLE"
    values = {
        "CI": "true",
        prefix + "_SERVICE_DSN": "host=physical port=5432 user=fixture password=private",
        prefix + "_SERVICE_IMAGE": services.POSTGRES_IMAGE,
        prefix + "_ROLE_SIGNATURE": "signature",
        prefix + "_WORKER_PASSWORD": "worker-private",
        prefix + "_CONTROL_PASSWORD": "control-private",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            if closed is not None:
                closed.append(True)

        def execute(self, statement, parameters=()):
            statements.append(statement)
            if "pg_try_advisory_lock(" in statement:
                value = grant
            elif statement.startswith("SELECT current_setting"):
                value = (settings or services.LIFECYCLE_SETTINGS)[parameters[0]]
            else:
                value = 170011
            return SimpleNamespace(fetchone=lambda: (value,))

    monkeypatch.setattr(services.Service, "connect", lambda *_args: Connection())
    monkeypatch.setattr(services, "_roles_signature", lambda _conn: "signature")
    monkeypatch.setattr(
        services,
        "_docker",
        lambda *_args, **_kwargs: pytest.fail("a configured worker must never control Docker"),
    )
    return statements


def test_configured_physical_service_releases_lease_after_case_failure(monkeypatch):
    statements = configured(monkeypatch)
    with pytest.raises(ValueError, match="case failed"):
        with services.lifecycle_service() as service:
            assert service.kind == "lifecycle" and service.host == "physical"
            assert service.worker_password == "worker-private"
            raise ValueError("case failed")
    assert sum("pg_try_advisory_lock(" in statement for statement in statements) == 1
    assert sum("pg_advisory_unlock(" in statement for statement in statements) == 1


def test_configured_physical_service_refuses_the_warm_nondurable_profile(monkeypatch):
    configured(monkeypatch, settings={**services.LIFECYCLE_SETTINGS, "fsync": "off"})
    with pytest.raises(RuntimeError, match="fsync"):
        with services.lifecycle_service():
            pytest.fail("the warm test cluster must not substitute for physical isolation")


def test_configured_physical_service_requires_exact_image(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setenv("SWINGSET_TEST_LIFECYCLE_SERVICE_IMAGE", "postgres:latest")
    with pytest.raises(RuntimeError, match="pinned image"):
        with services.lifecycle_service():
            pytest.fail("an unpinned physical service must be refused")


@pytest.mark.parametrize("failure", ["exit", "exception"])
def test_ci_host_owns_service_and_private_credentials_through_worker_failure(monkeypatch, failure):
    primary = SimpleNamespace(kind="test")
    physical = SimpleNamespace(kind="lifecycle")
    closed = []
    credentials = []
    monkeypatch.setattr(services, "ensure_service", lambda: primary)

    @contextmanager
    def lifecycle():
        try:
            yield physical
        finally:
            closed.append(True)

    monkeypatch.setattr(services, "lifecycle_service", lifecycle)
    monkeypatch.setattr(
        services, "ci_environment", lambda service: {service.kind + "_secret": "private"}
    )

    def worker(command, *, check):
        path = Path(command[command.index("--env-file") + 1])
        credentials.append(path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "test_secret=private" in path.read_text()
        assert "lifecycle_secret=private" in path.read_text()
        assert command[command.index("--memory") + 1] == "2g"
        assert command[command.index("--memory-swap") + 1] == "2g"
        assert not any("docker.sock" in part for part in command)
        assert not closed
        if failure == "exception":
            raise OSError("worker launch failed")
        return SimpleNamespace(returncode=9)

    monkeypatch.setattr(ci.subprocess, "run", worker)
    if failure == "exception":
        with pytest.raises(OSError, match="worker launch failed"):
            ci.run("fixture-image")
    else:
        assert ci.run("fixture-image") == 9
    assert closed == [True]
    assert credentials and all(not path.exists() for path in credentials)


@pytest.mark.parametrize(
    ("setting", "arguments", "fast"),
    [(None, [], True), ("0", [], False), ("1", [], True), (None, ["--collect-only"], False)],
)
def test_default_core_dispatch_keeps_explicit_routes_and_opt_out_native(
    monkeypatch, setting, arguments, fast
):
    from swingset.dev import commands, core_runner

    called = []

    class NativeExecuted(Exception):
        pass

    def native(_executable, command):
        called.append(command)
        raise NativeExecuted

    with monkeypatch.context() as configuration:
        configuration.delenv("CI", raising=False)
        configuration.delenv("SWINGSET_TEST_SERVICE_DSN", raising=False)
        if setting is None:
            configuration.delenv("SWINGSET_FAST_CORE", raising=False)
        else:
            configuration.setenv("SWINGSET_FAST_CORE", setting)
        configuration.setenv("SWINGSET_TEST_STARTED", "before")
        configuration.setattr(core_runner, "run", lambda **_kwargs: 23)
        configuration.setattr(commands.os, "execv", native)
        if fast:
            assert commands.main(["test", *arguments]) == 23
            assert not called
        else:
            with pytest.raises(NativeExecuted):
                commands.main(["test", *arguments])
            assert len(called) == 1 and "pytest" in called[0]
            assert all(argument in called[0] for argument in arguments)


@pytest.mark.parametrize("interrupted", [False, True])
def test_waiting_physical_lease_closes_on_timeout_or_cancellation(monkeypatch, interrupted):
    closed = []
    statements = configured(monkeypatch, grant=False, closed=closed)
    now = [0.0]
    monkeypatch.setattr(services.time, "monotonic", lambda: now[0])

    def sleep(_seconds):
        if interrupted:
            raise KeyboardInterrupt
        now[0] = 600

    monkeypatch.setattr(services.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt if interrupted else TimeoutError):
        with services.lifecycle_service():
            pytest.fail("a held physical lease was acquired")
    assert closed == [True, True]  # validation and waiting sessions both close
    assert sum("pg_try_advisory_lock(" in statement for statement in statements) == 1
    assert not any("pg_advisory_unlock(" in statement for statement in statements)


def test_waiting_physical_lease_has_no_snapshot_and_acquires_after_release(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from swingset.state.postgres_copy import Copies

    waiting, deleted, checked = (threading.Event() for _ in range(3))
    original_sleep = services.time.sleep
    with (
        services.lifecycle_service() as service,
        Copies.for_admin(service.dsn()) as copies,
        copies.empty() as holder,
    ):
        name = holder.info.dbname
        holder.execute(
            "CREATE TABLE lease_reclaim AS SELECT x,repeat('x',400) AS payload "
            "FROM generate_series(1,4000) AS x"
        )
        before = holder.execute("SELECT pg_relation_size('lease_reclaim')").fetchone()[0]
        with service.connect(name) as waiter, service.connect(name) as observer:
            assert holder.execute("SELECT pg_try_advisory_lock(1397969989,1818846821)").fetchone()[
                0
            ]

            def sleep(_seconds):
                row = observer.execute(
                    "SELECT state,backend_xmin::text,xact_start FROM pg_stat_activity WHERE pid=%s",
                    (waiter.info.backend_pid,),
                ).fetchone()
                assert tuple(row) == ("idle", None, None)
                waiting.set()
                if deleted.is_set():
                    checked.set()
                original_sleep(0.005)

            monkeypatch.setattr(services.time, "sleep", sleep)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(services._wait_for_lifecycle_lease, waiter)
                try:
                    assert waiting.wait(5)
                    assert not future.done()
                    holder.execute("DELETE FROM lease_reclaim")
                    deleted.set()
                    assert checked.wait(5)
                    holder.execute("VACUUM (FULL,ANALYZE) lease_reclaim")
                    after = holder.execute("SELECT pg_relation_size('lease_reclaim')").fetchone()[0]
                    assert before > 1_000_000 and after < before // 4
                    assert holder.execute("SELECT count(*) FROM lease_reclaim").fetchone()[0] == 0
                    assert not future.done()
                    holder.execute("SELECT pg_advisory_unlock(1397969989,1818846821)")
                    future.result(timeout=5)
                    assert not holder.execute(
                        "SELECT pg_try_advisory_lock(1397969989,1818846821)"
                    ).fetchone()[0]
                finally:
                    holder.execute("SELECT pg_advisory_unlock(1397969989,1818846821)")
                    waiter.execute("SELECT pg_advisory_unlock(1397969989,1818846821)")


def test_physical_lease_received_after_deadline_is_released(monkeypatch):
    closed = []
    statements = configured(monkeypatch, closed=closed)
    calls = [0]

    def monotonic():
        calls[0] += 1
        return 0 if calls[0] < 3 else 600

    monkeypatch.setattr(services.time, "monotonic", monotonic)
    with pytest.raises(TimeoutError):
        with services.lifecycle_service():
            pytest.fail("a late lease cannot start its physical fixture")
    assert closed == [True, True]
    assert sum("pg_advisory_unlock(" in statement for statement in statements) == 1
