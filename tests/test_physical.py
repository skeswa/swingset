"""Warm physical state is known, durable and clean; foreign state is never reset."""

import copy
import json
import stat
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from swingset.dev import physical, services


@pytest.fixture
def managed(tmp_path, monkeypatch):
    state = {
        "container": None,
        "volume": None,
        "calls": [],
        "initialized": [],
        "roles": "pinned-roles",
        "system": "123456789",
        "rows": [[1, "postgres", services.ROLE, "UTF8", "C", "C", False, True]],
    }
    monkeypatch.setattr(services, "home", lambda: tmp_path)
    monkeypatch.setattr(services, "_roles_signature", lambda conn: state["roles"])
    monkeypatch.setattr(services, "_validate_service", lambda service: None)
    monkeypatch.setattr(
        physical, "_initialize", lambda conn, service: state["initialized"].append(service)
    )

    class Connection:
        def execute(self, sql, parameters=()):
            if "pg_control_system" in str(sql):
                return SimpleNamespace(fetchone=lambda: (state["system"],))
            assert "FROM pg_database" in str(sql)
            return SimpleNamespace(fetchall=lambda: copy.deepcopy(state["rows"]))

    @contextmanager
    def connect(*args):
        yield Connection()

    monkeypatch.setattr(services.Service, "connect", connect)

    def docker(*args, **kwargs):
        state["calls"].append(args)
        if args[:2] == ("container", "inspect"):
            value = state["container"]
        elif args[:2] == ("volume", "inspect"):
            value = state["volume"]
        elif args[:2] == ("volume", "create"):
            record = services._json(physical._path())
            assert not record["complete"] and record["instance"]
            state["volume"] = {
                "Name": physical.VOLUME,
                "CreatedAt": "created-once",
                "Labels": physical._labels(record),
            }
            return SimpleNamespace(returncode=0, stdout=physical.VOLUME)
        elif args[0] == "run":
            record = services._json(physical._path())
            assert record["volume_created"] == "created-once"
            assert args[args.index("--memory") + 1] == "2g"
            assert args[args.index("--memory-swap") + 1] == "2g"
            assert args[args.index("--cpus") + 1] == "2"
            assert (
                "--tmpfs" not in args and "POSTGRES_INITDB_ARGS=--locale=C --encoding=UTF8" in args
            )
            for name in ("fsync=on", "full_page_writes=on", "synchronous_commit=on"):
                assert name in args
            state["container"] = {
                "Id": "a" * 64,
                "Name": "/" + physical.NAME,
                "Config": {"Image": services.POSTGRES_IMAGE, "Labels": physical._labels(record)},
                "State": {"Running": True},
                "HostConfig": {
                    "Memory": 2 * 1024**3,
                    "MemorySwap": 2 * 1024**3,
                    "NanoCpus": 2_000_000_000,
                    "Tmpfs": {},
                    "PidMode": "",
                    "PortBindings": {
                        "5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(physical.PORT)}]
                    },
                },
                "NetworkSettings": {
                    "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(physical.PORT)}]}
                },
                "Mounts": [
                    {
                        "Type": "volume",
                        "Name": physical.VOLUME,
                        "Destination": "/var/lib/postgresql/data",
                        "RW": True,
                    }
                ],
            }
            return SimpleNamespace(returncode=0, stdout="a" * 64)
        elif args[0] == "start":
            assert args[1] == state["container"]["Id"]
            state["container"]["State"]["Running"] = True
            return SimpleNamespace(returncode=0, stdout=args[1])
        else:
            pytest.fail(
                "No reset, deletion, or unknown Docker mutation is permitted: " + str(args[:2])
            )
        return SimpleNamespace(
            returncode=0 if value is not None else 1,
            stdout=json.dumps([value]) if value is not None else "",
        )

    monkeypatch.setattr(services, "_docker", docker)
    return state


def test_fresh_owner_baseline_is_private_then_reused_without_initialization(managed):
    service = services.ensure_service("physical")
    original = physical._path().read_bytes()
    record = json.loads(original)
    assert service.kind == "physical" and service.port == 15434
    assert (
        record["baseline"] == managed["rows"] and record["system_identifier"] == managed["system"]
    )
    assert stat.S_IMODE(physical._path().stat().st_mode) == 0o600
    assert len(managed["initialized"]) == 1
    managed["calls"].clear()
    assert services.ensure_service("physical") == service
    assert physical._path().read_bytes() == original
    assert len(managed["initialized"]) == 1
    assert not any(
        call[0] in {"run", "start", "rm"} or call[:2] == ("volume", "create")
        for call in managed["calls"]
    )


def test_fresh_wrong_collation_is_not_sealed_as_a_valid_baseline(managed):
    managed["rows"][0][4] = "en_US"
    with pytest.raises(RuntimeError, match="C collation"):
        physical.ensure()
    assert not services._json(physical._path())["complete"]
    assert managed["initialized"] == []


@pytest.mark.parametrize("foreign", ["container", "volume"])
def test_preexisting_resources_without_metadata_are_never_adopted(managed, foreign):
    managed[foreign] = {"foreign": True}
    with pytest.raises(RuntimeError, match="without their private ownership"):
        physical.ensure()
    assert not physical._path().exists()
    assert all(
        call[0] in {"container", "volume"} and call[1] == "inspect" for call in managed["calls"]
    )


@pytest.mark.parametrize(
    "damage",
    [
        "id",
        "image",
        "instance",
        "memory",
        "cpu",
        "public_port",
        "volume",
        "volume_created",
        "system",
        "roles",
        "database",
    ],
)
def test_foreign_identity_or_extra_state_blocks_without_adoption_or_reset(managed, damage):
    physical.ensure()
    original = physical._path().read_bytes()
    container = managed["container"]
    if damage == "id":
        container["Id"] = "b" * 64
    elif damage == "image":
        container["Config"]["Image"] = "postgres:latest"
    elif damage == "instance":
        container["Config"]["Labels"][services.LABEL + ".instance"] = "b" * 32
    elif damage == "memory":
        container["HostConfig"]["Memory"] *= 2
    elif damage == "cpu":
        container["HostConfig"]["NanoCpus"] *= 2
    elif damage == "public_port":
        container["HostConfig"]["PortBindings"]["5432/tcp"][0]["HostIp"] = "0.0.0.0"
    elif damage == "volume":
        container["Mounts"][0]["Name"] = "foreign"
    elif damage == "volume_created":
        managed["volume"]["CreatedAt"] = "replacement"
    elif damage == "system":
        managed["system"] = "different-cluster"
    elif damage == "roles":
        managed["roles"] = "different-roles"
    else:
        managed["rows"].append(
            [99, "retained-dead-owner", services.ROLE, "UTF8", "C", "C", False, True]
        )
    managed["calls"].clear()
    with pytest.raises(RuntimeError):
        physical.ensure()
    assert physical._path().read_bytes() == original
    assert not any(
        call[0] in {"run", "start", "rm"} or call[:2] in {("volume", "create"), ("volume", "rm")}
        for call in managed["calls"]
    )


def test_stopped_exact_owned_container_starts_without_recreation(managed):
    physical.ensure()
    managed["container"]["State"]["Running"] = False
    managed["calls"].clear()
    physical.ensure()
    assert ("start", "a" * 64) in managed["calls"]
    assert not any(call[0] == "run" for call in managed["calls"])


@pytest.mark.parametrize(
    "damage", ["incomplete", "baseline", "public_metadata", "missing_container"]
)
def test_partial_ownership_cannot_capture_a_new_baseline_or_replace_resources(managed, damage):
    physical.ensure()
    record = services._json(physical._path())
    if damage == "incomplete":
        record["complete"] = False
        physical._write(record)
    elif damage == "baseline":
        del record["baseline"]
        physical._write(record)
    elif damage == "public_metadata":
        physical._path().chmod(0o644)
    else:
        managed["container"] = None
    before = physical._path().read_bytes()
    managed["calls"].clear()
    with pytest.raises(RuntimeError):
        physical.ensure()
    assert physical._path().read_bytes() == before
    assert not any(call[0] in {"run", "start", "rm"} for call in managed["calls"])


def test_postrun_inventory_guard_revalidates_the_container_identity(managed):
    service = physical.ensure()
    managed["container"]["Id"] = "b" * 64
    with pytest.raises(RuntimeError, match="identity"):
        physical.assert_clean(service)


def test_fault_injection_still_refuses_the_warm_physical_profile():
    with pytest.raises(RuntimeError, match="cannot restart warm"):
        services.restart_lifecycle(
            services.Service(physical.NAME, "physical", physical.PORT, "private")
        )


def test_inventory_requires_exact_database_identity_and_clears_after_owned_cleanup():
    from swingset.state.postgres_copy import Copies

    with services.lifecycle_service() as service:
        assert service.kind == "lifecycle"
        if services.configured_ci_lifecycle_service() is None:
            assert service.name.startswith("swingset-fault-postgres-")
        with service.connect() as conn:
            baseline = physical.inventory(conn)
            with Copies.for_admin(service.dsn()) as copies:
                with copies.empty():
                    with pytest.raises(RuntimeError, match="fresh baseline"):
                        physical.verify_inventory(conn, baseline)
                physical.verify_inventory(conn, baseline)


def test_physical_settings_require_real_fsync_and_full_page_writes(monkeypatch):
    service = services.Service(physical.NAME, "physical", physical.PORT, "private")
    settings = {**services.LIFECYCLE_SETTINGS, "fsync": "off"}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, parameters=()):
            return SimpleNamespace(fetchone=lambda: (settings[parameters[0]],))

    monkeypatch.setattr(services.Service, "connect", lambda *args: Connection())
    with pytest.raises(RuntimeError, match="fsync"):
        services._validate_service(service)


def test_postrun_connection_loss_is_a_recordable_sanitized_failure(managed, monkeypatch):
    import psycopg

    service = physical.ensure()

    @contextmanager
    def disconnected(*args):
        raise psycopg.OperationalError("private connection detail")
        yield

    monkeypatch.setattr(services.Service, "connect", disconnected)
    with pytest.raises(RuntimeError, match="could not be verified") as error:
        physical.assert_clean(service)
    assert "private connection" not in str(error.value)


def test_postrun_settings_connection_loss_is_normalized_before_report_collection(
    managed, monkeypatch
):
    import psycopg

    service = physical.ensure()

    def disconnected(service):
        raise psycopg.OperationalError("private settings connection detail")

    monkeypatch.setattr(services, "_validate_service", disconnected)
    with pytest.raises(RuntimeError, match="could not be verified") as error:
        physical.assert_clean(service)
    assert "private settings" not in str(error.value)


def test_client_reaper_preserves_other_endpoint_records_then_reaps_both(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time

    from swingset.dev.core_client import reap_copies
    from swingset.state.postgres_copy import Copies

    primary = services.ensure_service()
    registry = tmp_path / "owners"
    ready = tmp_path / "ready.json"
    script = (
        "import json,os,time;from pathlib import Path;"
        "from swingset.state.postgres_copy import Copies\n"
        "root=Path(os.environ['OWNERS']);ready=Path(os.environ['READY'])\n"
        "with Copies.for_admin(os.environ['PRIMARY'],registry_root=root) as first, "
        "Copies.for_admin(os.environ['PHYSICAL'],registry_root=root) as second:\n"
        " with first.empty() as a,second.empty() as b:\n"
        "  ready.write_text(json.dumps([a.info.dbname,b.info.dbname]));time.sleep(60)\n"
    )
    with services.lifecycle_service() as service:
        with service.connect() as conn:
            baseline = physical.inventory(conn)
        environment = {
            **os.environ,
            "PRIMARY": primary.dsn(),
            "PHYSICAL": service.dsn(),
            "OWNERS": str(registry),
            "READY": str(ready),
        }
        child = subprocess.Popen(
            [sys.executable, "-c", script],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                assert child.poll() is None, "owned fixture exited before creating both targets"
                assert time.monotonic() < deadline, "owned fixture never created its targets"
                time.sleep(0.01)
            names = json.loads(ready.read_text())
            assert os.getpgid(child.pid) == child.pid
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=10)
            assert child.returncode == -signal.SIGKILL
            assert len(list(registry.glob("*.json"))) == 2
            assert reap_copies(primary, registry_root=registry) == 1
            assert len(list(registry.glob("*.json"))) == 1
            with primary.connect() as conn:
                assert not conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname=%s", (names[0],)
                ).fetchone()
            with service.connect() as conn:
                assert conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname=%s", (names[1],)
                ).fetchone()
            assert reap_copies(primary, service, registry_root=registry) == 2
            assert not list(registry.glob("*.json"))
            with service.connect() as conn:
                physical.verify_inventory(conn, baseline)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate(timeout=10)
            with (
                Copies.for_admin(primary.dsn(), registry_root=registry),
                Copies.for_admin(service.dsn(), registry_root=registry),
            ):
                pass
