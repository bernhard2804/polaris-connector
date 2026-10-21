import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import conftest_helpers  # noqa: F401 - sys.path

from polaris_curator import status_cache


class TestStatusCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "Projekt"
        (self.project / "raw" / "chatgpt" / "messages").mkdir(parents=True)
        (self.project / "inbox" / "documents").mkdir(parents=True)
        (self.project / ".polaris").mkdir()
        (self.project / ".polaris" / "curator-state.json").write_text("{}")
        self.calls = 0
        self.patches = [patch.object(status_cache, "CACHE_FILE", self.tmp / "cache.json"),
                        patch.object(status_cache.config_mod, "GLOBAL_CONFIG_FILE", self.tmp / "curator.yaml"),
                        patch.object(status_cache.config_mod, "PROJECTS_FILE", self.tmp / "projects.yaml")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def compute(self, _pid, path):
        self.calls += 1
        return {"name": path.name, "n": self.calls}

    def run_once(self, now=None):
        return status_cache.payloads([("id", self.project)], self.compute, now=now)

    def test_unchanged_project_is_not_recomputed(self):
        self.run_once(); self.run_once()
        self.assertEqual(self.calls, 1)

    def test_new_message_triggers_recompute(self):
        self.run_once()
        (self.project / "raw" / "chatgpt" / "messages" / "m1.json").write_text("{}")
        self.run_once()
        self.assertEqual(self.calls, 2)

    def test_new_document_and_state_change_trigger_recompute(self):
        self.run_once()
        (self.project / "inbox" / "documents" / "a.pdf").write_bytes(b"x")
        self.run_once()
        st = self.project / ".polaris" / "curator-state.json"
        st.write_text('{"x": 1}')
        os.utime(st, (time.time() + 5, time.time() + 5))
        self.run_once()
        self.assertEqual(self.calls, 3)

    def test_expires_after_max_age(self):
        now = time.time()
        self.run_once(now)
        self.run_once(now + status_cache.MAX_AGE + 1)
        self.assertEqual(self.calls, 2)

    def test_missing_cache_file_is_harmless(self):
        out = self.run_once()
        self.assertEqual(out[0]["name"], "Projekt")


if __name__ == "__main__":
    unittest.main()
