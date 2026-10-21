import os
import tempfile
import time
import unittest
from pathlib import Path

import conftest_helpers  # noqa: F401 - sys.path

from polaris_curator import storage


class TestAtomicWriteSetsMtime(unittest.TestCase):
    def test_replaced_file_gets_current_mtime(self):
        p = Path(tempfile.mkdtemp()) / "knowledge.md"
        p.write_text("alt")
        old = time.time() - 4 * 86400
        os.utime(p, (old, old))
        storage.atomic_write_text(p, "neu")
        self.assertEqual(p.read_text(), "neu")
        self.assertGreater(p.stat().st_mtime, time.time() - 60)


if __name__ == "__main__":
    unittest.main()
