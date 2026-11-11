"""One real verifier transaction preserves its error and rolls every trial write back."""

import contextlib
import json
import signal
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from swingset.deploy import unit_diagnostic as diagnostic


@pytest.mark.parametrize("outcome", ["error", "normal", "deadline"])
def test_real_verify_transaction_preserves_error_and_rolls_back(tmp_path, monkeypatch, outcome):
    from swingset.clock import FakeClock
    from swingset.project import process
    from swingset.state import verify, write_deadline
    from swingset.state.db import open_database
    from swingset.state.write_deadline import WriteDeadlineExceeded

    clock = FakeClock()
    bundle = SimpleNamespace(config=SimpleNamespace(derivation=SimpleNamespace(cutoff=False)))
    original_label = verify._live_label
    original_dispatch = process._dispatch
    written = []

    def transform(conn, *args, **kwargs):
        conn.execute("INSERT INTO ops.meta(key,value) VALUES('diagnostic-trial','must roll back')")
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES('trial-write',%s,1)",
            (clock.now().isoformat(),),
        )
        conn.execute(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
            "VALUES('project','inventory','all',%s)",
            (clock.now().isoformat(),),
        )
        conn.execute("UPDATE runs SET summary_json=%s WHERE run_id='graph_verify'", ('["dirty"]',))
        written.append(
            conn.execute("SELECT value FROM ops.meta WHERE key='diagnostic-trial'").fetchone()[0]
        )
        if outcome == "error":
            raise RuntimeError("inventory transform original cause")
        if outcome == "deadline":
            raise WriteDeadlineExceeded("injected ordinary atomic deadline")

    def producer(database, unit, bundle, clock, run_id, **kwargs):
        return process._dispatch(database.connection, unit, bundle, clock.now().isoformat(), run_id)

    monkeypatch.setattr(process, "_dispatch", transform)
    monkeypatch.setattr(process, "process_unit", producer)
    with open_database(tmp_path / "state", lock=False) as db:
        db.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run,summary_json) VALUES('graph_verify',%s,1,%s)",
            (clock.now().isoformat(), '["original"]'),
        )
        report = diagnostic.profile_derivation(db, bundle, clock)
        assert written == ["must roll back"]
        assert report["unit_result"]["state"] == "failed"
        expected = {
            "normal": "transform returned without completing its unit",
            "error": "RuntimeError: inventory transform original cause",
            "deadline": "WriteDeadlineExceeded: injected ordinary atomic deadline",
        }
        assert report["unit_result"]["error"] == expected[outcome]
        assert report["rollback_fingerprint_equal"] and report["observer_complete"]
        assert report["rollback_run_rows_equal"]
        assert report["write_budget_seconds"] == write_deadline.WRITE_SECONDS == 75
        assert (
            db.connection.execute("SELECT 1 FROM ops.meta WHERE key='diagnostic-trial'").fetchone()
            is None
        )
        assert (
            db.connection.execute("SELECT 1 FROM runs WHERE run_id='trial-write'").fetchone()
            is None
        )
        assert (
            db.connection.execute(
                "SELECT summary_json FROM runs WHERE run_id='graph_verify'"
            ).fetchone()[0]
            == '["original"]'
        )
        assert (
            db.connection.execute(
                "SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='inventory'"
            ).fetchone()
            is None
        )
    assert verify._live_label is original_label
    assert process._dispatch is transform and original_dispatch is not transform
    phases = {row["name"]: row for row in report["phases"]}
    assert phases["process"]["error_type"] == (
        None
        if outcome == "normal"
        else type(RuntimeError() if outcome == "error" else WriteDeadlineExceeded()).__name__
    )
    assert all(
        row["wall_seconds"] >= 0 and row["thread_cpu_seconds"] >= 0 for row in report["phases"]
    )


def test_inventory_observation_keeps_the_real_recording_entry_point(tmp_path, monkeypatch):
    from swingset.clock import FakeClock
    from swingset.project import history, process
    from swingset.state import read_sets
    from swingset.state.db import open_database
    from swingset.state.work import WorkUnit

    entered, original_process = [], process.process_unit
    bundle = SimpleNamespace(
        files={}, config=SimpleNamespace(derivation=SimpleNamespace(cutoff=False))
    )

    def body(conn, **kwargs):
        recorder = read_sets.active(conn)
        assert recorder is not None and recorder.unit == WorkUnit("project", "inventory", "all")
        entered.append(recorder.unit)
        conn.execute("INSERT INTO ops.meta(key,value) VALUES('observed-entry-witness','rollback')")
        raise RuntimeError("observed through the real recording entry point")

    monkeypatch.setattr(history, "prepare_inventory", body)
    with open_database(tmp_path / "state", lock=False) as database:
        report = diagnostic.profile_derivation(database, bundle, FakeClock())
        assert report["unit_result"]["error"] == (
            "RuntimeError: observed through the real recording entry point"
        )
        assert (
            database.connection.execute(
                "SELECT value FROM ops.meta WHERE key='observed-entry-witness'"
            ).fetchone()
            is None
        )
    assert entered == [WorkUnit("project", "inventory", "all")]
    assert report["observer_complete"] and report["rollback_fingerprint_equal"]
    assert report["observer"]["inventory_span_calls"]["prepare_inventory"] == 1
    assert process.process_unit is original_process and history.prepare_inventory is body


def test_observer_passes_helper_completions_and_labels_without_altering_producer(
    tmp_path, monkeypatch
):
    from swingset.clock import FakeClock
    from swingset.project import process
    from swingset.state import derivations, verify
    from swingset.state.db import open_database
    from swingset.state.work import WorkUnit

    calls = []
    helper = WorkUnit("project", "dancer", "1")
    original_label = verify._live_label
    original_complete = derivations.complete

    def completion(conn, selection, **kwargs):
        calls.append(selection.unit)
        return "helper-generation"

    def producer(database, unit, *args, **kwargs):
        # These are legitimate helper calls, not the selected unit's proof.
        assert verify._live_label(database.connection, helper) == original_label(
            database.connection, helper
        )
        assert (
            derivations.complete(database.connection, SimpleNamespace(unit=helper))
            == "helper-generation"
        )
        worker = threading.Thread(
            target=derivations.complete, args=(database.connection, SimpleNamespace(unit=helper))
        )
        worker.start()
        worker.join(timeout=1)
        assert not worker.is_alive()
        for _ in range(70):
            assert (
                derivations.complete(database.connection, SimpleNamespace(unit=helper))
                == "helper-generation"
            )

    monkeypatch.setattr(derivations, "complete", completion)
    monkeypatch.setattr(process, "process_unit", producer)
    monkeypatch.setattr(process, "_dispatch", lambda conn: None)
    with open_database(tmp_path / "state", lock=False) as db:
        report = diagnostic.profile_derivation(db, SimpleNamespace(), FakeClock())
    assert calls == [helper] * 72
    assert report["observer"]["other_completions"] == 72
    assert report["observer"]["other_labels"] == 1
    assert report["observer"]["overflow"] and not report["observer_complete"]
    assert report["observer"]["foreign_thread_calls"] == 1
    assert len(report["phases"]) == 64
    assert report["unit_result"]["error"] == "transform returned without completing its unit"
    assert derivations.complete is completion and original_complete is not completion


def test_failed_labeling_retains_original_error_and_marks_missing_witness(tmp_path, monkeypatch):
    from swingset.clock import FakeClock
    from swingset.state import verify
    from swingset.state.db import open_database

    def failed(*args):
        raise ValueError("original inventory label failure")

    monkeypatch.setattr(verify, "_live_label", failed)
    with open_database(tmp_path / "state", lock=False) as db:
        report = diagnostic.profile_derivation(db, SimpleNamespace(), FakeClock())
    assert report["unit_result"]["error"] == "ValueError: original inventory label failure"
    assert not report["rollback_fingerprint_equal"]
    assert report["rollback_error_type"] == "ValueError"


def test_inventory_subspans_exclude_labels_and_foreign_work_and_restore(monkeypatch):
    from swingset.project import history, process
    from swingset.state import derivations, verify

    token, calls = object(), []
    owner = threading.get_ident()
    native_wall, native_cpu = diagnostic.time.monotonic, diagnostic.time.thread_time

    def owner_clock(clock):
        def read():
            assert threading.get_ident() == owner
            return clock()

        return read

    monkeypatch.setattr(
        diagnostic,
        "time",
        SimpleNamespace(monotonic=owner_clock(native_wall), thread_time=owner_clock(native_cpu)),
    )

    def desired(*args, **kwargs):
        calls.append("desired")
        return token

    def capture(*args, **kwargs):
        assert derivations.desired("private argument") is token
        return token

    def prepare(*args, **kwargs):
        calls.append("prepare")
        return token

    def hints(*args, **kwargs):
        calls.append("hints")
        return token

    def label(*args):
        assert derivations.capture("private label argument") is token
        assert history.prepare_inventory("private label argument") is token
        return "same", 0

    def producer(database, unit):
        assert derivations.capture("private argument") is token
        assert history.prepare_inventory("private argument") is token
        worker = threading.Thread(target=history.prepare_inventory, args=("private argument",))
        worker.start()
        worker.join(timeout=1)
        assert not worker.is_alive()
        assert process._hint_links("private argument") is token
        return token

    def verifier(database, unit, *args):
        verify._live_label(database.connection, unit)
        assert process.process_unit(database=database, unit=unit) is token
        verify._live_label(database.connection, unit)
        return {"state": "verified"}

    monkeypatch.setattr(derivations, "desired", desired)
    monkeypatch.setattr(derivations, "capture", capture)
    monkeypatch.setattr(history, "prepare_inventory", prepare)
    monkeypatch.setattr(process, "_hint_links", hints)
    monkeypatch.setattr(process, "process_unit", producer)
    monkeypatch.setattr(verify, "verify_unit", verifier)
    monkeypatch.setattr(verify, "_live_label", label)
    monkeypatch.setattr(verify, "_pointer_label", lambda *args: "pointer")
    connection = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(fetchone=lambda: ("run",)),
        read_snapshot=contextlib.nullcontext,
    )
    report = diagnostic.profile_derivation(SimpleNamespace(connection=connection), None, None)
    assert report["observer"]["inventory_span_calls"] == {
        "capture": 1,
        "desired": 1,
        "prepare_inventory": 1,
        "post_link_hints": 1,
    }
    assert report["observer"]["quality"] and report["observer"]["foreign_thread_calls"] == 1
    assert not report["observer_complete"] and report["rollback_fingerprint_equal"]
    assert calls.count("desired") == 4 and calls.count("prepare") == 5
    assert "private" not in json.dumps(report)
    for module, name, function in (
        (derivations, "capture", capture),
        (derivations, "desired", desired),
        (history, "prepare_inventory", prepare),
        (process, "_hint_links", hints),
        (process, "process_unit", producer),
        (verify, "_live_label", label),
    ):
        assert getattr(module, name) is function


@pytest.mark.parametrize("producer_kind", [ValueError, KeyboardInterrupt, "uncertain", None])
@pytest.mark.parametrize("clock_kind", [OSError, KeyboardInterrupt])
def test_inventory_finalizer_fault_preserves_original_error_and_restores(
    monkeypatch, producer_kind, clock_kind
):
    from swingset.project import history, process
    from swingset.state import derivations, verify
    from swingset.state.engine import CommitUncertain

    producer_kind = CommitUncertain if producer_kind == "uncertain" else producer_kind
    original = None if producer_kind is None else producer_kind("private producer error")
    observer_error = clock_kind("private observer error")
    native_cpu, entered = diagnostic.time.thread_time, False

    def cpu():
        if entered:
            raise observer_error
        return native_cpu()

    monkeypatch.setattr(
        diagnostic, "time", SimpleNamespace(monotonic=diagnostic.time.monotonic, thread_time=cpu)
    )

    def prepare(*args, **kwargs):
        nonlocal entered
        entered = True
        if original is not None:
            raise original
        return "same result"

    def producer(*args):
        return history.prepare_inventory()

    def verifier(database, unit, *args):
        process.process_unit(database, unit)
        return {"state": "failed"}

    monkeypatch.setattr(history, "prepare_inventory", prepare)
    monkeypatch.setattr(process, "process_unit", producer)
    monkeypatch.setattr(verify, "verify_unit", verifier)
    monkeypatch.setattr(verify, "_pointer_label", lambda *args: "pointer")
    hooks = [
        (module, name, getattr(module, name))
        for module, name in (
            (derivations, "capture"),
            (derivations, "desired"),
            (history, "prepare_inventory"),
            (process, "_hint_links"),
            (process, "process_unit"),
            (verify, "_live_label"),
        )
    ]
    database = SimpleNamespace(
        connection=SimpleNamespace(
            execute=lambda *args: SimpleNamespace(fetchone=lambda: ("run",)),
            read_snapshot=contextlib.nullcontext,
        )
    )
    report = {}
    expected = (
        original
        if original is not None
        else (observer_error if clock_kind is KeyboardInterrupt else None)
    )
    if expected is not None:
        with pytest.raises(type(expected)) as caught:
            diagnostic.profile_derivation(database, None, None, report)
        assert caught.value is expected
    else:
        assert diagnostic.profile_derivation(database, None, None, report) is report
    assert not report["observer"]["quality"]
    assert report["observer"]["inventory_span_calls"]["prepare_inventory"] == 1
    assert all(getattr(module, name) is function for module, name, function in hooks)


def test_immediate_timer_delivery_restores_the_callers_handler_and_timer(monkeypatch):
    handler = signal.getsignal(signal.SIGALRM)
    timer = signal.getitimer(signal.ITIMER_REAL)
    actual = signal.setitimer
    fired = False

    def arm(which, seconds, interval=0):
        nonlocal fired
        if seconds == 90 and not fired:
            fired = True
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
        return actual(which, seconds, interval)

    monkeypatch.setattr(signal, "setitimer", arm)
    with pytest.raises(diagnostic._PhaseExpired):
        with diagnostic.phase_deadline(90):
            pytest.fail("entered the body after timer delivery")
    assert signal.getsignal(signal.SIGALRM) is handler
    assert signal.getitimer(signal.ITIMER_REAL) == timer


def test_phase_timeout_is_real_and_restores_an_earlier_owner_alarm():
    previous = signal.getsignal(signal.SIGALRM)
    original_timer = signal.getitimer(signal.ITIMER_REAL)
    assert original_timer == (0, 0)
    signal.setitimer(signal.ITIMER_REAL, 10)
    try:
        with pytest.raises(diagnostic._PhaseExpired):
            with diagnostic.phase_deadline(0.01):
                threading.Event().wait(0.1)
        assert 9 < signal.getitimer(signal.ITIMER_REAL)[0] < 10
        assert signal.getsignal(signal.SIGALRM) is previous
    finally:
        signal.setitimer(signal.ITIMER_REAL, *original_timer)


@pytest.mark.parametrize("timer", [(1, 0), (100, 5)])
def test_conflicting_alarm_is_not_replaced(monkeypatch, timer):
    monkeypatch.setattr(signal, "getitimer", lambda which: timer)
    monkeypatch.setattr(signal, "signal", lambda *args: pytest.fail("changed earlier alarm"))
    with pytest.raises(RuntimeError, match="earlier deadline"):
        with diagnostic.phase_deadline(90):
            pytest.fail("entered conflicting phase")


def test_existing_registry_refused_before_producer_or_receipt(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    registry.mkdir(mode=0o700)
    prior = registry / "old.json"
    prior.write_text("owned state must survive")
    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(registry))
    monkeypatch.setattr(
        diagnostic, "producer_identity", lambda: pytest.fail("adopted previous registry")
    )
    with pytest.raises(ValueError, match="existing ownership"):
        diagnostic.run(
            tmp_path / "source",
            tmp_path / "capture",
            tmp_path / "receipt.json",
            Path("config"),
            Path("overrides"),
            diagnostic.BUSINESS_CLOCK,
            diagnostic.SOURCE_CHECKPOINT,
            diagnostic.ACCEPTED_CHECKPOINT,
        )
    assert prior.read_text() == "owned state must survive"
    assert not (tmp_path / "receipt.json").exists()


def test_wrong_installed_producer_refused_before_any_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(tmp_path / "registry"))
    monkeypatch.setenv("SWINGSET_RUNTIME_IMAGE", diagnostic.RUNTIME_ARTIFACT)
    from swingset.state import inputs

    monkeypatch.setattr(inputs, "capture", lambda *args: pytest.fail("captured wrong producer"))
    with pytest.raises(ValueError, match="producer differs"):
        diagnostic.run(
            tmp_path / "source",
            tmp_path / "capture",
            tmp_path / "receipt.json",
            Path("config"),
            Path("overrides"),
            diagnostic.BUSINESS_CLOCK,
            diagnostic.SOURCE_CHECKPOINT,
            diagnostic.ACCEPTED_CHECKPOINT,
        )
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize("budget", [61.0, 74.0, 76.0, float("nan"), True])
def test_unknown_installed_write_budget_refuses_before_capture_or_receipt(
    tmp_path, monkeypatch, budget
):
    from swingset.state import inputs, write_deadline

    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(tmp_path / "registry"))
    monkeypatch.setattr(diagnostic, "producer_identity", lambda: {"linker_version": "9"})
    monkeypatch.setattr(write_deadline, "WRITE_SECONDS", budget)
    monkeypatch.setattr(inputs, "capture", lambda *args: pytest.fail("captured unknown policy"))
    with pytest.raises(ValueError, match="installed worker write budget"):
        diagnostic.run(
            tmp_path / "source",
            tmp_path / "capture",
            tmp_path / "receipt.json",
            Path("config"),
            Path("overrides"),
            diagnostic.BUSINESS_CLOCK,
            diagnostic.SOURCE_CHECKPOINT,
            diagnostic.ACCEPTED_CHECKPOINT,
        )
    assert write_deadline.WRITE_SECONDS is budget
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize("complete", [True, False])
def test_cli_distinguishes_complete_diagnosis_from_unit_success(monkeypatch, capsys, complete):
    order = []
    monkeypatch.setattr(
        diagnostic, "authorize_installed_verifier", lambda: order.append("authorize")
    )

    def run(*args):
        order.append("run")
        return {
            "diagnostic_complete": complete,
            "unit_passed": False,
            "trial": {"unit_result": {"state": "failed"}},
        }

    monkeypatch.setattr(diagnostic, "run", run)
    argv = sum(
        (
            ["--" + name, "value"]
            for name in (
                "checkpoint",
                "capture-state",
                "receipt",
                "config",
                "overrides",
                "at",
                "source-checkpoint",
                "expected-checkpoint",
            )
        ),
        [],
    )
    assert diagnostic.main(argv) == (0 if complete else 1)
    assert order == ["authorize", "run"]
    report = json.loads(capsys.readouterr().out)
    assert report["diagnostic_complete"] is complete and report["graph_proof"] is False
    assert report["unit_state"] == "failed" and report["unit_passed"] is False


@pytest.mark.parametrize(
    "installed_seconds,fault",
    [(60.0, None), (75.0, None)]
    + [
        (60.0, fault)
        for fault in (
            "source",
            "environment",
            "candidate",
            "selection",
            "second_copy",
            "timeout",
            "projection",
            "inputs",
            "fatal_observer_fault",
        )
    ],
)
def test_one_copy_flow_never_adopts_or_templates_and_keeps_failure_receipt(
    tmp_path, monkeypatch, installed_seconds, fault
):
    import contextlib

    from swingset.fetch.archive import canonical, digest
    from swingset.state import (
        checkpoint_binding,
        inputs,
        postgres_copy,
        verify_graph,
        write_deadline,
    )
    from swingset.state import db as db_module

    checkpoint, capture, receipt = (
        tmp_path / name for name in ("source", "capture", "result.json")
    )
    registry = tmp_path / "registry"
    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(registry))
    monkeypatch.setattr(diagnostic, "producer_identity", lambda: {"linker_version": "9"})
    monkeypatch.setattr(write_deadline, "WRITE_SECONDS", installed_seconds)
    calls = []
    bundle = object()

    class Connection:
        in_transaction = False

        def execute(self, *args, **kwargs):
            return "exact-result"

        @contextlib.contextmanager
        def read_snapshot(self):
            assert not self.in_transaction
            self.in_transaction = True
            try:
                yield self
            finally:
                self.in_transaction = False

    source = SimpleNamespace(
        connection=Connection(), environment="production" if fault == "environment" else "dev"
    )
    worker = SimpleNamespace(connection=Connection())

    @contextlib.contextmanager
    def database(path, **kwargs):
        calls.append(("open", path, kwargs))
        if path == checkpoint:
            assert kwargs == {"read_only": True, "lock": False, "allow_checkpoint_schema": True}
            yield source
        else:
            assert path == receipt.parent / "owned-copy" and kwargs == {"lock": False}
            yield worker

    class Copies:
        def __init__(self, path):
            assert path == checkpoint
            self.record = {"databases": []}
            self.identifier = "new-unique-owner"
            calls.append(("owner",))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert write_deadline.WRITE_SECONDS == installed_seconds
            calls.append(("cleanup", tuple(self.record["databases"])))
            self.record["databases"].clear()

        def freeze(self, conn, path):
            assert write_deadline.WRITE_SECONDS == installed_seconds
            assert conn is source.connection
            assert not path.exists()
            calls.append(("freeze",))
            self.record["databases"] = ["sst_copy_new_0"]
            if fault == "second_copy":
                self.record["databases"].append("sst_copy_new_1")

    def identity(conn):
        if conn is source.connection:
            return "wrong-source" if fault == "source" else diagnostic.SOURCE_CHECKPOINT
        assert conn is worker.connection
        return "wrong-candidate" if fault == "candidate" else diagnostic.ACCEPTED_CHECKPOINT

    def capture_inputs(*args):
        assert write_deadline.WRITE_SECONDS == installed_seconds
        assert args[:3] == (Path("config"), Path("overrides"), capture)
        assert "linker" in args[3]
        calls.append(("capture",))
        return bundle

    def accept(database, accepted, clock):
        assert write_deadline.WRITE_SECONDS == diagnostic.WRITE_SECONDS == 75.0
        assert database is worker and accepted is bundle
        assert not database.connection.in_transaction
        assert clock.now().isoformat() == diagnostic.BUSINESS_CLOCK
        calls.append(("accept",))
        if fault == "fatal_observer_fault":
            raise KeyboardInterrupt("original application interruption")

    def trial(database, accepted, clock, report):
        assert write_deadline.WRITE_SECONDS == diagnostic.WRITE_SECONDS == 75.0
        assert database is worker and accepted is bundle
        calls.append(("trial",))
        report.update(
            unit_result={"state": "failed", "error": "original transform error"},
            rollback_fingerprint_equal=True,
            observer_complete=True,
            phases=[],
        )
        if fault == "timeout":
            raise diagnostic._PhaseExpired("report ceiling")
        return report

    monkeypatch.setattr(db_module, "open_database", database)
    monkeypatch.setattr(postgres_copy, "Copies", Copies)
    monkeypatch.setattr(inputs, "capture", capture_inputs)
    monkeypatch.setattr(inputs, "accept", accept)
    monkeypatch.setattr(
        diagnostic.Observations, "acceptance", lambda self: contextlib.nullcontext()
    )
    monkeypatch.setattr(verify_graph, "checkpoint_identity", identity)
    full = [diagnostic.UNIT, "link/event/example"]
    binding = {
        "format": "swingset-input-checkpoint-binding-v1",
        "source_checkpoint": diagnostic.SOURCE_CHECKPOINT,
        "checkpoint": diagnostic.ACCEPTED_CHECKPOINT,
        "inputs": {"recipe/code/project/inventory": "original-captured-input"},
    }
    monkeypatch.setattr(diagnostic, "EXPECTED_UNITS", len(full))
    monkeypatch.setattr(diagnostic, "UNIT_SET_SHA256", digest(canonical(full)))
    monkeypatch.setattr(diagnostic, "INPUTS_SHA256", digest(canonical(binding["inputs"])))
    monkeypatch.setattr(diagnostic, "BINDING_SHA256", digest(canonical(binding)))

    def projection(conn, accepted, clock):
        assert conn is worker.connection and conn.in_transaction and accepted is bundle
        calls.append(("projection",))
        return {**binding, "checkpoint": "wrong"} if fault == "projection" else binding

    monkeypatch.setattr(checkpoint_binding, "input_acceptance_binding", projection)
    monkeypatch.setattr(
        verify_graph,
        "context",
        lambda conn: {"inputs": {} if fault == "inputs" else binding["inputs"]},
    )
    monkeypatch.setattr(
        verify_graph,
        "enumerate_units",
        lambda conn, scopes=None: (
            [] if fault == "selection" else full if scopes is None else [diagnostic.UNIT]
        ),
    )
    monkeypatch.setattr(diagnostic, "profile_derivation", trial)
    if fault == "fatal_observer_fault":
        from swingset.fetch import archive

        durable = archive.durable_write

        def failed_frames(self, error):
            raise RuntimeError("observer failure")

        def failed_receipt(path, body):
            if json.loads(body)["phase"] == "failed":
                raise OSError("receipt failure")
            return durable(path, body)

        monkeypatch.setattr(diagnostic.Observations, "failure", failed_frames)
        monkeypatch.setattr(archive, "durable_write", failed_receipt)
    args = (
        checkpoint,
        capture,
        receipt,
        Path("config"),
        Path("overrides"),
        diagnostic.BUSINESS_CLOCK,
        diagnostic.SOURCE_CHECKPOINT,
        diagnostic.ACCEPTED_CHECKPOINT,
    )
    if fault is None:
        result = diagnostic.run(*args)
        assert result["diagnostic_complete"] and result["owned_database_cleanup_complete"]
        assert result["trial"]["unit_result"]["state"] == "failed"
        assert result["graph_proof"] is False
        assert [row[0] for row in calls].count("freeze") == 1
        assert [row[0] for row in calls].count("trial") == 1
    else:
        kind = (
            diagnostic._PhaseExpired
            if fault == "timeout"
            else KeyboardInterrupt
            if fault == "fatal_observer_fault"
            else ValueError
        )
        with pytest.raises(kind):
            diagnostic.run(*args)
    assert write_deadline.WRITE_SECONDS == installed_seconds
    stored = json.loads(receipt.read_bytes())
    policy = stored["worker_write_policy"]
    assert policy["installed_seconds"] == installed_seconds
    assert policy["authorized_seconds"] == 75.0
    assert stored["diagnostic_complete"] == (fault is None)
    assert receipt.stat().st_mode & 0o777 == 0o600
    names = [row[0] for row in calls]
    assert names.count("freeze") <= 1 and names.count("owner") <= 1
    if fault == "environment":
        assert "owner" not in names
    else:
        assert names[-1] == "cleanup"
    if fault in {"source", "environment"}:
        assert "freeze" not in names and "accept" not in names
    if fault in {"candidate", "second_copy", "selection", "projection", "inputs"}:
        assert "trial" not in names
    if "accept" in names:
        assert names.index("projection") < names.index("accept")
        assert policy["applied_seconds"] == 75.0
    if "projection" in names and fault != "fatal_observer_fault":
        assert policy["restored"] is True
    if fault in {"source", "environment", "second_copy"}:
        assert policy["applied_seconds"] is None and policy["restored"] is None
    if fault == "timeout":
        assert stored["trial"]["unit_result"]["error"] == "original transform error"
        assert stored["error_type"] == "_PhaseExpired"


@pytest.mark.parametrize(
    "role,environment", [("operator", "dryrun"), ("verifier", "dev"), ("verifier", "dryrun")]
)
def test_standalone_authorizes_role_and_endpoint_before_loading_credentials(
    monkeypatch, role, environment
):
    from swingset.deploy import configuration, runtime, security, utility

    calls = []
    monkeypatch.setenv("SWINGSET_ENV", environment)
    monkeypatch.setenv("SWINGSET_RUNTIME_IMAGE", diagnostic.RUNTIME_ARTIFACT)

    def authorized(policy, directory, **kwargs):
        assert policy == Path("/run/swingset-config/network.json")
        assert directory == Path("/run/swingset-authorization") and kwargs == {"wait": True}
        calls.append("authorization")
        return {"role": role}

    def configure(actual_role, directory, member):
        assert actual_role == "verifier" and directory == Path("/run/secrets")
        assert member == {"database": "authorized-private-endpoint"}
        calls.append("configure")

    monkeypatch.setattr(runtime, "authorized", authorized)
    monkeypatch.setattr(configuration, "runtime_artifact", lambda image: calls.append("image"))
    monkeypatch.setattr(
        security,
        "load",
        lambda path: {"workers": {"verifier": {"database": "authorized-private-endpoint"}}},
    )
    monkeypatch.setattr(utility, "configure", configure)
    if (role, environment) == ("verifier", "dryrun"):
        diagnostic.authorize_installed_verifier()
        assert calls == ["authorization", "image", "configure"]
    else:
        with pytest.raises(RuntimeError, match="role or explicit"):
            diagnostic.authorize_installed_verifier()
        assert calls == ["authorization"]


def test_accept_observation_keeps_real_top_level_commit_and_includes_commit_time(
    tmp_path, monkeypatch
):
    from swingset.state.db import open_database

    observed = diagnostic.Observations()
    with open_database(tmp_path / "state", lock=False) as database:
        committed = []
        clock_observations = []
        commit, wall = database._commit, observed.wall

        def tracked_commit():
            commit()
            committed.append(True)

        def tracked_wall():
            clock_observations.append(bool(committed))
            return wall()

        monkeypatch.setattr(database, "_commit", tracked_commit)
        observed.wall = tracked_wall
        original = database.connection.execute
        assert not database.connection.in_transaction
        with observed.phase("accept"), observed.connection(database.connection):
            with database.transaction() as conn:
                conn.execute(
                    "INSERT INTO ops.meta(key,value) VALUES(%s,%s)", ("observer", "secret")
                )
        assert not database.connection.in_transaction
        assert database.connection.execute == original
        assert committed == [True] and clock_observations[-1] is True
        assert (
            database.connection.execute(
                "SELECT value FROM ops.meta WHERE key='observer'"
            ).fetchone()[0]
            == "secret"
        )
    assert observed.value["quality"]
    phase = observed.value["phases"][0]
    assert phase["name"] == "accept"
    assert 0 <= phase["owner_thread_cpu_seconds"] <= phase["wall_seconds"] + 0.001
    assert "secret" not in json.dumps(observed.value)


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, "deadline", "uncertain"])
def test_accept_observation_preserves_original_fatal_errors_and_restores_connection(failure):
    from swingset.state.engine import CommitUncertain
    from swingset.state.write_deadline import WriteDeadlineExceeded

    kind = {"deadline": WriteDeadlineExceeded, "uncertain": CommitUncertain}.get(failure, failure)
    original_error = kind("private exception message")

    class Connection:
        def execute(self, *args, **kwargs):
            raise original_error

    conn = Connection()
    original = conn.execute
    observed = diagnostic.Observations()

    def failed_clock():
        raise OSError("private clock diagnostic")

    observed.cpu = failed_clock
    with pytest.raises(kind) as caught:
        with observed.phase("accept"), observed.connection(conn):
            conn.execute("SELECT private_payload", ("secret DSN",))
    assert caught.value is original_error and conn.execute == original
    assert not observed.value["quality"] and observed.value["clock_errors"]
    observed.failure(original_error)
    encoded = json.dumps(observed.value)
    assert all(value not in encoded for value in ("private", "secret DSN", "private_payload"))
    assert observed.value["failure_frames"]
    assert all(set(row) == {"file", "function", "line"} for row in observed.value["failure_frames"])


def test_accept_observation_passes_exact_arguments_and_excludes_nested_and_foreign_clocks():
    owner = threading.get_ident()
    token = object()
    query = object()
    params = {"private": "DSN"}
    calls = []

    class Connection:
        def execute(self, *args, **kwargs):
            calls.append((args, kwargs))
            if args and args[0] == "outer":
                assert self.execute("nested", params) is token
            return token

    conn = Connection()
    original = conn.execute
    observed = diagnostic.Observations()
    wall, cpu = observed.wall, observed.cpu

    def guarded(clock):
        def read():
            assert threading.get_ident() == owner
            return clock()

        return read

    observed.wall, observed.cpu = guarded(wall), guarded(cpu)
    with observed.connection(conn):
        assert conn.execute(query, params, prepare=False) is token
        assert conn.execute("outer") is token
        worker = threading.Thread(target=conn.execute, args=("foreign private", params))
        worker.start()
        worker.join(timeout=1)
        assert not worker.is_alive()
    assert conn.execute == original
    assert calls[0] == ((query, params), {"prepare": False})
    assert observed.value["nested_execute_calls"] == 1
    assert observed.value["foreign_thread_calls"] == 1
    assert sum(row["calls"] for row in observed.value["families"].values()) == 2
    assert "private" not in json.dumps(observed.value)


def test_observation_bounds_do_not_replace_the_application_result():
    observed = diagnostic.Observations()
    called = []
    for _ in range(20):
        with observed.phase("accept"):
            called.append(True)
    assert len(called) == 20 and len(observed.value["phases"]) == 16
    assert observed.value["overflow"] and not observed.value["quality"]


def test_observer_finish_fault_never_replaces_application_success_or_fatal_error(monkeypatch):
    observed = diagnostic.Observations()

    def failed(*args):
        raise OSError("observation failure")

    monkeypatch.setattr(observed, "finish", failed)
    with observed.phase("accept"):
        result = "original success"
    assert result == "original success" and not observed.value["quality"]

    def fatal(*args):
        raise KeyboardInterrupt("secondary observer failure")

    monkeypatch.setattr(observed, "finish", fatal)
    error = KeyboardInterrupt("original fatal error")
    with pytest.raises(KeyboardInterrupt) as caught:
        with observed.phase("accept"):
            raise error
    assert caught.value is error


@pytest.mark.parametrize(
    "fault", ["count", "membership", "source", "candidate", "inputs", "component", "unsupported"]
)
def test_destination_prelude_refuses_semantic_drift_before_any_write(monkeypatch, fault):
    import contextlib

    from swingset.fetch.archive import canonical, digest
    from swingset.state import checkpoint_binding, verify_graph

    class Connection:
        readonly = False

        @contextlib.contextmanager
        def read_snapshot(self):
            self.readonly = True
            try:
                yield
            finally:
                self.readonly = False

    conn = Connection()
    expected = [diagnostic.UNIT, "link/event/example"]
    binding = {
        "format": "swingset-input-checkpoint-binding-v1",
        "source_checkpoint": diagnostic.SOURCE_CHECKPOINT,
        "checkpoint": diagnostic.ACCEPTED_CHECKPOINT,
        "inputs": {"accepted": "original"},
        "components": {"unchanged": 1},
    }
    monkeypatch.setattr(diagnostic, "EXPECTED_UNITS", 3 if fault == "count" else 2)
    monkeypatch.setattr(diagnostic, "UNIT_SET_SHA256", digest(canonical(expected)))
    monkeypatch.setattr(diagnostic, "INPUTS_SHA256", digest(canonical(binding["inputs"])))
    monkeypatch.setattr(diagnostic, "BINDING_SHA256", digest(canonical(binding)))
    monkeypatch.setattr(
        verify_graph,
        "enumerate_units",
        lambda connection: expected[:1] + ["changed"] if fault == "membership" else expected,
    )

    def projection(connection, bundle, clock):
        assert connection is conn and conn.readonly
        if fault == "unsupported":
            raise checkpoint_binding.UnsupportedCheckpointTransition("unsupported change")
        key = {
            "source": "source_checkpoint",
            "candidate": "checkpoint",
            "inputs": "inputs",
            "component": "components",
        }.get(fault)
        return binding if key is None else {**binding, key: "changed"}

    monkeypatch.setattr(checkpoint_binding, "input_acceptance_binding", projection)
    with pytest.raises(ValueError):
        diagnostic.destination_binding(SimpleNamespace(connection=conn), object(), object())
    assert not conn.readonly


@pytest.mark.parametrize("reference", ["artifact", "image_id"])
def test_installed_byte_proof_and_oci_reference_have_separate_identities(
    tmp_path, monkeypatch, reference
):
    import hashlib

    import swingset
    from swingset.deploy.configuration import runtime_artifact
    from swingset.link import service

    package = tmp_path / "swingset"
    package.mkdir()
    source = package / "__init__.py"
    body = b"# installed producer bytes\n"
    source.write_bytes(body)
    name = b"__init__.py"
    expected = (
        "source-sha256:"
        + hashlib.sha256(
            len(name).to_bytes(4, "big") + name + len(body).to_bytes(8, "big") + body
        ).hexdigest()
    )
    monkeypatch.setattr(swingset, "__file__", str(source))
    monkeypatch.setattr(diagnostic, "SOURCE", expected)
    monkeypatch.setattr(service, "LINKER_VERSION", "9")
    monkeypatch.setenv(
        "SWINGSET_REVISION", "a stale label cannot establish installed byte identity"
    )
    monkeypatch.setenv(
        "SWINGSET_RUNTIME_IMAGE",
        diagnostic.RUNTIME_ARTIFACT if reference == "artifact" else diagnostic.IMAGE,
    )
    if reference == "image_id":
        with pytest.raises(ValueError, match="producer differs"):
            diagnostic.producer_identity()
    else:
        result = diagnostic.producer_identity()
        assert result["source_revision"] == expected
        assert result["image"] == diagnostic.IMAGE
        assert runtime_artifact(result["runtime_artifact"]) == diagnostic.RUNTIME_ARTIFACT
        source.write_bytes(body + b"# changed after proof\n")
        with pytest.raises(ValueError, match="producer differs"):
            diagnostic.producer_identity()


@pytest.fixture
def acceptance_originals():
    """Use the real already-loaded producer aliases, including its prelude journal."""
    from swingset.state import identity_journal, inputs, recipes, work

    assert inputs.captured_recipe_inputs is recipes.captured_recipe_inputs
    assert identity_journal.enqueue is work.enqueue and inputs.accept_inputs is work.accept_inputs
    return diagnostic._acceptance_bindings()[0]


def test_acceptance_callees_use_real_aliases_and_keep_commit_rollback_and_noop(
    tmp_path, monkeypatch, acceptance_originals
):
    from swingset.clock import FakeClock
    from swingset.state import inputs, recipes, work
    from swingset.state.db import open_database

    package = tmp_path / "project/src/swingset"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n")
    artifact = recipes.capture_runtime(package)
    monkeypatch.setattr(inputs, "capture_runtime", lambda: artifact)
    hooks = acceptance_originals
    clock = FakeClock()
    with open_database(tmp_path / "state") as database:
        bundle = inputs.capture(Path("config"), Path("overrides"), database.state_dir, {})

        def state():
            return {
                table: [tuple(row) for row in database.connection.execute(query)]
                for table, query in {
                    "accepted": "SELECT consumer,input_name,digest FROM accepted_inputs ORDER BY 1,2",
                    "pending": "SELECT stage,unit_kind,unit_id,enqueued_at FROM pending_work ORDER BY 1,2,3",
                    "generations": "SELECT * FROM work_generations ORDER BY stage,unit_kind,unit_id",
                    "bundle": "SELECT value FROM meta WHERE key='input_bundle_hash'",
                }.items()
            }

        class Rollback(Exception):
            pass

        before = state()
        with pytest.raises(Rollback), database.transaction():
            expected_changed = inputs.accept(database, bundle, clock)
            expected = state()
            raise Rollback
        assert state() == before
        observed = diagnostic.Observations()
        with (
            observed.acceptance(),
            observed.phase("accept"),
            observed.connection(database.connection),
        ):
            actual_changed = inputs.accept(database, bundle, clock)
        assert actual_changed == expected_changed and actual_changed
        assert state() == expected
        value = observed.value["acceptance_callees"]
        assert observed.value["quality"] and value["quality"] and value["closed"]
        assert value["hooks_restored"] and value["other_caller_calls"] == 0
        rows = value["rows"]
        assert rows["recipe_pipeline"]["calls"] == rows["recipe_audit"]["calls"] == 1
        assert rows["affected_work"]["calls"] == len(actual_changed)
        assert rows["enqueue"]["calls"] == len(set(expected["accepted"]) - set(before["accepted"]))
        for row in rows.values():
            assert row["calls"] == row["normal_returns"] and row["errors"] == 0
            assert 0 <= row["owner_thread_cpu_seconds"] <= row["wall_seconds"] + 0.001
        assert all(getattr(module, name) is original for module, name, original in hooks)
        repeated = diagnostic.Observations()
        with repeated.acceptance():
            assert not inputs.accept(database, bundle, clock)
        assert state() == expected
        assert repeated.value["quality"]
        rows = repeated.value["acceptance_callees"]["rows"]
        assert rows["recipe_pipeline"]["calls"] == rows["recipe_audit"]["calls"] == 1
        assert rows["affected_work"]["calls"] == rows["enqueue"]["calls"] == 0
        assert work.enqueue is next(
            original for module, name, original in hooks if name == "enqueue"
        )


@pytest.mark.parametrize("primary_type", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("observer_type", [OSError, KeyboardInterrupt, SystemExit])
def test_acceptance_callee_primary_identity_wins_final_clock_fault(
    monkeypatch, primary_type, observer_type, acceptance_originals
):
    from swingset.state import recipes

    hooks = acceptance_originals
    primary, secondary = primary_type("private original"), observer_type("private clock")
    observed, armed = diagnostic.Observations(), False
    original_cpu = observed.cpu

    def digest(body):
        nonlocal armed
        armed = True
        raise primary

    def cpu():
        if armed:
            raise secondary
        return original_cpu()

    monkeypatch.setattr(recipes, "digest", digest)
    observed.cpu = cpu
    with pytest.raises(primary_type) as caught:
        with observed.acceptance():
            recipes.captured_audit_inputs({})
    assert caught.value is primary
    value = observed.value["acceptance_callees"]
    assert not value["quality"] and not observed.value["quality"] and value["hooks_restored"]
    assert value["rows"]["recipe_audit"]["errors"] == 1
    assert all(getattr(module, name) is original for module, name, original in hooks)
    assert "private" not in json.dumps(observed.value)


@pytest.mark.parametrize("observer_type", [KeyboardInterrupt, SystemExit])
def test_acceptance_callee_observer_fatal_propagates_after_alias_restoration(
    monkeypatch, observer_type, acceptance_originals
):
    from swingset.state import recipes

    hooks = acceptance_originals
    observed, armed = diagnostic.Observations(), False
    original_digest, original_cpu = recipes.digest, observed.cpu
    fatal = observer_type("private observer")

    def digest(body):
        nonlocal armed
        result = original_digest(body)
        armed = True
        return result

    def cpu():
        if armed:
            raise fatal
        return original_cpu()

    monkeypatch.setattr(recipes, "digest", digest)
    observed.cpu = cpu
    with pytest.raises(observer_type) as caught:
        with observed.acceptance():
            recipes.captured_audit_inputs({})
    assert caught.value is fatal
    assert not observed.value["quality"]
    assert observed.value["acceptance_callees"]["rows"]["recipe_audit"]["normal_returns"] == 1
    assert all(getattr(module, name) is original for module, name, original in hooks)
    assert "private" not in json.dumps(observed.value)


@pytest.mark.parametrize("primary_type", [None, KeyboardInterrupt])
@pytest.mark.parametrize("observer_type", [KeyboardInterrupt, SystemExit])
def test_acceptance_body_primary_wins_final_owner_fault(
    primary_type, observer_type, acceptance_originals
):
    hooks = acceptance_originals
    observed = diagnostic.Observations()
    primary = None if primary_type is None else primary_type("private body")
    fatal = observer_type("private owner")

    def identity():
        raise fatal

    with pytest.raises(observer_type if primary is None else primary_type) as caught:
        with observed.acceptance():
            observed.identity = identity
            if primary is not None:
                raise primary
    assert caught.value is (fatal if primary is None else primary)
    assert not observed.value["quality"] and not observed._acceptance_active
    assert all(getattr(module, name) is original for module, name, original in hooks)
    assert "private" not in json.dumps(observed.value)


@pytest.mark.parametrize("wall_times", [(float("nan"), float("nan")), (2.0, 1.0)])
def test_acceptance_bad_clock_keeps_real_result_but_refuses_quality(
    wall_times, acceptance_originals
):
    from swingset.state import recipes

    observed, times = diagnostic.Observations(), iter(wall_times)
    observed.wall, observed.cpu = lambda: next(times), lambda: 0.0
    with observed.acceptance():
        assert recipes.captured_audit_inputs({}) == {}
    value = observed.value["acceptance_callees"]
    assert not value["quality"] and value["hooks_restored"]
    row = value["rows"]["recipe_audit"]
    assert row["calls"] == row["normal_returns"] == 1
    assert row["wall_seconds"] == row["owner_thread_cpu_seconds"] == 0.0


def test_acceptance_foreign_and_unknown_callers_keep_original_results_without_foreign_clocks(
    acceptance_originals,
):
    from swingset.state import recipes

    observed, results, clock_threads = diagnostic.Observations(), [], []
    original_cpu = observed.cpu

    def cpu():
        clock_threads.append(threading.get_ident())
        return original_cpu()

    observed.cpu = cpu
    with observed.acceptance():
        thread = threading.Thread(target=lambda: results.append(recipes.captured_audit_inputs({})))
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        expected = recipes.captured_recipe_inputs({}, history_start="private name")
        assert set(expected) == {"policy/history_start"}
    value = observed.value["acceptance_callees"]
    assert results == [{}] and not clock_threads
    assert observed.value["foreign_thread_calls"] == 1
    assert value["other_caller_calls"] == 1 and not value["quality"]
    assert all(row["calls"] == 0 for row in value["rows"].values())
    assert "private name" not in json.dumps(observed.value)


def test_acceptance_peer_hook_survives_and_nested_observer_cannot_replace_it(
    monkeypatch, acceptance_originals
):
    from swingset.state import inputs, recipes

    original = recipes.captured_recipe_inputs

    def peer(*args, **kwargs):
        return "peer"

    observed = diagnostic.Observations()
    monkeypatch.setattr(recipes, "captured_recipe_inputs", original)
    with observed.acceptance():
        with pytest.raises(ValueError):
            with diagnostic.Observations().acceptance():
                pytest.fail("a nested observer must not replace existing hooks")
        recipes.captured_recipe_inputs = peer
    assert recipes.captured_recipe_inputs is peer
    assert inputs.captured_recipe_inputs is original
    value = observed.value["acceptance_callees"]
    assert (
        not value["quality"] and not value["hooks_restored"] and "hook_changed" in value["errors"]
    )


def test_acceptance_source_drift_refuses_before_hook_installation(
    monkeypatch, acceptance_originals
):
    hooks = acceptance_originals
    monkeypatch.setitem(diagnostic._ACCEPT_SOURCE, "inputs", "0" * 64)
    with pytest.raises(ValueError, match="source differs"):
        with diagnostic.Observations().acceptance():
            pytest.fail("changed producer source must not be observed")
    assert all(getattr(module, name) is original for module, name, original in hooks)


@pytest.mark.parametrize("prelude", ["missing", "changed"])
def test_acceptance_missing_or_changed_journal_alias_refuses_before_hooks(
    monkeypatch, acceptance_originals, prelude
):
    import sys

    from swingset.state import identity_journal

    if prelude == "missing":
        monkeypatch.delitem(sys.modules, "swingset.state.identity_journal")
    else:
        monkeypatch.setattr(identity_journal, "enqueue", object())
    with pytest.raises(ValueError, match="prelude journal alias"):
        with diagnostic.Observations().acceptance():
            pytest.fail("the ordinary prelude must leave the original journal alias loaded")
    assert all(getattr(module, name) is original for module, name, original in acceptance_originals)


def test_acceptance_callee_bound_marks_overflow_and_still_calls_original(
    monkeypatch, acceptance_originals
):
    from swingset.state import recipes

    monkeypatch.setattr(diagnostic, "_ACCEPT_CALL_LIMIT", 1)
    observed = diagnostic.Observations()
    with observed.acceptance():
        assert recipes.captured_audit_inputs({}) == {}
        assert recipes.captured_audit_inputs({}) == {}
    value = observed.value["acceptance_callees"]
    assert observed.value["overflow"] and not value["quality"] and value["hooks_restored"]
    assert value["rows"]["recipe_audit"]["calls"] == 1
    assert value["rows"]["recipe_audit"]["normal_returns"] == 1
