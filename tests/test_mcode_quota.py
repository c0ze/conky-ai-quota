"""Tests for mcode-quota. Run: python3 -m unittest discover -s tests"""

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

BIN = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))


def load_script(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(BIN / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


mcode = load_script("mcode-quota")


def remains(**window):
    entry = {"model_name": "general", "end_time": 1_791_122_893_889, "weekly_end_time": 1_791_709_693_889,
             "current_interval_status": 1, "current_weekly_status": 1, **window}
    return {"base_resp": {"status_code": 0}, "model_remains": [entry]}


class ParseTest(unittest.TestCase):
    def test_remaining_percent_becomes_used_percent(self):
        u = mcode.parse_usage(remains(current_interval_remaining_percent=70, current_weekly_remaining_percent=40))
        self.assertEqual((u["short_percent"], u["percent"]), (30.0, 60.0))
        self.assertEqual(u["reset_at"], 1_791_709_693.889)
        self.assertEqual(u["short_reset_at"], 1_791_122_893.889)

    def test_counts_used_when_percent_missing(self):
        u = mcode.parse_usage(remains(current_weekly_total_count=200, current_weekly_usage_count=50,
                                      current_interval_remaining_percent=100))
        self.assertEqual(u["percent"], 25.0)

    def test_unlimited_window_reads_as_unused(self):
        u = mcode.parse_usage(remains(current_weekly_status=3, current_interval_status=3))
        self.assertEqual((u["percent"], u["short_percent"]), (0.0, 0.0))

    def test_api_error_is_rejected(self):
        with self.assertRaises(ValueError):
            mcode.parse_usage({"base_resp": {"status_code": 1004, "status_msg": "bad"}})

    def test_missing_weekly_window_is_rejected(self):
        with self.assertRaises(ValueError):
            mcode.parse_usage({"base_resp": {"status_code": 0}, "model_remains": []})


class TokenTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name)
        (self.home / "preferences").mkdir()
        (self.home / "preferences/mcode-region.json").write_text(json.dumps({"regions": {"prod": "en"}}))
        self.auth = self.home / "auth/prod/en/mcode-public"
        self.auth.mkdir(parents=True)

    def tearDown(self):
        self.dir.cleanup()

    def token(self, expires_in):
        record = {"accessToken": "tok", "expiresAtMs": (time.time() + expires_in) * 1000}
        (self.auth / "auth.json").write_text(json.dumps({"records": {"k": record}}))

    def test_live_token_and_region_host(self):
        self.token(600)
        self.assertEqual(mcode.credentials(self.home), ("tok", "https://platform.minimax.io"))

    def test_expired_token_is_not_used(self):
        self.token(-60)
        with self.assertRaises(mcode.TokenExpired):
            mcode.credentials(self.home)


if __name__ == "__main__":
    unittest.main()
