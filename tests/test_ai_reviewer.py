"""Tests for ai-reviewer. Run: python3 -m unittest discover -s tests"""

import importlib.machinery
import importlib.util
from pathlib import Path
import unittest

BIN = Path(__file__).resolve().parent.parent / "bin"


def load_script(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(BIN / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


reviewer = load_script("ai-reviewer")
NOW = 1_000_000.0


class ExhaustedTest(unittest.TestCase):
    def test_weekly_at_limit_with_future_reset(self):
        self.assertTrue(reviewer.exhausted({"percent": 100, "reset_at": NOW + 60}, NOW))

    def test_short_window_at_limit(self):
        reading = {"percent": 10, "short_percent": 100, "short_reset_at": NOW + 60}
        self.assertTrue(reviewer.exhausted(reading, NOW))

    def test_window_that_already_reset_is_free(self):
        self.assertFalse(reviewer.exhausted({"percent": 100, "reset_at": NOW - 1}, NOW))

    def test_unknown_reading_is_not_exhausted(self):
        self.assertFalse(reviewer.exhausted({}, NOW))
        self.assertFalse(reviewer.exhausted({"error": "FileNotFoundError"}, NOW))


class BestReadingTest(unittest.TestCase):
    def test_local_reading_wins(self):
        local, remote = {"percent": 10}, {"percent": 90}
        self.assertEqual(reviewer.best_reading(local, lambda: remote), local)

    def test_remote_used_when_local_has_no_numbers(self):
        remote = {"percent": 90}
        self.assertEqual(reviewer.best_reading({"error": "KeyError"}, lambda: remote), remote)

    def test_remote_not_asked_when_local_is_fine(self):
        def remote():
            raise AssertionError("remote should not be asked")
        reviewer.best_reading({"percent": 10}, remote)

    def test_local_kept_when_remote_has_nothing_either(self):
        local = {"error": "FileNotFoundError"}
        self.assertEqual(reviewer.best_reading(local, lambda: None), local)


class ChooseTest(unittest.TestCase):
    def test_first_available_in_order_wins(self):
        verdicts = {"codex": (False, "all accounts blocked"), "muse": (True, "ok"), "mimo": (True, "ok")}
        self.assertEqual(reviewer.choose(["codex", "muse", "mimo"], verdicts.get), "muse")

    def test_order_is_respected(self):
        verdicts = {"codex": (True, "ok"), "muse": (True, "ok")}
        self.assertEqual(reviewer.choose(["muse", "codex"], verdicts.get), "muse")

    def test_none_available(self):
        verdicts = {"codex": (False, "x"), "muse": (False, "y")}
        self.assertIsNone(reviewer.choose(["codex", "muse"], verdicts.get))

    def test_later_agents_are_not_checked_once_one_is_chosen(self):
        seen = []

        def check(name):
            seen.append(name)
            return (True, "ok")

        reviewer.choose(["codex", "muse", "mimo"], check)
        self.assertEqual(seen, ["codex"])


if __name__ == "__main__":
    unittest.main()
