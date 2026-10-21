import json
from pathlib import Path
import tempfile
import unittest

from conftest_helpers import TempHome

from polaris import mapping, storage
from polaris.export_archive import archive_export, short_title

from test_export_import import T0, node, project_conversation


def conversation_with_file(conv_id="conv-f"):
    conv = project_conversation(conv_id=conv_id)
    conv["title"] = "Vertrag: Prüfung / Entwurf"
    conv["mapping"]["u1"]["message"]["metadata"]["attachments"] = [{"id": "file-AAA", "name": "Vertrag.pdf"}]
    conv["mapping"]["u2"]["message"]["metadata"]["attachments"] = [{"id": "file-ZIP", "name": "Postfach.pst"}]
    return conv


def branch_of(parent, conv_id="conv-b"):
    conv = json.loads(json.dumps(parent))
    conv["conversation_id"] = conv_id
    conv["title"] = "Teilgespräch · " + parent["title"]
    conv["create_time"] = T0 + 100
    conv["mapping"]["u3"] = node("u3", "a2", "user", "Frage 3", t=7)
    conv["mapping"]["a3"] = node("a3", "u3", "assistant", "Antwort 3", t=8)
    conv["current_node"] = "a3"
    return conv


class TestShortTitle(unittest.TestCase):
    def test_strips_branch_prefix_unsafe_chars_and_length(self):
        self.assertEqual(short_title("Teilgespräch · Vertrag: Prüfung / Entwurf"), "Vertrag Prüfung Entwurf")
        long = short_title("Sehr langer Titel " * 10)
        self.assertLessEqual(len(long), 50)
        self.assertFalse(long.endswith(" "))


class TestArchiveExport(unittest.TestCase):
    def _export(self, conversations, files=None):
        d = Path(tempfile.mkdtemp())
        (d / "conversations-000.json").write_text(json.dumps(conversations))
        names = {}
        for file_id, (name, data) in (files or {}).items():
            (d / f"{file_id}.dat").write_bytes(data)
            names[f"{file_id}.dat"] = name
        (d / "conversation_asset_file_names.json").write_text(json.dumps(names))
        return d

    def test_writes_markdown_and_files_not_raw(self):
        with TempHome() as h:
            project = h.make_project_root("Gärtnerei") / "Gärtnerei"
            mapping.manual_map("g-p-abc123", project, "Gärtnerei")
            conv = conversation_with_file()
            conv["create_time"] = T0
            conv["mapping"]["u2"]["message"]["metadata"]["attachments"] += [
                {"id": "file-GONE", "name": "Angebot.pdf"}, {"id": "file-GONE2", "name": "Anlage.docx"}]
            (project / "2026").mkdir()
            (project / "2026" / "Angebot.pdf").write_bytes(b"%PDF")
            export = self._export([conv], {"file-AAA": ("x", b"%PDF-1.4 test"), "file-ZIP": ("y", b"PK\x03\x04")})

            dry = archive_export(export, dry_run=True)
            self.assertEqual(dry.chats, {"Gärtnerei": 1})
            self.assertFalse((project / "ChatGPT-Archiv").exists())

            report = archive_export(export)
            md = project / "ChatGPT-Archiv" / "2026-09-16 Vertrag Prüfung Entwurf.md"
            text = md.read_text()
            self.assertIn("chatgpt_conversation_id: conv-f", text)
            self.assertIn("## Ich · ", text)
            self.assertIn("Antwort 2", text)
            self.assertNotIn("verworfene Antwort", text)
            self.assertIn("[Vertrag.pdf](<Dateien/2026-09-16 Vertrag Prüfung Entwurf/Vertrag.pdf>)", text)
            self.assertIn("Postfach.pst (nicht abgelegt)", text)
            self.assertTrue((project / "ChatGPT-Archiv/Dateien/2026-09-16 Vertrag Prüfung Entwurf/Vertrag.pdf").is_file())
            self.assertEqual(report.noted_only, 1)
            self.assertIn("Anlage.docx (nicht im Export)", text)
            self.assertIn("[Angebot.pdf](<../2026/Angebot.pdf>) (im Projektordner)", text)
            self.assertEqual(report.linked_local, 1)
            # Nichts fuer den Curator
            self.assertFalse(storage.messages_dir(project).exists())

            again = archive_export(export)
            self.assertEqual(again.chats, {})
            self.assertEqual(again.skipped_existing, 1)

    def test_branch_repeats_only_new_part(self):
        with TempHome() as h:
            project = h.make_project_root("ABC") / "ABC"
            mapping.manual_map("g-p-abc123", project, "ABC")
            parent = project_conversation()
            parent["create_time"] = T0
            export = self._export([branch_of(parent), parent])

            report = archive_export(export)
            self.assertEqual(report.chats, {"ABC": 2})
            self.assertEqual(report.deduplicated_turns, 4)
            branch = (project / "ChatGPT-Archiv" / "2026-09-16 Testchat (Abzweigung).md").read_text()
            self.assertIn("Die ersten 4 Nachrichten stehen in [[2026-09-16 Testchat]]", branch)
            self.assertIn("Frage 3", branch)
            self.assertNotIn("Frage 1", branch)

    def test_filters_since_and_min_turns(self):
        with TempHome() as h:
            project = h.make_project_root("ABC") / "ABC"
            mapping.manual_map("g-p-abc123", project, "ABC")
            export = self._export([project_conversation()])
            self.assertEqual(archive_export(export, since="2026-10-01", dry_run=True).skipped_filter, 1)
            self.assertEqual(archive_export(export, min_turns=5, dry_run=True).skipped_filter, 1)
            self.assertEqual(archive_export(export, projects={"Andere"}, dry_run=True).chats, {})


if __name__ == "__main__":
    unittest.main()
