"""Tests for codex-wrapper/codex. Run: python3 -m unittest discover -s tests"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

WRAPPER = Path(__file__).resolve().parent.parent / "codex-wrapper" / "codex"


def script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # The wrapper first on PATH, as installed.
        script(self.tmp / "wrap" / "codex", WRAPPER.read_text().split("\n", 1)[1])
        # An Omarchy-style launcher: resolves `codex` through PATH again.
        script(self.tmp / "launcher" / "codex", 'exec mise x "codex" -- "codex" "$@"\n')
        script(self.tmp / "real" / "codex", 'echo "real $*"\n')

    def run_wrapper(self, mise_body):
        script(self.tmp / "fakemise" / "mise", mise_body)
        path = ":".join(str(self.tmp / d) for d in ("fakemise", "wrap", "launcher", "real")) + ":/usr/bin:/bin"
        env = {"PATH": path, "HOME": str(self.tmp), "CODEX_AUTO_ACCOUNT": "0"}
        return subprocess.run([str(self.tmp / "wrap" / "codex"), "--version"], env=env,
                              capture_output=True, text=True, timeout=10)

    def test_skips_a_launcher_that_would_loop_back(self):
        # `mise which` unavailable: the PATH scan must skip the launcher.
        out = self.run_wrapper("exit 1\n")
        self.assertEqual(out.stdout.strip(), "real --version")

    def test_prefers_the_binary_mise_resolves(self):
        out = self.run_wrapper(f'[ "$1" = which ] && echo {self.tmp / "real" / "codex"}\n')
        self.assertEqual(out.stdout.strip(), "real --version")

    def test_never_resolves_to_itself(self):
        out = self.run_wrapper(f'[ "$1" = which ] && echo {self.tmp / "wrap" / "codex"}\n')
        self.assertEqual(out.stdout.strip(), "real --version")


if __name__ == "__main__":
    unittest.main()
