import json
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from swingset.deploy import health

NOW = datetime(2026, 11, 5, 20, tzinfo=UTC).timestamp()


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    requests = []
    response = {"status": 204}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append((self.path, body))
            self.send_response(response["status"])
            if response["status"] == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            if response["status"] != 204:
                self.wfile.write(b"PRIVATE_PROVIDER_BODY")

        def do_GET(self):
            requests.append((self.path, b""))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    for name, suffix in (("ALERT", "secret-alert"), ("DEADMAN", "secret-ping")):
        path = tmp_path / (name + ".url")
        path.write_text(base + "/" + suffix)
        path.chmod(0o600)
        monkeypatch.setenv("SWINGSET_" + name + "_URL_FILE", str(path))
    monkeypatch.setenv("NO_PROXY", "*")
    status = tmp_path / "containers.json"
    status.write_text(
        json.dumps(
            {
                "utc": NOW,
                "complete_inventory": True,
                "containers": [{"role": "derive", "running": True, "oom_killed": False}],
                "resource_exit": 0,
            }
        )
    )
    monkeypatch.setenv("SWINGSET_CONTAINER_STATUS", str(status))
    monkeypatch.setenv("SWINGSET_HOLDS_DIR", str(tmp_path / "no-holds"))
    try:
        yield requests, response, status
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        assert not thread.is_alive()


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(health, "_signals", lambda *_args, **_kwargs: set())


def test_watchdog_alone_owns_the_deadman_heartbeat(tmp_path, delivery, quiet):
    requests, _, _ = delivery
    state = tmp_path / "state"
    runtime = tmp_path / "runtime" / "derive"
    health._write(runtime / "heartbeat.json", {"utc": NOW, "group": "derive"})
    health.notify_tick(state, runtime, "derive", exit_code=0, now=NOW)
    assert requests == []
    assert json.loads((runtime / "heartbeat.json").read_text())["group"] == "derive"
    assert (runtime / "tick-health.json").exists()
    health.watchdog(state, runtime=tmp_path / "runtime" / "health", now=NOW)
    assert [path for path, _ in requests] == ["/secret-ping"]


def test_zero_exit_does_not_hide_a_durable_failed_cycle(tmp_path, delivery, monkeypatch):
    from swingset.ops import watchdog as legacy

    state = tmp_path / "state"
    (state / "runs").mkdir(parents=True)
    record = {
        "loop": "derive",
        "finished_at": datetime.fromtimestamp(NOW, UTC).isoformat(),
        "failed": True,
        "error": "PRIVATE_SOURCE_VALUE",
    }
    (state / "runs" / "run_20261105T200000Z.json").write_text(json.dumps(record))
    monkeypatch.setattr(health, "_database_signals", lambda *_args: set())
    assert legacy._loop_records(str(state), NOW).latest["derive"]["failed"]
    report = health.notify_tick(state, tmp_path / "runtime", "derive", exit_code=0, now=NOW)
    assert "run-failed" in report.alert_keys
    body = delivery[0][0][1].decode()
    assert "run-failed" in body
    assert "PRIVATE_SOURCE_VALUE" not in body


def test_held_callbacks_leave_a_fresh_restore_target_and_secrets_untouched(tmp_path, monkeypatch):
    target = tmp_path / "fresh-target"
    runtime = tmp_path / "runtime" / "derive"
    health._write(runtime / "heartbeat.json", {"utc": NOW, "group": "derive"})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("held callbacks must not open a database or secret")

    monkeypatch.setattr(health, "_signals", forbidden)
    monkeypatch.setattr(health, "_endpoint", forbidden)
    health.notify_tick(target, runtime, "derive", held=True, now=NOW)
    report = health.notify_tick(target, runtime, "derive", held=True, now=NOW + 3601)
    assert "hold-overdue" in report.alert_keys
    assert not target.exists()
    assert json.loads((runtime / "heartbeat.json").read_text()) == {"utc": NOW, "group": "derive"}


def test_alert_file_append_and_rotation_are_seen_without_echoing_own_logs(
    tmp_path, delivery, quiet
):
    state = tmp_path / "state"
    state.mkdir()
    runtime = tmp_path / "runtime"
    health.watchdog(state, runtime=runtime, now=NOW)
    (state / "ALERTS.log").write_text("PRIVATE_OPERATOR_TEXT\n")
    assert "alerts-appended" in health.watchdog(state, runtime=runtime, now=NOW + 1).alert_keys
    assert "alerts-appended" not in health.watchdog(state, runtime=runtime, now=NOW + 2).alert_keys
    (state / "ALERTS.log").rename(state / "prior-alerts")
    (state / "ALERTS.log").write_text("PRIVATE_OPERATOR_TEXT\n")
    assert "alerts-appended" in health.watchdog(state, runtime=runtime, now=NOW + 3).alert_keys
    assert "PRIVATE_OPERATOR_TEXT" not in (runtime / "ALERTS.log").read_text()


@pytest.mark.parametrize("code", [302, 500])
def test_failed_webhooks_do_not_follow_redirects_leak_bodies_or_ping_deadman(
    tmp_path, delivery, quiet, code
):
    requests, response, _ = delivery
    response["status"] = code
    report = health.notify_tick(
        tmp_path / "state", tmp_path / "runtime", "derive", exit_code=1, now=NOW
    )
    assert report.delivery_failures == ("alert-delivery-failed",)
    assert len(requests) == 1
    recorded = json.dumps(report.safe_dict()) + (tmp_path / "runtime" / "ALERTS.log").read_text()
    assert "secret-alert" not in recorded and "PRIVATE_PROVIDER_BODY" not in recorded
    response["status"] = 204
    health.notify_tick(tmp_path / "state", tmp_path / "runtime", "derive", exit_code=1, now=NOW + 1)
    assert len(requests) == 2


@pytest.mark.parametrize("old_oom", [False, True])
def test_normal_recreation_is_quiet_but_oom_or_missing_role_alerts(
    tmp_path, delivery, quiet, old_oom
):
    _, _, status = delivery
    value = json.loads(status.read_text())
    value["containers"].append({"role": "derive", "running": False, "oom_killed": old_oom})
    status.write_text(json.dumps(value))
    assert (
        "container-failed"
        in health.watchdog(tmp_path / "state", runtime=tmp_path / "runtime", now=NOW).alert_keys
    ) is old_oom
    value["containers"].append({"role": "release", "running": False, "oom_killed": False})
    value["complete_inventory"] = False
    status.write_text(json.dumps(value))
    report = health.watchdog(tmp_path / "state", runtime=tmp_path / "runtime", now=NOW + 1)
    assert {"container-failed", "platform-incomplete"} <= set(report.alert_keys)


def test_secret_file_is_required_and_no_url_or_provider_text_is_logged(
    tmp_path, monkeypatch, quiet
):
    monkeypatch.delenv("SWINGSET_ALERT_URL_FILE", raising=False)
    monkeypatch.delenv("SWINGSET_DEADMAN_URL_FILE", raising=False)
    report = health.notify_tick(
        tmp_path / "state", tmp_path / "runtime", "derive", exit_code=1, now=NOW
    )
    assert report.delivery_failures == ("notification-unconfigured",)


def test_native_reads_detect_idle_successful_ticks_pause_and_errors(tmp_path, delivery):
    from swingset.state.db import open_database
    from swingset.state.loop_state import LoopStateRow, upsert

    state = tmp_path / "state"
    old = datetime.fromtimestamp(NOW - 3601, UTC).isoformat()
    current = datetime.fromtimestamp(NOW, UTC).isoformat()
    with open_database(state) as database:
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO watches(watch_id,source,kind,method,url,parser,state,last_checked_at) VALUES('health','fixture','fixture','GET','https://owned.invalid/','fixture','active',%s)",
                (old,),
            )
            upsert(
                connection,
                LoopStateRow(
                    "derive",
                    updated_at=current,
                    finished_at=current,
                    failed=0,
                    last_progress_at=old,
                ),
            )
        report = health.notify_tick(state, tmp_path / "runtime", "derive", exit_code=0, now=NOW)
        assert {"fetch-idle", "progress-idle"} <= set(report.alert_keys)
        with database.transaction() as connection:
            connection.execute("UPDATE watches SET last_checked_at=%s", (current,))
            upsert(
                connection,
                LoopStateRow(
                    "derive",
                    updated_at=current,
                    failed=1,
                    error="PRIVATE_DATABASE_VALUE",
                    last_progress_at=current,
                ),
            )
            connection.execute(
                "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason,created_at) VALUES('all','all',NULL,'PRIVATE_PAUSE_REASON',%s)",
                (old,),
            )
        report = health.notify_tick(state, tmp_path / "runtime", "derive", exit_code=0, now=NOW)
        assert {"run-failed", "paused", "hold-overdue"} <= set(report.alert_keys)
        assert not {"fetch-idle", "progress-idle"} & set(report.alert_keys)
        assert all("PRIVATE_" not in body.decode() for _, body in delivery[0])


def test_existing_watchdog_entrypoint_and_its_required_arguments_work(
    tmp_path, delivery, monkeypatch
):
    monkeypatch.setattr(health, "_database_signals", lambda *_args: set())
    report = health.watchdog(tmp_path / "state", runtime=tmp_path / "runtime", now=NOW)
    assert "backup-stale" in report.alert_keys
    assert "watchdog-error" not in report.alert_keys


def test_report_interval_cannot_silently_exceed_two_hours():
    with pytest.raises(SystemExit):
        health.main(["watchdog", "--state", "/unused", "--interval", "7201"])


def test_slow_checks_use_fresh_platform_time_and_stamp_completed_delivery(
    tmp_path, delivery, monkeypatch
):
    _, _, status = delivery
    clock = [NOW]
    monkeypatch.setattr(health, "time", SimpleNamespace(time=lambda: clock[0]))

    def signals(*_args, **_kwargs):
        clock[0] += 102
        value = json.loads(status.read_text())
        value["utc"] = NOW + 80
        status.write_text(json.dumps(value))
        return {"fetch-idle"}

    sent = []

    read = health._read

    def read_with_new_status(path):
        if path == status:
            clock[0] += 1
            value = json.loads(status.read_text())
            value["utc"] = clock[0]
            status.write_text(json.dumps(value))
        return read(path)

    def post(target, body):
        sent.append((target, body))
        clock[0] += 1

    monkeypatch.setattr(health, "_signals", signals)
    monkeypatch.setattr(health, "_read", read_with_new_status)
    monkeypatch.setattr(health, "_post", post)
    runtime = tmp_path / "runtime" / "health"
    report = health.watchdog(tmp_path / "state", runtime=runtime)
    assert report.alert_keys == ("fetch-idle",)
    assert report.delivery_failures == ()
    assert len(sent) == 2
    assert report.utc == NOW + 105
    assert json.loads((runtime / "heartbeat.json").read_text())["utc"] == NOW + 105
    assert json.loads((runtime / "health.json").read_text())["utc"] == NOW + 105


def test_watchdog_interval_includes_check_work_without_catchup_bursts(tmp_path, monkeypatch):
    clock = [0.0]
    starts = []
    waits = []
    durations = iter([102.0, 350.0, 5.0])

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, seconds):
            waits.append(seconds)
            clock[0] += seconds
            self.stopped = len(waits) == 3
            return self.stopped

    stop = Stop()
    monkeypatch.setattr(health, "threading", SimpleNamespace(Event=lambda: stop))
    monkeypatch.setattr(health, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def watchdog(*_args, **_kwargs):
        starts.append(clock[0])
        clock[0] += next(durations)
        return health.Report(NOW + clock[0], ())

    monkeypatch.setattr(health, "watchdog", watchdog)
    health.main(["watchdog", "--state", str(tmp_path), "--watch", "--interval", "300"])
    assert starts == [0.0, 300.0, 650.0]
    assert waits == [198.0, 0.0, 295.0]


def test_corrupt_monitoring_clocks_alert_instead_of_silencing_the_watcher(
    tmp_path, delivery, quiet
):
    runtime = tmp_path / "runtime"
    health._write(runtime / "health.json", {"notified_at": "PRIVATE_BROKEN_VALUE"})
    report = health.watchdog(tmp_path / "state", runtime=runtime, now=NOW)
    assert "health-state-invalid" in report.alert_keys
    health._write(runtime / "tick.json", {"held": True, "hold_since": "PRIVATE_BROKEN_VALUE"})
    report = health.notify_tick(tmp_path / "fresh", runtime, "derive", held=True, now=NOW)
    assert {"health-state-invalid", "hold-overdue"} <= set(report.alert_keys)
    assert not (tmp_path / "fresh").exists()
    assert "PRIVATE_BROKEN_VALUE" not in json.dumps(report.safe_dict())


def test_task_cleanup_removes_only_exact_expired_dead_owners_without_following_links(tmp_path):
    tasks = tmp_path / "tasks"
    program = "from pathlib import Path; from swingset.deploy.health import create_task_directory; import json,sys; print(json.dumps([str(create_task_directory(Path(sys.argv[1]),now=float(sys.argv[2]))) for _ in range(2)]))"
    child = subprocess.run(
        [sys.executable, "-c", program, str(tasks), str(NOW - 7200)],
        capture_output=True,
        text=True,
        check=True,
        start_new_session=True,
    )
    valid, replaced = [Path(value) for value in json.loads(child.stdout)]
    outside = tmp_path / "retained"
    outside.mkdir()
    (outside / "keep").write_text("retained")
    (valid / "nested-link").symlink_to(outside, target_is_directory=True)
    record = (replaced / "owner.json").read_bytes()
    replaced.rename(tmp_path / "preserved-original")
    replaced.mkdir()
    (replaced / "owner.json").write_bytes(record)
    (tasks / ("task-" + "f" * 32)).symlink_to(outside, target_is_directory=True)
    assert health.cleanup_task_directories(tasks, older_than=3600, now=NOW) == [valid.name]
    assert (outside / "keep").read_text() == "retained"
    assert replaced.exists() and (tmp_path / "preserved-original").exists()


def test_task_cleanup_keeps_live_or_unknown_namespace_owners_and_never_globally_prunes(tmp_path):
    tasks = tmp_path / "tasks"
    live = health.create_task_directory(tasks, now=NOW - 7200)
    unrelated = tasks / "operator-files"
    unrelated.mkdir()
    assert health.cleanup_task_directories(tasks, older_than=3600, now=NOW) == []
    owner = json.loads((live / "owner.json").read_text())
    owner.update(pid=999999999, pid_namespace="unverified-old-container")
    (live / "owner.json").write_text(json.dumps(owner))
    assert health.cleanup_task_directories(tasks, older_than=3600, now=NOW) == []
    assert live.exists() and unrelated.exists()
    with pytest.raises(ValueError):
        health.cleanup_task_directories(tmp_path, older_than=3600, now=NOW)
