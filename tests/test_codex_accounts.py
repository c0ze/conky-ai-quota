"""Tests for codex-accounts. Run: python3 -m unittest discover -s tests"""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest

BIN = Path(__file__).resolve().parent.parent / "bin"


def load_script(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(BIN / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


accounts = load_script("codex-accounts")
NOW = 1_000_000.0


def usage(percent=40, reset_at=NOW + 86400, short_percent=0, short_reset_at=None):
    return {"percent": percent, "reset_at": reset_at,
            "short_percent": short_percent, "short_reset_at": short_reset_at}


def pick(snaps):
    with contextlib.redirect_stderr(io.StringIO()):
        return accounts.pick(snaps, NOW)


class PickTest(unittest.TestCase):
    def test_soonest_weekly_reset_wins(self):
        snaps = {"a": usage(50, NOW + 500), "b": usage(10, NOW + 300), "c": usage(0, NOW + 900)}
        self.assertEqual(pick(snaps), "b")

    def test_weekly_limit_skips_account(self):
        self.assertEqual(pick({"a": usage(100, NOW + 200), "b": usage(10, NOW + 300)}), "b")

    def test_five_hour_limit_skips_account(self):
        snaps = {"a": usage(10, NOW + 200, 100, NOW + 50), "b": usage(10, NOW + 300)}
        self.assertEqual(pick(snaps), "b")

    def test_elapsed_five_hour_window_is_usable(self):
        snaps = {"a": usage(10, NOW + 200, 100, NOW - 10), "b": usage(10, NOW + 300)}
        self.assertEqual(pick(snaps), "a")

    def test_missing_login_skips_account(self):
        self.assertEqual(pick({"a": {"error": "HTTP 401"}, "b": usage()}), "b")

    def test_all_blocked_takes_the_one_that_frees_first(self):
        snaps = {"a": usage(100, NOW + 500), "b": usage(100, NOW + 400), "c": {"error": "x"}}
        self.assertEqual(pick(snaps), "b")


class PreferTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "prefer"

    def tearDown(self):
        self.dir.cleanup()

    def pick(self, snaps, prefer):
        with contextlib.redirect_stderr(io.StringIO()):
            return accounts.pick(snaps, NOW, prefer)

    def test_preferred_account_wins_while_usable(self):
        snaps = {"a": usage(50, NOW + 300), "b": usage(10, NOW + 900)}
        self.assertEqual(self.pick(snaps, "b"), "b")

    def test_blocked_preferred_account_falls_back(self):
        snaps = {"a": usage(50, NOW + 300), "b": usage(10, NOW + 900, 100, NOW + 60)}
        self.assertEqual(self.pick(snaps, "b"), "a")

    def test_unknown_preferred_account_is_ignored(self):
        self.assertEqual(self.pick({"a": usage()}, "gone"), "a")

    def test_prefer_file_round_trip(self):
        self.assertIsNone(accounts.read_prefer(self.path))
        accounts.write_prefer(self.path, "b")
        self.assertEqual(accounts.read_prefer(self.path), "b")
        accounts.write_prefer(self.path, None)
        self.assertIsNone(accounts.read_prefer(self.path))


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def home_dir(self, name, *, login=True):
        path = self.home / name
        path.mkdir()
        (path / ("auth.json" if login else "config.toml")).write_text("{}")
        return path

    def link(self, name, target):
        (self.home / name).symlink_to(target)

    def test_finds_named_homes_including_logged_out(self):
        self.home_dir(".codex-work")
        self.home_dir(".codex-play", login=False)
        (self.home / ".codex-empty").mkdir()  # neither auth.json nor config.toml
        self.assertEqual(accounts.discover(self.home, {}), ["play", "work"])

    def test_real_directory_name_beats_compat_symlink(self):
        self.home_dir(".codex-mail-business")
        self.link(".codex-mail", ".codex-mail-business")
        self.assertEqual(accounts.discover(self.home, {}), ["mail-business"])

    def test_symlink_to_default_home_counts(self):
        self.home_dir(".codex")
        self.link(".codex-main", ".codex")
        self.assertEqual(accounts.discover(self.home, {}), ["main"])

    def test_env_list_overrides_discovery(self):
        self.home_dir(".codex-work")
        self.assertEqual(accounts.discover(self.home, {"CODEX_ACCOUNTS": "b a"}), ["b", "a"])


if __name__ == "__main__":
    unittest.main()
