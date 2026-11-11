"""A metadata collector owns only its reviewed query client, never an actor."""

import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from swingset.deploy import metadata_capture as capture


@pytest.fixture
def collector(tmp_path):
    job = object.__new__(capture._Capture)
    job.directory = tmp_path
    job.output = tmp_path / "capture"
    job.output.mkdir()
    job.started = time.monotonic()
    job.cleaning = False
    job.alarm_fired = False
    job.boot = "boot"
    job.nonce = "reviewed-nonce"
    job.tracked = {}
    job.process = None
    job.sql = b"BEGIN READ ONLY; SET LOCAL ROLE swingset_worker; ROLLBACK;"
    job.policy_raw = b"policy"
    job.kill_script = b"reviewed namespace signal script"
    job.policy = {
        "private_pg": {"id": "pg", "restart": 0},
        "private_pg_incarnation": {
            "host_pid": 123,
            "started_at": "start",
            "start_ticks": "ticks",
            "nspid": [123, 1],
            "namespace_pid": 1,
            "cgroup": "owned",
            "boot_id": "boot",
        },
        "runtime_image": "image",
        "source_revision": "revision",
        "database": "reviewed_db",
        "kill_client_sha256": capture.digest(job.kill_script),
    }
    job.result = {"complete": False}
    return job


def test_import_is_inert_and_optimized_python_refuses_before_opening_packet(tmp_path):
    script = Path(capture.__file__)
    # Running the same bytes standalone must work without importing swingset.
    result = subprocess.run(
        [sys.executable, "-O", str(script), "--directory", str(tmp_path), "--review", "absent"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"complete": False, "error_type": "RuntimeError"}
    assert list(tmp_path.iterdir()) == []
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from swingset.deploy import metadata_capture; "
            "assert 'psycopg' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr


@pytest.mark.parametrize("failure", [False, True])
def test_imported_run_restores_umask_handlers_and_timer(monkeypatch, tmp_path, failure):
    alarm = signal.getsignal(signal.SIGALRM)
    term = signal.getsignal(signal.SIGTERM)
    previous = os.umask(0o027)
    old_timer = signal.setitimer(signal.ITIMER_REAL, 60)

    class Job:
        def __init__(self, directory, review):
            assert directory == tmp_path and review == tmp_path / "review.json"

        def execute(self):
            if failure:
                raise ValueError("safe test failure")
            return {"complete": False}

    monkeypatch.setattr(capture, "_Capture", Job)
    try:
        if failure:
            with pytest.raises(ValueError):
                capture.run(tmp_path, tmp_path / "review.json")
        else:
            assert capture.run(tmp_path, tmp_path / "review.json") == {"complete": False}
        assert signal.getsignal(signal.SIGALRM) == alarm
        assert signal.getsignal(signal.SIGTERM) == term
        assert 55 < signal.getitimer(signal.ITIMER_REAL)[0] <= 60
        assert os.umask(0o027) == 0o027
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
        os.umask(previous)


def test_docker_top_uses_separate_pid_format_and_skips_header(collector, monkeypatch):
    commands = []
    monkeypatch.setattr(collector, "pg_mapping", lambda: collector.policy["private_pg_incarnation"])

    def command(argv):
        commands.append(argv)
        return b"PID\n987654321\n"

    monkeypatch.setattr(collector, "command", command)
    original = Path.read_bytes

    def read(path):
        if str(path) == "/proc/987654321/cmdline":
            return b"psql\0-v\0packet_nonce=someone-else\0"
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    collector.discover()
    assert commands == [["docker", "top", "pg", "-eo", "pid"]]
    assert collector.tracked == {}


@pytest.mark.parametrize("changed", ["ownership", "cgroup", "incarnation"])
def test_identity_mismatch_never_sends_namespace_signal(collector, monkeypatch, changed):
    record = {
        "namespace_pid": 4,
        "start_ticks": "ticks",
        "argv_sha256": "sha",
        "host_cgroup": "owned",
    }
    monkeypatch.setattr(collector, "still_owned", lambda r: changed != "ownership")

    def mapping():
        if changed == "incarnation":
            raise AssertionError("Postgres incarnation moved")
        return {"cgroup": "foreign" if changed == "cgroup" else "owned"}

    monkeypatch.setattr(collector, "pg_mapping", mapping)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("signal sent"))
    with pytest.raises(AssertionError):
        collector.signal_namespace(record, "TERM")


@pytest.mark.parametrize(
    "alarm,seconds,complete",
    [(False, 1, True), (True, 1, False), (False, 600, False), (False, 601, False)],
)
def test_deadline_cannot_publish_false_success(collector, monkeypatch, alarm, seconds, complete):
    collector.result["complete"] = True
    collector.alarm_fired = alarm
    monkeypatch.setattr(capture.time, "monotonic", lambda: collector.started + seconds)
    collector.finish()
    result = json.loads((collector.output / "result.json").read_bytes())
    assert result["complete"] is complete
    assert result["overall_alarm_fired"] is alarm
    with pytest.raises(FileExistsError):
        collector.finish()


@pytest.mark.parametrize("top_error", [False, True])
def test_owner_fence_precedes_sql_and_top_failure_prevents_sql(collector, monkeypatch, top_error):
    events = []
    argv_seen = []

    class Stdin(io.BytesIO):
        def write(self, data):
            events.append("SQL")
            assert "owner.json" in events
            assert (
                json.loads((collector.output / "owner.json").read_bytes())["nonce"]
                == collector.nonce
            )
            raise RuntimeError("stop before SQL reaches any server")

    def popen(argv, **kwargs):
        argv_seen.extend(argv)
        return SimpleNamespace(
            pid=123456789, stdin=Stdin(), stdout=io.BytesIO(), stderr=io.BytesIO(), poll=lambda: 0
        )

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(collector, "guards", lambda **kwargs: None)
    monkeypatch.setattr(collector, "inspect", lambda cid: {"id": "image", "revision": "revision"})
    monkeypatch.setattr(collector, "birth", lambda pid: "ticks")
    monkeypatch.setattr(collector, "host_cgroup", lambda pid: "owned")
    monkeypatch.setattr(collector, "incarnation_alive", lambda record: False)
    monkeypatch.setattr(collector, "stop_owned", lambda: events.append("cleanup"))
    monkeypatch.setattr(
        collector,
        "command",
        lambda argv: (
            json.dumps({"id": "image", "revision": "revision"}).encode()
            if argv[1] == "image"
            else b"0"
        ),
    )
    real_save = collector.save

    def save(name, value):
        real_save(name, value)
        events.append(name)

    monkeypatch.setattr(collector, "save", save)
    original_read = Path.read_bytes

    def read(path):
        if str(path) == "/proc/123456789/cmdline":
            return b"\0".join(item.encode() for item in argv_seen) + b"\0"
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read)

    def discover():
        if top_error:
            raise capture.CommandFailed(["docker", "top", "secret", "private argument"], 125)
        collector.tracked[17] = {"argv": ["psql"]}

    monkeypatch.setattr(collector, "discover", discover)
    result = collector.execute()
    assert result["complete"] is False and result["owned_processes_absent"] is True
    assert events[-1] == "result.json" and "cleanup" in events
    assert "default_transaction_read_only=on" in " ".join(argv_seen)
    assert "statement_timeout=30000" in " ".join(argv_seen)
    assert argv_seen[argv_seen.index("--user") + 1] == "postgres"
    assert argv_seen[-2:] == ["-f", "-"]
    if top_error:
        assert "SQL" not in events and "owner.json" not in events
        assert result["command_failure"] == {"kind": "top", "exit_code": 125}
        assert "secret" not in json.dumps(result)
    else:
        assert events.index("owner.json") < events.index("SQL") < events.index("cleanup")


def test_command_failure_keeps_safe_kind_and_code_without_stderr(collector, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=125, stdout=b"", stderr=b"private credentials"),
    )
    with pytest.raises(capture.CommandFailed) as raised:
        collector.command(["docker", "top", "private-name", "private-argument"])
    assert raised.value.safe_details() == {"kind": "top", "exit_code": 125}
    assert "private" not in str(raised.value)


def test_cli_and_standalone_use_the_same_run(monkeypatch, tmp_path):
    from swingset.cli import main

    calls = []
    monkeypatch.setattr(capture, "main", lambda argv: calls.append(argv) or 1)
    assert (
        main(
            ["graph-forensics", "--directory", str(tmp_path), "--review", str(tmp_path / "review")]
        )
        == 1
    )
    assert calls == [["--directory", str(tmp_path), "--review", str(tmp_path / "review")]]


@pytest.mark.parametrize("mode", [0o600, 0o644, 0o444, 0o620, 0o602])
def test_collector_accepts_private_staged_or_installed_readonly_code(tmp_path, mode):
    source = tmp_path / "metadata_capture.py"
    source.write_bytes(b"reviewed exact maintained code")
    source.chmod(mode)
    if mode & 0o022:
        with pytest.raises(AssertionError):
            capture.collector_bytes(source)
    else:
        assert capture.collector_bytes(source) == b"reviewed exact maintained code"
    alias = tmp_path / "alias.py"
    alias.symlink_to(source)
    with pytest.raises(AssertionError):
        capture.collector_bytes(alias)


@pytest.mark.parametrize("separator", ["", "\n", " \t\r\n"])
def test_json_stream_decodes_formatted_composite_aggregates_and_ndjson(separator):
    documents = [
        {
            "section": "units",
            "complete": True,
            "rows": [
                {"unit": "project/inventory/all", "newest_equivalences": []},
                {"unit": "project/year/2026", "newest_equivalences": []},
            ],
        },
        {"section": "dependencies", "complete": False, "sets": []},
    ]
    raw = (" \n" + separator.join(json.dumps(doc, indent=2) for doc in documents) + "\n").encode()
    assert capture.decode_documents(raw, maximum_bytes=len(raw), maximum_documents=2) == documents
    ndjson = b"\n".join(json.dumps(doc).encode() for doc in documents)
    assert (
        capture.decode_documents(ndjson, maximum_bytes=len(ndjson), maximum_documents=2)
        == documents
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"section":',
        b"{} trailing junk",
        b"{} {",
        b"[]",
        b"null",
        b'{"value":NaN}',
        b"{}\xc2\xa0{}",
        b"\xff",
    ],
)
def test_json_stream_rejects_truncation_junk_nonobjects_and_invalid_json(raw):
    with pytest.raises(ValueError):
        capture.decode_documents(raw, maximum_bytes=1024, maximum_documents=4)


@pytest.mark.parametrize(
    "maximum_bytes,maximum_documents,raw",
    [(1, 1, b"{}"), (1024, 1, b"{}{}"), (0, 1, b""), (1, 0, b"")],
)
def test_json_stream_enforces_byte_and_document_bounds(maximum_bytes, maximum_documents, raw):
    with pytest.raises(ValueError):
        capture.decode_documents(
            raw, maximum_bytes=maximum_bytes, maximum_documents=maximum_documents
        )


@pytest.mark.parametrize(
    "problem", ["none", "incomplete", "duplicate", "missing", "reordered", "oversize"]
)
def test_formatted_sections_preserve_complete_identity_and_output_bounds(collector, problem):
    collector.policy["expected_sections"] = ["units", "dependencies"]
    collector.policy["limits"] = {"public_output_bytes": 65536 + 4096}
    documents = [
        {
            "section": "units",
            "complete": True,
            "rows": [{"newest_equivalences": [{"recipe_bytes": 128}]}],
        },
        {"section": "dependencies", "complete": problem != "incomplete", "sets": []},
    ]
    if problem == "duplicate":
        documents[1] = documents[0]
    elif problem == "missing":
        documents.pop()
    elif problem == "reordered":
        documents.reverse()
    path = collector.output / "data.jsonl"
    path.write_text("\n".join(json.dumps(doc, indent=2) for doc in documents))
    if problem == "oversize":
        path.write_bytes(path.read_bytes() + b" " * 4096)
    if problem == "none":
        collector.verify_sections(path)
        assert collector.result["sections"] == ["units", "dependencies"]
    else:
        with pytest.raises((AssertionError, ValueError)):
            collector.verify_sections(path)
        assert "sections" not in collector.result
    assert collector.result["complete"] is False


@pytest.mark.parametrize(
    "problem", ["none", "missing", "duplicate", "wrong_kind", "changed_payload", "incomplete"]
)
def test_all_four_link_sections_bind_exact_event_targets_and_retained_labels(collector, problem):
    identifiers = [
        "2025-07-midwest-westie-fest",
        "2026-02-rose-city-swing",
        "2026-05-soswing",
        "2026-07-midwest-westie-fest",
    ]
    documents = []
    targets = []
    for identifier in identifiers:
        retained = [
            {
                "table_name": "identity_links",
                "record_key": identifier,
                "payload_json": json.dumps({"event": identifier, "name": "Joséphine"}),
            }
        ]
        raw = b"".join(
            json.dumps(
                [row["table_name"], row["record_key"], row["payload_json"]],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
            + b"\n"
            for row in retained
        )
        targets.append({"unit": "link/event/" + identifier, "expected": [capture.digest(raw)]})
        documents.append({"section": "link/" + identifier, "complete": True, "retained": retained})
    if problem == "missing":
        targets.pop()
    elif problem == "duplicate":
        targets.append(dict(targets[-1]))
    elif problem == "wrong_kind":
        targets[-1]["unit"] = "link/" + identifiers[-1]
    elif problem == "changed_payload":
        documents[-1]["retained"][0]["payload_json"] = "different retained output"
    elif problem == "incomplete":
        documents[-1]["complete"] = False
    collector.policy.update(
        {
            "expected_sections": ["link/" + identifier for identifier in identifiers],
            "targets": targets,
            "limits": {"public_output_bytes": 65536 + 8192},
        }
    )
    path = collector.output / "data.jsonl"
    path.write_text("\n".join(json.dumps(doc, indent=2) for doc in documents))
    if problem == "none":
        collector.verify_sections(path)
        assert collector.result["sections"] == collector.policy["expected_sections"]
    else:
        with pytest.raises((ValueError, AssertionError)):
            collector.verify_sections(path)
        assert "sections" not in collector.result
    assert collector.result["complete"] is False
