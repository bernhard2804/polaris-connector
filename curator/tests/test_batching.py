import unittest

from conftest_helpers import fast_config

from polaris_curator.batching import build_day_batches
from polaris_curator.chatgpt_source import ChatMessage


def msg(mid, conv, date, content="x", role="user"):
    return ChatMessage(
        message_id=mid,
        conversation_id=conv,
        conversation_title="T",
        role=role,
        content=content,
        parent_id=None,
        source_timestamp=None,
        captured_at=f"{date}T10:00:00.000Z",
        local_date=date,
    )


class TestBatching(unittest.TestCase):
    def test_single_day_single_batch(self):
        cfg = fast_config()
        messages = [msg("m1", "c1", "2026-09-01"), msg("m2", "c1", "2026-09-01")]
        batches = build_day_batches(messages, cfg)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].day, "2026-09-01")
        self.assertEqual(batches[0].message_ids, ["m1", "m2"])

    def test_multiple_days_separate_batches(self):
        cfg = fast_config()
        messages = [msg("m1", "c1", "2026-09-01"), msg("m2", "c1", "2026-09-02")]
        batches = build_day_batches(messages, cfg)
        self.assertEqual([b.day for b in batches], ["2026-09-01", "2026-09-02"])

    def test_oversized_day_splits_into_subbatches(self):
        cfg = fast_config(max_messages_per_batch=2)
        messages = [
            msg("m1", "c1", "2026-09-01"),
            msg("m2", "c2", "2026-09-01"),
            msg("m3", "c3", "2026-09-01"),
        ]
        batches = build_day_batches(messages, cfg)
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[0].batch_id, "2026-09-01#1")
        self.assertEqual(batches[1].batch_id, "2026-09-01#2")
        total_ids = batches[0].message_ids + batches[1].message_ids
        self.assertEqual(total_ids, ["m1", "m2", "m3"])

    def test_conversation_not_split_across_batches_when_it_fits_together(self):
        cfg = fast_config(max_messages_per_batch=2)
        messages = [
            msg("m1", "c1", "2026-09-01"),
            msg("m2", "c1", "2026-09-01"),
            msg("m3", "c2", "2026-09-01"),
        ]
        batches = build_day_batches(messages, cfg)
        # c1-Block (m1,m2) bleibt zusammen in Batch 1, c2 (m3) startet Batch 2
        self.assertEqual(batches[0].message_ids, ["m1", "m2"])
        self.assertEqual(batches[1].message_ids, ["m3"])

    def test_oversized_single_conversation_kept_together_even_if_over_budget(self):
        cfg = fast_config(max_messages_per_batch=2)
        messages = [msg(f"m{i}", "c1", "2026-09-01") for i in range(5)]
        batches = build_day_batches(messages, cfg)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0].messages), 5)

    def test_char_budget_splits_batches(self):
        cfg = fast_config(max_input_chars_per_batch=15)
        messages = [
            msg("m1", "c1", "2026-09-01", content="1234567890"),
            msg("m2", "c2", "2026-09-01", content="1234567890"),
        ]
        batches = build_day_batches(messages, cfg)
        self.assertEqual(len(batches), 2)


if __name__ == "__main__":
    unittest.main()
