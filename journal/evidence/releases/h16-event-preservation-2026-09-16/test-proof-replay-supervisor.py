"""Offline mocks of the prepared supervisor. Never invokes systemd or replay."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).with_name('supervise-proof-replay.py')
spec = importlib.util.spec_from_file_location('proof_supervisor', SCRIPT)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class MonitorTests(unittest.TestCase):
    def test_anonymous_memory_limit_fails(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(supervisor, 'held'):
            root = Path(directory)
            (root / 'group').mkdir()
            (root / 'group/memory.stat').write_text(f'anon {6 * 1024**3}\n')
            state = {'SubState': 'running', 'ActiveState': 'active', 'MainPID': '42', 'ControlGroup': '/group'}
            with (root / 'samples').open('w') as samples, patch.object(supervisor, 'unit_status', return_value=state), patch.object(supervisor, 'Path', side_effect=lambda value: root if value == '/sys/fs/cgroup' else Path(value)), self.assertRaisesRegex(ValueError, '6 GiB'):
                supervisor.monitor('scratch.service', None, None, {}, samples)

    def test_monitor_deadline_fails(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(supervisor, 'held'), patch.object(supervisor.time, 'monotonic', side_effect=[0, 961]), patch.object(supervisor, 'unit_status', return_value={'SubState': 'running', 'ActiveState': 'active', 'MainPID': '42'}):
            with Path(directory, 'samples').open('w') as samples, self.assertRaisesRegex(ValueError, 'deadline'):
                supervisor.monitor('scratch.service', None, None, {}, samples)

    def test_prepare_exit_needs_no_replay_receipt(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(supervisor, 'held'):
            with Path(directory, 'samples').open('w') as samples, patch.object(supervisor, 'unit_status', return_value={'SubState': 'exited', 'ActiveState': 'active', 'MainPID': '0', 'Result': 'success', 'ExecMainStatus': '0'}):
                self.assertIsNone(supervisor.monitor('scratch.service', None, None, {}, samples))

    def receipt(self, **updates):
        value = {'source_receipt_sha256': supervisor.SOURCE_SHA, 'scratch': str(supervisor.SCRATCH), 'marker_sha256': 'marker', 'network_requests': 0, 'parse_executed': False, 'published': False, 'input_bundle_hash': 'a' * 64, 'status': 'current'}
        value.update(updates)
        return value

    def run_monitor(self, receipt, outcomes=None, state=None, binding=None):
        with tempfile.TemporaryDirectory() as directory, patch.object(supervisor, 'held'), patch.object(supervisor, 'attempts', return_value=outcomes or {}), patch.object(supervisor, 'EVIDENCE', Path(directory)), patch.object(supervisor, 'unit_status', return_value=state or {'SubState': 'exited', 'ActiveState': 'active', 'MainPID': '0', 'Result': 'success', 'ExecMainStatus': '0'}):
            remote = Path(directory, 'receipt.json')
            remote.write_text(json.dumps(receipt))
            with Path(directory, 'samples').open('w') as samples:
                return supervisor.monitor('scratch.service', remote, 0, binding or {'marker_sha256': 'marker'}, samples)

    def test_current_binds_actual_bundle(self):
        binding = {'marker_sha256': 'marker'}
        value = self.run_monitor(self.receipt(), binding=binding)
        self.assertEqual(value['status'], 'current')
        self.assertEqual(binding['input_bundle_hash'], 'a' * 64)

    def test_unsafe_statuses_fail(self):
        for status in ('interrupted', 'no_runnable_progress', 'running', 'starting'):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.run_monitor(self.receipt(status=status))

    def test_changed_bundle_fails(self):
        with self.assertRaisesRegex(ValueError, 'bundle changed'):
            self.run_monitor(self.receipt(), binding={'marker_sha256': 'marker', 'input_bundle_hash': 'b' * 64})

    def test_running_or_failed_attempts_fail_after_exit(self):
        for outcome in ('running', 'blocked', 'transient', 'unavailable', 'interrupted', 'superseded'):
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                self.run_monitor(self.receipt(), outcomes={outcome: 1})

    def test_failed_service_fails(self):
        with self.assertRaises(ValueError):
            self.run_monitor(self.receipt(), state={'SubState': 'exited', 'ActiveState': 'active', 'MainPID': '0', 'Result': 'exit-code', 'ExecMainStatus': '1'})

    def test_marker_and_scope_changes_fail(self):
        for updates in ({'marker_sha256': 'other'}, {'network_requests': 1}, {'parse_executed': True}, {'published': True}, {'source_receipt_sha256': 'other'}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.run_monitor(self.receipt(**updates))


class FiniteSupervisorTests(unittest.TestCase):
    def run_main(self, results, expected_error=None, running_checkpoint=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, scratch = root / 'evidence', root / 'scratch'
            evidence.mkdir()
            gate = root / 'gate.json'
            gate.write_text(json.dumps({'passed': True, 'source': str(supervisor.SOURCE), 'source_receipt_sha256': supervisor.SOURCE_SHA}))
            calls = []
            def fake_sha(path):
                if path == gate:
                    return 'gatehash'
                if path == supervisor.SOURCE / 'h16-source.json':
                    return supervisor.SOURCE_SHA
                if path == supervisor.CHECKPOINT / 'checkpoint.json':
                    return supervisor.CHECKPOINT_SHA
                if path == supervisor.CHECKPOINT / 'state.sqlite':
                    return supervisor.DATABASE_SHA
                return 'artifacthash'
            def launch(unit, tail):
                calls.append((unit, tail))
                if '--prepare-from' in tail:
                    scratch.mkdir()
                    (scratch / 'offline-derivation-scratch.json').write_text(json.dumps({'checkpoint_manifest_sha256': supervisor.CHECKPOINT_SHA, 'origin_database_sha256': supervisor.DATABASE_SHA, 'source_receipt_sha256': supervisor.SOURCE_SHA}))
                    with sqlite3.connect(scratch / 'state.sqlite') as conn:
                        conn.execute('CREATE TABLE work_attempts(attempt_id INTEGER, outcome TEXT)')
                        conn.execute('CREATE TABLE derivation_generations(id TEXT)')
                        if running_checkpoint:
                            conn.execute("INSERT INTO work_attempts VALUES(1,'running')")
            remaining = iter(results)
            def monitor(unit, remote, after_id, binding, samples):
                if remote is None:
                    return None
                binding['input_bundle_hash'] = 'a' * 64
                value = next(remaining)
                if isinstance(value, Exception):
                    raise value
                return value
            with patch.object(supervisor, 'EVIDENCE', evidence), patch.object(supervisor, 'SCRATCH', scratch), patch.object(supervisor.os, 'geteuid', return_value=0), patch.object(supervisor, 'held'), patch.object(supervisor, 'sha', side_effect=fake_sha), patch.object(supervisor, 'launch', side_effect=launch), patch.object(supervisor, 'monitor', side_effect=monitor), patch.object(supervisor, 'retain'), patch.object(supervisor.subprocess, 'run') as stopped, patch('sys.argv', ['script', '--frozen-tests-receipt', str(gate), '--frozen-tests-sha256', 'gatehash']):
                supervisor.OWNED_UNITS.clear()
                if expected_error:
                    with self.assertRaises(expected_error):
                        supervisor.main()
                else:
                    supervisor.main()
                self.assertFalse(stopped.called, 'Mocks must not stop unowned units')
            return calls, json.loads((evidence / 'replay-supervisor.json').read_bytes())

    def current(self, **updates):
        result = {'status': 'current', 'completed': 1, 'generation_count': 1, 'unfinished_by_scope': {}}
        result.update(updates)
        return result

    def test_current_stops_after_one_worker(self):
        calls, report = self.run_main([self.current()])
        self.assertEqual(len(calls), 2)
        self.assertTrue(report['passed'])

    def test_no_progress_launches_no_next_worker(self):
        calls, report = self.run_main([self.current(status='bounded_stop', completed=0, generation_count=0)], ValueError)
        self.assertEqual(len(calls), 2)
        self.assertFalse(report['passed'])

    def test_current_with_unfinished_scopes_rejected(self):
        calls, report = self.run_main([self.current(unfinished_by_scope={'project/event': 1})], ValueError)
        self.assertEqual(len(calls), 2)
        self.assertFalse(report['passed'])

    def test_finite_twenty_worker_limit(self):
        calls, report = self.run_main([self.current(status='bounded_stop', generation_count=index) for index in range(1, 21)], RuntimeError)
        self.assertEqual(len(calls), 21)
        self.assertEqual(len(report['invocations']), 20)

    def test_running_checkpoint_attempts_rejected_before_replay(self):
        calls, report = self.run_main([], ValueError, running_checkpoint=True)
        self.assertEqual(len(calls), 1)
        self.assertFalse(report['passed'])

    def test_monitor_failure_launches_no_next_worker(self):
        calls, report = self.run_main([ValueError('blocked')], ValueError)
        self.assertEqual(len(calls), 2)
        self.assertFalse(report['passed'])

    def test_launch_properties_and_existing_unit_refusal(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(supervisor, 'EVIDENCE', Path(directory)), patch.object(supervisor, 'held'), patch.object(supervisor, 'unit_status', return_value={'LoadState': 'not-found'}), patch.object(supervisor, 'command') as run:
            supervisor.launch('unit.service', ['--max-seconds', '600'])
            argv = run.call_args.args[0]
            for arg in ('--property=Type=exec', '--property=RuntimeMaxSec=900', '--property=RemainAfterExit=yes', '--property=User=swingset', '--property=MemoryAccounting=yes'):
                self.assertIn(arg, argv)
        with patch.object(supervisor, 'held'), patch.object(supervisor, 'unit_status', return_value={'LoadState': 'loaded'}), patch.object(supervisor, 'command') as run, self.assertRaises(ValueError):
            supervisor.launch('existing.service', [])
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
