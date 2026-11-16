"""A metadata collector owns only its reviewed query client, never an actor."""

import base64
import copy
import hashlib
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
def dependency_packet(tmp_path):
    # Golden witness: substituting inventory moves its entry ahead of the
    # unmodified entry. Float 1.0, negative zero, bool and null stay distinct.
    raw = (
        '[{"fingerprint":"' + "8" * 64 + '","key":"unmodified",'
        '"kind":"dependency_set","note":"DO_NOT_EXPORT_PERSON_OR_SQL",'
        '"typed":[true,1,1.0,-0.0,null]},'
        '{"fingerprint":"' + "f" * 64 + '","key":"selected_project_inventory",'
        '"kind":"dependency_set"}]'
    ).encode()
    root = "98bdcd58584b938cbfdbbab26349b2d1d3e78c59cd8040c32ce4210e8b2e9f8c"
    assert hashlib.sha256(raw).hexdigest() == root
    unit = {
        "unit": "project/resolution/example-event",
        "generation_id": "dg_" + "1" * 64,
        "label": ["3" * 64, 2],
        "graph_found_label": ["3" * 64, 2],
        "expected_root": root,
        "found_root": "5bc182c8588c90f28e9f0fe4f521009071328e997a26e56d8fd2a9610581cd2a",
        "stored_versions": {
            "recipe/code/project/resolution": "5a79f84d820d759357fd719f286d7ba18341406645535e99982ad1d30f93f000"
        },
    }
    request = {
        "format": "swingset-inventory-dependency-request-v1",
        "graph_receipt_sha256": capture.DEPENDENCY_RECEIPT,
        "graph_context_sha256": capture.DEPENDENCY_CONTEXT,
        "source_checkpoint": capture.DEPENDENCY_CHECKPOINT,
        "source_boundary_sha256": "4" * 64,
        "inventory_generation_id": "dg_" + "2" * 64,
        "inventory_group_sha256": "1d1027958ec9a5c7c17c34486012e854ea5f32e511fa3fa6ad4b205625d2a66a",
        "roots": [root],
        "units": [unit],
    }
    frames = [
        {
            "kind": "header",
            "format": "swingset-inventory-dependency-stream-v1",
            "request_sha256": "pending",
            "source": dict(capture.DEPENDENCY_SOURCE),
            "snapshot": {
                "backend_pid": 123,
                "pg_snapshot": "10:10:",
                "default_transaction_read_only": "on",
                "transaction_read_only": "on",
                "isolation": "repeatable read",
                "statement_timeout": "15s",
                "lock_timeout": "2s",
                "transaction_timeout": "10min",
            },
        },
        {
            "kind": "inventory",
            "pointer_generation_id": "dg_" + "2" * 64,
            "selected_generation_id": "dg_" + "2" * 64,
        },
        {"kind": "root_begin", "id": root, "raw_bytes": len(raw)},
        {"kind": "root_chunk", "offset": 0, "base64": base64.b64encode(raw).decode()},
        {"kind": "root_end", "id": root},
        {
            "kind": "unit",
            "unit": unit["unit"],
            "pointer_generation_id": unit["generation_id"],
            "generation_id": unit["generation_id"],
            "label": unit["label"],
            "dependency_id": root,
            "stored_versions": unit["stored_versions"],
        },
        {"kind": "committed", "request_sha256": "pending", "roots": 1, "units": 1},
    ]

    def encode(changed_request=None, changed_frames=None):
        req = request if changed_request is None else changed_request
        rows = copy.deepcopy(frames if changed_frames is None else changed_frames)
        body = json.dumps(req, sort_keys=True).encode()
        expected = hashlib.sha256(body).hexdigest()
        for frame in rows:
            if frame.get("request_sha256") == "pending":
                frame["request_sha256"] = expected
        wire = b"".join(json.dumps(frame).encode() + b"\n" for frame in rows)
        return body, expected, wire

    return SimpleNamespace(
        request=request, frames=frames, encode=encode, path=tmp_path / "request.json"
    )


def test_dependency_witness_proves_whole_hash_and_outer_sort_without_exporting_payload(
    dependency_packet,
):
    raw, sha, wire = dependency_packet.encode()
    report = capture._dependency_witness(raw, sha, io.BytesIO(wire))
    assert report["complete"] and report["remaining_units"] == []
    assert report["units"][0]["explained"] is True
    assert (
        report["units"][0]["counterfactual_root"]
        == dependency_packet.request["units"][0]["found_root"]
    )
    assert report["operation_authority"] is False and report["source_process_exit_not_observed"]
    assert report["source_checkpoint"]["basis"] == "accepted_prior_boundary"
    assert "source_scope_pointer" in report["inventory_basis"]
    text = json.dumps(report)
    assert "DO_NOT_EXPORT" not in text and "typed" not in text and "manifest" not in text
    assert report["graph_context_sha256"] == capture.DEPENDENCY_CONTEXT
    assert "graph_context_sha256" not in report["source"]


def test_dependency_counterfactual_failure_stays_named_and_does_not_become_a_waiver(
    dependency_packet,
):
    request = copy.deepcopy(dependency_packet.request)
    request["units"][0]["found_root"] = "5" * 64
    raw, sha, wire = dependency_packet.encode(request)
    report = capture._dependency_witness(raw, sha, io.BytesIO(wire))
    assert report["complete"] and report["units"][0]["explained"] is False
    assert report["remaining_units"] == ["project/resolution/example-event"]
    assert report["operation_authority"] is False


@pytest.mark.parametrize(
    "change", ["duplicate", "reordered", "truncated", "uncommitted", "extra", "offset"]
)
def test_dependency_stream_order_and_commit_EOF_are_mandatory(dependency_packet, change):
    frames = copy.deepcopy(dependency_packet.frames)
    if change == "duplicate":
        frames.insert(1, frames[0])
    elif change == "reordered":
        frames[1], frames[2] = frames[2], frames[1]
    elif change == "truncated":
        frames = frames[:4]
    elif change == "uncommitted":
        frames = frames[:-1]
    elif change == "extra":
        frames.append(frames[-1])
    else:
        frames[3]["offset"] = 1
    raw, sha, wire = dependency_packet.encode(changed_frames=frames)
    with pytest.raises(capture.DependencyWitnessRefused):
        capture._dependency_witness(raw, sha, io.BytesIO(wire))


@pytest.mark.parametrize(
    "change", ["source", "snapshot", "pointer", "label", "recipe", "root", "frame_type"]
)
def test_dependency_original_binding_refuses_tampering_before_reconstruction(
    dependency_packet, change
):
    frames = copy.deepcopy(dependency_packet.frames)
    if change == "source":
        frames[0]["source"]["database_id"] = "foreign"
    elif change == "snapshot":
        frames[0]["snapshot"]["transaction_read_only"] = "off"
    elif change == "pointer":
        frames[5]["pointer_generation_id"] = "dg_" + "6" * 64
    elif change == "label":
        frames[5]["label"] = ["3" * 64, True]
    elif change == "recipe":
        frames[5]["stored_versions"] = {}
    elif change == "root":
        frames[3]["base64"] = base64.b64encode(b"DO_NOT_EXPORT_PRIVATE_SQL").decode()
    else:
        frames[6]["roots"] = True
    raw, sha, wire = dependency_packet.encode(changed_frames=frames)
    with pytest.raises(capture.DependencyWitnessRefused):
        capture._dependency_witness(raw, sha, io.BytesIO(wire))


@pytest.mark.parametrize(
    "change", ["sha", "context", "checkpoint", "bound", "duplicate", "type", "foreign_unit"]
)
def test_dependency_request_is_fixed_and_independently_hash_bound(dependency_packet, change):
    req = copy.deepcopy(dependency_packet.request)
    if change == "context":
        req["graph_context_sha256"] = "5cc5" + "0" * 60
    elif change == "checkpoint":
        req["source_checkpoint"] = "0" * 64
    elif change == "bound":
        req["roots"] = ["0" * 64] * 129
    elif change == "duplicate":
        req["units"] *= 2
    elif change == "type":
        req["units"][0]["label"][1] = 2.0
    elif change == "foreign_unit":
        req["units"][0]["unit"] = "link/event/foreign"
    raw, sha, wire = dependency_packet.encode(req)
    if change == "sha":
        sha = "0" * 64
    with pytest.raises(capture.DependencyWitnessRefused):
        capture._dependency_witness(raw, sha, io.BytesIO(wire))


def test_dependency_root_budget_includes_the_selected_inventory_group(dependency_packet):
    for count in [127, 128]:
        request = copy.deepcopy(dependency_packet.request)
        request["roots"] = [f"{i:064x}" for i in range(count)]
        request["units"] = [
            dict(request["units"][0], unit=f"project/resolution/event-{i:03d}", expected_root=root)
            for i, root in enumerate(request["roots"])
        ]
        raw, sha, _ = dependency_packet.encode(request)
        if count == 127:
            assert len(capture._dependency_request(raw, sha)["roots"]) + 1 == 128
        else:
            with pytest.raises(capture.DependencyWitnessRefused):
                capture._dependency_request(raw, sha)


def test_dependency_framing_duplicate_keys_and_oversize_never_reach_report(dependency_packet):
    raw, sha, wire = dependency_packet.encode()
    duplicate = wire.replace(b'"kind": "header"', b'"kind": "header", "kind": "header"', 1)
    for bad in [duplicate, b" " * 65536 + b"\n", wire[:-1]]:
        with pytest.raises(capture.DependencyWitnessRefused):
            capture._dependency_witness(raw, sha, io.BytesIO(bad))
    frames = copy.deepcopy(dependency_packet.frames)
    frames[2]["raw_bytes"] = 262145
    raw, sha, wire = dependency_packet.encode(changed_frames=frames)
    with pytest.raises(capture.DependencyWitnessRefused):
        capture._dependency_witness(raw, sha, io.BytesIO(wire))


def _dependency_changed_root(packet, body):
    request = copy.deepcopy(packet.request)
    root = hashlib.sha256(body).hexdigest()
    request["roots"] = [root]
    request["units"][0]["expected_root"] = root
    frames = copy.deepcopy(packet.frames[:2])
    frames.append({"kind": "root_begin", "id": root, "raw_bytes": len(body)})
    for offset in range(0, len(body), 32768):
        frames.append(
            {
                "kind": "root_chunk",
                "offset": offset,
                "base64": base64.b64encode(body[offset : offset + 32768]).decode(),
            }
        )
    frames.append({"kind": "root_end", "id": root})
    unit = dict(packet.frames[5], dependency_id=root)
    frames.extend([unit, packet.frames[-1]])
    return packet.encode(request, frames)


def test_dependency_missing_inventory_is_an_explicit_residual_not_silently_dropped(
    dependency_packet,
):
    raw, sha, wire = _dependency_changed_root(dependency_packet, b"[]")
    report = capture._dependency_witness(raw, sha, io.BytesIO(wire))
    assert report["remaining_units"] == ["project/resolution/example-event"]
    assert report["units"][0]["counterfactual_root"] is None
    assert report["units"][0]["inventory_entry_count"] == 0


@pytest.mark.parametrize("body", [b'[ {"kind":"replay"} ]', b'[{"kind":"a","kind":"b"}]'])
def test_dependency_raw_hash_does_not_waive_canonical_or_duplicate_key_proof(
    dependency_packet, body
):
    raw, sha, wire = _dependency_changed_root(dependency_packet, body)
    with pytest.raises(capture.DependencyWitnessRefused):
        capture._dependency_witness(raw, sha, io.BytesIO(wire))


def test_dependency_root_chunks_are_bounded_and_lossless_across_UTF8_boundaries(dependency_packet):
    members = [
        {
            "kind": "dependency_set",
            "key": "unmodified",
            "fingerprint": "8" * 64,
            "private": "É" * 20000,
        }
    ]
    body = json.dumps(members, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    raw, sha, wire = _dependency_changed_root(dependency_packet, body)
    report = capture._dependency_witness(raw, sha, io.BytesIO(wire))
    assert report["roots"] == 1 and report["remaining_units"]
    assert "É" not in json.dumps(report, ensure_ascii=False)
    # A duplicate chunk cannot be accepted as a valid continuation offset.
    frames = [json.loads(line) for line in wire.splitlines()]
    frames[4]["offset"] = 0
    changed = b"".join(json.dumps(frame).encode() + b"\n" for frame in frames)
    with pytest.raises(capture.DependencyWitnessRefused):
        capture._dependency_witness(raw, sha, io.BytesIO(changed))


def test_dependency_elapsed_and_report_bounds_refuse_without_partial_success(
    monkeypatch, dependency_packet
):
    raw, sha, wire = dependency_packet.encode()
    readings = iter([0, 601])
    with monkeypatch.context() as scoped:
        scoped.setattr(time, "monotonic", lambda: next(readings))
        with pytest.raises(capture.DependencyWitnessRefused):
            capture._dependency_witness(raw, sha, io.BytesIO(wire))
    monkeypatch.setattr(capture, "DEPENDENCY_REPORT_BYTES", 100)
    with pytest.raises(capture.DependencyWitnessRefused):
        capture._dependency_witness(raw, sha, io.BytesIO(wire))


def test_dependency_worker_preserves_primary_fatal_and_restores_alarm(dependency_packet):
    raw, sha, _ = dependency_packet.encode()
    dependency_packet.path.write_bytes(raw)
    failure = KeyboardInterrupt("DO_NOT_EXPORT_PRIVATE_SQL")
    previous = signal.getsignal(signal.SIGALRM)

    class Interrupted(io.BytesIO):
        def readline(self, size=-1):
            raise failure

    with pytest.raises(KeyboardInterrupt) as caught:
        capture.dependency_witness(dependency_packet.path, sha, Interrupted())
    assert caught.value is failure
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_dependency_blocked_reader_alarm_is_finite_and_does_not_publish_success(dependency_packet):
    raw, sha, _ = dependency_packet.encode()
    dependency_packet.path.write_bytes(raw)
    previous = signal.getsignal(signal.SIGALRM)

    class Expired(io.BytesIO):
        def readline(self, size=-1):
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
            pytest.fail("alarm returned")

    with pytest.raises(TimeoutError):
        capture.dependency_witness(dependency_packet.path, sha, Expired())
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_dependency_existing_alarm_refuses_without_opening_request(monkeypatch, dependency_packet):
    prior = signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "open", lambda *a, **k: pytest.fail("opened request"))
            with pytest.raises(capture.DependencyWitnessRefused):
                capture.dependency_witness(dependency_packet.path, "0" * 64, io.BytesIO())
        assert 55 < signal.getitimer(signal.ITIMER_REAL)[0] <= 60
    finally:
        signal.setitimer(signal.ITIMER_REAL, *prior)


def test_dependency_absolute_alarm_covers_report_encoding_and_flush(monkeypatch, dependency_packet):
    raw, sha, wire = dependency_packet.encode()
    dependency_packet.path.write_bytes(raw)
    previous = signal.getsignal(signal.SIGALRM)
    failure = KeyboardInterrupt("DO_NOT_EXPORT_REPORT_FAILURE")

    class Blocked:
        def write(self, value):
            assert signal.getsignal(signal.SIGALRM) != previous
            assert 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 600
            raise failure

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stdout", Blocked())
        with pytest.raises(KeyboardInterrupt) as caught:
            capture.dependency_witness(dependency_packet.path, sha, io.BytesIO(wire), emit=True)
    assert caught.value is failure
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_dependency_cli_has_hash_only_stdout_and_sanitized_failure(
    monkeypatch, capsys, dependency_packet
):
    raw, sha, wire = dependency_packet.encode()
    dependency_packet.path.write_bytes(raw)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(wire)))
    argv = ["--dependency-request", str(dependency_packet.path), "--request-sha256", sha]
    assert capture.main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["complete"] and report["units"][0]["explained"]
    assert list(dependency_packet.path.parent.iterdir()) == [dependency_packet.path]

    class Failed(io.BytesIO):
        def readline(self, size=-1):
            raise ValueError("DO_NOT_EXPORT_PRIVATE_SQL")

    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=Failed()))
    assert capture.main(argv) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"complete": False, "error_type": "ValueError"}
    assert captured.err == "" and "DO_NOT_EXPORT" not in captured.out


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
