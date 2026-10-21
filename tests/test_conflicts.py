import json
import unittest

from conftest_helpers import TempHome, make_event

from polaris import config, conflicts, mapping, pipeline


def put(event, name):
    (config.INBOX_DIR / name).write_text(json.dumps(event), encoding="utf-8")


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.h = TempHome()
        self.h.__enter__()
        self.root = self.h.make_project_root("ABC", "Falke")
        mapping.manual_map("g-p-abc", self.root / "ABC", "ABC")
        mapping.manual_map("g-p-lau", self.root / "Falke", "Falke")
        # Falsche Kopie kommt zuerst (in Falke), richtige wird abgewehrt.
        put(make_event(project_id="g-p-lau", project_name="Falke", conversation_id="c-lau", content="ABC-Text"), "a.json")
        pipeline.process_inbox()
        put(make_event(project_id="g-p-abc", project_name="ABC", conversation_id="c-abc", content="ABC-Text"), "b.json")
        pipeline.process_inbox()
        self.name = conflicts.list_conflicts()[0].name

    def tearDown(self):
        self.h.__exit__(None, None, None)

    def msgs(self, folder):
        return sorted(p.name for p in (self.root / folder / "raw" / "chatgpt" / "messages").glob("*.json"))

    def test_keep_rejected_moves_wrong_copy_away_and_stores_right_one(self):
        res = conflicts.resolve(self.name, "rejected")
        self.assertEqual(self.msgs("Falke"), [])
        self.assertEqual(self.msgs("ABC"), ["msg_1.json"])
        self.assertEqual(list(config.CONFLICTS_DIR.glob("*.json")), [])
        self.assertTrue(any("existing__Falke__msg_1.json" in q for q in res.quarantined))
        conv = json.loads((self.root / "Falke" / "raw" / "chatgpt" / "conversations" / "c-lau.json").read_text())
        self.assertNotIn("msg_1", conv["known_message_ids"])

    def test_keep_existing_only_quarantines_record(self):
        conflicts.resolve(self.name, "existing")
        self.assertEqual(self.msgs("Falke"), ["msg_1.json"])
        self.assertFalse((self.root / "ABC" / "raw").exists() and self.msgs("ABC"))
        self.assertEqual(list(config.CONFLICTS_DIR.glob("*.json")), [])

    def test_hint_when_wrong_copy_was_already_curated(self):
        pol = self.root / "Falke" / ".polaris"
        pol.mkdir(parents=True, exist_ok=True)
        (pol / "curator-state.json").write_text(json.dumps({"processed_message_ids": ["msg_1"]}), encoding="utf-8")
        res = conflicts.resolve(self.name, "rejected")
        self.assertIn("retract Falke msg_1", res.hint)


if __name__ == "__main__":
    unittest.main()


class TestMovedChat(unittest.TestCase):
    def test_moved_chat_is_resolved_in_one_step(self):
        with TempHome() as h:
            root = h.make_project_root("ABC", "Falke")
            mapping.manual_map("g-p-abc", root / "ABC", "ABC")
            mapping.manual_map("g-p-lau", root / "Falke", "Falke")
            for i in (1, 2):
                put(make_event(project_id="g-p-lau", project_name="Falke", conversation_id="c-moved",
                               message_id=f"m{i}", content=f"Text {i}"), f"a{i}.json")
            pipeline.process_inbox()
            for i in (1, 2):  # in ChatGPT nach ABC verschoben
                put(make_event(project_id="g-p-abc", project_name="ABC", conversation_id="c-moved",
                               message_id=f"m{i}", content=f"Text {i}"), f"b{i}.json")
            pipeline.process_inbox()
            self.assertEqual(len(conflicts.list_conflicts()), 2)
            results = conflicts.resolve_conversation("c-moved", "rejected")
            self.assertEqual(len(results), 2)
            self.assertEqual(conflicts.list_conflicts(), [])
            abc = sorted(p.name for p in (root / "ABC" / "raw" / "chatgpt" / "messages").glob("*.json"))
            lau = sorted(p.name for p in (root / "Falke" / "raw" / "chatgpt" / "messages").glob("*.json"))
            self.assertEqual(abc, ["m1.json", "m2.json"])
            self.assertEqual(lau, [])

    def test_mixing_of_two_chats_is_not_bulk_resolved(self):
        with TempHome() as h:
            root = h.make_project_root("ABC", "Falke")
            mapping.manual_map("g-p-abc", root / "ABC", "ABC")
            mapping.manual_map("g-p-lau", root / "Falke", "Falke")
            put(make_event(project_id="g-p-lau", project_name="Falke", conversation_id="c-lau", content="X"), "a.json")
            pipeline.process_inbox()
            put(make_event(project_id="g-p-abc", project_name="ABC", conversation_id="c-abc", content="X"), "b.json")
            pipeline.process_inbox()
            for chat in ("c-lau", "c-abc"):
                with self.assertRaises(conflicts.ResolveError):
                    conflicts.resolve_conversation(chat, "rejected")
            self.assertEqual(len(conflicts.list_conflicts()), 1)



class TestCrossDevice(unittest.TestCase):
    def test_keep_rejected_works_across_filesystems(self):
        import errno, os
        from unittest.mock import patch
        with TempHome() as h:
            root = h.make_project_root("ABC", "Falke")
            mapping.manual_map("g-p-abc", root / "ABC", "ABC")
            mapping.manual_map("g-p-lau", root / "Falke", "Falke")
            put(make_event(project_id="g-p-lau", project_name="Falke", conversation_id="c-lau", content="ABC-Text"), "a.json")
            pipeline.process_inbox()
            put(make_event(project_id="g-p-abc", project_name="ABC", conversation_id="c-abc", content="ABC-Text"), "b.json")
            pipeline.process_inbox()
            name = conflicts.list_conflicts()[0].name
            real = os.replace

            def exdev(src, dst, *a, **k):
                if str(src).startswith(str(root)) and "quarantine" in str(dst):
                    raise OSError(errno.EXDEV, "Invalid cross-device link")
                return real(src, dst, *a, **k)
            with patch("os.replace", side_effect=exdev), patch("os.rename", side_effect=exdev):
                conflicts.resolve(name, "rejected")
            self.assertEqual(sorted(p.name for p in (root / "Falke" / "raw" / "chatgpt" / "messages").glob("*.json")), [])
            self.assertEqual(sorted(p.name for p in (root / "ABC" / "raw" / "chatgpt" / "messages").glob("*.json")), ["msg_1.json"])
