"""Architektur-Review 2026-09-29 (nach Livegang der Konzepte): Verlust- und
Vermischungspfade an Komponentengrenzen."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from conftest_helpers import FakeLlm, TempProject, fast_config, make_change

from polaris_curator import chatgpt_source, config, journal, layout, pipeline, state as state_mod


def _mapping_file(tmp: Path, entries: dict) -> Path:
    f = tmp / "projects.yaml"
    lines = ["projects:"]
    for pid, path in entries.items():
        lines += [f"  {pid}:", f"    local_path: {path}", "    status: ok"]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


class TestProjectFilter(unittest.TestCase):
    def test_messages_of_unmapped_project_are_not_curated(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("own-message-00001", content="Eigener Inhalt des Kunden")
            foreign = tp.write_message("foreign-msg-00001", content="Inhalt eines anderen Kunden")
            data = json.loads(foreign.read_text())
            data["project"]["id"] = "g-p-anderes"  # nach unmap keinem Ordner zugeordnet
            foreign.write_text(json.dumps(data))
            mf = _mapping_file(project.parent, {"proj_1": project})
            with patch.object(config, "PROJECTS_FILE", mf):
                llm = FakeLlm([make_change()])
                report = pipeline.run_project(project, fast_config(), llm_fn=llm)
            self.assertEqual(len(llm.calls), 1)
            self.assertIn("Eigener Inhalt", llm.calls[0])
            self.assertNotIn("anderen Kunden", llm.calls[0])
            self.assertTrue(any("nicht zugeordnet" in w for w in report.warnings))
            st = state_mod.load_state(project)
            self.assertFalse(st.is_message_processed("foreign-msg-00001"))

    def test_without_any_mapping_nothing_is_filtered(self):
        with TempProject() as project:
            self.assertIsNone(chatgpt_source.allowed_project_ids(project))


class TestShortTail(unittest.TestCase):
    def test_short_but_substantial_tail_is_curated(self):
        tp = TempProject()
        with tp as project:
            first = "Wir vergleichen die Angebote A und B ausfuehrlich nach Preis, Leistung und Fristen."
            tp.write_message("m1", role="assistant", content=first)
            st = state_mod.load_state(project)
            st.mark_messages_processed("2026-09-01", ["m1"])
            st.processed_revisions["m1"] = 1
            st.fed_paragraphs["m1"] = [chatgpt_source.paragraph_hash(chatgpt_source._comparable(first))]
            state_mod.save_state(project, st)
            rev2 = project / "raw" / "chatgpt" / "messages" / "m1.rev2.json"
            data = json.loads((project / "raw" / "chatgpt" / "messages" / "m1.json").read_text())
            data["message"]["content"] = first + "\n\nEmpfehlung: Angebot B, Frist 15.10., 12.500 EUR"
            rev2.write_text(json.dumps(data))
            absorbed = []
            msgs = chatgpt_source.load_new_messages(project, state_mod.load_state(project), absorbed=absorbed)
            self.assertEqual(len(msgs), 1)
            self.assertIn("12.500 EUR", msgs[0].content)
            self.assertEqual(absorbed, [])

    def test_trivial_tail_is_absorbed(self):
        tp = TempProject()
        with tp as project:
            first = "Wir vergleichen die Angebote A und B ausfuehrlich nach Preis, Leistung und Fristen."
            tp.write_message("m1", role="assistant", content=first)
            st = state_mod.load_state(project)
            st.mark_messages_processed("2026-09-01", ["m1"])
            st.processed_revisions["m1"] = 1
            st.fed_paragraphs["m1"] = [chatgpt_source.paragraph_hash(chatgpt_source._comparable(first))]
            state_mod.save_state(project, st)
            data = json.loads((project / "raw" / "chatgpt" / "messages" / "m1.json").read_text())
            data["message"]["content"] = first + "\n\nKopieren"
            (project / "raw" / "chatgpt" / "messages" / "m1.rev2.json").write_text(json.dumps(data))
            absorbed = []
            msgs = chatgpt_source.load_new_messages(project, state_mod.load_state(project), absorbed=absorbed)
            self.assertEqual(msgs, [])
            self.assertEqual(len(absorbed), 1)


class TestCompaction(unittest.TestCase):
    KNOW = ("# Projektwissen\n\n## S\n- **[Fakt]** Miete 1.200 EUR ab 01.10.\n  - ID: K1\n"
            "  <!-- polaris-source: type=chatgpt message_ids=a -->\n"
            "- **[Fakt]** Die Miete betraegt 1.200 EUR.\n  - ID: K2\n"
            "  <!-- polaris-source: type=chatgpt message_ids=b -->\n"
            "- **[Fakt]** Anderes Thema bleibt stehen\n  - ID: K3\n"
            "  <!-- polaris-source: type=chatgpt message_ids=c -->\n")

    def _plan(self, content):
        return {"merges": [{"ids": ["E1", "E2"], "entry_type": "fact", "scope": "local",
                            "evidence_strength": "single_source", "confidence": "medium", "content": content,
                            "economic_relevance": None, "operating_model_relevance": None,
                            "key_person_dependency": None, "causal_chain": None, "reason": "dup"}],
                "superseded": [], "notes": None}

    def _run(self, content):
        from polaris_curator import compact
        from polaris_curator.llm import LlmResult
        with TempProject() as project:
            layout.knowledge_path(project).write_text(self.KNOW, encoding="utf-8")
            res = compact.compact_project(
                project, fast_config(), lambda *_a, **_k: LlmResult(data=self._plan(content), model="m", tokens_used=1))
            k = layout.knowledge_path(project).read_text()
            arch = (project / "knowledge-archiv.md").read_text() if (project / "knowledge-archiv.md").exists() else ""
            return res, k, arch, journal.existing_path(project) is not None

    def test_merge_losing_a_number_is_rejected(self):
        res, k, _arch, _j = self._run("Miete 1.200 EUR")  # Datum 01.10. fehlt
        self.assertEqual(res.merged_groups, 0)
        self.assertTrue(res.rejected)
        self.assertIn("ab 01.10.", k)

    def test_merged_originals_are_archived_through_journal(self):
        res, k, arch, journal_left = self._run("Miete 1.200 EUR ab 01.10.")
        self.assertEqual(res.merged_groups, 1)
        self.assertIn("Die Miete betraegt 1.200 EUR.", arch)
        self.assertIn("Zusammengeführt: zusammengeführt (Originalfassung)", arch)
        self.assertIn("Zusammengeführt: K2", k)
        self.assertFalse(journal_left)


class TestMountGuard(unittest.TestCase):
    def test_missing_project_folder_is_never_created(self):
        import tempfile
        from polaris_curator import storage
        tmp = Path(tempfile.mkdtemp(prefix="polaris-mount-"))
        root = tmp / "Projekte"
        root.mkdir()  # Mountpunkt ohne eingehaengtes Drive: leer
        (tmp / "cfg").mkdir()
        (tmp / "cfg" / "config.yaml").write_text(f"project_roots:\n- {root}\n", encoding="utf-8")
        with patch.object(config, "GLOBAL_CONFIG_DIR", tmp / "cfg"):
            with self.assertRaises(OSError):
                storage.atomic_write_text(root / "ABC" / "knowledge.md", "x")
            with self.assertRaises(OSError):
                pipeline.run_project(root / "ABC", fast_config(), llm_fn=FakeLlm([]))
            self.assertEqual(list(root.iterdir()), [])
            (root / "ABC").mkdir()
            storage.atomic_write_text(root / "ABC" / ".polaris" / "x.json", "{}")  # vorhandenes Projekt: ok
            self.assertTrue((root / "ABC" / ".polaris" / "x.json").is_file())


class TestCurationHealth(unittest.TestCase):
    def test_run_result_feeds_widget(self):
        from polaris_curator import cli, systemhealth
        tp = TempProject()
        with tp as project:
            tp.write_message("m-alt-000000001", content="Alte, noch offene Nachricht mit Inhalt",
                             captured_at="2026-09-01T10:00:00.000Z")
            report = pipeline.RunReport(project=project.name)
            report.warnings = ["inbox/mail/x.eml: gekuerzt kuratiert (…)", "unwichtig"]
            pipeline.record_last_run(project, fast_config(), report, cli._important_warning)
            rec = json.loads((layout.polaris_dir(project) / "last-run.json").read_text())
            self.assertEqual(rec["warnings"], ["inbox/mail/x.eml: gekuerzt kuratiert (…)"])
            self.assertEqual(rec["oldest_pending_day"], "2026-09-01")
            self.assertIsNotNone(state_mod.load_state(project).last_successful_run)
            c = systemhealth.check_curation({"p": project})
            self.assertEqual(c["level"], "warn")
            self.assertIn("gekuerzt", c["detail"])
            self.assertIn("Rückstand seit", c["detail"])

    def test_failure_record_does_not_count_as_success(self):
        with TempProject() as project:
            st = state_mod.load_state(project)
            state_mod.save_state(project, st)
            self.assertIsNone(state_mod.load_state(project).last_successful_run)


class TestSuccessorAlias(unittest.TestCase):
    def test_replaced_number_found_in_merged_successor(self):
        from polaris_curator import merge
        with TempProject() as project:
            layout.knowledge_path(project).write_text(
                "# Projektwissen\n\n## S\n- **[Fakt]** Neu\n  - ID: K2 · Zusammengeführt: K7\n  <!-- p -->\n",
                encoding="utf-8")
            (project / "knowledge-archiv.md").write_text(
                "# Wissensarchiv\n\n## Abgelöst 2026-09-29\n- **[Fakt]** Alt\n  - ID: K7 · Ersetzt: K3\n", encoding="utf-8")
            res = merge.apply_change_object(project, make_change(knowledge_updates=[
                {"section": "S", "content": "Neuer", "mode": "replace", "replaces_id": "K3"}]), "<!-- q -->")
            self.assertEqual(res.warnings, [])
            self.assertNotIn("** Neu\n", layout.knowledge_path(project).read_text())


class TestLlmErrorSummary(unittest.TestCase):
    def test_prompt_echo_is_dropped_and_usage_limit_named(self):
        from polaris_curator.llm import _stderr_summary
        err = ("Kundeninhalt: Miete 1.200 EUR\n=== ENDE QUELLINHALT ===\n"
               "ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
               "https://chatgpt.com/codex/settings/usage to purchase more credits or try again at "
               "Sep 30th, 2026 12:13 AM.\n")
        out = _stderr_summary(err)
        self.assertEqual(out, "Codex-Nutzungslimit erreicht, wieder ab Sep 30th, 2026 12:13 AM")
        self.assertNotIn("Miete", out)


if __name__ == "__main__":
    unittest.main()
