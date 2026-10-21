import unittest

from conftest_helpers import FakeLlm, TempProject, fast_config

from polaris_curator import reclassify

KNOWLEDGE = """# Projektwissen

## Kennzahlen / Fakten
- **[Stakeholder-Aussage]** Claude berichtet fehlende Miteigentumsanteile bei 517 von 690 Objekten.
  - Scope: funktional · Evidenzgrad: Einzelbeleg · Konfidenz: niedrig
  <!-- polaris-source: type=claude conversation_id=c1 message_ids=m1 -->
- **[Stakeholder-Aussage]** Kim Beispiel berichtet, die Adresse funktioniere nicht.
  - Scope: lokal · Evidenzgrad: Einzelbeleg · Konfidenz: mittel
  <!-- polaris-source: type=document path=inbox/mail/archive/processed/a.eml hash=aaa -->
- **[Fakt]** Der Bestand umfasst 10.694 Einheiten.
  - Scope: funktional · Evidenzgrad: Einzelbeleg · Konfidenz: mittel
  <!-- polaris-source: type=claude conversation_id=c1 message_ids=m2 -->
- **[Stakeholder-Aussage]** Claude sieht eine fragmentierte Landschaft.
  - Scope: funktional · Evidenzgrad: Einzelbeleg · Konfidenz: niedrig
  <!-- polaris-source: type=claude conversation_id=c1 message_ids=m3 -->
  <!-- polaris-source: type=chatgpt conversation_id=g1 message_ids=m4 -->
"""


def change(cid, **kw):
    base = {"id": cid, "entry_type": "fact", "scope": "functional", "evidence_strength": "single_source",
            "confidence": "medium", "content": "Laut Claude-Auswertung des ERP-Systems fehlen "
            "Miteigentumsanteile bei 517 von 690 Objekten.", "economic_relevance": None,
            "operating_model_relevance": None, "key_person_dependency": None, "causal_chain": None,
            "reason": "abgerufene Daten"}
    base.update(kw)
    return base


class TestReclassify(unittest.TestCase):
    def _project(self, tp):
        (tp / "knowledge.md").write_text(KNOWLEDGE, encoding="utf-8")
        return tp

    def test_only_chat_sourced_stakeholder_entries_are_candidates(self):
        with TempProject() as tp:
            project = self._project(tp)
            llm = FakeLlm([{"changes": [], "notes": None}])
            result = reclassify.reclassify_project(project, fast_config(), llm)
            self.assertEqual(result.candidates, 2)  # E1 und E4, nicht Mail (E2) und nicht Fakt (E3)
            self.assertIn("[E1]", llm.calls[0])
            self.assertIn("[E4]", llm.calls[0])
            self.assertNotIn("Kim Beispiel", llm.calls[0])
            self.assertIn("2a. Antworten eines KI-Assistenten", llm.calls[0])

    def test_plan_then_apply_keeps_provenance_and_backs_up(self):
        with TempProject() as tp:
            project = self._project(tp)
            hyp = change("E4", entry_type="hypothesis", confidence="low",
                         content="Möglicherweise ist die Systemlandschaft fragmentiert (Deutung von Claude).")
            llm = FakeLlm([{"changes": [change("E1"), hyp], "notes": None}])
            planned = reclassify.reclassify_project(project, fast_config(), llm)
            self.assertFalse(planned.written)
            self.assertEqual((project / "knowledge.md").read_text(encoding="utf-8"), KNOWLEDGE)

            applied = reclassify.reclassify_project(project, fast_config(), FakeLlm([]), apply=True)
            text = (project / "knowledge.md").read_text(encoding="utf-8")
            self.assertTrue(applied.written)
            self.assertEqual(applied.by_type, {"fact": 1, "hypothesis": 1})
            self.assertIn("- **[Fakt]** Laut Claude-Auswertung des ERP-Systems", text)
            self.assertIn("Konfidenz: mittel", text)
            self.assertIn("- **[Hypothese]** Möglicherweise", text)
            for prov in ("message_ids=m1", "message_ids=m3", "message_ids=m4", "hash=aaa"):
                self.assertIn(prov, text)
            self.assertIn("Kim Beispiel berichtet", text)  # unberuehrt
            backup = (project / ".polaris" / "backups").glob("knowledge.md.*-before-reclassify")
            self.assertEqual(next(backup).read_text(encoding="utf-8"), KNOWLEDGE)
            self.assertFalse((project / ".polaris" / "reclassify-plan.json").exists())

    def test_unsafe_changes_are_rejected(self):
        with TempProject() as tp:
            project = self._project(tp)
            bad = [
                change("E1", content="Laut Claude fehlen viele Miteigentumsanteile."),  # Zahlen weg
                change("E4", scope="company_wide"),  # Scope steigt
                change("E2"),  # keine Chat-Quelle
            ]
            result = reclassify.reclassify_project(project, fast_config(), FakeLlm([{"changes": bad, "notes": None}]))
            reasons = {r["id"]: r["reason"] for r in result.plan["rejected"]}
            self.assertEqual(result.changed, 0)
            self.assertIn("Zahlen", reasons["E1"])
            self.assertIn("Scope", reasons["E4"])
            self.assertIn("ID", reasons["E2"])

    def test_apply_refuses_if_knowledge_changed(self):
        with TempProject() as tp:
            project = self._project(tp)
            reclassify.reclassify_project(project, fast_config(),
                                          FakeLlm([{"changes": [change("E1")], "notes": None}]))
            (project / "knowledge.md").write_text(KNOWLEDGE + "\n## Neu\n- x\n", encoding="utf-8")
            with self.assertRaises(reclassify.ReclassifyError):
                reclassify.reclassify_project(project, fast_config(), FakeLlm([]), apply=True)

    def test_apply_without_plan_fails(self):
        with TempProject() as tp:
            project = self._project(tp)
            with self.assertRaises(reclassify.ReclassifyError):
                reclassify.reclassify_project(project, fast_config(), FakeLlm([]), apply=True)


if __name__ == "__main__":
    unittest.main()
