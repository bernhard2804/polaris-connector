"""polaris roots: Projektordner-Wurzeln ohne Handarbeit in config.yaml
verwalten (seit 2026-10-07, fuer Setups ohne Google-Drive-Mount)."""

import contextlib
import io
import unittest

from conftest_helpers import TempHome


def run(*argv):
    from polaris import cli

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TestRoots(unittest.TestCase):
    def test_add_list_remove(self):
        from polaris import config

        with TempHome() as h:
            root = h.tmp / "Kunden"
            (root / "Alpha").mkdir(parents=True)
            code, _o, _e = run("roots", "add", str(root))
            self.assertEqual(code, 0)
            self.assertEqual(config.load_config().project_roots, [str(root.resolve())])
            code, out, _e = run("roots")
            self.assertEqual(code, 0)
            self.assertIn("1 Projektordner", out)
            self.assertEqual(run("roots", "add", str(root))[0], 1)  # doppelt
            self.assertEqual(run("roots", "remove", str(root))[0], 0)
            self.assertEqual(config.load_config().project_roots, [])

    def test_add_missing_dir_fails(self):
        with TempHome() as h:
            code, _o, err = run("roots", "add", str(h.tmp / "gibtsnicht"))
            self.assertEqual(code, 1)
            self.assertIn("kein vorhandener Ordner", err)

    def test_remove_refuses_with_mappings(self):
        from polaris import config, mapping

        with TempHome() as h:
            root = h.make_project_root("Alpha")
            store = mapping.load_store()
            store.projects["g-p-1"] = mapping.ProjectMapping(
                project_id="g-p-1", chatgpt_name="Alpha", local_path=str(root / "Alpha"),
                mapping_method="manual", confidence=1.0, status="ok")
            mapping.save_store(store)
            code, _o, err = run("roots", "remove", str(root))
            self.assertEqual(code, 1)
            self.assertIn("Alpha", err)
            self.assertEqual(len(config.load_config().project_roots), 1)
            self.assertEqual(run("roots", "remove", str(root), "--force")[0], 0)
            self.assertEqual(config.load_config().project_roots, [])

    def test_add_requires_path(self):
        with TempHome():
            self.assertEqual(run("roots", "add")[0], 2)


if __name__ == "__main__":
    unittest.main()
