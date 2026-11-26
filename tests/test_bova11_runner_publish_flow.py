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
        old_run = runner.subprocess.run
        old_prune = runner.prune_site

        class StagedChanges:
            returncode = 1

        def fake_check_call(cmd, cwd=None, stdout=None, stderr=None):
            calls.append((cmd, cwd, stdout, stderr))

        def fake_run(cmd, cwd=None, **kwargs):
            calls.append((cmd, cwd, None, None))
            return StagedChanges()

        try:
            runner.subprocess.check_call = fake_check_call
            runner.subprocess.run = fake_run
            runner.prune_site = lambda output_dir: ["bova11_gex_pro_1mar_vs_2mar.html"]
            runner.publish_output_dashboard(now_str="2026-07-28 20:00:00")
        finally:
            runner.subprocess.check_call = old_check_call
            runner.subprocess.run = old_run
            runner.prune_site = old_prune

        self.assertEqual(
            [cmd for cmd, _, _, _ in calls],
            [
                ["git", "add", "-A"],
                ["git", "diff", "--cached", "--quiet"],
                ["git", "commit", "-m", "Auto-update: 2026-07-28 20:00:00"],
                ["git", "push", "origin", "gh-pages"],
            ],
        )
        self.assertTrue(all(cwd == runner.OUTPUT_DIR for _, cwd, _, _ in calls))

    def test_publish_output_dashboard_skips_push_when_nothing_changed(self):
        calls = []
        old_check_call = runner.subprocess.check_call
        old_run = runner.subprocess.run
        old_prune = runner.prune_site

        class NothingStaged:
            returncode = 0

        def fake_check_call(cmd, cwd=None, stdout=None, stderr=None):
            calls.append((cmd, cwd, stdout, stderr))

        def fake_run(cmd, cwd=None, **kwargs):
            calls.append((cmd, cwd, None, None))
            return NothingStaged()

        try:
            runner.subprocess.check_call = fake_check_call
            runner.subprocess.run = fake_run
            runner.prune_site = lambda output_dir: []
            with contextlib.redirect_stdout(io.StringIO()) as output:
                runner.publish_output_dashboard(now_str="2026-07-28 20:00:00")
        finally:
            runner.subprocess.check_call = old_check_call
            runner.subprocess.run = old_run
            runner.prune_site = old_prune

        self.assertEqual([cmd for cmd, _, _, _ in calls], [["git", "add", "-A"], ["git", "diff", "--cached", "--quiet"]])
        self.assertIn("Nada novo", output.getvalue())


if __name__ == "__main__":
    unittest.main()
