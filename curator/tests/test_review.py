"""Unabhaengige Pruefung neuer Eintraege (review.py, seit 2026-09-30)."""

import json
import unittest
from unittest.mock import patch

from conftest_helpers import FakeLlm, TempProject, fast_config, make_change

from polaris_curator import layout, llm, pipeline


def ku(content, **kw):
    return {"section": "Status", "content": content, **kw}


class TestReview(unittest.TestCase):
    def _run(self, reviewer_reply=None, reviewer_error=None, model="gpt-x"):
        tp = TempProject()
        project = tp.__enter__()
        self.addCleanup(tp.__exit__, None, None, None)
        tp.write_message("msg-000000000001", content="Die Lieferung kommt vielleicht im Oktober.")
        cfg = fast_config()
        cfg.reviewer_model = "opus"
        seen = {}

        def fake_claude(prompt, m, c, schema=None):
            seen["prompt"] = prompt
            if reviewer_error:
                raise reviewer_error
            return llm.LlmResult(data=reviewer_reply, model="claude:opus", tokens_used=1)

        fake = FakeLlm([make_change(knowledge_updates=[ku("Die Lieferung kommt sicher im Oktober."), ku("Zweiter Punkt")])])
        fake._model = model
        orig = fake.__call__

        def call(prompt, c, images=None, schema=None):
            r = orig(prompt, c, images=images, schema=schema)
            r.model = model
            return r
        with patch.object(llm, "_run_claude", side_effect=fake_claude):
            report = pipeline.run_project(project, cfg, llm_fn=call)
        return project, report, seen

    def test_major_finding_is_marked_not_dropped(self):
        reply = {"findings": [
            {"item": "W1", "verdict": "major", "issue_type": "overstated", "explanation": "Quelle sagt vielleicht, Eintrag sicher."},
            {"item": "W2", "verdict": "ok", "issue_type": "none", "explanation": "passt"}]}
        project, report, seen = self._run(reply)
        k = layout.knowledge_path(project).read_text()
        self.assertIn("sicher im Oktober", k)                       # nicht verworfen
        self.assertIn("  - Prüfhinweis: overstated: Quelle sagt vielleicht", k)
        self.assertEqual(k.count("Prüfhinweis"), 1)
        self.assertIn("vielleicht im Oktober", seen["prompt"])       # ganze Quelle beim Pruefer
        self.assertTrue(any(w.startswith("Prüfhinweis") for w in report.warnings))
        log = json.loads((layout.polaris_dir(project) / "review-findings.json").read_text())
        self.assertEqual(len(log[0]["findings"]), 2)

    def test_reviewer_failure_writes_unreviewed_with_warning(self):
        project, report, _s = self._run(reviewer_error=llm.LlmUnavailableError("limit"))
        self.assertIn("sicher im Oktober", layout.knowledge_path(project).read_text())
        self.assertTrue(any("Prüfung ausgefallen" in w for w in report.warnings))

    def test_no_self_review_when_claude_curated(self):
        project, report, seen = self._run({"findings": []}, model="claude:opus")
        self.assertNotIn("prompt", seen)

    def test_malformed_reviewer_output_never_stops_curation(self):
        project, report, _s = self._run({"findings": ["kaputt", {"item": "W1:", "verdict": "major",
                                                                  "issue_type": "other", "explanation": "x"}]})
        k = layout.knowledge_path(project).read_text()
        self.assertIn("sicher im Oktober", k)
        self.assertIn("Prüfhinweis", k)  # "W1:" wird erkannt, der kaputte Befund uebergangen
        self.assertEqual(report.errors, [])

    def test_breaker_after_unavailable_and_no_content_in_warning(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m-day1-000000001", content="Tag eins Inhalt", captured_at="2026-09-01T10:00:00.000Z")
            tp.write_message("m-day2-000000001", content="Tag zwei Inhalt", captured_at="2026-09-02T10:00:00.000Z")
            cfg = fast_config()
            cfg.reviewer_model = "opus"
            calls = []

            def fake_claude(prompt, m, c, schema=None):
                calls.append(1)
                raise llm.LlmUnavailableError("Claude-Limit")
            fake = FakeLlm([make_change(knowledge_updates=[ku("Geheim A")]), make_change(knowledge_updates=[ku("Geheim B")])])
            with patch.object(llm, "_run_claude", side_effect=fake_claude):
                report = pipeline.run_project(project, cfg, llm_fn=fake)
            self.assertEqual(len(calls), 1)                  # zweiter Batch ohne erneuten Versuch
            self.assertEqual(report.llm_calls_made, 2)
            self.assertIn("Geheim B", layout.knowledge_path(project).read_text())
            self.assertFalse(any("Geheim" in w for w in report.warnings))


if __name__ == "__main__":
    unittest.main()
