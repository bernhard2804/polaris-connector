import json
import unittest
from pathlib import Path

from conftest_helpers import TempHome, make_event

from polaris import config, mapping, pipeline, storage


def write_inbox_file(event: dict, name: str) -> Path:
    path = config.INBOX_DIR / name
    path.write_text(json.dumps(event), encoding="utf-8")
    return path


class TestPipelineHappyPath(unittest.TestCase):
    def test_known_project_gets_processed(self):
        with TempHome() as h:
            root = h.make_project_root("Musterfirma")
            event = make_event(project_id="proj_1", project_name="Musterfirma")
            write_inbox_file(event, "a.json")

            counts = pipeline.process_inbox()

            self.assertEqual(counts, {"processed": 1, "pending": 0, "failed": 0})
            msg_file = root / "Musterfirma" / "raw" / "chatgpt" / "messages" / "msg_1.json"
            self.assertTrue(msg_file.is_file())
            conv_file = (
                root / "Musterfirma" / "raw" / "chatgpt" / "conversations" / "conv_xyz.json"
            )
            self.assertTrue(conv_file.is_file())
            self.assertEqual(len(list(config.INBOX_DIR.glob("*.json"))), 0)
            self.assertEqual(len(list(config.PROCESSED_DIR.glob("*.json"))), 1)

    def test_idempotent_reexport_no_duplicate(self):
        with TempHome() as h:
            h.make_project_root("Musterfirma")
            event = make_event(project_id="proj_1", project_name="Musterfirma")

            write_inbox_file(event, "a.json")
            pipeline.process_inbox()
            write_inbox_file(event, "a_again.json")
            counts = pipeline.process_inbox()

            self.assertEqual(counts["processed"], 1)
            msgs = list((Path(mapping.load_store().get("proj_1").local_path)).glob(
                "raw/chatgpt/messages/*.json"
            ))
            self.assertEqual(len(msgs), 1)

    def test_revision_on_changed_content_same_id(self):
        with TempHome() as h:
            h.make_project_root("Musterfirma")
            event1 = make_event(project_id="proj_1", project_name="Musterfirma", content="v1")
            write_inbox_file(event1, "a.json")
            pipeline.process_inbox()

            event2 = make_event(project_id="proj_1", project_name="Musterfirma", content="v2")
            write_inbox_file(event2, "b.json")
            pipeline.process_inbox()

            local_path = Path(mapping.load_store().get("proj_1").local_path)
            messages = sorted(storage.messages_dir(local_path).glob("*.json"))
            names = [p.name for p in messages]
            self.assertIn("msg_1.json", names)
            self.assertIn("msg_1.rev2.json", names)
            original = json.loads((storage.messages_dir(local_path) / "msg_1.json").read_text())
            self.assertEqual(original["message"]["content"], "v1")

    def test_repeated_final_content_creates_only_one_revision(self):
        with TempHome() as h:
            h.make_project_root("Musterfirma")
            for i, content in enumerate(["teil", "final", "final", "final"]):
                write_inbox_file(make_event(project_id="proj_1", project_name="Musterfirma", content=content), f"{i}.json")
                pipeline.process_inbox()
            local_path = Path(mapping.load_store().get("proj_1").local_path)
            names = sorted(p.name for p in storage.messages_dir(local_path).glob("*.json"))
            self.assertEqual(names, ["msg_1.json", "msg_1.rev2.json"])

    def test_parent_before_child_and_child_before_parent(self):
        with TempHome() as h:
            h.make_project_root("Musterfirma")
            parent = make_event(
                project_id="proj_1", project_name="Musterfirma", message_id="m_parent"
            )
            child = make_event(
                project_id="proj_1",
                project_name="Musterfirma",
                message_id="m_child",
                parent_id="m_parent",
            )

            # Kind zuerst
            write_inbox_file(child, "child.json")
            counts1 = pipeline.process_inbox()
            self.assertEqual(counts1["processed"], 1)

            # Eltern-Nachricht danach
            write_inbox_file(parent, "parent.json")
            counts2 = pipeline.process_inbox()
            self.assertEqual(counts2["processed"], 1)

            local_path = Path(mapping.load_store().get("proj_1").local_path)
            self.assertTrue((storage.messages_dir(local_path) / "m_child.json").is_file())
            self.assertTrue((storage.messages_dir(local_path) / "m_parent.json").is_file())


class TestPipelineErrors(unittest.TestCase):
    def test_invalid_json_goes_to_failed(self):
        with TempHome() as h:
            h.make_project_root("Musterfirma")
            path = config.INBOX_DIR / "broken.json"
            path.write_text("{not valid json", encoding="utf-8")

            counts = pipeline.process_inbox()

            self.assertEqual(counts, {"processed": 0, "pending": 0, "failed": 1})
            self.assertEqual(len(list(config.FAILED_DIR.glob("*.json"))), 1)

    def test_invalid_schema_goes_to_failed(self):
        with TempHome() as h:
            h.make_project_root("Musterfirma")
            event = make_event()
            del event["message"]["content"]
            write_inbox_file(event, "bad_schema.json")

            counts = pipeline.process_inbox()

            self.assertEqual(counts, {"processed": 0, "pending": 0, "failed": 1})


class TestPipelineRobustness(unittest.TestCase):
    """Review 2026-09-29: eine kaputte Datei darf den Rest nicht blockieren."""

    def test_bad_files_do_not_block_following_ones(self):
        with TempHome() as h:
            root = h.make_project_root("ABC")
            (config.INBOX_DIR / "a_bad_utf8.json").write_bytes(b"\xff\xfe{}")
            numeric = make_event(project_id="p", project_name="ABC")
            numeric["message"]["id"] = 12345
            write_inbox_file(numeric, "b_numeric_id.json")
            blank = make_event(project_id="p", project_name="ABC")
            blank["message"]["id"] = "   "
            write_inbox_file(blank, "c_blank_id.json")
            write_inbox_file(make_event(project_id="p", project_name="ABC", message_id="ok1"), "d_good.json")

            counts = pipeline.process_inbox()

            self.assertEqual(counts["processed"], 1)
            self.assertEqual(counts["failed"], 3)
            self.assertTrue((root / "ABC" / "raw" / "chatgpt" / "messages" / "ok1.json").is_file())
            self.assertFalse((root / "ABC" / "raw" / "chatgpt" / "messages" / "unknown.json").exists())
            self.assertIsNotNone(pipeline.last_run())

    def test_write_error_leaves_file_for_retry(self):
        from unittest.mock import patch
        with TempHome() as h:
            h.make_project_root("ABC")
            write_inbox_file(make_event(project_id="p", project_name="ABC"), "a.json")
            with patch.object(storage, "write_message", side_effect=OSError("Transport endpoint is not connected")):
                counts = pipeline.process_inbox()
            self.assertEqual(counts.get("retry"), 1)
            self.assertTrue((config.INBOX_DIR / "a.json").exists())
            self.assertEqual(list(config.FAILED_DIR.glob("*.json")), [])
            self.assertEqual(pipeline.process_inbox()["processed"], 1)

    def test_schema_rejects_bool_version_and_bad_timestamp(self):
        from polaris.schema import ValidationError, validate_and_normalize
        e = make_event()
        e["schema_version"] = True
        with self.assertRaises(ValidationError):
            validate_and_normalize(e)
        e = make_event(captured_at="gestern")
        with self.assertRaises(ValidationError):
            validate_and_normalize(e)


class TestPipelinePending(unittest.TestCase):
    def test_unknown_project_goes_to_pending_then_manual_map(self):
        with TempHome() as h:
            root = h.make_project_root()  # keine passenden Ordner
            event = make_event(project_id="proj_secret", project_name="Super Secret Project")
            write_inbox_file(event, "a.json")

            counts = pipeline.process_inbox()
            self.assertEqual(counts, {"processed": 0, "pending": 1, "failed": 0})
            self.assertFalse((root).joinpath("Super Secret Project").exists())

            target = root / "Super Secret Project"
            target.mkdir()
            from polaris.cli import cmd_map
            import argparse

            args = argparse.Namespace(project_id="proj_secret", path=str(target))
            rc = cmd_map(args)
            self.assertEqual(rc, 0)

            msg_file = target / "raw" / "chatgpt" / "messages" / "msg_1.json"
            self.assertTrue(msg_file.is_file())
            self.assertEqual(len(list(config.PENDING_DIR.glob("*/*.json"))), 0)


class TestPipelineMappingSafety(unittest.TestCase):
    """Review 2026-09-29: keine ungefragte Zuordnung zu einem aehnlich
    benannten Kundenordner; Mount-Aussetzer duerfen nicht dauerhaft sperren."""

    def test_similar_name_is_not_auto_mapped(self):
        with TempHome() as h:
            root = h.make_project_root("Schmitt Bau", "Falke")
            for i, name in enumerate(("Schmidt Bau", "Falke 2")):
                write_inbox_file(make_event(project_id=f"p{i}", project_name=name, message_id=f"m{i}"), f"{i}.json")
            counts = pipeline.process_inbox()
            self.assertEqual(counts, {"processed": 0, "pending": 2, "failed": 0})
            self.assertFalse((root / "Schmitt Bau" / "raw").exists())
            self.assertFalse((root / "Falke" / "raw").exists())
            self.assertIsNone(mapping.load_store().get("p0"))

    def test_numbering_prefix_does_not_auto_map(self):
        with TempHome() as h:
            root = h.make_project_root("Falke")
            write_inbox_file(make_event(project_id="p", project_name="2 Falke"), "a.json")
            self.assertEqual(pipeline.process_inbox()["pending"], 1)

    def test_corrupt_projects_yaml_means_retry_not_failed(self):
        with TempHome() as h:
            h.make_project_root("ABC")
            config.PROJECTS_FILE.write_text("projects: [kaputt", encoding="utf-8")
            write_inbox_file(make_event(project_id="p", project_name="ABC"), "a.json")
            counts = pipeline.process_inbox()
            self.assertEqual(counts.get("retry"), 1)
            self.assertTrue((config.INBOX_DIR / "a.json").exists())
            self.assertEqual(list(config.FAILED_DIR.glob("*.json")), [])
            self.assertIn("a.json", json.loads((config.STATE_DIR / "retry.json").read_text()))

    def test_exact_normalized_name_is_still_auto_mapped(self):
        with TempHome() as h:
            root = h.make_project_root("Schmitt-Bau")
            write_inbox_file(make_event(project_id="p", project_name="schmitt bau"), "a.json")
            self.assertEqual(pipeline.process_inbox()["processed"], 1)
            self.assertTrue((root / "Schmitt-Bau" / "raw" / "chatgpt" / "messages" / "msg_1.json").is_file())

    def test_missing_target_recovers_when_folder_returns(self):
        with TempHome() as h:
            root = h.make_project_root("ABC")
            write_inbox_file(make_event(project_id="p", project_name="ABC"), "a.json")
            pipeline.process_inbox()
            (root / "ABC").rename(root / "ABC.weg")  # Mount kurz weg
            write_inbox_file(make_event(project_id="p", project_name="ABC", message_id="m2"), "b.json")
            self.assertEqual(pipeline.process_inbox()["pending"], 1)
            self.assertEqual(mapping.load_store().get("p").status, "missing_target")

            (root / "ABC.weg").rename(root / "ABC")
            counts = pipeline.process_inbox()
            self.assertEqual(counts["processed"], 1)  # wartende Datei nachgeholt
            self.assertEqual(mapping.load_store().get("p").status, "ok")
            self.assertTrue((root / "ABC" / "raw" / "chatgpt" / "messages" / "m2.json").is_file())

    def test_project_root_itself_is_not_a_safe_target(self):
        with TempHome() as h:
            root = h.make_project_root("ABC")
            self.assertFalse(mapping.is_path_safe(root, [root]))
            self.assertTrue(mapping.is_path_safe(root / "ABC", [root]))


class TestPipelineCaptureMethod(unittest.TestCase):
    def test_other_capture_method_does_not_create_revision(self):
        with TempHome() as h:
            root = h.make_project_root("ABC")
            dom = make_event(project_id="p", project_name="ABC", conversation_id="c1",
                             content="Siehe Quelle\nText aus DOM")
            write_inbox_file(dom, "a.json")
            pipeline.process_inbox()
            api = make_event(project_id="p", project_name="ABC", conversation_id="c1",
                             content="Siehe [Quelle](https://example.org/x)\n\n**Text** aus DOM")
            api["message"]["capture"] = "api"
            write_inbox_file(api, "b.json")
            counts = pipeline.process_inbox()

            self.assertEqual(counts["processed"], 1)
            names = sorted(p.name for p in (root / "ABC" / "raw" / "chatgpt" / "messages").glob("*.json"))
            self.assertEqual(names, ["msg_1.json"])

    def test_truncated_dom_copy_completed_via_api_is_kept_as_revision(self):
        # Review Runde 2: die Regel "andere Erfassungsart -> keine Revision"
        # verschluckte den Rest einer abgeschnittenen Antwort.
        with TempHome() as h:
            root = h.make_project_root("ABC")
            write_inbox_file(make_event(project_id="p", project_name="ABC", content="Teil 1"), "a.json")
            pipeline.process_inbox()
            full = make_event(project_id="p", project_name="ABC", content="**Teil 1** und Teil 2")
            full["message"]["capture"] = "api"
            write_inbox_file(full, "b.json")
            pipeline.process_inbox()
            names = sorted(p.name for p in (root / "ABC" / "raw" / "chatgpt" / "messages").glob("*.json"))
            self.assertEqual(names, ["msg_1.json", "msg_1.rev2.json"])

    def test_same_capture_method_with_new_content_still_revises(self):
        with TempHome() as h:
            root = h.make_project_root("ABC")
            for name, content in (("a.json", "v1"), ("b.json", "v2")):
                e = make_event(project_id="p", project_name="ABC", conversation_id="c1", content=content)
                e["message"]["capture"] = "api"
                write_inbox_file(e, name)
                pipeline.process_inbox()
            names = sorted(p.name for p in (root / "ABC" / "raw" / "chatgpt" / "messages").glob("*.json"))
            self.assertEqual(names, ["msg_1.json", "msg_1.rev2.json"])


class TestPipelineCrossContext(unittest.TestCase):
    """2026-09-29: Nach einem Chatwechsel exportierte die Extension die alten
    Nachrichten unter der neuen Adresse. Die Bridge darf eine bereits
    archivierte ID nicht in ein anderes Projekt/einen anderen Chat kopieren."""

    def test_same_message_id_in_other_project_is_rejected(self):
        with TempHome() as h:
            root = h.make_project_root("ABC", "Falke")
            original = make_event(
                project_id="proj_abc", project_name="ABC", conversation_id="conv_abc"
            )
            write_inbox_file(original, "a.json")
            pipeline.process_inbox()

            stale = make_event(
                project_id="proj_falke", project_name="Falke", conversation_id="conv_falke"
            )
            write_inbox_file(stale, "b.json")
            counts = pipeline.process_inbox()

            self.assertEqual(counts, {"processed": 0, "pending": 0, "failed": 0, "conflict": 1})
            self.assertFalse((root / "Falke" / "raw" / "chatgpt" / "messages" / "msg_1.json").exists())
            self.assertFalse(
                (root / "Falke" / "raw" / "chatgpt" / "conversations" / "conv_falke.json").exists()
            )
            self.assertTrue((root / "ABC" / "raw" / "chatgpt" / "messages" / "msg_1.json").is_file())
            # Mit Fundort abgelegt, damit der Curator beide Projekte sperren kann.
            files = list(config.CONFLICTS_DIR.glob("*.json"))
            self.assertEqual(len(files), 1)
            info = json.loads(files[0].read_text())["_polaris_conflict"]
            self.assertEqual(info["existing_folder"], "ABC")
            self.assertEqual(info["existing_conversation_id"], "conv_abc")
            self.assertEqual(info["rejected_folder"], "Falke")
            self.assertEqual(list(config.FAILED_DIR.glob("*.json")), [])

    def test_same_message_id_in_other_conversation_of_same_project_is_rejected(self):
        with TempHome() as h:
            root = h.make_project_root("ABC")
            write_inbox_file(make_event(project_id="p", project_name="ABC", conversation_id="c1"), "a.json")
            pipeline.process_inbox()
            write_inbox_file(make_event(project_id="p", project_name="ABC", conversation_id="c2"), "b.json")
            counts = pipeline.process_inbox()

            self.assertEqual(counts["conflict"], 1)
            self.assertFalse((root / "ABC" / "raw" / "chatgpt" / "conversations" / "c2.json").exists())

    def test_reexport_in_same_conversation_still_fine(self):
        with TempHome() as h:
            h.make_project_root("ABC")
            event = make_event(project_id="p", project_name="ABC", conversation_id="c1")
            write_inbox_file(event, "a.json")
            pipeline.process_inbox()
            write_inbox_file(dict(event, captured_at="2026-09-13T10:00:00Z"), "b.json")
            counts = pipeline.process_inbox()

            self.assertEqual(counts, {"processed": 1, "pending": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()
