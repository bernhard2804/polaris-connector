import json
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from conftest_helpers import FakeLlm, fast_config, make_change

from polaris_curator import integrity, layout, pipeline


def _event(pid, conv, mid, url=None, content="x"):
    return {
        "schema_version": 1,
        "source": "chatgpt",
        "project": {"id": pid, "name": pid},
        "conversation": {"id": conv, "title": "t", "url": url or f"https://chatgpt.com/g/{pid}/c/{conv}"},
        "message": {"id": mid, "id_source": "native", "role": "user", "content": content},
        "captured_at": "2026-09-01T10:00:00.000Z",
    }


class Root:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="polaris-integrity-"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def project(self, name):
        p = self.tmp / name
        (p / "raw" / "chatgpt" / "messages").mkdir(parents=True, exist_ok=True)
        (p / "raw" / "chatgpt" / "conversations").mkdir(parents=True, exist_ok=True)
        return p

    def put(self, project, event, name=None):
        path = project / "raw" / "chatgpt" / "messages" / (name or f"{event['message']['id']}.json")
        path.write_text(json.dumps(event), encoding="utf-8")
        return path

    def conv_index(self, project, conv, ids):
        path = project / "raw" / "chatgpt" / "conversations" / f"{conv}.json"
        path.write_text(json.dumps({"conversation_id": conv, "known_message_ids": ids}), encoding="utf-8")


class TestScan(unittest.TestCase):
    def test_clean_archive_has_no_findings(self):
        with Root() as r:
            abc, lau = r.project("ABC"), r.project("Falke")
            r.put(abc, _event("g-p-abc", "c1", "m1"))
            r.put(lau, _event("g-p-lau", "c2", "m2"))
            r.conv_index(abc, "c1", ["m1"])
            self.assertEqual(integrity.scan({"g-p-abc": abc, "g-p-lau": lau}), [])

    def test_same_id_in_two_projects(self):
        # Vorfall 2026-09-29: ABC-Nachricht zusaetzlich im Falke-Ordner.
        with Root() as r:
            abc, lau = r.project("ABC"), r.project("Falke")
            r.put(abc, _event("g-p-abc", "c1", "m1"))
            r.put(lau, _event("g-p-lau", "c2", "m1"))
            findings = integrity.scan({"g-p-abc": abc, "g-p-lau": lau})
            self.assertEqual([f.kind for f in findings], ["duplicate_id"])
            self.assertEqual(findings[0].projects, ["ABC", "Falke"])
            self.assertTrue(findings[0].blocking)

    def test_revision_in_other_conversation_counts(self):
        with Root() as r:
            abc = r.project("ABC")
            r.put(abc, _event("g-p-abc", "c1", "m1"))
            r.put(abc, _event("g-p-abc", "c2", "m1", content="y"), name="m1.rev2.json")
            kinds = [f.kind for f in integrity.scan({"g-p-abc": abc})]
            self.assertEqual(kinds, ["duplicate_id"])

    def test_revision_in_same_conversation_is_fine(self):
        with Root() as r:
            abc = r.project("ABC")
            r.put(abc, _event("g-p-abc", "c1", "m1"))
            r.put(abc, _event("g-p-abc", "c1", "m1", content="y"), name="m1.rev2.json")
            self.assertEqual(integrity.scan({"g-p-abc": abc}), [])

    def test_project_id_mapped_to_other_folder(self):
        with Root() as r:
            abc, lau = r.project("ABC"), r.project("Falke")
            r.put(lau, _event("g-p-abc", "c1", "m1"))
            kinds = {f.kind for f in integrity.scan({"g-p-abc": abc, "g-p-lau": lau})}
            self.assertIn("foreign_project", kinds)

    def test_url_mismatch(self):
        with Root() as r:
            abc = r.project("ABC")
            r.put(abc, _event("g-p-abc", "c1", "m1", url="https://chatgpt.com/g/g-p-lau-x/c/c9"))
            findings = integrity.scan({"g-p-abc": abc})
            self.assertEqual([f.kind for f in findings], ["url_mismatch"])
            self.assertIn("g-p-lau", findings[0].detail)
            self.assertIn("c9", findings[0].detail)

    def test_index_mismatch_is_not_blocking(self):
        with Root() as r:
            abc = r.project("ABC")
            r.put(abc, _event("g-p-abc", "c1", "m1"))
            r.conv_index(abc, "c2", ["m1"])
            findings = integrity.scan({"g-p-abc": abc})
            self.assertEqual([f.kind for f in findings], ["index_mismatch"])
            self.assertFalse(findings[0].blocking)

    def test_check_project_includes_unmapped_folder_and_filters(self):
        with Root() as r:
            abc, lau, other = r.project("ABC"), r.project("Falke"), r.project("Other")
            r.put(abc, _event("g-p-abc", "c1", "m1"))
            r.put(lau, _event("g-p-lau", "c2", "m1"))
            r.put(other, _event("g-p-o", "c3", "m3"))
            mappings = {"g-p-abc": abc}
            blocking = lambda fs: [f.kind for f in fs if f.blocking]  # noqa: E731
            self.assertEqual(blocking(integrity.check_project(lau, mappings)), ["duplicate_id"])
            self.assertEqual(blocking(integrity.check_project(other, mappings)), [])
            # nicht zugeordnete project.id wird sichtbar, blockiert aber nicht
            self.assertEqual({f.kind for f in integrity.check_project(other, mappings)}, {"unknown_project"})


class TestSameFolderNameTwoRoots(unittest.TestCase):
    def test_findings_are_attributed_by_path_not_name(self):
        with Root() as r1, Root() as r2:
            a, b = r1.project("ABC"), r2.project("ABC")
            other = r1.project("X")
            r1.put(a, _event("g-p-a", "c1", "m1"))
            r1.put(other, _event("g-p-x", "c2", "m1"))  # Dublette betrifft nur r1/ABC
            r2.put(b, _event("g-p-b", "c3", "m9"))
            mappings = {"g-p-a": a, "g-p-b": b, "g-p-x": other}
            self.assertEqual([f.kind for f in integrity.check_project(a, mappings) if f.blocking], ["duplicate_id"])
            self.assertEqual([f for f in integrity.check_project(b, mappings) if f.blocking], [])


class TestMoreChecks(unittest.TestCase):
    def test_bridge_conflict_blocks_both_projects(self):
        with Root() as r:
            abc, lau = r.project("ABC"), r.project("Falke")
            conflicts = r.tmp / "conflicts"
            conflicts.mkdir()
            record = _event("g-p-lau", "c2", "m1")
            record["_polaris_conflict"] = {
                "existing_folder": "ABC", "existing_conversation_id": "c1",
                "rejected_folder": "Falke", "rejected_conversation_id": "c2",
            }
            (conflicts / "x.json").write_text(json.dumps(record), encoding="utf-8")
            with unittest.mock.patch.object(integrity, "CONFLICTS_DIR", conflicts):
                mappings = {"g-p-abc": abc, "g-p-lau": lau}
                for project in (abc, lau):
                    kinds = [f.kind for f in integrity.check_project(project, mappings) if f.blocking]
                    self.assertEqual(kinds, ["bridge_conflict"], project.name)

    def test_unreadable_file_is_reported(self):
        with Root() as r:
            abc = r.project("ABC")
            (abc / "raw" / "chatgpt" / "messages" / "bad.json").write_bytes(b"\xff\xfe{")
            findings = integrity.scan({"g-p-abc": abc})
            self.assertEqual([f.kind for f in findings], ["unreadable"])
            self.assertFalse(findings[0].blocking)

    def test_claude_url_mismatch(self):
        with Root() as r:
            nov = r.project("MusterFirma")
            d = nov / "raw" / "claude" / "messages"
            d.mkdir(parents=True)
            e = _event("claude-p-1", "c1", "m1", url="https://claude.ai/chat/c9")
            e["source"] = "claude"
            (d / "m1.json").write_text(json.dumps(e), encoding="utf-8")
            kinds = [f.kind for f in integrity.scan({"claude-p-1": nov})]
            self.assertEqual(kinds, ["url_mismatch"])


class TestPipelineGate(unittest.TestCase):
    def test_blocking_finding_skips_project_without_llm_call(self):
        with Root() as r:
            abc, lau = r.project("ABC"), r.project("Falke")
            r.put(abc, _event("g-p-abc", "c1", "m1"))
            r.put(lau, _event("g-p-lau", "c2", "m1"))
            fake = FakeLlm([make_change()])
            mappings = {"g-p-abc": abc, "g-p-lau": lau}

            report = pipeline.run_project(
                lau, fast_config(), llm_fn=fake,
                integrity_check=lambda p: integrity.check_project(p, mappings),
            )

            self.assertEqual(report.aborted_reason, "integrity")
            self.assertEqual(report.llm_calls_made, 0)
            self.assertEqual(len(report.integrity_findings), 1)
            self.assertFalse(layout.knowledge_path(lau).exists())

            dry = pipeline.dry_run_project(
                lau, fast_config(), integrity_check=lambda p: integrity.check_project(p, mappings)
            )
            self.assertEqual(dry.aborted_reason, "integrity")

    def test_non_blocking_finding_still_runs(self):
        with Root() as r:
            abc = r.project("ABC")
            r.put(abc, _event("g-p-abc", "c1", "m1"))
            r.conv_index(abc, "c2", ["m1"])
            fake = FakeLlm([make_change()])
            report = pipeline.run_project(
                abc, fast_config(), llm_fn=fake,
                integrity_check=lambda p: integrity.check_project(p, {"g-p-abc": abc}),
            )
            self.assertIsNone(report.aborted_reason)
            self.assertEqual(report.llm_calls_made, 1)


if __name__ == "__main__":
    unittest.main()
