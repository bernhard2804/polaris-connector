import json
import unittest

from conftest_helpers import FakeLlm, TempProject, fast_config

from polaris_curator import compact

KNOWLEDGE = """# Wissen

## Status
- **[Fakt]** Go-Live PZE/BDE ist für den 01.10.2026 geplant.
  - Scope: lokal · Evidenzgrad: Einzelbeleg · Konfidenz: hoch
  <!-- polaris-source: type=document path=inbox/mail/archive/processed/a.eml hash=aaa -->
- **[Stakeholder-Aussage]** Julian Schmid hält den Termin für realistisch.
  - Scope: lokal · Evidenzgrad: Einzelbeleg · Konfidenz: mittel
  <!-- polaris-source: type=document path=inbox/mail/archive/processed/b.eml hash=bbb -->
- **[Fakt]** Der PZE/BDE-Go-Live ist auf den 01.10.2026 terminiert.
  - Scope: lokal · Evidenzgrad: Einzelbeleg · Konfidenz: hoch
  <!-- polaris-source: type=chatgpt conversation_id=c1 message_ids=m1 -->
- **[Stakeholder-Aussage]** Julian Schmid bewertet den Termin am 02.09. als ROT und nicht realistisch.
  - Scope: lokal · Evidenzgrad: Einzelbeleg · Konfidenz: hoch
  <!-- polaris-source: type=document path=inbox/mail/archive/processed/c.eml hash=ccc -->

## Technik
- **[Fakt]** APplus ist das Ziel-ERP.
  <!-- polaris-source: type=chatgpt conversation_id=c2 message_ids=m2 -->
"""

MERGE = {"ids": ["E1", "E3"], "entry_type": "fact", "scope": "local", "evidence_strength": "multiple_sources",
         "confidence": "high", "content": "Go-Live PZE/BDE ist für den 01.10.2026 geplant (Mail und Chat).",
         "economic_relevance": None, "operating_model_relevance": None, "key_person_dependency": None,
         "causal_chain": None, "reason": "gleicher Sachverhalt"}
SUPERSEDE = {"id": "E2", "superseded_by": "E4", "reason": "spätere Bewertung ROT"}


def plan(merges=None, superseded=None):
    return {"merges": merges if merges is not None else [MERGE],
            "superseded": superseded if superseded is not None else [SUPERSEDE], "notes": None}


class TestCompaction(unittest.TestCase):
    def _run(self, project, p, dry_run=False):
        (project / "knowledge.md").write_text(KNOWLEDGE, encoding="utf-8")
        fake = FakeLlm([p])
        return compact.compact_project(project, fast_config(), fake, dry_run=dry_run), fake

    def test_merge_and_supersede(self):
        with TempProject() as project:
            result, fake = self._run(project, plan())
            self.assertIn("[E1] (Abschnitt: Status)", fake.calls[0])
            self.assertNotIn("polaris-source", fake.calls[0], "Provenance wird nicht ans Modell geschickt")
            text = (project / "knowledge.md").read_text(encoding="utf-8")
            self.assertEqual(text.count("Go-Live PZE/BDE ist für den 01.10.2026 geplant (Mail und Chat)."), 1)
            self.assertNotIn("auf den 01.10.2026 terminiert", text)
            self.assertIn("path=inbox/mail/archive/processed/a.eml", text)
            self.assertIn("conversation_id=c1", text, "Provenance beider Einträge bleibt erhalten")
            self.assertNotIn("für realistisch", text)
            self.assertIn("ROT und nicht realistisch", text)
            self.assertIn("APplus ist das Ziel-ERP", text)
            archive = (project / compact.ARCHIVE_NAME).read_text(encoding="utf-8")
            self.assertIn("für realistisch", archive)
            self.assertIn("spätere Bewertung ROT", archive)
            backups = list((project / ".polaris" / "backups").glob("knowledge.md.*-before-compact"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), KNOWLEDGE)
            self.assertTrue(result.written)
            self.assertEqual((result.merged_groups, result.merged_entries, result.superseded), (1, 2, 1))

    def test_dry_run_writes_nothing(self):
        with TempProject() as project:
            result, _ = self._run(project, plan(), dry_run=True)
            self.assertFalse(result.written)
            self.assertEqual((project / "knowledge.md").read_text(encoding="utf-8"), KNOWLEDGE)
            self.assertFalse((project / compact.ARCHIVE_NAME).exists())

    def test_empty_plan_is_noop(self):
        with TempProject() as project:
            result, _ = self._run(project, plan(merges=[], superseded=[]))
            self.assertFalse(result.written)

    def test_invalid_plans_are_rejected(self):
        bad = [
            plan(merges=[dict(MERGE, ids=["E1", "E99"])], superseded=[]),
            plan(merges=[dict(MERGE, ids=["E1"])], superseded=[]),
            plan(merges=[MERGE], superseded=[dict(SUPERSEDE, id="E3")]),
            plan(merges=[dict(MERGE, ids=["E1", "E5"])], superseded=[]),  # abschnittsübergreifend
            plan(merges=[dict(MERGE, entry_type="gerücht")], superseded=[]),
        ]
        for p in bad:
            with TempProject() as project, self.subTest(p=p):
                with self.assertRaises(compact.CompactionError):
                    self._run(project, p)
                self.assertEqual((project / "knowledge.md").read_text(encoding="utf-8"), KNOWLEDGE)

    def test_emergency_brake(self):
        with TempProject() as project:
            p = plan(merges=[], superseded=[{"id": f"E{i}", "superseded_by": None, "reason": "x"} for i in (1, 2, 3, 4)])
            # Seit 2026-09-30: ohne Nachfolger wird gar nicht erst archiviert
            # (drop_bad_superseded) - noch vor der Notbremse.
            try:
                res, _fake = self._run(project, p)
                self.assertEqual(len(res.rejected), 4)
            except compact.CompactionError:
                pass
            self.assertEqual((project / "knowledge.md").read_text(encoding="utf-8"), KNOWLEDGE)

    def test_due(self):
        with TempProject() as project:
            (project / "knowledge.md").write_text("x" * 50)
            self.assertFalse(compact.due(project, fast_config(compact_threshold_chars=100)))
            (project / "knowledge.md").write_text("x" * 150)
            cfg = fast_config(compact_threshold_chars=100, compact_min_interval_days=7)
            self.assertTrue(compact.due(project, cfg))
            compact.record_run(project, compact.CompactionResult())
            self.assertFalse(compact.due(project, cfg))
            self.assertFalse(compact.due(project, fast_config(compact_threshold_chars=0)))

    def test_idle_backoff(self):
        from datetime import datetime, timedelta
        with TempProject() as project:
            (project / "knowledge.md").write_text("x" * 150)
            cfg = fast_config(compact_threshold_chars=100, compact_min_interval_days=7)
            compact.record_run(project, compact.CompactionResult(chars_after=150))
            state = json.loads(compact._state_path(project).read_text())
            self.assertEqual(state["idle_streak"], 1)
            now = datetime.now()
            self.assertFalse(compact.due(project, cfg, now + timedelta(days=8)))   # 14 statt 7 Tage
            self.assertTrue(compact.due(project, cfg, now + timedelta(days=15)))
            compact.record_run(project, compact.CompactionResult(chars_after=150))
            self.assertFalse(compact.due(project, cfg, now + timedelta(days=27)))  # 28 Tage
            compact.record_run(project, compact.CompactionResult(chars_after=150))
            self.assertTrue(compact.due(project, cfg, now + timedelta(days=29)))   # gedeckelt bei 4x
            # Wachstum um 10 % -> wieder Grundabstand
            (project / "knowledge.md").write_text("x" * 170)
            self.assertTrue(compact.due(project, cfg, now + timedelta(days=8)))
            # Ertrag setzt zurueck
            compact.record_run(project, compact.CompactionResult(merged_groups=1, chars_after=170))
            self.assertEqual(json.loads(compact._state_path(project).read_text())["idle_streak"], 0)
            self.assertTrue(compact.due(project, cfg, now + timedelta(days=8)))
            # abschaltbar
            compact.record_run(project, compact.CompactionResult(chars_after=170))
            self.assertTrue(compact.due(project, fast_config(compact_threshold_chars=100, idle_backoff_max=1),
                                        now + timedelta(days=8)))

    def test_idle_backoff_legacy_state(self):
        self.assertEqual(compact.idle_streak({"last_run": "x", "merged_groups": 0, "superseded": 0}), 1)
        self.assertEqual(compact.idle_streak({"last_run": "x", "merged_groups": 0, "superseded": 1}), 0)
        self.assertEqual(compact.idle_streak({}), 0)


if __name__ == "__main__":
    unittest.main()
