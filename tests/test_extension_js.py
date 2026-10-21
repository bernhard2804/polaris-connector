"""Fuehrt die Node-Regressionstests der Firefox-Extension (tests/extension/)
mit aus, damit `python3 -m unittest discover` auch content.js abdeckt
(seit 2026-09-29). Ohne Node wird uebersprungen, nicht bestanden."""

import shutil
import subprocess
import unittest
from pathlib import Path

EXTENSION_TESTS = Path(__file__).resolve().parent / "extension"


class TestExtensionJs(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node nicht installiert")
    def test_node_suite(self):
        proc = subprocess.run(
            ["node", "--test", str(EXTENSION_TESTS)], capture_output=True, text=True, timeout=120
        )
        self.assertEqual(proc.returncode, 0, proc.stdout[-4000:] + proc.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
