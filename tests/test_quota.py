"""Tests for the quota pollers. Run: python3 -m unittest discover -s tests"""

from contextlib import redirect_stdout
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

BIN = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import ai_quota_common as common  # noqa: E402


def load_script(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(BIN / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


claude = load_script("claude-quota")
codex = load_script("codex-quota")
grok = load_script("grok-quota")
mimo = load_script("mimo-quota")
muse = load_script("muse-quota")


def snapshot(percent=40, reset_in=3 * 86400, age=0, **extra):
    now = time.time()
    return {"percent": percent, "reset_at": now + reset_in, "fetched_at": now - age,
            "attempted_at": now - age, "schema": 2, **extra}


class TempCache(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cache = Path(self.dir.name) / "quota.json"

    def tearDown(self):
        self.dir.cleanup()

    def write(self, data):
        self.cache.write_text(json.dumps(data))


class RenderTest(unittest.TestCase):
    def test_days_column_has_fixed_width_in_every_state(self):
        states = [
            snapshot(),                                   # 03d00h
            snapshot(reset_in=5 * 3600),                  # 05h
            snapshot(reset_in=600),                       # 10m
            snapshot(age=1000),                           # stale 03d00h*
            snapshot(reset_in=-60),                       # stale
            {**snapshot(), "reset_at": None},             # ?
            {"error": "HTTP 401", "schema": 2},           # auth!
            {"error": "URLError", "schema": 2},           # error
            {"error": "FileNotFoundError", "schema": 2},  # auth! (not logged in)
            {},                                           # --
        ]
        labels = [common.render(s, "days") for s in states]
        self.assertTrue(all(len(label) == common.DAYS_WIDTH for label in labels), labels)
        self.assertEqual([label.strip() for label in labels],
                         ["02d23h", "04h", "09m", "02d23h*", "stale", "?", "auth!", "error", "auth!", "--"])

    def test_percent_drops_to_zero_once_the_window_has_reset(self):
        self.assertEqual(common.render(snapshot(percent=100, reset_in=-60), "percent"), "0")
        self.assertEqual(common.render(snapshot(percent=100), "percent"), "100")

    def test_rows_stay_live_between_fetch_cycles(self):
        # The threaded fetch runs every 60 s but only hits the network after TTL.
        self.assertFalse(common.stale(snapshot(age=common.TTL + 60)))
        self.assertTrue(common.stale(snapshot(age=2 * common.TTL)))
        self.assertFalse(common.stale(snapshot(age=2 * common.TTL), ttl=1800))

    def test_color(self):
        self.assertEqual(common.render(snapshot(), "color"), common.NORMAL_COLOR)
        self.assertEqual(common.render(snapshot(percent=97), "color"), common.ALERT_COLOR)
        burst = snapshot(short_percent=99, short_reset_at=time.time() + 600)
        self.assertEqual(common.render(burst, "color"), common.ALERT_COLOR)
        self.assertEqual(common.render(snapshot(reset_in=-60), "color"), common.STALE_COLOR)


class CacheTest(TempCache):
    def test_read_cache_tolerates_missing_and_corrupt_files(self):
        self.assertEqual(common.read_cache(self.cache), {})
        self.cache.write_text("{not json")
        self.assertEqual(common.read_cache(self.cache), {})
        self.write({"percent": 5, "fetched_at": 1, "reset_secs": 60})  # pre-schema-2 cache
        self.assertEqual(common.read_cache(self.cache), {})

    def test_fresh_cache_is_served_without_fetching(self):
        self.write(snapshot(percent=12))
        data = common.load_cache(self.cache, lambda: self.fail("fetched a fresh cache"))
        self.assertEqual(data["percent"], 12)

    def test_due_cache_is_refetched(self):
        self.write(snapshot(percent=12, age=common.TTL + 1))
        data = common.load_cache(self.cache, lambda: {"percent": 30, "reset_at": time.time() + 99})
        self.assertEqual(data["percent"], 30)
        self.assertNotIn("error", data)
        self.assertEqual(common.read_cache(self.cache)["percent"], 30)

    def test_crossing_the_reset_forces_a_fetch(self):
        self.write(snapshot(percent=100, reset_in=-1, age=120))  # fetched before the reset
        data = common.load_cache(self.cache, lambda: {"percent": 0, "reset_at": time.time() + 99})
        self.assertEqual(data["percent"], 0)

    def test_failed_fetch_keeps_last_data_and_records_only_the_error_type(self):
        self.write(snapshot(percent=12, age=common.TTL + 1))

        def boom():
            raise ValueError("token=secret")
        data = common.load_cache(self.cache, boom)
        self.assertEqual((data["percent"], data["error"]), (12, "ValueError"))
        self.assertNotIn("secret", self.cache.read_text())
        # Backoff: the next call inside TTL does not retry.
        common.load_cache(self.cache, lambda: self.fail("retried inside the backoff"))


class RunTest(TempCache):
    def run_mode(self, mode, fetch):
        out = io.StringIO()
        with redirect_stdout(out):
            common.run(mode, self.cache, fetch)
        return out.getvalue()

    def test_conky_modes_never_touch_the_network(self):
        self.write(snapshot(percent=12, age=common.TTL + 1))
        for mode in common.CACHED_MODES:
            self.run_mode(mode, lambda: self.fail(f"{mode} fetched"))

    def test_fetch_mode_updates_the_cache_silently(self):
        output = self.run_mode("fetch", lambda: {"percent": 55, "reset_at": time.time() + 99})
        self.assertEqual(output, "")
        self.assertEqual(self.run_mode("percent", None), "55\n")

    def test_status_fetches_when_due(self):
        status = json.loads(self.run_mode("status", lambda: {"percent": 7, "reset_at": time.time() + 99}))
        self.assertEqual(status["percent"], 7)
        self.assertFalse(status["stale"])

    def test_unknown_mode_exits(self):
        with self.assertRaises(SystemExit):
            common.run("bogus", self.cache, None)


class ParseTest(unittest.TestCase):
    def test_claude_needs_the_weekly_window(self):
        data = claude.parse_usage({"seven_day": {"utilization": 39, "resets_at": "2026-09-30T00:00:00Z"},
                                   "five_hour": {"utilization": 82}})
        self.assertEqual((data["percent"], data["short_percent"]), (39, 82))
        with self.assertRaises(ValueError):
            claude.parse_usage({"five_hour": {"utilization": 82}})

    def test_codex_picks_weekly_and_burst_windows(self):
        payload = {"rate_limit": {
            "primary_window": {"used_percent": 96, "limit_window_seconds": 18000, "reset_after_seconds": 60},
            "secondary_window": {"used_percent": 40, "limit_window_seconds": 604800 - 120,
                                 "reset_after_seconds": 3600},
        }}
        data = codex.parse_usage(payload, now=1000)
        self.assertEqual((data["percent"], data["reset_at"]), (40, 4600))
        self.assertEqual((data["short_percent"], data["short_reset_at"]), (96, 1060))

    def test_codex_without_a_weekly_window_is_an_error(self):
        with self.assertRaises(ValueError):
            codex.parse_usage({"rate_limit": {"primary_window": {"used_percent": 5, "limit_window_seconds": 18000}}})

    def test_grok_treats_an_omitted_percent_as_zero(self):
        data = grok.parse_usage({"config": {"currentPeriod": {"end": "2026-10-01T00:00:00Z"}}})
        self.assertEqual(data["percent"], 0)
        with self.assertRaises(ValueError):
            grok.parse_usage({"config": {}})

    def test_muse_reads_the_subscription_event_from_the_stream(self):
        stream = [b"event: response.created\n", b'data: {"type":"response.created"}\n', b"\n",
                  b'data: {"type":"response.subscription_usage","subscription":{"tier":"1",'
                  b'"weekly":{"resets_at":1790553600,"used_percent":49},'
                  b'"window":{"resets_at":1790252260,"used_percent":8,"window_duration_mins":300}}}\n']
        data = muse.parse_usage(muse.usage_event(stream))
        self.assertEqual(data, {"percent": 49, "reset_at": 1790553600, "short_percent": 8,
                                "short_reset_at": 1790252260})
        with self.assertRaises(ValueError):
            muse.usage_event([b'data: {"type":"response.completed"}\n'])

    def test_mimo_prefers_the_monthly_window(self):
        usage = {"monthUsage": {"items": [{"name": "month_total_token", "used": 250, "limit": 1000}]},
                 "usage": {"items": [{"name": "plan_total_token", "used": 900, "limit": 1000}]}}
        data = mimo.parse_usage(usage, {"currentPeriodEnd": "2026-10-03 23:59:59"})
        self.assertEqual(data["percent"], 25)
        self.assertEqual(data["reset_at"], 1791043199)  # 2026-10-03 15:59:59 UTC
        fallback = mimo.parse_usage({"usage": usage["usage"]}, {})
        self.assertEqual((fallback["percent"], fallback["reset_at"]), (90, None))
        with self.assertRaises(ValueError):
            mimo.parse_usage({"monthUsage": {"items": [{"used": 0, "limit": 0}]}}, {})

    def test_mimo_sends_account_cookies_only_to_xiaomi(self):
        self.assertTrue(mimo.is_xiaomi("https://account.xiaomi.com/pass/serviceLogin?sid=api-platform"))
        self.assertFalse(mimo.is_xiaomi("https://platform.xiaomimimo.com/sts?nonce=1"))
        self.assertFalse(mimo.is_xiaomi("https://xiaomi.com.evil.example/"))


if __name__ == "__main__":
    unittest.main()
