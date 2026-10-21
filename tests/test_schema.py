import unittest

from conftest_helpers import make_event

from polaris.schema import ValidationError, validate_and_normalize


class TestSchema(unittest.TestCase):
    def test_valid_event_passes(self):
        event = make_event()
        normalized = validate_and_normalize(event)
        self.assertEqual(normalized["message"]["id"], "msg_1")
        self.assertEqual(normalized["message"]["id_source"], "native")

    def test_missing_project_raises(self):
        event = make_event()
        del event["project"]
        with self.assertRaises(ValidationError):
            validate_and_normalize(event)

    def test_missing_message_content_raises(self):
        event = make_event()
        del event["message"]["content"]
        with self.assertRaises(ValidationError):
            validate_and_normalize(event)

    def test_wrong_schema_version_raises(self):
        event = make_event()
        event["schema_version"] = 2
        with self.assertRaises(ValidationError):
            validate_and_normalize(event)

    def test_missing_message_id_gets_fallback(self):
        event = make_event(message_id=None)
        event["message"]["id"] = None
        normalized = validate_and_normalize(event)
        self.assertTrue(normalized["message"]["id"].startswith("fb_"))
        self.assertEqual(normalized["message"]["id_source"], "fallback")

    def test_fallback_id_is_deterministic(self):
        event1 = make_event(message_id=None)
        event1["message"]["id"] = None
        event2 = make_event(message_id=None)
        event2["message"]["id"] = None
        n1 = validate_and_normalize(event1)
        n2 = validate_and_normalize(event2)
        self.assertEqual(n1["message"]["id"], n2["message"]["id"])


if __name__ == "__main__":
    unittest.main()
