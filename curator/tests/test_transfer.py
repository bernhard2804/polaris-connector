from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from conftest_helpers import TempProject

from polaris_curator import cli, transfer

KNOWLEDGE = """# Projektwissen

## Zielbild
- **[Stakeholder-Aussage]** Aus Claude: Ziel X.
  <!-- polaris-source: type=claude conversation_id=c1 message_ids=m1 -->
- **[Fakt]** Aus ChatGPT: Ziel Y.
<!-- polaris-source: type=chatgpt conversation_id=g1 message_ids=m2 -->
- Beides.
<!-- polaris-source: type=chatgpt+claude conversation_id=g1,c1 message_ids=m3 -->
"""


def _fill(project: Path) -> None:
    (project / "knowledge.md").write_text(KNOWLEDGE, encoding="utf-8")
    (project / "decisions.md").write_text("# Entscheidungen\n", encoding="utf-8")
    (project / "open-loops.md").write_text(
        "# Offen\n- Punkt\n<!-- polaris-source: type=document path=inbox/mail/a.eml -->\n",
        encoding="utf-8",
    )
    (project / "knowledge-archiv.md").write_text("# Archiv\n", encoding="utf-8")
    claude_raw = project / "raw" / "claude" / "messages"
    claude_raw.mkdir(parents=True)
    (claude_raw / "m1.json").write_text('{"text": "ROHCHAT"}', encoding="utf-8")
    (project / "raw" / "chatgpt" / "messages" / "m2.json").write_text('{"text": "ROHCHAT"}')
    (project / "inbox" / "mail" / "a.eml").write_text("Subject: ROH\n\nROHMAIL")
    (project / ".polaris").mkdir()
    (project / ".polaris" / "curator-state.json").write_text("{}")


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.out_dir = Path(tempfile.mkdtemp(prefix="polaris-export-test-"))

    def tearDown(self):
        for p in self.out_dir.iterdir():
            p.unlink()
        self.out_dir.rmdir()

    def test_exports_only_curated_files_with_provenance(self):
        with TempProject() as project:
            _fill(project)
            out = self.out_dir / "t.zip"
            result = transfer.export([project], output=out, now=datetime(2026, 9, 25, 21, 0))

            with zipfile.ZipFile(out) as zf:
                names = sorted(zf.namelist())
                knowledge = zf.read(f"{project.name}/knowledge.md").decode()
                readme = zf.read("LIESMICH.md").decode()
                blob = b"".join(zf.read(n) for n in names)

        self.assertEqual(
            names,
            sorted(
                [
                    "LIESMICH.md",
                    f"{project.name}/knowledge.md",
                    f"{project.name}/decisions.md",
                    f"{project.name}/open-loops.md",
                ]
            ),
        )
        self.assertEqual(knowledge, KNOWLEDGE)  # unveraendert, Provenance bleibt
        self.assertNotIn(b"ROHCHAT", blob)
        self.assertNotIn(b"ROHMAIL", blob)
        self.assertIn("claude: 1", readme)
        self.assertIn("chatgpt+claude: 1", readme)
        self.assertEqual(
            result.projects[0].sources["knowledge.md"],
            {"chatgpt": 1, "chatgpt+claude": 1, "claude": 1},
        )
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        self.assertEqual(result.size_bytes, out.stat().st_size)

    def test_archive_and_index_optional(self):
        with TempProject() as project:
            _fill(project)
            (project / "dokumente-index.md").write_text("# Dokumente\n", encoding="utf-8")
            out = self.out_dir / "t.zip"
            transfer.export([project], output=out, with_archive=True)
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
        self.assertIn(f"{project.name}/knowledge-archiv.md", names)
        self.assertIn(f"{project.name}/dokumente-index.md", names)

    def test_no_temp_files_left_behind(self):
        with TempProject() as project:
            _fill(project)
            transfer.export([project], output=self.out_dir / "t.zip")
        self.assertEqual([p.name for p in self.out_dir.iterdir()], ["t.zip"])

    def test_default_output_name(self):
        now = datetime(2026, 9, 25, 21, 5)
        self.assertEqual(
            transfer.default_output(["Gärtnerei"], now).name,
            "polaris-projektwissen-Gärtnerei-20260925-2105.zip",
        )
        self.assertEqual(
            transfer.default_output(["A", "B"], now).name, "polaris-projektwissen-alle-20260925-2105.zip"
        )

    def test_cli_export_all_json(self):
        with TempProject() as a, TempProject() as b:
            _fill(a)
            out = self.out_dir / "all.zip"
            stdout = io.StringIO()
            with patch(
                "polaris_curator.cli.config_mod.list_known_projects",
                return_value={"p1": a, "p2": b},
            ), redirect_stdout(stdout):
                rc = cli.main(["export", "--all", "--output", str(out), "--json"])
            data = json.loads(stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(data["path"], str(out))
        self.assertEqual(len(data["projects"]), 2)
        empty = next(p for p in data["projects"] if p["files"] == [])
        self.assertEqual(empty["name"], b.name)

    def test_cli_requires_project_or_all(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["export"]), 2)


if __name__ == "__main__":
    unittest.main()
