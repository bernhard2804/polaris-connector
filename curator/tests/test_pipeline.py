import unittest

from conftest_helpers import FakeLlm, TempProject, fast_config, make_change

from polaris_curator import layout, pipeline, state as state_mod
from polaris_curator.llm import LlmOutputError, LlmUnavailableError


class TestPipelineHappyPath(unittest.TestCase):
    def test_first_run_processes_and_writes_knowledge(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m1", captured_at="2026-09-01T10:00:00.000Z")
            tp.write_message("m2", captured_at="2026-09-01T11:00:00.000Z")

            fake = FakeLlm(
                [
                    make_change(
                        knowledge_updates=[
                            {"section": "Status", "content": "Kickoff erfolgt", "mode": "append"}
                        ]
                    )
                ]
            )
            cfg = fast_config()
            report = pipeline.run_project(project, cfg, llm_fn=fake)

            self.assertEqual(report.new_messages, 2)
            self.assertEqual(report.llm_calls_made, 1)
            self.assertTrue(report.knowledge_changed)
            self.assertEqual(report.errors, [])
            self.assertTrue(layout.knowledge_path(project).is_file())

            state = state_mod.load_state(project)
            self.assertIn("m1", state.processed_message_ids)
            self.assertIn("m2", state.processed_message_ids)

    def test_successful_document_is_moved_to_processed_archive(self):
        tp = TempProject()
        with tp as project:
            source = project / "inbox" / "documents" / "briefing.txt"
            source.write_text("Belastbarer Projektstand", encoding="utf-8")

            report = pipeline.run_project(
                project, fast_config(), llm_fn=FakeLlm([make_change()])
            )

            archived = (
                project
                / "inbox"
                / "documents"
                / "archive"
                / "processed"
                / "briefing.txt"
            )
            self.assertEqual(report.documents_processed, ["documents/briefing.txt"])
            self.assertFalse(source.exists())
            self.assertEqual(archived.read_text(encoding="utf-8"), "Belastbarer Projektstand")

            state = state_mod.load_state(project)
            signature = state.file_signature("documents:briefing.txt")
            self.assertIsNotNone(signature)
            self.assertEqual(
                signature["archive_path"],
                "inbox/documents/archive/processed/briefing.txt",
            )

    def test_archive_collision_never_overwrites_existing_file(self):
        tp = TempProject()
        with tp as project:
            source = project / "inbox" / "notes" / "status.md"
            source.write_text("Neue Fassung", encoding="utf-8")
            archive = project / "inbox" / "notes" / "archive" / "processed"
            archive.mkdir(parents=True)
            existing = archive / "status.md"
            existing.write_text("Alte Fassung", encoding="utf-8")

            report = pipeline.run_project(
                project, fast_config(), llm_fn=FakeLlm([make_change()])
            )

            self.assertEqual(report.errors, [])
            self.assertEqual(existing.read_text(encoding="utf-8"), "Alte Fassung")
            archived_files = sorted(archive.glob("status--*.md"))
            self.assertEqual(len(archived_files), 1)
            self.assertEqual(archived_files[0].read_text(encoding="utf-8"), "Neue Fassung")

    def test_second_run_is_idempotent(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m1")
            fake = FakeLlm([make_change()])
            cfg = fast_config()
            pipeline.run_project(project, cfg, llm_fn=fake)

            fake2 = FakeLlm([])  # darf gar nicht aufgerufen werden
            report2 = pipeline.run_project(project, cfg, llm_fn=fake2)

            self.assertEqual(report2.new_messages, 0)
            self.assertEqual(report2.llm_calls_made, 0)
            self.assertEqual(fake2.calls, [])

    def test_same_day_nachtrag_processed_as_new_batch(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m1", captured_at="2026-09-01T10:00:00.000Z")
            cfg = fast_config()
            pipeline.run_project(project, cfg, llm_fn=FakeLlm([make_change()]))

            # Spaeter am selben Tag: weitere Nachricht.
            tp.write_message("m2", captured_at="2026-09-01T20:00:00.000Z")
            fake2 = FakeLlm([make_change()])
            report2 = pipeline.run_project(project, cfg, llm_fn=fake2)

            self.assertEqual(report2.new_messages, 1)
            self.assertEqual(report2.llm_calls_made, 1)
            state = state_mod.load_state(project)
            self.assertIn("m2", state.processed_message_ids)


class TestPipelineCatchUp(unittest.TestCase):
    def test_multi_day_catchup_oldest_first_respecting_budget(self):
        tp = TempProject()
        with tp as project:
            for i, day in enumerate(["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]):
                tp.write_message(f"m{i}", conversation_id=f"c{i}", captured_at=f"{day}T10:00:00.000Z")

            cfg = fast_config(max_batches_per_run=2, max_llm_calls_per_run=2)
            fake = FakeLlm([make_change(), make_change()])
            report = pipeline.run_project(project, cfg, llm_fn=fake)

            self.assertEqual(report.llm_calls_made, 2)
            self.assertEqual(report.day_batches_processed, ["2026-09-01#1", "2026-09-02#1"])
            self.assertEqual(len(report.day_batches_planned), 4)

            state = state_mod.load_state(project)
            self.assertIn("m0", state.processed_message_ids)
            self.assertIn("m1", state.processed_message_ids)
            self.assertNotIn("m2", state.processed_message_ids)
            self.assertNotIn("m3", state.processed_message_ids)

            # Naechster Lauf holt die restlichen Tage nach.
            fake2 = FakeLlm([make_change(), make_change()])
            report2 = pipeline.run_project(project, cfg, llm_fn=fake2)
            self.assertEqual(report2.day_batches_processed, ["2026-09-03#1", "2026-09-04#1"])
            state2 = state_mod.load_state(project)
            self.assertIn("m2", state2.processed_message_ids)
            self.assertIn("m3", state2.processed_message_ids)


class TestPipelineBudget(unittest.TestCase):
    def test_char_budget_stops_run_early(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m1", captured_at="2026-09-01T10:00:00.000Z", content="x" * 100)
            tp.write_message(
                "m2", conversation_id="c2", captured_at="2026-09-02T10:00:00.000Z", content="y" * 100
            )

            cfg = fast_config(max_total_input_chars_per_run=150, max_batches_per_run=5, max_llm_calls_per_run=5)
            fake = FakeLlm([make_change()])
            report = pipeline.run_project(project, cfg, llm_fn=fake)

            self.assertEqual(report.llm_calls_made, 1)
            self.assertEqual(report.day_batches_processed, ["2026-09-01#1"])
            state = state_mod.load_state(project)
            self.assertIn("m1", state.processed_message_ids)
            self.assertNotIn("m2", state.processed_message_ids)


class TestPipelineFailureRecovery(unittest.TestCase):
    def test_failed_document_stays_in_inbox(self):
        tp = TempProject()
        with tp as project:
            source = project / "inbox" / "documents" / "kaputt.txt"
            source.write_text("Noch nicht verarbeitet", encoding="utf-8")

            report = pipeline.run_project(
                project,
                fast_config(),
                llm_fn=FakeLlm([LlmOutputError("ungueltige Ausgabe")]),
            )

            self.assertEqual(len(report.errors), 1)
            self.assertTrue(source.is_file())
            self.assertFalse(layout.inbox_processed_dir(project, "documents").exists())
            state = state_mod.load_state(project)
            self.assertIsNone(state.file_signature("documents:kaputt.txt"))

    def test_llm_unavailable_aborts_without_marking_processed(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m1")
            cfg = fast_config()
            fake = FakeLlm([LlmUnavailableError("boom")])
            report = pipeline.run_project(project, cfg, llm_fn=fake)

            self.assertEqual(report.aborted_reason, "llm_unavailable")
            self.assertEqual(report.llm_calls_made, 0)
            state = state_mod.load_state(project)
            self.assertNotIn("m1", state.processed_message_ids)
            self.assertFalse(layout.knowledge_path(project).exists())

            # Naechster Lauf mit funktionierendem LLM kann sauber wiederholen.
            fake2 = FakeLlm([make_change()])
            report2 = pipeline.run_project(project, cfg, llm_fn=fake2)
            self.assertEqual(report2.llm_calls_made, 1)
            self.assertEqual(report2.errors, [])
            state2 = state_mod.load_state(project)
            self.assertIn("m1", state2.processed_message_ids)

    def test_invalid_llm_output_skips_batch_but_continues(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m1", captured_at="2026-09-01T10:00:00.000Z")
            tp.write_message("m2", conversation_id="c2", captured_at="2026-09-02T10:00:00.000Z")

            cfg = fast_config()
            fake = FakeLlm([LlmOutputError("kaputt"), make_change()])
            report = pipeline.run_project(project, cfg, llm_fn=fake)

            self.assertEqual(len(report.errors), 1)
            self.assertEqual(report.day_batches_processed, ["2026-09-02#1"])
            state = state_mod.load_state(project)
            self.assertNotIn("m1", state.processed_message_ids)
            self.assertIn("m2", state.processed_message_ids)

    def test_invalid_change_object_not_marked_processed(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m1")
            cfg = fast_config()
            fake = FakeLlm([{"totally": "wrong shape"}])
            report = pipeline.run_project(project, cfg, llm_fn=fake)

            self.assertEqual(len(report.errors), 1)
            state = state_mod.load_state(project)
            self.assertNotIn("m1", state.processed_message_ids)

    def test_failed_self_review_skips_merge_and_batch(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m1")
            cfg = fast_config()
            bad_change = make_change(
                knowledge_updates=[{"section": "Status", "content": "Sollte nicht gemerged werden"}]
            )
            bad_change["self_review"]["no_overgeneralization_from_single_case"] = False
            bad_change["self_review"]["notes"] = "Einzelfall faelschlich als Muster dargestellt"
            fake = FakeLlm([bad_change])
            report = pipeline.run_project(project, cfg, llm_fn=fake)

            self.assertEqual(len(report.errors), 1)
            self.assertIn("no_overgeneralization_from_single_case", report.errors[0])
            self.assertFalse(report.knowledge_changed)
            self.assertFalse(layout.knowledge_path(project).exists())
            state = state_mod.load_state(project)
            self.assertNotIn("m1", state.processed_message_ids)

            # Naechster Lauf mit korrektem Self-Review verarbeitet erfolgreich.
            fake2 = FakeLlm([make_change()])
            report2 = pipeline.run_project(project, cfg, llm_fn=fake2)
            self.assertEqual(report2.errors, [])
            state2 = state_mod.load_state(project)
            self.assertIn("m1", state2.processed_message_ids)


class TestPipelineDryRun(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m1")
            cfg = fast_config()
            report = pipeline.dry_run_project(project, cfg)

            self.assertTrue(report.dry_run)
            self.assertEqual(report.new_messages, 1)
            self.assertFalse(layout.knowledge_path(project).exists())
            self.assertFalse(layout.state_path(project).exists())


if __name__ == "__main__":
    unittest.main()
