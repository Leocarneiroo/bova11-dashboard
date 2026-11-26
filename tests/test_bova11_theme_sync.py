import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import bova11_trade_score_james as trade_score


class ThemeSyncHtmlTests(unittest.TestCase):
    def test_trade_score_uses_shared_theme_sync_and_theme_tokens(self):
        row = {
            "k": 184.0,
            "score": 73.0,
            "d_iv": None,
            "d_oi": 8400.0,
            "vol_d": 1200.0,
            "bias": "NEUTRAL",
            "conviction": "STRONG",
            "side": "PUT",
            "venc": "29 mai W5",
            "driver": "MIXED/MIX",
            "inst_sc": 36.0,
            "flow_sc": 27.0,
            "conv_sc": 10.0,
            "spread_pct": 12.0,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            old_output_dir = trade_score.OUTPUT_DIR
            trade_score.OUTPUT_DIR = tmpdir
            try:
                trade_score.gen_html({"29 mai W5": [row]}, "trade.html", "27/Mai", "28/Mai", 172.54, 171.70)
                with open(os.path.join(tmpdir, "trade.html"), encoding="utf-8") as fh:
                    html = fh.read()
            finally:
                trade_score.OUTPUT_DIR = old_output_dir

        self.assertIn('<script src="shared.js"></script>', html)
        self.assertNotIn('background:#21262d;border:1px solid #30363d', html)
        self.assertIn('background:var(--bg2);border:1px solid var(--brd)', html)


if __name__ == "__main__":
    unittest.main()
