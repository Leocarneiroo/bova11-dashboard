import builtins
import contextlib
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bova11_runner as runner


class RunnerPublishFlowTests(unittest.TestCase):
    def test_finalize_run_prompts_even_when_partial(self):
        prompts = []
        output = io.StringIO()
        old_input = builtins.input
        old_publish = runner.publish_output_dashboard

        def fake_input(prompt):
            prompts.append(prompt)
            return "n"

        try:
            builtins.input = fake_input
            runner.publish_output_dashboard = lambda: self.fail("publish should not run")
            with contextlib.redirect_stdout(output):
                runner.finalize_dashboard_run(["bova11_demand_flow.py"])
        finally:
            builtins.input = old_input
            runner.publish_output_dashboard = old_publish

        self.assertEqual(len(prompts), 1)
        self.assertIn("atualização parcial", prompts[0].lower())
        self.assertIn("bova11_demand_flow.py", output.getvalue())

    def test_publish_output_dashboard_runs_git_sequence(self):
        calls = []
        old_check_call = runner.subprocess.check_call

        def fake_check_call(cmd, cwd=None, stdout=None, stderr=None):
            calls.append((cmd, cwd, stdout, stderr))

        try:
            runner.subprocess.check_call = fake_check_call
            runner.publish_output_dashboard(now_str="2026-07-28 20:00:00")
        finally:
            runner.subprocess.check_call = old_check_call

        self.assertEqual(
            [cmd for cmd, _, _, _ in calls],
            [
                ["git", "add", "."],
                ["git", "commit", "-m", "Auto-update: 2026-07-28 20:00:00"],
                ["git", "push", "origin", "main"],
            ],
        )
        self.assertTrue(all(cwd == runner.OUTPUT_DIR for _, cwd, _, _ in calls))


if __name__ == "__main__":
    unittest.main()
