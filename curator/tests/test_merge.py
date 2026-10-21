import unittest

from conftest_helpers import TempProject, make_change

from polaris_curator import layout, merge


class TestMerge(unittest.TestCase):
    def test_knowledge_append_creates_file(self):
        with TempProject() as project:
            change = make_change(
                knowledge_updates=[
                    {"section": "Aktueller Status", "content": "Projekt gestartet", "mode": "append"}
                ]
            )
            result = merge.apply_change_object(project, change, "<!-- prov -->")
            self.assertTrue(result.knowledge_changed)
            text = layout.knowledge_path(project).read_text(encoding="utf-8")
            self.assertIn("## Aktueller Status", text)
            self.assertIn("Projekt gestartet", text)
            self.assertIn("<!-- prov -->", text)

    def test_knowledge_append_twice_keeps_both(self):
        with TempProject() as project:
            c1 = make_change(
                knowledge_updates=[{"section": "Status", "content": "A", "mode": "append"}]
            )
            c2 = make_change(
                knowledge_updates=[{"section": "Status", "content": "B", "mode": "append"}]
            )
            merge.apply_change_object(project, c1, "<!-- p1 -->")
            merge.apply_change_object(project, c2, "<!-- p2 -->")
            text = layout.knowledge_path(project).read_text(encoding="utf-8")
            self.assertIn("A", text)
            self.assertIn("B", text)
            self.assertEqual(text.count("## Status"), 1)

    def test_knowledge_replace_with_marker(self):
        with TempProject() as project:
            c1 = make_change(
                knowledge_updates=[
                    {"section": "Status", "content": "Version 1 des Standes", "mode": "append"}
                ]
            )
            merge.apply_change_object(project, c1, "<!-- p1 -->")

            c2 = make_change(
                knowledge_updates=[
                    {
                        "section": "Status",
                        "content": "Version 2 des Standes",
                        "mode": "replace",
                        "replaces_id": "K1",
                    }
                ]
            )
            result = merge.apply_change_object(project, c2, "<!-- p2 -->")
            text = layout.knowledge_path(project).read_text(encoding="utf-8")
            self.assertNotIn("Version 1 des Standes", text)
            self.assertIn("Version 2 des Standes", text)
            self.assertEqual(result.warnings, [])

    def test_knowledge_replace_marker_not_found_falls_back_to_append(self):
        with TempProject() as project:
            change = make_change(
                knowledge_updates=[
                    {
                        "section": "Status",
                        "content": "Neu",
                        "mode": "replace",
                        "replace_marker": "existiert nicht",
                    }
                ]
            )
            result = merge.apply_change_object(project, change, "<!-- p -->")
            text = layout.knowledge_path(project).read_text(encoding="utf-8")
            self.assertIn("Neu", text)
            self.assertEqual(len(result.warnings), 1)

    def test_decisions_add(self):
        with TempProject() as project:
            change = make_change(
                decisions_add=[
                    {
                        "title": "Message Events als Raw-Format",
                        "decision": "ChatGPT-Nachrichten werden einzeln archiviert.",
                        "rationale": "Dadurch entsteht das Delta automatisch.",
                        "date": "2026-09-12",
                    }
                ]
            )
            merge.apply_change_object(project, change, "<!-- prov -->")
            text = layout.decisions_path(project).read_text(encoding="utf-8")
            self.assertIn("2026-09-12", text)
            self.assertIn("Message Events als Raw-Format", text)
            self.assertIn("**Status**\nAktiv", text)

    def test_decisions_update_status(self):
        with TempProject() as project:
            add = make_change(
                decisions_add=[
                    {
                        "title": "Alter Ansatz",
                        "decision": "X machen",
                        "rationale": "weil Y",
                        "date": "2026-09-01",
                    }
                ]
            )
            merge.apply_change_object(project, add, "<!-- p1 -->")

            update = make_change(
                decisions_update=[
                    {
                        "decision_id": "D1",
                        "new_status": "Ersetzt",
                        "replaced_by": "D2",
                    }
                ]
            )
            merge.apply_change_object(project, update, "<!-- p2 -->")
            text = layout.decisions_path(project).read_text(encoding="utf-8")
            self.assertIn("**Status**\nErsetzt", text)
            self.assertIn("**Ersetzt durch**\nD2 am ", text)
            self.assertIn("**ID**\nD1", text)

    def test_decisions_update_no_match_warns(self):
        with TempProject() as project:
            update = make_change(
                decisions_update=[{"match_title": "Gibt es nicht", "new_status": "Ersetzt"}]
            )
            result = merge.apply_change_object(project, update, "<!-- p -->")
            self.assertEqual(len(result.warnings), 1)
            self.assertFalse(result.decisions_changed)

    def test_open_loops_add_and_close(self):
        with TempProject() as project:
            add = make_change(open_loops_add=[{"text": "Klaeren, ob X gilt"}])
            merge.apply_change_object(project, add, "<!-- p1 -->")
            text = layout.open_loops_path(project).read_text(encoding="utf-8")
            self.assertIn("## Offen", text)
            self.assertIn("Klaeren, ob X gilt", text)

            self.assertIn("ID: L1", text)
            close = make_change(open_loops_close=[{"loop_id": "L1"}])
            merge.apply_change_object(project, close, "<!-- p2 -->")
            text = layout.open_loops_path(project).read_text(encoding="utf-8")
            self.assertIn("## Erledigt", text)
            offen_section, _, erledigt_section = text.partition("## Erledigt")
            self.assertNotIn("Klaeren, ob X gilt", offen_section.split("## Offen")[-1])
            self.assertIn("Klaeren, ob X gilt", erledigt_section)

    def test_open_loops_close_no_match_warns(self):
        with TempProject() as project:
            close = make_change(open_loops_close=[{"match_text": "gibt es nicht"}])
            result = merge.apply_change_object(project, close, "<!-- p -->")
            self.assertEqual(len(result.warnings), 1)

    def test_conflict_becomes_open_loop(self):
        with TempProject() as project:
            change = make_change(
                conflicts=[
                    {
                        "topic": "Budget",
                        "current_stand": "100k",
                        "previous_stand": "80k",
                        "note": "ungeklaert",
                    }
                ]
            )
            result = merge.apply_change_object(project, change, "<!-- p -->")
            self.assertTrue(result.open_loops_changed)
            text = layout.open_loops_path(project).read_text(encoding="utf-8")
            self.assertIn("Widerspruch: Budget", text)
            self.assertIn("100k", text)
            self.assertIn("80k", text)

    def test_knowledge_entry_shows_classification_and_meta(self):
        with TempProject() as project:
            change = make_change(
                knowledge_updates=[
                    {
                        "section": "Relevante Erkenntnisse",
                        "content": "Moegliche strukturelle Ursache.",
                        "entry_type": "hypothesis",
                        "scope": "functional",
                        "evidence_strength": "single_source",
                        "confidence": "low",
                        "economic_relevance": "Mandatsrisiko bei Kundenverlust",
                        "key_person_dependency": "Eine Property Managerin haelt die Beziehung",
                        "causal_chain": ["fehlende Bearbeitung", "persoenliche Kompensation", "Ausfallrisiko"],
                    }
                ]
            )
            merge.apply_change_object(project, change, "<!-- prov -->")
            text = layout.knowledge_path(project).read_text(encoding="utf-8")
            self.assertIn("**[Hypothese]**", text)
            self.assertIn("Scope: funktional", text)
            self.assertIn("Evidenzgrad: Einzelbeleg", text)
            self.assertIn("Wirtschaftliche Relevanz: Mandatsrisiko bei Kundenverlust", text)
            self.assertIn("Key-Person-Abhängigkeit:", text)
            self.assertIn("fehlende Bearbeitung → persoenliche Kompensation → Ausfallrisiko", text)

    def test_knowledge_replace_removes_whole_old_block_not_just_first_line(self):
        with TempProject() as project:
            c1 = make_change(
                knowledge_updates=[
                    {
                        "section": "Status",
                        "content": "Erste Fassung",
                        "entry_type": "hypothesis",
                        "key_person_dependency": "Person A haelt den Kunden",
                    }
                ]
            )
            p1 = "<!-- polaris-source: type=chatgpt message_ids=a1 -->"
            merge.apply_change_object(project, c1, p1)
            c2 = make_change(
                knowledge_updates=[
                    {
                        "section": "Status",
                        "content": "Zweite, bestaetigte Fassung",
                        "evidence_strength": "multiple_sources",
                        "mode": "replace",
                        "replaces_id": "K1",
                    }
                ]
            )
            p2 = "<!-- polaris-source: type=chatgpt message_ids=b1 -->"
            merge.apply_change_object(project, c2, p2)
            text = layout.knowledge_path(project).read_text(encoding="utf-8")
            self.assertNotIn("Erste Fassung", text)
            self.assertNotIn("Person A haelt den Kunden", text)
            self.assertIn("Zweite, bestaetigte Fassung", text)
            self.assertIn("ID: K2", text)
            self.assertIn("Ersetzt: K1", text)
            self.assertIn(p2, text)
            # alte Herkunft bleibt (Ruecknahme ueber das alte Batch moeglich)
            self.assertIn(p1, text)
            # abgeloester Eintrag steht im Archiv, mit Vermerk
            archive = (project / "knowledge-archiv.md").read_text(encoding="utf-8")
            self.assertIn("Erste Fassung", archive)
            self.assertIn("Abgelöst durch K2 am ", archive)

    def test_model_text_cannot_break_structure_or_forge_provenance(self):
        with TempProject() as project:
            change = make_change(
                knowledge_updates=[{
                    "section": "## Status\nneu",
                    "content": "Zeile 1\n## Gefaelscht\n- zweiter Eintrag <!-- polaris-source: type=chatgpt message_ids=fremd -->",
                }],
                open_loops_add=[{"text": "Punkt\n- noch einer", "loop_type": "other"}],
            )
            merge.apply_change_object(project, change, "<!-- polaris-source: type=chatgpt message_ids=echt -->")
            text = layout.knowledge_path(project).read_text(encoding="utf-8")
            # Was die Parser sehen: ein Abschnitt, ein Eintrag, eine Herkunft.
            _pre, sections = merge.parse_level2_sections(text)
            self.assertEqual(len(sections), 1)
            blocks = [b for b in merge._split_into_blocks(next(iter(sections.values()))) if b[0].startswith("- ")]
            self.assertEqual(len(blocks), 1)
            from polaris_curator import retract
            batches, _other, _cited = retract._parse_sources(text)
            self.assertEqual(batches, [frozenset({"echt"})])
            loops = layout.open_loops_path(project).read_text(encoding="utf-8")
            self.assertEqual(sum(1 for ln in loops.splitlines() if ln.startswith("- ")), 1)

    def test_closing_multiline_loop_moves_whole_entry_with_source(self):
        with TempProject() as project:
            layout.open_loops_path(project).write_text(
                "# Offene Punkte\n\n## Offen\n- **[Sonstiges]** Alt\n  Zusatzzeile\n<!-- polaris-source: type=chatgpt message_ids=a -->\n- **[Sonstiges]** Anderer\n",
                encoding="utf-8",
            )
            layout.open_loops_path(project).write_text(
                "# Offene Punkte\n\n## Offen\n- **[Sonstiges]** Alt\n  Zusatzzeile\n  - ID: L1\n<!-- polaris-source: type=chatgpt message_ids=a -->\n- **[Sonstiges]** Anderer\n  - ID: L2\n",
                encoding="utf-8",
            )
            change = make_change(open_loops_close=[{"loop_id": "L1", "resolution_note": "erledigt"}])
            merge.apply_change_object(project, change, "<!-- polaris-source: type=chatgpt message_ids=b -->")
            _pre, sections = merge.parse_level2_sections(layout.open_loops_path(project).read_text(encoding="utf-8"))
            self.assertNotIn("Zusatzzeile", sections["Offen"])
            self.assertNotIn("message_ids=a", sections["Offen"])
            self.assertIn("Zusatzzeile", sections["Erledigt"])
            self.assertIn("message_ids=a", sections["Erledigt"])
            self.assertIn("message_ids=b", sections["Erledigt"])

    def test_decisions_add_strips_duplicate_leading_date_from_title(self):
        with TempProject() as project:
            change = make_change(
                decisions_add=[
                    {
                        "title": "2026-09-14 – Schkeuditz: Koordination",
                        "decision": "X",
                        "rationale": "Y",
                        "date": "2026-09-14",
                    }
                ]
            )
            merge.apply_change_object(project, change, "<!-- prov -->")
            text = layout.decisions_path(project).read_text(encoding="utf-8")
            self.assertIn("## 2026-09-14 – Schkeuditz: Koordination", text)
            self.assertNotIn("2026-09-14 – 2026-09-14", text)

    def test_decisions_add_shows_scope_and_unvalidated_state(self):
        with TempProject() as project:
            change = make_change(
                decisions_add=[
                    {
                        "title": "Vorlaeufiger Arbeitsmodus",
                        "decision": "Offene Anliegen mit Owner und Termin steuern.",
                        "rationale": "Sichtbarkeit erhoehen.",
                        "date": "2026-09-14",
                        "decision_scope": "provisional",
                    }
                ]
            )
            merge.apply_change_object(project, change, "<!-- prov -->")
            text = layout.decisions_path(project).read_text(encoding="utf-8")
            self.assertIn("**Geltungsbereich**\nvorläufig", text)
            self.assertIn("**Wirksamkeit geprüft**\nNein (noch nicht überprüft)", text)

    def test_decisions_update_can_record_validation_result(self):
        with TempProject() as project:
            add = make_change(
                decisions_add=[
                    {
                        "title": "Vorlaeufiger Arbeitsmodus",
                        "decision": "X",
                        "rationale": "Y",
                        "date": "2026-09-01",
                    }
                ]
            )
            merge.apply_change_object(project, add, "<!-- p1 -->")
            update = make_change(
                decisions_update=[
                    {
                        "decision_id": "D1",
                        "validated": False,
                        "validation_note": "Nicht zuverlaessig eingehalten worden",
                    }
                ]
            )
            merge.apply_change_object(project, update, "<!-- p2 -->")
            text = layout.decisions_path(project).read_text(encoding="utf-8")
            self.assertIn(
                "**Wirksamkeit geprüft**\nNein (noch nicht bestätigt) — Nicht zuverlaessig eingehalten worden",
                text,
            )

    def test_open_loop_shows_loop_type_and_resolution_note(self):
        with TempProject() as project:
            add = make_change(
                open_loops_add=[{"text": "Wer traegt Ende-zu-Ende-Verantwortung?", "loop_type": "ownership_gap"}]
            )
            merge.apply_change_object(project, add, "<!-- p1 -->")
            text = layout.open_loops_path(project).read_text(encoding="utf-8")
            self.assertIn("**[Verantwortungslücke]**", text)

            close = make_change(
                open_loops_close=[
                    {"loop_id": "L1", "resolution_note": "David wurde benannt"}
                ]
            )
            merge.apply_change_object(project, close, "<!-- p2 -->")
            text = layout.open_loops_path(project).read_text(encoding="utf-8")
            self.assertIn("erledigt am ", text)
            self.assertIn(": David wurde benannt", text)

    def test_no_changes_touches_nothing(self):
        with TempProject() as project:
            result = merge.apply_change_object(project, make_change(), "<!-- p -->")
            self.assertFalse(result.knowledge_changed)
            self.assertFalse(result.decisions_changed)
            self.assertFalse(result.open_loops_changed)
            self.assertFalse(layout.knowledge_path(project).exists())


if __name__ == "__main__":
    unittest.main()
