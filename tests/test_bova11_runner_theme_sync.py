import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bova11_runner as runner


class RunnerThemeSyncTests(unittest.TestCase):
    def test_index_syncs_theme_when_iframe_loads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_output_dir = runner.OUTPUT_DIR
            runner.OUTPUT_DIR = tmpdir
            try:
                index_path = runner.gerar_index_html("27mai", "28mai", "27/Mai", "28/Mai", "teste")
                with open(index_path, encoding="utf-8") as fh:
                    html = fh.read()
            finally:
                runner.OUTPUT_DIR = old_output_dir

        self.assertIn("function checkIframe()", html)
        self.assertIn("function syncThemeToIframe()", html)
        self.assertIn("syncThemeToIframe();", html.split("function checkIframe()", 1)[1].split("function refreshCurrentFrame()", 1)[0])


if __name__ == "__main__":
    unittest.main()
