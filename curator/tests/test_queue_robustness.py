"""Warteschlange des Curators (Review 2026-09-29): nichts darf sie dauerhaft
und still blockieren, und das Budget muss echte Aufrufe zaehlen."""

import unittest

from conftest_helpers import FakeLlm, TempProject, fast_config, make_change

from polaris_curator import pipeline, state as state_mod
from polaris_curator.llm import LlmOutputError


class TestOversizedConversation(unittest.TestCase):
    def test_conversation_larger_than_run_budget_is_split_and_progresses(self):
        tp = TempProject()
        with tp as project:
            for i in range(3):
                tp.write_message(f"m{i}", content="x" * 45000, captured_at=f"2026-09-01T10:0{i}:00.000Z")
            cfg = fast_config(max_input_chars_per_batch=50000, max_total_input_chars_per_run=60000)
            fake = FakeLlm([make_change(), make_change(), make_change()])

            report = pipeline.run_project(project, cfg, llm_fn=fake)

            self.assertGreaterEqual(report.llm_calls_made, 1)
            self.assertEqual(report.errors, [])
            st = state_mod.load_state(project)
            self.assertIn("m0", st.processed_message_ids)

    def test_single_message_bigger_than_run_budget_runs_alone_with_warning(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("big", content="y" * 90000)
            cfg = fast_config(max_input_chars_per_batch=50000, max_total_input_chars_per_run=60000)
            report = pipeline.run_project(project, cfg, llm_fn=FakeLlm([make_change()]))
            self.assertEqual(report.llm_calls_made, 1)
            self.assertTrue(any("groesser als das Laufbudget" in w for w in report.warnings))

    def test_dry_run_plans_exactly_what_the_run_does(self):
        tp = TempProject()
        with tp as project:
            for i, size in enumerate((40000, 30000, 1000)):
                tp.write_message(f"m{i}", content="z" * size, conversation_id=f"c{i}",
                                 captured_at=f"2026-09-0{i + 1}T10:00:00.000Z")
            cfg = fast_config(max_total_input_chars_per_run=60000)
            dry = pipeline.dry_run_project(project, cfg)
            real = pipeline.run_project(project, cfg, llm_fn=FakeLlm([make_change()] * 3))
            self.assertEqual(dry.llm_calls_made, real.llm_calls_made)


class TestFailures(unittest.TestCase):
    def test_invalid_output_counts_against_budget(self):
        tp = TempProject()
        with tp as project:
            for i in range(5):
                tp.write_message(f"m{i}", conversation_id=f"c{i}", captured_at=f"2026-09-0{i + 1}T10:00:00.000Z")
            fake = FakeLlm([LlmOutputError("kaputt")] * 5)
            report = pipeline.run_project(project, fast_config(max_llm_calls_per_run=3, max_batches_per_run=3), llm_fn=fake)
            self.assertEqual(len(fake.calls), 3)
            self.assertEqual(report.llm_calls_made, 3)

    def test_repeatedly_failing_batches_do_not_starve_newer_ones(self):
        tp = TempProject()
        with tp as project:
            for i in range(4):
                tp.write_message(f"m{i}", conversation_id=f"c{i}", captured_at=f"2026-09-0{i + 1}T10:00:00.000Z")
            cfg = fast_config(max_llm_calls_per_run=3, max_batches_per_run=3)
            # Die drei aeltesten scheitern zweimal ...
            for _ in range(2):
                pipeline.run_project(project, cfg, llm_fn=FakeLlm([LlmOutputError("x")] * 3))
            # ... danach kommt der vierte (neueste) zuerst dran.
            fake = FakeLlm([make_change(), LlmOutputError("x"), LlmOutputError("x")])
            pipeline.run_project(project, cfg, llm_fn=fake)
            self.assertIn("m3", state_mod.load_state(project).processed_message_ids)

    def test_repeated_failure_is_reported_and_cleared_on_success(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m0")
            cfg = fast_config()
            for _ in range(3):
                pipeline.run_project(project, cfg, llm_fn=FakeLlm([LlmOutputError("kaputt")]))
            report = pipeline.run_project(project, cfg, llm_fn=FakeLlm([make_change()]))
            self.assertTrue(any("scheitert wiederholt" in w for w in report.warnings))
            self.assertEqual(state_mod.load_state(project).failures, {})



class TestJournal(unittest.TestCase):
    """Write-Ahead-Journal (journal.py). Review Runde 2: ein halb
    geschriebener Merge wurde vom frueheren Abgleich dauerhaft gemacht."""

    def _change(self):
        return make_change(
            knowledge_updates=[{"section": "Status", "content": "Einmal"}],
            decisions_add=[{"title": "Wichtig", "decision": "D", "rationale": "R", "date": "2026-09-01"}],
        )

    def test_crash_after_state_save_is_completed_without_new_llm_call(self):
        from unittest.mock import patch
        tp = TempProject()
        with tp as project:
            tp.write_message("m1")
            cfg = fast_config()
            st0 = state_mod.CuratorState()
            st0.coverage_migrated = True  # Migration schon erledigt (sonst trifft der Patch sie)
            state_mod.save_state(project, st0)
            with patch.object(state_mod, "save_state", side_effect=RuntimeError("kill")):
                with self.assertRaises(RuntimeError):
                    pipeline.run_project(project, cfg, llm_fn=FakeLlm([self._change()]))
            fake = FakeLlm([])
            report = pipeline.run_project(project, cfg, llm_fn=fake)
            self.assertEqual(fake.calls, [])
            self.assertTrue(any("abgeschlossen" in w for w in report.warnings))
            self.assertIn("m1", state_mod.load_state(project).processed_message_ids)
            from polaris_curator import layout
            self.assertEqual(layout.knowledge_path(project).read_text().count("Einmal"), 1)

    def test_write_breaks_between_files_and_next_run_completes_the_decision(self):
        from unittest.mock import patch
        from polaris_curator import layout, storage
        tp = TempProject()
        with tp as project:
            tp.write_message("m1")
            cfg = fast_config()
            real = storage.atomic_write_text

            def flaky(path, text):
                if path.name == "decisions.md":
                    raise OSError("Transport endpoint is not connected")
                return real(path, text)

            with patch.object(storage, "atomic_write_text", side_effect=flaky):
                first = pipeline.run_project(project, cfg, llm_fn=FakeLlm([self._change()]))
            self.assertEqual(first.aborted_reason, "journal_pending")
            self.assertFalse(layout.decisions_path(project).exists())

            fake = FakeLlm([])
            pipeline.run_project(project, cfg, llm_fn=fake)
            self.assertEqual(fake.calls, [])
            self.assertIn("Wichtig", layout.decisions_path(project).read_text())
            self.assertIn("m1", state_mod.load_state(project).processed_message_ids)

    def test_foreign_change_meanwhile_blocks_instead_of_overwriting(self):
        from unittest.mock import patch
        from polaris_curator import layout, storage
        tp = TempProject()
        with tp as project:
            tp.write_message("m1")
            cfg = fast_config()
            real = storage.atomic_write_text

            def flaky(path, text):
                if path.name == "decisions.md":
                    raise OSError("weg")
                return real(path, text)

            with patch.object(storage, "atomic_write_text", side_effect=flaky):
                pipeline.run_project(project, cfg, llm_fn=FakeLlm([self._change()]))
            layout.decisions_path(project).write_text("# Entscheidungen\n\nvon Hand\n", encoding="utf-8")
            report = pipeline.run_project(project, cfg, llm_fn=FakeLlm([]))
            self.assertEqual(report.aborted_reason, "journal")
            self.assertIn("von Hand", layout.decisions_path(project).read_text())


class TestVisibility(unittest.TestCase):
    def test_document_without_text_is_reported_not_silent(self):
        from polaris_curator import layout
        tp = TempProject()
        with tp as project:
            (layout.inbox_dir(project, "documents") / "scan.txt").write_text("   ", encoding="utf-8")
            (layout.inbox_dir(project, "documents") / "bild.xyz").write_bytes(b"x")
            report = pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([]))
            joined = " | ".join(report.warnings)
            self.assertIn("scan.txt: kein Text gefunden", joined)
            self.assertIn("bild.xyz: Dateityp nicht unterstuetzt", joined)

    def test_context_is_cut_at_an_entry_boundary(self):
        text = "# Wissen\n\n## A\n- **[Fakt]** " + "x" * 100 + "\n- **[Fakt]** Ende\n"
        out = pipeline._tail_at_boundary(text, 40)
        self.assertTrue(out.startswith("[… aeltere Eintraege gekuerzt …]\n- "), out)
        self.assertIn("Ende", out)
        self.assertEqual(pipeline._tail_at_boundary("kurz", 40), "kurz")


if __name__ == "__main__":
    unittest.main()
