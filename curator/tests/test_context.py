"""Kontext je Modellaufruf (context.py, seit 2026-09-30)."""

import unittest

from conftest_helpers import FakeLlm, TempProject, fast_config, make_change

from polaris_curator import context, layout, pipeline


def _knowledge(n, extra=""):
    lines = ["# Projektwissen", "<!-- polaris-ids: K=%d -->" % n, "", "## Status"]
    for i in range(1, n + 1):
        lines += [f"- **[Fakt]** Eintrag {i} ueber Thema{i} mit etwas Fuelltext " + "x" * 300,
                  "  - Scope: lokal · Evidenzgrad: Einzelbeleg · Konfidenz: mittel",
                  f"  - ID: K{i}",
                  "  <!-- polaris-source: type=chatgpt message_ids=" + ",".join(f"m{j}" for j in range(20)) + " -->"]
    return "\n".join(lines) + extra + "\n"


class TestContext(unittest.TestCase):
    def test_provenance_is_never_sent(self):
        ctx = context.build(_knowledge(3), "", "", "egal")
        self.assertNotIn("polaris-source", ctx.knowledge)
        self.assertNotIn("polaris-ids", ctx.knowledge)
        self.assertIn("ID: K1", ctx.knowledge)
        self.assertEqual(ctx.index_only, set())

    def test_large_file_index_plus_relevant_full_text(self):
        text = _knowledge(200)
        ctx = context.build(text, "", "", "Neues zu Thema17 und sonst nichts", full_chars=40000, newest=5,
                            index_cap=0)  # 0 = altes Verhalten: jeder Eintrag im Verzeichnis
        self.assertLess(len(ctx.knowledge), len(context.strip_provenance(text)))
        self.assertNotIn("K17", ctx.index_only)          # passend -> Volltext
        self.assertNotIn("K200", ctx.index_only)         # neueste -> Volltext
        self.assertIn("K3", ctx.index_only)              # uebrige -> Verzeichnis
        self.assertIn("- K3 (nur Verzeichnis): **[Fakt]** Eintrag 3", ctx.knowledge)
        for i in range(1, 201):                          # jede Nummer bleibt sichtbar
            self.assertIn(f"K{i}", ctx.knowledge)

    def test_decisions_index(self):
        dec = "# Entscheidungen\n" + "".join(
            f"\n## 2026-09-{i % 28 + 1:02d} – Entscheidung {i} zu Punkt{i}\n**ID**\nD{i}\n\n**Entscheidung**\n{'y' * 400}\n"
            for i in range(1, 80))
        ctx = context.build("", dec, "", "Punkt5 wird geaendert", full_chars=8000, newest=3, index_cap=0)
        self.assertNotIn("D5", ctx.index_only)
        self.assertIn("D10", ctx.index_only)
        self.assertIn("- D10 (nur Verzeichnis): 2026-09-11 – Entscheidung 10", ctx.decisions)

    def test_index_only_entry_is_never_replaced(self):
        tp = TempProject()
        with tp as project:
            layout.knowledge_path(project).write_text(_knowledge(200), encoding="utf-8")
            tp.write_message("msg-0000000001", content="Neues zu Thema17")
            cfg = fast_config()
            cfg.full_context_chars = 40000
            change = make_change(knowledge_updates=[
                {"section": "Status", "content": "Ersatz fuer 3", "mode": "replace", "replaces_id": "K3"},
                {"section": "Status", "content": "Ersatz fuer 17", "mode": "replace", "replaces_id": "K17"}])
            llm = FakeLlm([change])
            report = pipeline.run_project(project, cfg, llm_fn=llm)
            k = layout.knowledge_path(project).read_text()
            self.assertIn("Eintrag 3 ueber", k)                  # nicht abgeloest
            self.assertNotIn("Eintrag 17 ueber", k)              # sichtbar -> abgeloest
            self.assertTrue(any("K3 nur im Verzeichnis" in w for w in report.warnings))
            self.assertNotIn("polaris-source", llm.calls[0].split("=== QUELLINHALT")[0])

    def test_default_limits_never_trigger_tail_cut(self):
        """Review 2026-09-30, M1: Verzeichnis zaehlt zum Budget."""
        from polaris_curator import config as cfgmod, pipeline
        d = cfgmod.CuratorConfig()
        text = _knowledge(300)
        ctx = context.build(text, "", "", "Thema7", full_chars=d.full_context_chars, max_chars=d.max_curated_context_chars)
        self.assertLessEqual(len(ctx.knowledge), d.max_curated_context_chars)
        self.assertEqual(pipeline._tail_at_boundary(ctx.knowledge, d.max_curated_context_chars), ctx.knowledge)

    def test_compounds_hyphens_and_inflection_match(self):
        entries = [f"- **[Fakt]** Anderes Thema Nummer {i} " + "y" * 200 + f"\n  - ID: K{i}" for i in range(1, 60)]
        entries += ["- **[Fakt]** Testinventur ergab Bewertungsdifferenz\n  - ID: K60",
                    "- **[Fakt]** Die PZE-Einführung ist geplant\n  - ID: K61",
                    "- **[Fakt]** Stückliste wird ueberarbeitet\n  - ID: K62"]
        text = "# P\n\n## S\n" + "\n".join(entries[::-1]) + "\n"
        for q, want in (("Beim Inventurtest gab es Bewertungsdifferenzen", "K60"),
                        ("PZE Einfuehrung verschiebt sich", "K61"), ("Stücklistenlogik", "K62")):
            ctx = context.build(text, "", "", q, full_chars=9000, newest=0)
            self.assertNotIn(want, ctx.index_only, q)



class TestIndexCap(unittest.TestCase):
    """Verzeichnis gedeckelt (seit 2026-10-15): Kontext waechst nicht mehr mit der Datei."""

    def test_index_is_capped_and_context_stays_bounded(self):
        sizes = []
        for n in (300, 1200):
            ctx = context.build(_knowledge(n), "", "", "Neues zu Thema17", full_chars=40000, newest=5)
            sizes.append(len(ctx.knowledge))
            self.assertNotIn("K17", ctx.index_only)               # passend -> Volltext
            self.assertNotIn(f"K{n}", ctx.index_only)             # neueste -> Volltext
            self.assertGreater(ctx.hidden, 0)
            self.assertIn("weitere Einträge in diesem Abschnitt nicht gezeigt)", ctx.knowledge)
            self.assertLessEqual(len(ctx.knowledge), 40000 + 500)
        self.assertLess(abs(sizes[1] - sizes[0]), 2000)           # 4x Eintraege, gleicher Kontext

    def test_hidden_entries_are_never_replaceable(self):
        ctx = context.build(_knowledge(1200), "", "", "Thema17", full_chars=40000, newest=5)
        shown = {f"K{i}" for i in range(1, 1201) if f"- K{i} " in ctx.knowledge or f"ID: K{i}\n" in ctx.knowledge}
        hidden = {f"K{i}" for i in range(1, 1201)} - shown
        self.assertTrue(hidden)
        self.assertTrue(hidden <= ctx.index_only)

    def test_index_prefers_matching_entries(self):
        entries = [f"- **[Fakt]** Allerlei Nummer {i} " + "y" * 3000 + f"\n  - ID: K{i}" for i in range(1, 400)]
        entries.append("- **[Fakt]** Kundenportal Migration verschoben " + "y" * 9000 + "\n  - ID: K400")
        text = "# P\n\n## S\n" + "\n".join(entries) + "\n"
        ctx = context.build(text, "", "", "Kundenportal Migration", full_chars=8000, newest=0, index_cap=0.05)
        # zu gross fuer den Volltext, aber passend -> im (knappen) Verzeichnis
        self.assertIn("- K400 (nur Verzeichnis)", ctx.knowledge)

    def test_index_lines_are_short(self):
        e = context._Entry("S", ["- **[Fakt]** " + "wort " * 80, "  - ID: K1"], "K1", 0)
        self.assertLessEqual(len(context._index_line(e, False)), len("- K1 (nur Verzeichnis): ") + 102)

    def test_status_entries_preferred_for_full_text(self):
        text = ("# P\n\n## Relevante Erkenntnisse\n- **[Fakt]** TP08 Kennzahlen Abstimmung " + "a" * 900
                + "\n  - ID: K1\n\n## Aktueller Status\n- **[Fakt]** TP08 Kennzahlen Abstimmung " + "b" * 900
                + "\n  - ID: K2\n" + "".join(f"\n- **[Fakt]** Fueller {i} " + "c" * 900 + f"\n  - ID: K{i}\n"
                                              for i in range(3, 60)))
        ctx = context.build(text, "", "", "TP08 Kennzahlen Abstimmung", full_chars=6000, newest=0)
        self.assertNotIn("K2", ctx.index_only)

    def test_config_index_cap(self):
        from polaris_curator import config as cfgmod
        self.assertEqual(cfgmod.CuratorConfig().index_cap, 0.25)

    def test_prompts_carry_status_and_hidden_rules(self):
        from polaris_curator import compact, prompts
        self.assertIn("EINEN Eintrag mit dem aktuellen Stand", prompts.SYSTEM_INSTRUCTIONS)
        self.assertIn("nicht gezeigt", prompts.SYSTEM_INSTRUCTIONS)
        self.assertIn('Abschnitt "Aktueller Status"', compact.INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
