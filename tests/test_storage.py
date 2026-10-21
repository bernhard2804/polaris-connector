import json
import unittest

from conftest_helpers import TempHome

from polaris import storage


class TestStorage(unittest.TestCase):
    def test_atomic_write_creates_no_tmp_leftover(self):
        with TempHome() as h:
            target = h.tmp / "sub" / "file.json"
            storage.atomic_write_json(target, {"a": 1})

            self.assertTrue(target.is_file())
            self.assertEqual(json.loads(target.read_text()), {"a": 1})
            leftovers = list(target.parent.glob("*.tmp"))
            self.assertEqual(leftovers, [])

    def test_sanitize_id_strips_unsafe_chars(self):
        self.assertEqual(storage.sanitize_id("../../etc/passwd"), ".._.._etc_passwd")
        self.assertEqual(storage.sanitize_id("msg-123_abc.json"), "msg-123_abc.json")


if __name__ == "__main__":
    unittest.main()


class TestClaudeSource(unittest.TestCase):
    def test_claude_events_are_stored_separately(self):
        from conftest_helpers import TempHome, make_event
        from polaris import storage
        from polaris.schema import validate_and_normalize

        with TempHome() as h:
            root = h.make_project_root("MusterFirma")
            project = root / "MusterFirma"
            event = validate_and_normalize(dict(make_event(project_id="claude-p-1", project_name="COO"), source="claude"))
            path = storage.write_message(project, event)
            self.assertEqual(path.parent, project / "raw" / "claude" / "messages")
            conv = storage.update_conversation_metadata(project, event, event["message"]["id"])
            self.assertEqual(conv.parent, project / "raw" / "claude" / "conversations")
            import json
            self.assertEqual(json.loads(conv.read_text())["source"], "claude")

    def test_unknown_source_rejected(self):
        from conftest_helpers import make_event
        from polaris.schema import ValidationError, validate_and_normalize

        with self.assertRaises(ValidationError):
            validate_and_normalize(dict(make_event(), source="gemini"))
