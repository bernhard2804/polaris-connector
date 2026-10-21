"""Abdeckung je Chat (Review Runde 3, 2026-09-29): jeder Text eines Chats
wird genau einmal kuratiert - unabhaengig von ID, Erfassungsweg, Revision."""

import json
import unittest

from conftest_helpers import FakeLlm, TempProject, fast_config, make_change

from polaris_curator import pipeline, state as state_mod

LONG_A = "Die Hausverwaltung uebernimmt ab Oktober die Abrechnung fuer alle Einheiten im Objekt."
LONG_B = "Der Beirat hat die Sonderumlage fuer das Dach am Montag beschlossen und protokolliert."


def rev(base, n, content):
    p = base.with_name(f"{base.stem}.rev{n}.json")
    e = json.loads(base.read_text())
    e["message"]["content"] = content
    p.write_text(json.dumps(e))
    return p


class TestCoverage(unittest.TestCase):
    def run_once(self, project, responses=1):
        fake = FakeLlm([make_change()] * responses)
        report = pipeline.run_project(project, fast_config(), llm_fn=fake)
        return fake, report

    def test_same_text_under_other_id_is_not_curated_again(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("a2", role="assistant", content=LONG_A)
            self.run_once(project)
            tp.write_message("th", role="assistant", content=LONG_A, captured_at="2026-09-01T12:00:00.000Z")
            fake, _ = self.run_once(project, 0)
            self.assertEqual(fake.calls, [])
            self.assertIn("th", state_mod.load_state(project).processed_message_ids)

    def test_merged_turn_from_split_dom_units_adds_nothing(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("a1", role="assistant", content="Ich schaue nach")
            tp.write_message("a2", role="assistant", content=LONG_A, captured_at="2026-09-01T10:01:00.000Z")
            self.run_once(project)
            base = project / "raw" / "chatgpt" / "messages" / "a1.json"
            rev(base, 2, "Ich schaue nach\n\n" + LONG_A)
            fake, _ = self.run_once(project, 0)
            self.assertEqual(fake.calls, [])

    def test_formatting_only_revision_is_absorbed(self):
        tp = TempProject()
        with tp as project:
            base = tp.write_message("a1", role="assistant", content=LONG_A)
            self.run_once(project)
            rev(base, 2, "**" + LONG_A.replace("Oktober", "[Oktober](https://x.example)") + "** citeturn0search0")
            fake, _ = self.run_once(project, 0)
            self.assertEqual(fake.calls, [])

    def test_real_new_paragraph_is_curated_once(self):
        tp = TempProject()
        with tp as project:
            base = tp.write_message("a1", role="assistant", content=LONG_A)
            self.run_once(project)
            rev(base, 2, LONG_A + "\n\n" + LONG_B + " " + LONG_B)
            fake, _ = self.run_once(project, 1)
            self.assertEqual(len(fake.calls), 1)
            src = fake.calls[0].split("=== QUELLINHALT")[-1]
            self.assertIn("Sonderumlage", src)
            self.assertNotIn("Abrechnung", src)
            fake2, _ = self.run_once(project, 0)
            self.assertEqual(fake2.calls, [])

    def test_retracted_message_is_never_curated_again(self):
        tp = TempProject()
        with tp as project:
            base = tp.write_message("x1", role="assistant", content=LONG_A)
            self.run_once(project)
            st = state_mod.load_state(project)
            st.retracted_ids.add("x1")
            state_mod.save_state(project, st)
            rev(base, 2, LONG_A + "\n\n" + LONG_B + " " + LONG_B)
            fake, _ = self.run_once(project, 0)
            self.assertEqual(fake.calls, [])

    def test_legacy_migration_refeeds_only_what_later_revisions_add(self):
        # Review Runde 5: bei aelteren States ist nur die erste Fassung sicher
        # kuratiert. Was eine spaetere hinzufuegt, geht einmal an das Modell -
        # nur der neue Absatz (hoechstens kleine Dublette, nie stiller Verlust).
        tp = TempProject()
        with tp as project:
            base = tp.write_message("a1", role="assistant", content=LONG_A)
            self.run_once(project)
            rev(base, 2, LONG_A + "\n\n" + LONG_B + " " + LONG_B)
            st = state_mod.load_state(project)
            st.processed_revisions, st.fed_paragraphs, st.coverage_migrated = {}, {}, False
            state_mod.save_state(project, st)
            fake, _ = self.run_once(project, 1)
            src = fake.calls[0].split("=== QUELLINHALT")[-1]
            self.assertIn("Sonderumlage", src)
            self.assertNotIn("Abrechnung", src)
            fake2, _ = self.run_once(project, 0)
            self.assertEqual(fake2.calls, [])

    def test_legacy_uncited_processed_message_does_not_cover(self):
        # Review Runde 5, F3: verarbeitet, aber in keiner Herkunftsangabe ->
        # hat nichts beigetragen und darf nichts "abdecken".
        tp = TempProject()
        with tp as project:
            tp.write_message("w1", role="assistant", content=LONG_A)
            st = state_mod.CuratorState()
            st.mark_messages_processed("2026-09-01", ["w1"])
            state_mod.save_state(project, st)
            tp.write_message("y1", role="assistant", content=LONG_A, captured_at="2026-09-01T12:00:00.000Z")
            fake, _ = self.run_once(project, 1)
            self.assertIn("Abrechnung", fake.calls[0])

    def test_off_branch_turn_is_skipped(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("u1", content="Frage")
            tp.write_message("old", role="assistant", content=LONG_A, captured_at="2026-09-01T10:01:00.000Z")
            tp.write_message("new", role="assistant", content=LONG_B, captured_at="2026-09-01T10:02:00.000Z")
            (project / "raw" / "chatgpt" / "conversations" / "conv_1.json").write_text(
                json.dumps({"conversation_id": "conv_1", "active_message_ids": ["u1", "new"]}), encoding="utf-8")
            fake, _ = self.run_once(project, 1)
            src = fake.calls[0].split("=== QUELLINHALT")[-1]
            self.assertIn("Sonderumlage", src)
            self.assertNotIn("Abrechnung", src)

    def test_complete_lower_revision_beats_truncated_higher_one(self):
        tp = TempProject()
        with tp as project:
            base = tp.write_message("a1", role="assistant", content=LONG_A + "\n\n" + LONG_B)
            rev(base, 2, LONG_A[:30])
            fake, _ = self.run_once(project, 1)
            self.assertIn("Sonderumlage", fake.calls[0])

    def test_r4_rebuild_requeued_message_is_not_absorbed_by_an_absorbed_duplicate(self):
        # Review Runde 4, F1: A curated, A2 (gleicher Text) absorbiert, A per
        # retract --rebuild neu eingestellt -> A muss wieder an das Modell.
        tp = TempProject()
        with tp as project:
            tp.write_message("a", role="assistant", content=LONG_A)
            self.run_once(project)
            tp.write_message("a2", role="assistant", content=LONG_A, captured_at="2026-09-01T12:00:00.000Z")
            self.run_once(project, 0)
            st = state_mod.load_state(project)
            st.processed_message_ids.discard("a")  # wie retract --rebuild
            for day in st.daily_batches.values():
                day.processed_message_ids = [m for m in day.processed_message_ids if m != "a"]
            state_mod.save_state(project, st)
            fake, _ = self.run_once(project, 1)
            self.assertEqual(len(fake.calls), 1)
            self.assertIn("Abrechnung", fake.calls[0])

    def test_r4_legacy_cutoff_is_frozen_once(self):
        import os, time
        tp = TempProject()
        with tp as project:
            base = tp.write_message("a1", role="assistant", content=LONG_A)
            self.run_once(project)
            st = state_mod.load_state(project)
            st.processed_revisions, st.fed_paragraphs, st.coverage_migrated = {}, {}, False
            state_mod.save_state(project, st)
            self.run_once(project, 0)  # Migration friert Fassung 1 ein
            rev(base, 2, LONG_A + "\n\n" + LONG_B + " " + LONG_B)
            st = state_mod.load_state(project)
            state_mod.save_state(project, st)  # Zeitgrenze wandert - darf nichts mehr bewirken
            fake, _ = self.run_once(project, 1)
            self.assertEqual(len(fake.calls), 1)
            self.assertIn("Sonderumlage", fake.calls[0])

    def test_r4_dom_fallback_turn_after_snapshot_is_not_off_branch(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("u1", content="Frage")
            tp.write_message("u2", content=LONG_B, captured_at="2026-09-01T11:00:00.000Z")
            (project / "raw" / "chatgpt" / "conversations" / "conv_1.json").write_text(json.dumps(
                {"conversation_id": "conv_1", "active_message_ids": ["u1"],
                 "active_as_of": "2026-09-01T10:30:00+00:00"}), encoding="utf-8")
            fake, _ = self.run_once(project, 1)
            self.assertIn("Sonderumlage", fake.calls[0])

    def _legacy(self, project):
        st = state_mod.load_state(project)
        st.processed_revisions, st.fed_paragraphs, st.coverage_migrated = {}, {}, False
        state_mod.save_state(project, st)

    def test_r6_legacy_coverage_survives_absorption_and_partial_feeds(self):
        # Review Runde 6, F1: legacy m1 zitiert; kleine rev2 wird absorbiert;
        # danach darf eine Kopie unter anderer ID NICHT erneut ans Modell.
        from polaris_curator import merge
        tp = TempProject()
        with tp as project:
            base = tp.write_message("m1", role="assistant", content=LONG_A)
            merge.apply_change_object(project, make_change(
                knowledge_updates=[{"section": "S", "content": "x"}]),
                merge.build_provenance("chatgpt", message_ids="m1"))
            st = state_mod.CuratorState()
            st.mark_messages_processed("2026-09-01", ["m1"])
            state_mod.save_state(project, st)
            rev(base, 2, LONG_A + " ok")  # kosmetisch
            self.run_once(project, 0)
            tp.write_message("api-1", role="assistant", content=LONG_A, captured_at="2026-09-01T12:00:00.000Z")
            fake, _ = self.run_once(project, 0)
            self.assertEqual(fake.calls, [])

    def test_r6_legacy_revision_already_fed_is_recognised_from_provenance(self):
        # F2: alter Code schickte rev1 und rev2 zusammen - ID steht zweimal in der Liste.
        from polaris_curator import merge
        tp = TempProject()
        with tp as project:
            base = tp.write_message("m1", role="assistant", content="Kurz")
            rev(base, 2, "Kurz\n\n" + LONG_B)
            merge.apply_change_object(project, make_change(
                knowledge_updates=[{"section": "S", "content": "x"}]),
                merge.build_provenance("chatgpt", message_ids="m1,m1"))
            st = state_mod.CuratorState()
            st.mark_messages_processed("2026-09-01", ["m1"])
            state_mod.save_state(project, st)
            fake, _ = self.run_once(project, 0)
            self.assertEqual(fake.calls, [])
            self.assertEqual(state_mod.load_state(project).processed_revisions["m1"], 2)

    def test_r7_revision_fed_in_a_later_batch_counts(self):
        # Alter Code: rev1 in Batch 1, rev2 in Batch 2 -> zwei Listen mit je einmal m1.
        from polaris_curator import merge
        tp = TempProject()
        with tp as project:
            base = tp.write_message("m1", role="assistant", content="Kurz")
            rev(base, 2, "Kurz\n\n" + LONG_B)
            for ids in ("m1,x1", "m1"):
                merge.apply_change_object(project, make_change(
                    knowledge_updates=[{"section": "S", "content": ids}]),
                    merge.build_provenance("chatgpt", message_ids=ids))
            st = state_mod.CuratorState()
            st.mark_messages_processed("2026-09-01", ["m1"])
            state_mod.save_state(project, st)
            fake, _ = self.run_once(project, 0)
            self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
