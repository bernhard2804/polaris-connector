import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import conftest_helpers  # noqa: F401

from polaris_curator import config


class TestKnownProjectsDedup(unittest.TestCase):
    def test_one_entry_per_folder_chatgpt_id_preferred(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "MusterFirma").mkdir()
        (tmp / "Falke").mkdir()
        f = tmp / "projects.yaml"
        f.write_text(yaml.safe_dump({"projects": {
            "claude-p-coo": {"local_path": str(tmp / "MusterFirma"), "status": "ok"},
            "g-p-muster": {"local_path": str(tmp / "MusterFirma"), "status": "ok"},
            "claude-p-verw": {"local_path": str(tmp / "MusterFirma"), "status": "ok"},
            "g-p-falke": {"local_path": str(tmp / "Falke"), "status": "ok"},
            "g-p-weg": {"local_path": str(tmp / "Gibtsnicht"), "status": "ok"},
        }}))
        with patch.object(config, "PROJECTS_FILE", f):
            known = config.list_known_projects()
        self.assertEqual(sorted(known), ["g-p-falke", "g-p-muster"])


if __name__ == "__main__":
    unittest.main()
