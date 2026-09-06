#!/usr/bin/env python3
"""Exercise preflight fallback without network, real waits, or publication."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("recovery_resolver", SCRIPTS / "resolve-runtime-context.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


class ContextRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.error = OSError(R.errno.EDEADLK, "Resource deadlock avoided")
        self.config = Path("/fixture/automation.local.env")
        self.workdir = Path("/fixture")
        self.stderr = io.StringIO()
        self.enterContext(contextlib.redirect_stderr(self.stderr))
        self.sleep = self.enterContext(mock.patch.object(R.time, "sleep"))

    def recover(self, outcomes):
        with mock.patch.object(R, "resolve_context_once", side_effect=self.error) as initial:
            with mock.patch.object(R, "run_local_command", side_effect=outcomes) as workers:
                result = R.resolve_context(self.config, self.workdir)
        return result, initial, workers

    def test_delayed_worker_recovers_after_short_window(self):
        result, initial, workers = self.recover([
            subprocess.CompletedProcess([], 75, "", "transient"),
            subprocess.CompletedProcess([], 0, json.dumps({"current": "verified"}), ""),
        ])
        self.assertEqual(result, {"current": "verified"})
        self.assertEqual(initial.call_count, 30)
        self.assertEqual(workers.call_count, 2)
        self.assertEqual(self.sleep.call_args_list[-2:], [mock.call(30), mock.call(60)])
        args, kwargs = workers.call_args
        self.assertEqual(args[0], [sys.executable, str(SCRIPTS / "resolve-runtime-context.py"),
                                  "--recovery-once", str(self.config), str(self.workdir)])
        self.assertEqual(kwargs["timeout"], 120)

    def test_persistent_failure_has_four_worker_limit(self):
        with mock.patch.object(R, "resolve_context_once", side_effect=self.error) as initial:
            with mock.patch.object(R, "run_local_command", return_value=
                                   subprocess.CompletedProcess([], 75, "", "")) as workers:
                with self.assertRaisesRegex(R.ContextError, "exhausted"):
                    R.resolve_context(self.config, self.workdir)
        self.assertEqual(initial.call_count, 30)
        self.assertEqual(workers.call_count, 4)
        self.assertEqual(sum(c.args[0] for c in self.sleep.call_args_list), 539)

    def test_worker_timeout_uses_next_bounded_attempt(self):
        result, _, workers = self.recover([
            subprocess.TimeoutExpired(["python"], 120),
            subprocess.CompletedProcess([], 0, '{"fresh":"yes"}', ""),
        ])
        self.assertEqual(result, {"fresh": "yes"})
        self.assertEqual(workers.call_count, 2)

    def test_permanent_error_does_not_enter_fallback(self):
        for error in [R.ContextError("digest mismatch"), PermissionError(13, "denied"),
                      FileNotFoundError(2, "missing"),
                      subprocess.CalledProcessError(128, ["git"], stderr="not a git repository")]:
            with self.subTest(error=error), mock.patch.object(R, "resolve_context_once", side_effect=error):
                with mock.patch.object(R, "run_local_command") as worker:
                    with self.assertRaises(type(error)):
                        R.resolve_context(self.config, self.workdir)
                    worker.assert_not_called()
        self.sleep.assert_not_called()

    def test_new_permanent_worker_error_stops_recovery(self):
        with mock.patch.object(R, "resolve_context_once", side_effect=self.error):
            with mock.patch.object(R, "run_local_command", return_value=
                                   subprocess.CompletedProcess([], 78, "", "private-value")) as worker:
                with self.assertRaisesRegex(R.ContextError, "rejected current context"):
                    R.resolve_context(self.config, self.workdir)
        self.assertEqual(worker.call_count, 1)
        self.assertNotIn("private-value", self.stderr.getvalue())

    def test_malformed_success_is_not_accepted_or_retried(self):
        for body in ["not json", "[]", "{}", '{"field":null}']:
            with self.subTest(body=body), mock.patch.object(R, "resolve_context_once", side_effect=self.error):
                with mock.patch.object(R, "run_local_command", return_value=
                                       subprocess.CompletedProcess([], 0, body, "")) as worker:
                    with self.assertRaises((R.ContextError, ValueError)):
                        R.resolve_context(self.config, self.workdir)
                self.assertEqual(worker.call_count, 1)

    def test_worker_mode_classifies_transient_and_never_recurses(self):
        with mock.patch.object(R, "resolve_context_once", side_effect=self.error) as once:
            with mock.patch.object(R, "resolve_context") as normal:
                self.assertEqual(R.main(["resolver", "--recovery-once", str(self.config), str(self.workdir)]), 75)
        once.assert_called_once()
        normal.assert_not_called()
        self.sleep.assert_not_called()

    def test_worker_mode_preserves_permanent_rejection(self):
        with mock.patch.object(R, "resolve_context_once", side_effect=R.ContextError("digest mismatch")):
            self.assertEqual(R.main(["resolver", "--recovery-once", str(self.config), str(self.workdir)]), 78)
        self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
