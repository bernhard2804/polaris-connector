"""Journal im Zusammenspiel (Review Runde 3, 2026-09-29)."""

import json
import unittest
from unittest.mock import patch

from conftest_helpers import FakeLlm, TempProject, fast_config, make_change

from polaris_curator import cli, journal, layout, pipeline, retract, state as state_mod, storage, systemhealth


MID = "message-0001-aaaa"


def pending_journal(tp, project):
    """Lauf, der zwischen den Dateien abbricht -> offenes Journal."""
    tp.write_message(MID)
    real = storage.atomic_write_text

    def flaky(path, text):
        if path.name == "decisions.md":
            raise OSError("weg")
        return real(path, text)

    change = make_change(
        knowledge_updates=[{"section": "Status", "content": "Eintrag"}],
        decisions_add=[{"title": "Wichtig", "decision": "D", "rationale": "R", "date": "2026-09-01"}],
    )
    with patch.object(storage, "atomic_write_text", side_effect=flaky):
        pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([change]))
    assert journal.path(project).is_file()


class TestJournalOps(unittest.TestCase):
    def test_retract_refuses_while_journal_pending_and_run_completes_it(self):
        tp = TempProject()
        with tp as project:
            pending_journal(tp, project)
            with self.assertRaises(retract.RetractError):
                retract.retract(project, [MID], apply=True, force=True)
            pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([]))
            self.assertIn("Wichtig", layout.decisions_path(project).read_text())
            self.assertFalse(journal.path(project).exists())

    def test_compact_refuses_while_journal_pending(self):
        from polaris_curator import compact
        tp = TempProject()
        with tp as project:
            pending_journal(tp, project)
            layout.knowledge_path(project).write_text(
                "# Wissen\n\n## A\n- **[Fakt]** eins\n  <!-- polaris-source: type=chatgpt message_ids=m1 -->\n"
                "- **[Fakt]** eins doppelt\n  <!-- polaris-source: type=chatgpt message_ids=m1 -->\n", encoding="utf-8")
            plan = {"merges": [{"ids": ["E1", "E2"], "entry_type": "fact", "scope": "local",
                                "evidence_strength": "single_source", "confidence": "medium", "content": "eins",
                                "economic_relevance": None, "operating_model_relevance": None,
                                "key_person_dependency": None, "causal_chain": None, "reason": "dup"}],
                    "superseded": [], "notes": None}
            with self.assertRaises(compact.CompactionError):
                compact.compact_project(project, fast_config(), FakeLlm([plan]))

    def test_corrupt_journal_blocks_only_its_project_and_is_visible(self):
        tp_a, tp_b = TempProject(), TempProject()
        with tp_a as a, tp_b as b:
            tp_a.write_message("ma")
            tp_b.write_message("mb")
            journal.path(a).parent.mkdir(parents=True, exist_ok=True)
            journal.path(a).write_text('{"files": {"knowl', encoding="utf-8")
            rep_a = pipeline.run_project(a, fast_config(), llm_fn=FakeLlm([]))
            rep_b = pipeline.run_project(b, fast_config(), llm_fn=FakeLlm([make_change()]))
            self.assertEqual(rep_a.aborted_reason, "journal")
            self.assertIsNone(rep_b.aborted_reason)
            line = systemhealth.check_journals({"a": a, "b": b})
            self.assertEqual(line["level"], "error")
            self.assertIn(a.name, line["detail"])

    def test_journal_cli_shows_and_discards_with_backup(self):
        tp = TempProject()
        with tp as project:
            pending_journal(tp, project)
            with patch.object(cli, "_resolve_project", return_value=project):
                self.assertEqual(cli.main(["journal", "x"]), 1)
                self.assertTrue(journal.path(project).exists())
                self.assertEqual(cli.main(["journal", "x", "--discard"]), 0)
            self.assertFalse(journal.path(project).exists())
            self.assertTrue(list((layout.polaris_dir(project) / "backups").glob("journal.json.*-discarded")))

    def test_document_replay_keeps_archive_path(self):
        tp = TempProject()
        with tp as project:
            doc = layout.inbox_dir(project, "documents") / "notiz.txt"
            doc.write_text("Wichtige Notiz ueber das Projekt " * 5, encoding="utf-8")
            with patch.object(journal, "finish", side_effect=RuntimeError("kill")):
                with self.assertRaises(RuntimeError):
                    pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([make_change()]))
            before = state_mod.load_state(project).file_signature("documents:notiz.txt")
            self.assertIn("archive_path", before)
            pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([]))
            after = state_mod.load_state(project).file_signature("documents:notiz.txt")
            self.assertEqual(after.get("archive_path"), before["archive_path"])
            self.assertFalse(journal.path(project).exists())

    def test_interrupted_document_part_is_completed_not_repeated(self):
        """Abbruch mitten in Teil 1 eines grossen Dokuments (seit 2026-10-07):
        der naechste Lauf vollendet Teil 1 aus dem Journal und macht mit Teil 2
        weiter, statt Teil 1 erneut zu kuratieren."""
        with TempProject() as project:
            doc = layout.inbox_dir(project, "documents") / "gross.txt"
            doc.write_text("".join(f"Zeile {i} mit Inhalt\n" for i in range(300)), encoding="utf-8")
            cfg = fast_config(max_input_chars_per_batch=2500, max_batches_per_run=1, max_llm_calls_per_run=1)
            real = storage.atomic_write_text

            def flaky(path, text):
                if path.name == "decisions.md":
                    raise OSError("weg")
                return real(path, text)

            change = make_change(
                knowledge_updates=[{"section": "Status", "content": "Eintrag"}],
                decisions_add=[{"title": "Wichtig", "decision": "D", "rationale": "R", "date": "2026-09-01"}],
            )
            with patch.object(storage, "atomic_write_text", side_effect=flaky):
                pipeline.run_project(project, cfg, llm_fn=FakeLlm([change]))
            self.assertTrue(journal.path(project).is_file())
            self.assertEqual(json.loads(journal.path(project).read_text())["state_update"]["kind"], "document_part")

            fake = FakeLlm([make_change()])
            report = pipeline.run_project(project, cfg, llm_fn=fake)
            self.assertFalse(journal.path(project).exists())
            self.assertIn("(Teil 2/", report.documents_processed[0])
            self.assertIn("Teil 2 von", fake.calls[0])
            self.assertEqual(state_mod.load_state(project).document_parts["documents:gross.txt"]["done"], 2)


if __name__ == "__main__":
    unittest.main()


class TestJournalLocation(unittest.TestCase):
    """Seit 2026-10-10 lokal statt auf dem Drive-Mount (verlorenes Loeschen
    sperrte ABC); ein Journal am alten Ort wird weiter vollendet."""

    def test_new_journal_is_local_not_in_project(self):
        tp = TempProject()
        with tp as project:
            pending_journal(tp, project)
            self.assertFalse(journal.legacy_path(project).exists())
            self.assertTrue(str(journal.path(project)).startswith(str(journal.journal_dir())))
            pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([]))
            self.assertIsNone(journal.existing_path(project))

    def test_projects_with_same_name_get_separate_journals(self):
        tp_a, tp_b = TempProject(), TempProject()
        with tp_a as a, tp_b as b:
            (a / "X").mkdir()
            (b / "X").mkdir()
            self.assertNotEqual(journal.path(a / "X"), journal.path(b / "X"))

    def test_legacy_journal_is_completed_and_removed(self):
        tp = TempProject()
        with tp as project:
            pending_journal(tp, project)
            layout.ensure_polaris_dir(project)
            journal.path(project).replace(journal.legacy_path(project))
            self.assertEqual(journal.existing_path(project), journal.legacy_path(project))
            pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([]))
            self.assertIn("Wichtig", layout.decisions_path(project).read_text())
            self.assertIsNone(journal.existing_path(project))

    def test_discard_handles_legacy_journal(self):
        tp = TempProject()
        with tp as project:
            pending_journal(tp, project)
            layout.ensure_polaris_dir(project)
            journal.path(project).replace(journal.legacy_path(project))
            layout.knowledge_path(project).write_text("# anders\n", encoding="utf-8")
            with patch.object(cli, "_resolve_project", return_value=project):
                cli.cmd_journal(type("A", (), {"project": "x", "discard": True})())
            self.assertIsNone(journal.existing_path(project))
            self.assertTrue(list((layout.polaris_dir(project) / "backups").glob("journal.json.*-discarded")))
