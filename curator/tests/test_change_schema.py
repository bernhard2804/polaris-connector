import unittest

from conftest_helpers import make_change

from polaris_curator.change_schema import (
    ChangeValidationError,
    failed_self_review_checks,
    validate_change_object,
)


class TestChangeSchema(unittest.TestCase):
    def test_empty_change_is_valid(self):
        validate_change_object(make_change())

    def test_missing_top_level_key_rejected(self):
        change = make_change()
        del change["conflicts"]
        with self.assertRaises(ChangeValidationError):
            validate_change_object(change)

    def test_not_a_dict_rejected(self):
        with self.assertRaises(ChangeValidationError):
            validate_change_object(["not", "a", "dict"])

    def test_knowledge_update_missing_field_rejected(self):
        change = make_change(knowledge_updates=[{"section": "Status"}])
        with self.assertRaises(ChangeValidationError):
            validate_change_object(change)

    def test_knowledge_update_invalid_mode_rejected(self):
        change = make_change(
            knowledge_updates=[{"section": "Status", "content": "x", "mode": "delete"}]
        )
        with self.assertRaises(ChangeValidationError):
            validate_change_object(change)

    def test_valid_knowledge_update_accepted(self):
        change = make_change(
            knowledge_updates=[{"section": "Status", "content": "x", "mode": "append"}]
        )
        validate_change_object(change)

    def test_decisions_add_requires_fields(self):
        change = make_change(decisions_add=[{"title": "X"}])
        with self.assertRaises(ChangeValidationError):
            validate_change_object(change)

    def test_open_loops_add_empty_text_rejected(self):
        change = make_change(open_loops_add=[{"text": "   "}])
        with self.assertRaises(ChangeValidationError):
            validate_change_object(change)

    def test_knowledge_update_invalid_entry_type_rejected(self):
        change = make_change(
            knowledge_updates=[{"section": "Status", "content": "x", "entry_type": "opinion"}]
        )
        with self.assertRaises(ChangeValidationError):
            validate_change_object(change)

    def test_knowledge_update_full_taxonomy_accepted(self):
        change = make_change(
            knowledge_updates=[
                {
                    "section": "Relevante Erkenntnisse",
                    "content": "Moeglicherweise strukturelles Muster.",
                    "entry_type": "hypothesis",
                    "scope": "functional",
                    "evidence_strength": "single_source",
                    "confidence": "low",
                    "causal_chain": ["fehlende Bearbeitung", "persoenliche Kompensation", "Ausfallrisiko"],
                }
            ]
        )
        validate_change_object(change)

    def test_decisions_add_validated_true_rejected(self):
        change = make_change(
            decisions_add=[
                {
                    "title": "X",
                    "decision": "Y",
                    "rationale": "Z",
                    "date": "2026-09-14",
                    "validated": True,
                }
            ]
        )
        with self.assertRaises(ChangeValidationError):
            validate_change_object(change)

    def test_decisions_update_without_any_change_rejected(self):
        change = make_change(decisions_update=[{"match_title": "X"}])
        with self.assertRaises(ChangeValidationError):
            validate_change_object(change)

    def test_missing_self_review_key_rejected(self):
        change = make_change()
        del change["self_review"]["no_overgeneralization_from_single_case"]
        with self.assertRaises(ChangeValidationError):
            validate_change_object(change)

    def test_failed_self_review_checks_lists_false_entries(self):
        change = make_change()
        change["self_review"]["key_person_dependencies_considered"] = False
        validate_change_object(change)
        self.assertEqual(
            failed_self_review_checks(change), ["key_person_dependencies_considered"]
        )

    def test_failed_self_review_checks_empty_when_all_true(self):
        change = make_change()
        validate_change_object(change)
        self.assertEqual(failed_self_review_checks(change), [])


if __name__ == "__main__":
    unittest.main()
