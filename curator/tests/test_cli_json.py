from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from polaris_curator import cli, pipeline


class CliJsonTests(unittest.TestCase):
    def _status_report(self):
        return pipeline.RunReport(
            project="Alpha",
            dry_run=True,
            new_messages=7,
            new_documents=3,
            day_batches_planned=["2026-09-14#1", "2026-09-14#2"],
            documents_planned=["documents/brief.pdf", "mail/update.eml", "notes/idea.md"],
            llm_calls_made=3,
            total_input_chars=12345,
        )

    @patch("polaris_curator.cli.pipeline.dry_run_project")
    @patch("polaris_curator.cli.state_mod.load_state")
    @patch("polaris_curator.cli.config_mod.load_config")
    @patch("polaris_curator.cli.config_mod.list_known_projects")
    def test_status_all_json_is_structured(
        self, list_projects, load_config, load_state, dry_run
    ):
        list_projects.return_value = {"project-1": Path("/tmp/Alpha")}
        load_config.return_value = object()
        load_state.return_value = SimpleNamespace(last_successful_run="2026-09-14T10:00:00+00:00")
        dry_run.return_value = self._status_report()

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = cli.main(["status-all", "--json"])

        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["summary"]["pending_projects"], 1)
        self.assertEqual(data["summary"]["pending_units"], 5)
        self.assertEqual(data["summary"]["backlog_units"], 2)
        self.assertEqual(data["projects"][0]["new_documents"], 1)
        self.assertEqual(data["projects"][0]["new_emails"], 1)
        self.assertEqual(data["projects"][0]["new_notes"], 1)

    @patch("polaris_curator.cli.pipeline.run_project")
    @patch("polaris_curator.cli.config_mod.load_config")
    @patch("polaris_curator.cli.config_mod.find_project_path")
    def test_run_json_contains_full_report(self, find_project, load_config, run_project):
        find_project.return_value = Path("/tmp/Alpha")
        load_config.return_value = object()
        run_project.return_value = pipeline.RunReport(
            project="Alpha",
            new_messages=2,
            day_batches_processed=["2026-09-14#1"],
            llm_calls_made=1,
            knowledge_changed=True,
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = cli.main(["run", "Alpha", "--json"])

        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertTrue(data["ok"])
        self.assertEqual(data["report"]["project"], "Alpha")
        self.assertTrue(data["report"]["knowledge_changed"])

    @patch("polaris_curator.cli.pipeline.run_project")
    @patch("polaris_curator.cli.config_mod.load_config")
    @patch("polaris_curator.cli.config_mod.find_project_path")
    def test_locked_run_is_not_reported_as_success(self, find_project, load_config, run_project):
        find_project.return_value = Path("/tmp/Alpha")
        load_config.return_value = object()
        run_project.return_value = pipeline.RunReport(
            project="Alpha", aborted_reason="bereits ein Lauf aktiv (Lock)"
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = cli.main(["run", "Alpha", "--json"])

        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(stdout.getvalue())["ok"])


class RunAllExitCodeTests(unittest.TestCase):
    @patch("polaris_curator.cli.config_mod.count_mapped_projects", return_value=5)
    @patch("polaris_curator.cli.config_mod.list_known_projects", return_value={})
    def test_unreachable_project_drive_is_tempfail(self, _list, _count):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["run-all"]), cli.EXIT_TEMPFAIL)

    @patch("polaris_curator.cli.config_mod.count_mapped_projects", return_value=0)
    @patch("polaris_curator.cli.config_mod.list_known_projects", return_value={})
    def test_no_mappings_is_success(self, _list, _count):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["run-all"]), 0)

    @patch("polaris_curator.cli.pipeline.run_project")
    @patch("polaris_curator.cli.config_mod.load_config", return_value=object())
    @patch("polaris_curator.cli.config_mod.list_known_projects")
    def test_llm_unavailable_is_tempfail_other_errors_are_1(self, list_projects, _cfg, run_project):
        list_projects.return_value = {"a": Path("/tmp/A"), "b": Path("/tmp/B")}
        run_project.side_effect = [
            pipeline.RunReport(project="A", errors=["Schema"]),
            pipeline.RunReport(project="B", errors=["weg"], aborted_reason="llm_unavailable"),
        ]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["run-all"]), cli.EXIT_TEMPFAIL)

        run_project.side_effect = [
            pipeline.RunReport(project="A", errors=["Schema"]),
            pipeline.RunReport(project="B"),
        ]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["run-all"]), 1)


if __name__ == "__main__":
    unittest.main()
