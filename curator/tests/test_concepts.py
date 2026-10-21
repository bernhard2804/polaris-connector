"""Die drei uebernommenen Konzepte (2026-09-29):
1. Aenderungen ueber die Nummer des Eintrags statt ueber eine Textmarke,
2. "Gültig seit" / "Abgelöst durch" statt Ueberschreiben,
3. Herkunft je Aussage statt je Tagesbatch."""

import json
import unittest

from conftest_helpers import FakeLlm, TempProject, fast_config, make_change

from polaris_curator import change_schema, ids, layout, merge, pipeline, retract, state as state_mod


def ku(content, **kw):
    return {"section": kw.pop("section", "Status"), "content": content, **kw}


class TestNumbers(unittest.TestCase):
    def test_new_entries_get_numbers_and_valid_from(self):
        with TempProject() as project:
            merge.apply_change_object(project, make_change(
                knowledge_updates=[ku("Eins"), ku("Zwei")],
                decisions_add=[{"title": "T", "decision": "D", "rationale": "R", "date": "2026-09-01"}],
                open_loops_add=[{"text": "Offen?"}],
            ), "<!-- polaris-source: type=document path=x hash=1 -->")
            k = layout.knowledge_path(project).read_text()
            self.assertIn("ID: K1 · Gültig seit: ", k)
            self.assertIn("ID: K2 · Gültig seit: ", k)
            self.assertIn("**ID**\nD1", layout.decisions_path(project).read_text())
            self.assertIn("ID: L1 · offen seit ", layout.open_loops_path(project).read_text())

    def test_replace_by_number_moves_old_entry_to_archive_even_across_sections(self):
        with TempProject() as project:
            merge.apply_change_object(project, make_change(knowledge_updates=[
                ku("Miete 1.200 EUR", section="Kennzahlen"), ku("Kaution drei Monatsmieten", section="Vertrag")]), "<!-- p1 -->")
            res = merge.apply_change_object(project, make_change(knowledge_updates=[
                ku("Kaution zwei Monatsmieten (korrigiert)", section="Kennzahlen", mode="replace", replaces_id="K2")]),
                "<!-- p2 -->")
            k = layout.knowledge_path(project).read_text()
            self.assertIn("Miete 1.200 EUR", k)  # der andere bleibt unangetastet
            self.assertNotIn("drei Monatsmieten", k)
            self.assertIn("ID: K3", k)
            self.assertIn("Ersetzt: K2", k)
            archive = (project / "knowledge-archiv.md").read_text()
            self.assertIn("drei Monatsmieten", archive)
            self.assertIn("Abgelöst durch K3 am ", archive)
            self.assertEqual(res.warnings, [])

    def test_unknown_number_never_guesses(self):
        with TempProject() as project:
            merge.apply_change_object(project, make_change(knowledge_updates=[ku("Bestehend")]), "<!-- p1 -->")
            res = merge.apply_change_object(project, make_change(knowledge_updates=[
                ku("Neu", mode="replace", replaces_id="K99")]), "<!-- p2 -->")
            k = layout.knowledge_path(project).read_text()
            self.assertIn("Bestehend", k)
            self.assertIn("Neu", k)
            self.assertTrue(any("K99 nicht gefunden" in w for w in res.warnings))

    def test_replace_without_number_is_appended_not_rejected(self):
        change = make_change(knowledge_updates=[ku("x", mode="replace"), ku("y")])
        change_schema.validate_change_object(change)  # verwirft nicht die ganze Antwort
        with TempProject() as project:
            res = merge.apply_change_object(project, change, "<!-- p -->")
            k = layout.knowledge_path(project).read_text()
            self.assertIn("** x\n", k)
            self.assertIn("** y\n", k)
            self.assertTrue(any("ohne replaces_id" in w for w in res.warnings))

    def test_decision_and_loop_by_number(self):
        with TempProject() as project:
            merge.apply_change_object(project, make_change(
                decisions_add=[{"title": "A", "decision": "a", "rationale": "r", "date": "2026-09-01"},
                               {"title": "B", "decision": "b", "rationale": "r", "date": "2026-09-02"}],
                open_loops_add=[{"text": "Frage eins"}, {"text": "Frage zwei"}]), "<!-- p1 -->")
            res = merge.apply_change_object(project, make_change(
                decisions_update=[{"decision_id": "D2", "new_status": "Abgelöst", "replaced_by": "D1"},
                                  {"decision_id": "D9", "note": "x"}],
                open_loops_close=[{"loop_id": "L2", "resolution_note": "geklärt"}]), "<!-- p2 -->")
            d = layout.decisions_path(project).read_text()
            _pre, secs = merge.parse_level2_sections(d)
            b = next(v for h, v in secs.items() if h.endswith("B"))
            self.assertIn("**Status**\nAbgelöst", b)
            self.assertIn("**Ersetzt durch**\nD1 am ", b)
            a = next(v for h, v in secs.items() if h.endswith("A"))
            self.assertIn("**Status**\nAktiv", a)
            _pre, loops = merge.parse_level2_sections(layout.open_loops_path(project).read_text())
            self.assertIn("Frage zwei", loops["Erledigt"])
            self.assertIn("Frage eins", loops["Offen"])
            self.assertTrue(any("D9 nicht gefunden" in w for w in res.warnings))

    def test_existing_entries_are_numbered_once_with_backup(self):
        tp = TempProject()
        with tp as project:
            legacy = ("# Projektwissen\n\n## Status\n- **[Fakt]** Alt eins\n  - Scope: lokal\n"
                      "  <!-- polaris-source: type=chatgpt message_ids=a -->\n- **[Fakt]** Alt zwei\n"
                      "  <!-- polaris-source: type=chatgpt message_ids=b -->\n")
            layout.knowledge_path(project).write_text(legacy, encoding="utf-8")
            layout.decisions_path(project).write_text("# Entscheidungen\n\n## 2026-09-01 – X\n**Entscheidung**\nx\n", encoding="utf-8")
            pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([]))
            k = layout.knowledge_path(project).read_text()
            self.assertIn("ID: K1", k)
            self.assertIn("ID: K2", k)
            self.assertIn("Alt eins", k)
            self.assertEqual(k.count("<!-- polaris-source"), 2)
            self.assertIn("**ID**\nD1", layout.decisions_path(project).read_text())
            self.assertTrue(list((layout.polaris_dir(project) / "backups").glob("knowledge.md.*-before-ids")))
            before = k
            pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([]))
            self.assertEqual(layout.knowledge_path(project).read_text(), before)  # idempotent

    def test_merged_entry_keeps_first_number_and_finds_the_others(self):
        from polaris_curator import compact
        with TempProject() as project:
            merge.apply_change_object(project, make_change(knowledge_updates=[ku("Doppelt A"), ku("Doppelt B")]), "<!-- polaris-source: type=chatgpt message_ids=a -->")
            plan = {"merges": [{"ids": ["E1", "E2"], "entry_type": "fact", "scope": "local",
                                "evidence_strength": "single_source", "confidence": "medium", "content": "Doppelt",
                                "economic_relevance": None, "operating_model_relevance": None,
                                "key_person_dependency": None, "causal_chain": None, "reason": "dup"}],
                    "superseded": [], "notes": None}
            text, _arch = compact.apply_plan(layout.knowledge_path(project).read_text(), plan)
            self.assertIn("ID: K1", text)
            self.assertIn("Zusammengeführt: K2", text)
            layout.knowledge_path(project).write_text(text, encoding="utf-8")
            res = merge.apply_change_object(project, make_change(knowledge_updates=[
                ku("Doppelt, präzisiert", mode="replace", replaces_id="K2")]), "<!-- p2 -->")
            self.assertEqual(res.warnings, [])
            self.assertNotIn("- **[Fakt]** Doppelt\n", layout.knowledge_path(project).read_text())


class TestReviewFindings(unittest.TestCase):
    def test_numbers_are_never_reused_after_merge_or_removal(self):
        from polaris_curator import compact
        with TempProject() as project:
            merge.apply_change_object(project, make_change(knowledge_updates=[ku("A"), ku("B"), ku("C")]),
                                      "<!-- polaris-source: type=chatgpt message_ids=a -->")
            k = layout.knowledge_path(project).read_text()
            plan = {"merges": [{"ids": ["E2", "E3"], "entry_type": "fact", "scope": "local",
                                "evidence_strength": "single_source", "confidence": "medium", "content": "BC",
                                "economic_relevance": None, "operating_model_relevance": None,
                                "key_person_dependency": None, "causal_chain": None, "reason": "dup"}],
                    "superseded": [], "notes": None}
            text, _a = compact.apply_plan(k, plan)
            # K3 ist nur noch als Verweis da; zusaetzlich loescht jemand K2 von Hand
            text = "\n".join(ln for ln in text.splitlines() if "Zusammengeführt" not in ln and "] BC" not in ln)
            layout.knowledge_path(project).write_text(text + "\n", encoding="utf-8")
            merge.apply_change_object(project, make_change(knowledge_updates=[ku("Neu")]), "<!-- p -->")
            k2 = layout.knowledge_path(project).read_text()
            self.assertIn("ID: K4", k2)
            self.assertIn("<!-- polaris-ids: K=4 -->", k2)

    def test_number_only_counts_as_own_line_outside_code(self):
        blk = ["- **[Fakt]** Kunden-ID: K7 laut Vertrag", "  ```", "  - ID: K9", "  ```",
               "  <!-- polaris-source: x -->"]
        self.assertIsNone(ids.block_id(blk))
        text = "# Projektwissen\n\n## S\n" + "\n".join(blk) + "\n"
        new, nxt = ids.ensure_block_ids(text, "K", 1)
        self.assertIn("  - ID: K1\n  <!-- polaris-source", new)
        self.assertEqual(nxt, 2)

    def test_numbering_only_inserts_lines(self):
        legacy = ("# Projektwissen\n\nVorwort von Hand.\n\n## Status\n- Eins\n  - Scope: lokal\n\n"
                  "### Notizen von Hand\nfreier Text\n\n## Status\n- Zwei (doppelte Überschrift)\n\n"
                  "## Leer\n\n## Code\n```\n- keine Liste\n```\n")
        new, nxt = ids.ensure_block_ids(legacy, "K", 1)
        self.assertEqual([ln for ln in new.splitlines() if "- ID:" not in ln], legacy.splitlines())
        self.assertEqual(nxt, 3)
        self.assertIn("- Eins\n  - Scope: lokal\n  - ID: K1\n", new)
        dec = "# Entscheidungen\n\n## 2026-09-01 – X\n**Entscheidung**\na\n\n## 2026-09-01 – X\n**Entscheidung**\nb\n"
        new_d, nd = ids.ensure_decision_ids(dec, 1)
        self.assertEqual(nd, 3)
        self.assertIn("X\n**ID**\nD1\n\n**Entscheidung**\na", new_d)
        self.assertIn("X\n**ID**\nD2\n\n**Entscheidung**\nb", new_d)

    def test_duplicate_decision_headings_stay_separate(self):
        with TempProject() as project:
            layout.decisions_path(project).write_text(
                "# Entscheidungen\n\n## 2026-09-01 – X\n**Status**\nAktiv\n\n"
                "## 2026-09-01 – X\n**Status**\nAktiv\n", encoding="utf-8")
            pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([]))
            merge.apply_change_object(project, make_change(decisions_add=[
                {"title": "Y", "decision": "y", "rationale": "r", "date": "2026-09-02"}]), "<!-- p -->")
            res = merge.apply_change_object(project, make_change(
                decisions_update=[{"decision_id": "D2", "new_status": "Abgelöst"}]), "<!-- p -->")
            self.assertEqual(res.warnings, [])
            _pre, secs = merge.parse_level2_sections(layout.decisions_path(project).read_text())
            self.assertIn("Abgelöst", secs["2026-09-01 – X (2)"])
            self.assertNotIn("Abgelöst", secs["2026-09-01 – X"])

    def test_replace_finds_entry_with_code_block(self):
        with TempProject() as project:
            layout.knowledge_path(project).write_text(
                "# Projektwissen\n\n## S\n- Eins\n  - ID: K1\n- Zwei mit Code\n```\n- keine Liste\n```\n"
                "  - ID: K2\n  <!-- p0 -->\n", encoding="utf-8")
            res = merge.apply_change_object(project, make_change(knowledge_updates=[
                ku("Zwei neu", mode="replace", replaces_id="K2", section="S")]), "<!-- p1 -->")
            self.assertEqual(res.warnings, [])
            self.assertNotIn("Zwei mit Code", layout.knowledge_path(project).read_text())

    def test_replacing_same_number_twice_follows_the_chain(self):
        with TempProject() as project:
            merge.apply_change_object(project, make_change(knowledge_updates=[ku("Alt")]), "<!-- p1 -->")
            res = merge.apply_change_object(project, make_change(knowledge_updates=[
                ku("Neu 1", mode="replace", replaces_id="K1"), ku("Neu 2", mode="replace", replaces_id="K1")]),
                "<!-- p2 -->")
            k = layout.knowledge_path(project).read_text()
            self.assertEqual(res.warnings, [])
            self.assertNotIn("Neu 1", k)
            self.assertIn("Neu 2", k)

    def test_replace_of_already_replaced_number_follows_successor(self):
        with TempProject() as project:
            merge.apply_change_object(project, make_change(knowledge_updates=[ku("Alt")]), "<!-- p1 -->")
            merge.apply_change_object(project, make_change(knowledge_updates=[
                ku("Neu 1", mode="replace", replaces_id="K1")]), "<!-- p2 -->")
            res = merge.apply_change_object(project, make_change(knowledge_updates=[
                ku("Neu 2", mode="replace", replaces_id="K1")]), "<!-- p3 -->")
            k = layout.knowledge_path(project).read_text()
            self.assertEqual(res.warnings, [])
            self.assertNotIn("Neu 1", k)
            self.assertIn("Ersetzt: K2", k)

    def test_stale_number_follows_whole_chain(self):
        with TempProject() as project:
            merge.apply_change_object(project, make_change(knowledge_updates=[ku("v1")]), "<!-- p1 -->")
            for n, rid in ((2, "K1"), (3, "K2"), (4, "K1")):
                res = merge.apply_change_object(project, make_change(knowledge_updates=[
                    ku(f"v{n}", mode="replace", replaces_id=rid)]), f"<!-- p{n} -->")
                self.assertEqual(res.warnings, [])
            k = layout.knowledge_path(project).read_text()
            self.assertIn("** v4\n", k)
            self.assertNotIn("** v3\n", k)

    def test_unclosed_fence_does_not_swallow_entries(self):
        body = "- A\n  ```\n  offen\n- B\n  - ID: K2"
        self.assertEqual(len(merge._split_into_blocks(body)), 2)
        self.assertEqual(ids.block_id(["- B", "  - ID: K2"]), "K2")
        new, _n = ids.ensure_block_ids("# P\n\n## S\n" + body + "\n", "K", 3)
        self.assertIn("  offen\n  - ID: K3\n- B", new)

    def test_compact_keeps_aliases_findable_when_first_entry_unnumbered(self):
        from polaris_curator import compact
        text = ("# Projektwissen\n\n## S\n- **[Fakt]** Handnotiz\n  <!-- polaris-source: type=chatgpt message_ids=a -->\n"
                "- **[Fakt]** B\n  - ID: K1\n  <!-- polaris-source: type=chatgpt message_ids=b -->\n"
                "- **[Fakt]** C\n  - ID: K2\n  <!-- polaris-source: type=chatgpt message_ids=c -->\n")
        plan = {"merges": [{"ids": ["E1", "E2", "E3"], "entry_type": "fact", "scope": "local",
                            "evidence_strength": "single_source", "confidence": "medium", "content": "ABC",
                            "economic_relevance": None, "operating_model_relevance": None,
                            "key_person_dependency": None, "causal_chain": None, "reason": "dup"}],
                "superseded": [], "notes": None}
        out, _a = compact.apply_plan(text, plan)
        self.assertIn("  - ID: K1 · Zusammengeführt: K2", out)

    def test_prose_does_not_inflate_counter(self):
        self.assertEqual(ids.next_number("K", "- **[Fakt]** Kunden-ID: K7000000\n  - ID: K3\n"), 4)
        self.assertIsNone(ids.block_id(["- x", "  - ID: K7-Vertrag"]))

    def test_update_under_duplicate_heading_hits_only_its_decision(self):
        with TempProject() as project:
            layout.decisions_path(project).write_text(
                "# Entscheidungen\n\n## 2026-09-01 – X\n**ID**\nD1\n\n**Status**\nAktiv\n\n"
                "## 2026-09-01 – X\n**ID**\nD2\n\n**Status**\nAktiv\n", encoding="utf-8")
            res = merge.apply_change_object(project, make_change(
                decisions_update=[{"decision_id": "D2", "new_status": "Abgelöst"}]), "<!-- p -->")
            _pre, secs = merge.parse_level2_sections(layout.decisions_path(project).read_text())
            self.assertIn("**Status**\nAktiv", secs["2026-09-01 – X"])
            self.assertIn("**Status**\nAbgelöst", secs["2026-09-01 – X (2)"])
            self.assertEqual(res.warnings, [])

    def test_replace_keeps_hand_written_subsection(self):
        with TempProject() as project:
            merge.apply_change_object(project, make_change(knowledge_updates=[ku("Alt")]), "<!-- p1 -->")
            k = layout.knowledge_path(project).read_text().rstrip("\n") + "\n\n### Meine Notizen\nwichtig\n"
            layout.knowledge_path(project).write_text(k, encoding="utf-8")
            merge.apply_change_object(project, make_change(knowledge_updates=[
                ku("Neu", mode="replace", replaces_id="K1")]), "<!-- p2 -->")
            k2 = layout.knowledge_path(project).read_text()
            self.assertIn("### Meine Notizen\nwichtig", k2)
            self.assertNotIn("Meine Notizen", (project / "knowledge-archiv.md").read_text())

    def test_numbering_failure_does_not_block_curation(self):
        with TempProject() as project:
            layout.knowledge_path(project).write_bytes(b"# Projektwissen\n\n## S\n- kaputt \xff\n")
            report = pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([]))
            self.assertTrue(any("Nummerierung uebersprungen" in w for w in report.warnings))


class TestProvenancePerStatement(unittest.TestCase):
    def test_entry_cites_only_its_messages_and_retract_is_precise(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("good-message-0001", content="Sauberer Inhalt", captured_at="2026-09-01T10:00:00.000Z")
            tp.write_message("bad-message-00001", content="Fremder Inhalt", captured_at="2026-09-01T10:01:00.000Z")
            change = make_change(knowledge_updates=[
                ku("Aus sauberer Nachricht", source_message_ids=["good-message-0001"]),
                ku("Aus fremder Nachricht", source_message_ids=["bad-message-00001"]),
                ku("Ohne Angabe"),
                ku("Mit falscher Angabe", source_message_ids=["erfunden"]),
            ])
            report = pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([change]))
            k = layout.knowledge_path(project).read_text()
            _pre, secs = merge.parse_level2_sections(k)
            blocks = {b[0]: "\n".join(b) for b in merge._split_into_blocks(secs["Status"]) if b[0].startswith("- ")}
            clean = next(v for h, v in blocks.items() if "sauberer" in h)
            self.assertIn("message_ids=good-message-0001 ", clean)
            self.assertIn("batch_ids=good-message-0001,bad-message-00001", clean)  # ganzer Batch bleibt belegt
            whole = next(v for h, v in blocks.items() if "Ohne Angabe" in h)
            self.assertIn("good-message-0001,bad-message-00001", whole)  # Rueckfall: ganzer Batch
            self.assertTrue(any("passen nicht zum Batch" in w for w in report.warnings))

            # Ohne rebuild wird nichts nur nach der Angabe geloescht (sonst
            # verloere falsch Zitiertes sauberes Wissen); gemeldet wird es.
            (project / "raw" / "chatgpt" / "messages" / "bad-message-00001.json").unlink()
            res = retract.retract(project, ["bad-message-00001"], apply=True)
            self.assertEqual(res.full, [])
            self.assertEqual(len(res.cited_only), 1)
            self.assertIn("Aus fremder Nachricht", res.cited_only[0])
            res = retract.retract(project, ["bad-message-00001"], apply=True, rebuild=True)
            k2 = layout.knowledge_path(project).read_text()
            self.assertNotIn("Aus fremder Nachricht", k2)
            self.assertEqual(res.requeued_ids, 1)  # sauberer Teil wird neu aufgebaut

    def test_rebuild_removes_entries_whose_batch_saw_the_foreign_message(self):
        """Das Modell kann falsch zitieren: fuer den Neuaufbau zaehlt der ganze
        Batch, nicht die Angabe (Review 2026-09-29, HIGH)."""
        tp = TempProject()
        with tp as project:
            tp.write_message("good-message-0001", content="Sauberer Inhalt", captured_at="2026-09-01T10:00:00.000Z")
            tp.write_message("bad-message-00001", content="Fremder Inhalt", captured_at="2026-09-01T10:01:00.000Z")
            change = make_change(knowledge_updates=[
                ku("Fremdinhalt, aber falsch zitiert", source_message_ids=["good-message-0001"])])
            pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([change]))
            (project / "raw" / "chatgpt" / "messages" / "bad-message-00001.json").unlink()
            res = retract.retract(project, ["bad-message-00001"], apply=True, rebuild=True)
            self.assertNotIn("falsch zitiert", layout.knowledge_path(project).read_text())
            self.assertEqual(res.requeued_ids, 1)


if __name__ == "__main__":
    unittest.main()
