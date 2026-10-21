"""Tests fuer memory/tools/memory-mirror (Spiegel des Maschinengedaechtnisses)."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "memory-mirror"


class TestMemoryMirror(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.src = self.home / ".config" / "ai-agents"
        (self.src / "handoff").mkdir(parents=True)
        (self.src / "AGENTS.md").write_text("regeln\n")
        (self.src / "handoff" / "a.md").write_text("alt\n")
        (self.home / ".config" / "polaris").mkdir(parents=True)
        self.project = self.home / "Projekte" / "Polaris"
        self.target = self.project / "Maschinengedaechtnis"
        (self.home / ".config" / "polaris" / "config.yaml").write_text(
            f"project_roots: []\nmemory_mirror: {self.target}\n")

    def run_mirror(self, *args):
        env = {**os.environ, "HOME": str(self.home)}
        env.pop("POLARIS_MEMORY_DIR", None)
        return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True)

    def state(self):
        return json.loads((self.home / ".local" / "state" / "polaris" / "memory-mirror.json").read_text())

    def test_unreachable_project_is_never_created(self):
        r = self.run_mirror()
        self.assertEqual(r.returncode, 75)
        self.assertFalse(self.project.exists())
        self.assertEqual(self.state()["last_result"], "unreachable")
        self.assertIsNone(self.state()["last_success"])

    def test_mirror_and_keep_old_versions(self):
        self.project.mkdir(parents=True)
        self.assertEqual(self.run_mirror().returncode, 0)
        self.assertEqual((self.target / "handoff" / "a.md").read_text(), "alt\n")
        (self.src / "handoff" / "a.md").write_text("neu\n")
        (self.src / "AGENTS.md").unlink()
        self.assertEqual(self.run_mirror().returncode, 0)
        self.assertEqual((self.target / "handoff" / "a.md").read_text(), "neu\n")
        self.assertFalse((self.target / "AGENTS.md").exists())
        alt = list((self.target / ".alt").iterdir())
        self.assertEqual(len(alt), 1)
        self.assertTrue((alt[0] / "AGENTS.md").exists())
        self.assertEqual((alt[0] / "handoff" / "a.md").read_text(), "alt\n")
        self.assertEqual(self.state()["last_result"], "ok")
        # ein weiterer Lauf loescht .alt nicht
        self.assertEqual(self.run_mirror().returncode, 0)
        self.assertTrue((self.target / ".alt").is_dir())

    def test_second_run_while_locked_does_nothing(self):
        self.project.mkdir(parents=True)
        lock = self.home / ".local" / "state" / "polaris" / "memory-mirror.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(["flock", str(lock), "sleep", "5"])
        try:
            import time
            time.sleep(0.5)
            r = self.run_mirror()
        finally:
            holder.kill()
            holder.wait()
        self.assertEqual(r.returncode, 0)
        self.assertIn("laeuft bereits", r.stdout)
        self.assertFalse(self.target.exists())

    def test_newer_mirror_is_not_overwritten(self):
        self.project.mkdir(parents=True)
        self.assertEqual(self.run_mirror().returncode, 0)
        # Spiegel bekommt neueren Stand (z. B. vor einer Wiederherstellung)
        newer = self.target / "handoff" / "a.md"
        newer.write_text("neuer im Spiegel\n")
        import time as _t
        old = _t.time() - 86400
        for f in self.src.rglob("*"):
            if f.is_file():
                os.utime(f, (old, old))
        r = self.run_mirror()
        self.assertEqual(r.returncode, 1)
        self.assertIn("Spiegel ist neuer", r.stderr)
        self.assertEqual(newer.read_text(), "neuer im Spiegel\n")
        self.assertEqual(self.state()["last_result"], "mirror_newer")
        env = {**os.environ, "HOME": str(self.home), "POLARIS_MIRROR_FORCE": "1"}
        r = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(newer.read_text(), "alt\n")

    def test_not_configured_does_nothing(self):
        (self.home / ".config" / "polaris" / "config.yaml").write_text("project_roots: []\n")
        r = self.run_mirror()
        self.assertEqual(r.returncode, 0)
        self.assertIn("kein Ziel", r.stdout)

    def test_dry_run_changes_nothing(self):
        self.project.mkdir(parents=True)
        self.assertEqual(self.run_mirror("--dry-run").returncode, 0)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
