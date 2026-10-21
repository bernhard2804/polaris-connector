"""Aufraeumen gegen Wachstum (seit 2026-09-30): Fruehwarnung, Pruefung
offener Punkte, ueberholte Momentaufnahmen nur mit neuerem Nachfolger."""

import json
import unittest

from conftest_helpers import FakeLlm, TempProject, fast_config

from polaris_curator import compact, context, journal, layout, loops_review, systemhealth


def loops_file(n):
    lines = ["# Offene Punkte", "", "## Offen"]
    for i in range(1, n + 1):
        lines += [f"- **[Sonstiges]** Frage {i}: Wann kommt Lieferung {i}?", f"  - ID: L{i} · offen seit 2026-09-01"]
    return "\n".join(lines) + "\n\n## Erledigt\n"


KNOW = ("# Projektwissen\n\n## Status\n- **[Fakt]** Lieferung 1 kam am 20.09.\n  - ID: K1\n"
        "- **[Fakt]** Lieferung 2 kommt am 05.10.\n  - ID: K2\n")


class TestLoopsReview(unittest.TestCase):
    def _run(self, plan, n=20, dry_run=False):
        tp = TempProject()
        project = tp.__enter__()
        self.addCleanup(tp.__exit__, None, None, None)
        layout.open_loops_path(project).write_text(loops_file(n), encoding="utf-8")
        layout.knowledge_path(project).write_text(KNOW, encoding="utf-8")
        fake = FakeLlm([plan])
        res = loops_review.review(project, fast_config(), fake, dry_run=dry_run)
        return project, res, fake

    def test_closes_with_evidence_and_rejects_without(self):
        plan = {"close": [
            {"loop_id": "L1", "status": "erledigt", "evidence_id": "K1", "reason": "Lieferung 1 kam am 20.09."},
            {"loop_id": "L2", "status": "erledigt", "evidence_id": "K99", "reason": "x"},
            {"loop_id": "L77", "status": "erledigt", "evidence_id": "K1", "reason": "x"}], "notes": None}
        project, res, fake = self._run(plan)
        self.assertEqual(res.closed, ["L1"])
        self.assertEqual(len(res.rejected), 2)
        _pre, secs = __import__("polaris_curator.merge", fromlist=["x"]).parse_level2_sections(
            layout.open_loops_path(project).read_text())
        self.assertIn("Frage 1", secs["Erledigt"])
        self.assertIn("erledigt laut Prüfung (K1): Lieferung 1 kam am 20.09.", secs["Erledigt"])
        self.assertNotIn("Frage 1:", secs["Offen"])
        self.assertIn("Frage 2", secs["Offen"])
        self.assertIn("[L2] **[Sonstiges]** Frage 2", fake.calls[0])
        self.assertTrue(list((layout.polaris_dir(project) / "backups").glob("open-loops.md.*-before-review")))
        self.assertIsNone(journal.existing_path(project))

    def test_emergency_brake(self):
        plan = {"close": [{"loop_id": f"L{i}", "status": "erledigt", "evidence_id": "K1", "reason": "x"}
                          for i in range(1, 12)], "notes": None}
        with self.assertRaises(loops_review.LoopsReviewError):
            self._run(plan, n=20)

    def test_dry_run_writes_nothing(self):
        plan = {"close": [{"loop_id": "L1", "status": "erledigt", "evidence_id": "K1", "reason": "ok"}], "notes": None}
        project, res, _f = self._run(plan, dry_run=True)
        self.assertEqual(res.closed, ["L1"])
        self.assertEqual(layout.open_loops_path(project).read_text(), loops_file(20))

    def test_due(self):
        with TempProject() as project:
            layout.open_loops_path(project).write_text(loops_file(5), encoding="utf-8")
            self.assertFalse(loops_review.due(project, fast_config()))
            layout.open_loops_path(project).write_text(loops_file(20), encoding="utf-8")
            self.assertTrue(loops_review.due(project, fast_config()))
            loops_review.record_run(project, loops_review.ReviewResult())
            self.assertFalse(loops_review.due(project, fast_config()))

    def test_idle_backoff(self):
        from datetime import datetime, timedelta
        with TempProject() as project:
            layout.open_loops_path(project).write_text(loops_file(20), encoding="utf-8")
            cfg = fast_config()
            now = datetime.now()
            loops_review.record_run(project, loops_review.ReviewResult())
            self.assertFalse(loops_review.due(project, cfg, now + timedelta(days=8)))
            self.assertTrue(loops_review.due(project, cfg, now + timedelta(days=15)))
            # Fehlschlag aendert den Zaehler nicht
            loops_review.record_run(project, loops_review.ReviewResult(rejected=["Pruefung fehlgeschlagen: x"]))
            self.assertTrue(loops_review.due(project, cfg, now + timedelta(days=15)))
            loops_review.record_run(project, loops_review.ReviewResult())
            self.assertFalse(loops_review.due(project, cfg, now + timedelta(days=27)))
            loops_review.record_run(project, loops_review.ReviewResult())
            self.assertTrue(loops_review.due(project, cfg, now + timedelta(days=29)))
            loops_review.record_run(project, loops_review.ReviewResult(closed=["L1"]))
            self.assertTrue(loops_review.due(project, cfg, now + timedelta(days=8)))
        self.assertEqual(loops_review.idle_streak({"last_run": "x", "closed": []}), 1)
        self.assertEqual(loops_review.idle_streak({"last_run": "x", "closed": ["L1"]}), 0)


class TestOutdatedSnapshots(unittest.TestCase):
    def test_only_archived_with_newer_successor(self):
        text = ("# W\n\n## S\n- **[Fakt]** Stand 11.09.: Test geplant\n  - ID: K1\n"
                "- **[Fakt]** Stand 25.09.: Test durchgefuehrt\n  - ID: K2\n")
        _p, _s, entries = compact.parse_entries(text)
        plan = {"merges": [], "superseded": [
            {"id": "E2", "superseded_by": "E1", "reason": "falsch herum"},
            {"id": "E1", "superseded_by": None, "reason": "alt"}], "notes": None}
        dropped = compact.drop_bad_superseded(plan, entries)
        self.assertEqual(len(dropped), 2)
        self.assertEqual(plan["superseded"], [])
        plan = {"merges": [], "superseded": [{"id": "E2", "superseded_by": "E2", "reason": "selbst"}], "notes": None}
        self.assertEqual(len(compact.drop_bad_superseded(plan, entries)), 1)
        plan = {"merges": [], "superseded": [{"id": "E1", "superseded_by": "E2", "reason": "neuer Stand"}], "notes": None}
        self.assertEqual(compact.drop_bad_superseded(plan, entries), [])
        self.assertEqual(len(plan["superseded"]), 1)


class TestEarlyWarning(unittest.TestCase):
    def test_index_share_warns_before_budget_is_eaten(self):
        many = "# P\n\n## S\n" + "".join(f"- **[Fakt]** Eintrag {i} " + "z" * 150 + f"\n  - ID: K{i}\n" for i in range(1, 1200))
        self.assertGreater(context.index_share(many, 80000, index_cap=0), 0.5)   # ohne Deckel
        self.assertLess(context.index_share(many, 80000), 0.5)                  # mit Deckel (seit 2026-10-15)
        self.assertEqual(context.index_share("# P\n\n## S\n- a\n  - ID: K1\n", 80000), 0.0)
        with TempProject() as project:
            layout.knowledge_path(project).write_text(many, encoding="utf-8")
            c = systemhealth.check_curation({"p": project})
            self.assertNotIn("Verzeichnis belegt", str(c))
            (project / ".polaris").mkdir(exist_ok=True)
            (project / ".polaris" / "curator-config.yaml").write_text("context:\n  index_cap: 0\n", encoding="utf-8")
            c = systemhealth.check_curation({"p": project})
            self.assertEqual(c["level"], "warn")
            self.assertIn("Verzeichnis belegt", c["detail"])


if __name__ == "__main__":
    unittest.main()
