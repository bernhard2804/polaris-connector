import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from conftest_helpers import TempHome

from polaris import mapping, storage
from polaris.export_import import events_from_conversation, import_export

T0 = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc).timestamp()


def node(nid, parent, role=None, text=None, ctype="text", hidden=False, t=0):
    msg = None
    if role:
        msg = {"id": f"msg-{nid}", "author": {"role": role}, "create_time": T0 + t,
               "content": {"content_type": ctype, "parts": [text] if text is not None else []},
               "metadata": {"is_visually_hidden_from_conversation": hidden}}
    return {"id": nid, "parent": parent, "message": msg}


def project_conversation(gizmo="g-p-abc123", conv_id="conv-1"):
    nodes = [
        node("root", None),
        node("sys", "root", "system", "Systemprompt", hidden=True),
        node("u1", "sys", "user", "Frage 1", t=1),
        node("a1-old", "u1", "assistant", "verworfene Antwort", t=2),
        node("a1", "u1", "assistant", "Antwort 1", t=3),
        node("tool", "a1", "tool", "Werkzeug-Ausgabe", t=4),
        node("u2", "tool", "user", "Frage 2", t=5),
        node("a2", "u2", "assistant", "Antwort 2", t=6),
    ]
    return {"title": "Testchat", "conversation_id": conv_id, "gizmo_id": gizmo,
            "current_node": "a2", "mapping": {n["id"]: n for n in nodes}}


class TestEventsFromConversation(unittest.TestCase):
    def test_current_branch_only_and_parent_chain(self):
        events = events_from_conversation(project_conversation(), "now")
        self.assertEqual([e["message"]["content"] for e in events], ["Frage 1", "Antwort 1", "Frage 2", "Antwort 2"])
        self.assertEqual([e["message"]["parent_id"] for e in events], [None, "msg-u1", "msg-a1", "msg-u2"])
        self.assertTrue(events[0]["captured_at"].startswith("2026-09-16T10:00:01"))
        self.assertEqual(events[0]["project"]["id"], "g-p-abc123")

    def test_non_project_conversation_is_ignored(self):
        conv = project_conversation()
        conv["gizmo_id"] = None
        self.assertEqual(events_from_conversation(conv, "now"), [])

    def test_newer_export_project_id_in_conversation_template_id(self):
        conv = project_conversation()
        del conv["gizmo_id"]
        conv["conversation_template_id"] = "g-p-abc123"
        events = events_from_conversation(conv, "now")
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0]["project"]["id"], "g-p-abc123")
        conv["conversation_template_id"] = "g-hTcustomgpt"
        self.assertEqual(events_from_conversation(conv, "now"), [])


class TestImportExport(unittest.TestCase):
    def _zip(self, conversations):
        path = Path(tempfile.mkdtemp()) / "export.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("conversations.json", json.dumps(conversations))
            zf.writestr("user.json", "{}")
        return path

    def test_imports_skips_existing_and_reports_unmapped(self):
        with TempHome() as h:
            root = h.make_project_root("Falke")
            project = root / "Falke"
            mapping.manual_map("g-p-abc123", project, "Falke")
            export = self._zip([project_conversation(), project_conversation("g-p-unknown", "conv-2")])

            dry = import_export(export, dry_run=True)
            self.assertEqual(dry.imported, {"Falke": 4})
            self.assertFalse(storage.messages_dir(project).exists() and any(storage.messages_dir(project).iterdir()))

            first = import_export(export)
            self.assertEqual(first.imported, {"Falke": 4})
            self.assertEqual(first.unmapped, {"g-p-unknown": 4})
            files = sorted(p.name for p in storage.messages_dir(project).iterdir())
            self.assertEqual(files, ["msg-a1.json", "msg-a2.json", "msg-u1.json", "msg-u2.json"])

            second = import_export(export)
            self.assertEqual(second.imported, {})
            self.assertEqual(second.existing, 4)
            self.assertFalse(any(".rev" in p.name for p in storage.messages_dir(project).iterdir()))

    def test_assistant_parts_and_tool_calls_are_one_turn_like_the_extension(self):
        nodes = [
            node("root", None),
            node("u1", "root", "user", "Frage", t=1),
            node("a1", "u1", "assistant", "Suche", t=2),
            node("tool", "a1", "tool", "Werkzeug", t=3),
            node("a2", "tool", "assistant", "Ergebnis", t=4),
        ]
        nodes[2]["message"]["recipient"] = "browser"  # Werkzeugaufruf
        conv = {"title": "t", "conversation_id": "c", "gizmo_id": "g-p-abc123",
                "current_node": "a2", "mapping": {n["id"]: n for n in nodes}}
        events = events_from_conversation(conv, "2026-09-16T00:00:00+00:00")
        # ID = frueheste Nachricht des Turns (stabil), alle Teile als member_ids
        self.assertEqual([(e["message"]["id"], e["message"]["content"]) for e in events],
                         [("msg-u1", "Frage"), ("msg-a1", "Ergebnis")])
        self.assertEqual(events[1]["message"]["member_ids"], ["msg-a1", "msg-tool", "msg-a2"])

    def test_id_archived_in_other_project_is_not_imported(self):
        with TempHome() as h:
            root = h.make_project_root("Falke", "ABC")
            mapping.manual_map("g-p-abc123", root / "Falke", "Falke")
            mapping.manual_map("g-p-abc", root / "ABC", "ABC")
            clash = events_from_conversation(project_conversation(), "x")[0]
            clash["project"]["id"] = "g-p-abc"
            clash["conversation"]["id"] = "conv-abc"
            storage.write_message(root / "ABC", clash)

            report = import_export(self._zip([project_conversation()]))

            self.assertEqual(report.conflicts, 1)
            from polaris import config
            records = list(config.CONFLICTS_DIR.glob("*.json"))
            self.assertEqual(len(records), 1)
            self.assertEqual(json.loads(records[0].read_text())["_polaris_conflict"]["existing_folder"], "ABC")
            self.assertEqual(report.imported, {"Falke": 3})
            self.assertFalse((storage.messages_dir(root / "Falke") / "msg-u1.json").exists())

    def test_refuses_while_bridge_is_busy(self):
        from polaris import pipeline
        from polaris.export_import import ImportBusyError
        with TempHome() as h:
            h.make_project_root("Falke")
            with pipeline.pipeline_lock() as got:
                self.assertTrue(got)
                with self.assertRaises(ImportBusyError):
                    import_export(self._zip([project_conversation()]))

    def test_since_filter(self):
        with TempHome() as h:
            root = h.make_project_root("Falke")
            mapping.manual_map("g-p-abc123", root / "Falke", "Falke")
            report = import_export(self._zip([project_conversation()]),
                                   since=datetime(2026, 9, 16, 10, 0, 4, tzinfo=timezone.utc))
            self.assertEqual(report.imported, {"Falke": 2})
            self.assertEqual(report.skipped_before_since, 2)


if __name__ == "__main__":
    unittest.main()
