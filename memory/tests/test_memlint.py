"""Tests fuer memlint.py (Repo: python3 -m unittest discover -s memory/tests; live: ~/.config/ai-agents/tools).

Synthetische Gedaechtnisse in Temp-Verzeichnissen; dazu ein Test gegen das
echte Gedaechtnis (ohne Systemteil), damit ein kaputter Zustand auffaellt.
"""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))  # Repo-Kopie: memory/tools
import memlint  # noqa: E402

LONG = ("Dieser Absatz ist absichtlich lang genug, um als inhaltlicher Absatz zu zaehlen und "
        "damit von der Rueckkopie-Pruefung erfasst zu werden. Er beschreibt eine alte Episode "
        "mit vielen Details, die ins Archiv gehoeren und nicht mehr in den aktuellen Handoff.")


def tag(**kw):
    base = {"id": "x", "type": "current_state", "status": "current", "date": "2026-09-25"}
    base.update(kw)
    return "<!-- mem: " + " ".join(f"{k}={v}" for k, v in base.items()) + " -->"


class MemRoot:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("handoff", "knowledge", "history"):
            (self.root / d).mkdir()
        self.write("AGENTS.md", "# Regeln\n")
        self.write("AI_HANDOFF.md", "# Index\n- [T](handoff/t.md)\n")
        self.write("handoff/t.md", f"# T\n\n## Stand\n{tag(id='t-stand')}\nAlles gut. Details: knowledge/t.md, history/t.md\n")
        self.write("knowledge/t.md", f"# T Wissen\n\n## Gotcha\n{tag(id='t-gotcha', type='gotcha')}\nNie X tun.\n")
        self.write("history/t.md", f"# T Historie\n\n## 2026-09-01 Episode\n{tag(id='t-ep', type='history', status='historical')}\n{LONG}\n")

    def write(self, rel, text):
        p = self.root / rel
        p.write_text(textwrap.dedent(text), encoding="utf-8")
        return p

    def append(self, rel, text):
        p = self.root / rel
        p.write_text(p.read_text(encoding="utf-8") + text, encoding="utf-8")

    def lint(self):
        return memlint.lint(self.root, system=False)

    def checks(self):
        return {(f.level, f.check) for f in self.lint().findings}


class TestMemlint(unittest.TestCase):
    def setUp(self):
        self.m = MemRoot()
        self.addCleanup(self.m.tmp.cleanup)

    def test_clean_memory_passes(self):
        r = self.m.lint()
        self.assertEqual(r.findings, [], r.findings)
        self.assertEqual(r.stats["tags"], 3)

    def test_format_example_in_agents_md_is_ignored(self):
        self.m.write("AGENTS.md", "# Regeln\nFormat: `<!-- mem: id=<id> type=<typ> -->`\n")
        self.assertEqual(self.m.lint().errors, [])

    def test_missing_entrypoint_files(self):
        (self.m.root / "AGENTS.md").unlink()
        self.assertIn(("error", "structure"), self.m.checks())

    def test_handoff_not_in_index(self):
        self.m.write("handoff/neu.md", f"# Neu\n\n## Stand\n{tag(id='neu')}\nok\n")
        self.assertIn(("error", "structure"), self.m.checks())

    def test_unlinked_knowledge_file(self):
        self.m.write("knowledge/waise.md", f"# W\n\n## A\n{tag(id='w', type='gotcha')}\nx\n")
        self.assertIn(("error", "structure"), self.m.checks())

    def test_broken_link(self):
        self.m.append("handoff/t.md", "Siehe history/gibtsnicht.md\n")
        self.assertIn(("error", "links"), self.m.checks())

    def test_placeholder_links_ignored(self):
        self.m.append("handoff/t.md", "Muster: handoff/<thema>.md\n")
        self.assertEqual(self.m.lint().errors, [])

    def test_tag_missing_required_field(self):
        self.m.append("knowledge/t.md", "\n## Kaputt\n<!-- mem: id=k type=gotcha -->\nx\n")
        self.assertIn(("error", "provenance"), self.m.checks())

    def test_invalid_type_status_date(self):
        self.m.append("knowledge/t.md", f"\n## A\n{tag(id='a', type='quatsch')}\n")
        self.m.append("knowledge/t.md", f"\n## B\n{tag(id='b', status='vielleicht')}\n")
        self.m.append("knowledge/t.md", f"\n## C\n{tag(id='c', date='25.09.2026')}\n")
        msgs = " ".join(f.message for f in self.m.lint().errors)
        self.assertIn("type", msgs)
        self.assertIn("status", msgs)
        self.assertIn("date", msgs)

    def test_duplicate_ids(self):
        self.m.append("knowledge/t.md", f"\n## Doppelt\n{tag(id='t-stand', type='gotcha')}\n")
        self.assertTrue(any("doppelt" in f.message for f in self.m.lint().errors))

    def test_section_without_tag_in_knowledge(self):
        self.m.append("knowledge/t.md", "\n## Ohne Herkunft\nirgendwas\n")
        self.assertTrue(any("ohne mem-Tag" in f.message for f in self.m.lint().errors))

    def test_superseded_requires_existing_successor(self):
        self.m.append("knowledge/t.md", f"\n## Alt\n{tag(id='alt', type='decision', status='superseded', superseded_by='fehlt')}\n")
        self.assertIn(("error", "superseding"), self.m.checks())

    def test_superseded_without_successor(self):
        self.m.append("knowledge/t.md", f"\n## Alt\n{tag(id='alt', type='decision', status='superseded')}\n")
        self.assertTrue(any("ohne superseded_by" in f.message for f in self.m.lint().errors))

    def test_valid_superseding_chain(self):
        self.m.append("knowledge/t.md",
                      f"\n## Veraltet: Alt\n{tag(id='alt', type='decision', status='superseded', superseded_by='neu')}\nErsetzt durch: Neu\n"
                      f"\n## Neu\n{tag(id='neu', type='decision', supersedes='alt')}\n")
        self.assertEqual(self.m.lint().errors, [])

    def test_superseded_must_be_visible(self):
        self.m.append("knowledge/t.md",
                      f"\n## Alt\n{tag(id='alt', type='decision', status='superseded', superseded_by='t-gotcha')}\nText\n")
        msgs = [f.message for f in self.m.lint().errors if f.check == "readability"]
        self.assertEqual(len(msgs), 2, msgs)

    def test_deprecated_needs_visible_prefix(self):
        self.m.append("knowledge/t.md", f"\n## Abgekündigt: Alt\n{tag(id='d1', type='procedure', status='deprecated')}\n")
        self.m.append("knowledge/t.md", f"\n## Alt2\n{tag(id='d2', type='procedure', status='deprecated')}\n")
        msgs = [f.file for f in self.m.lint().errors if f.check == "readability"]
        self.assertEqual(len(msgs), 1, msgs)

    def test_successor_marker_needs_superseded_status(self):
        self.m.append("knowledge/t.md", f"\n## Alt\n{tag(id='alt', type='decision', superseded_by='t-gotcha')}\n")
        self.assertIn(("error", "superseding"), self.m.checks())

    def test_superseded_entry_in_handoff_is_error(self):
        self.m.append("handoff/t.md", f"\n## Alt\n{tag(id='h-alt', status='superseded', superseded_by='t-stand')}\n")
        self.assertIn(("error", "current-state"), self.m.checks())

    def test_deprecated_and_history_in_handoff_are_errors(self):
        self.m.append("handoff/t.md", f"\n## D\n{tag(id='h-d', status='deprecated')}\n")
        self.m.append("handoff/t.md", f"\n## H\n{tag(id='h-h', type='history')}\n")
        errs = [f for f in self.m.lint().errors if f.check == "current-state"]
        self.assertEqual(len(errs), 2)

    def test_mem_ref_must_resolve(self):
        self.m.append("handoff/t.md", f"\n## R\n{tag(id='r', ref='mem:nirgends')}\n")
        self.assertIn(("error", "provenance"), self.m.checks())

    def test_history_ref_must_resolve(self):
        self.m.append("knowledge/t.md", f"\n## R\n{tag(id='r', type='gotcha', ref='history:t-ep')}\n")
        self.assertEqual(self.m.lint().errors, [])
        self.m.append("knowledge/t.md", f"\n## S\n{tag(id='s', type='gotcha', ref='history:weg')}\n")
        self.assertIn(("error", "provenance"), self.m.checks())

    def test_copy_back_from_history_detected(self):
        self.m.append("handoff/t.md", f"\n{LONG}\n")
        errs = [f for f in self.m.lint().errors if f.check == "duplicate"]
        self.assertEqual(len(errs), 1)
        self.assertIn("history/t.md", errs[0].message)

    def test_copy_back_ignores_whitespace_changes(self):
        self.m.append("handoff/t.md", "\n" + LONG.replace(" ", "\n", 3) + "\n")
        self.assertIn(("error", "duplicate"), self.m.checks())

    def test_copy_back_of_single_list_item_detected(self):
        self.m.append("history/t.md", f"\n## 2026-09-02 Liste\n{tag(id='t-l', type='history', status='historical')}\n- kurz\n- {LONG} (Liste)\n- noch kurz\n")
        self.m.append("handoff/t.md", f"\n- {LONG} (Liste)\n")
        self.assertIn(("error", "duplicate"), self.m.checks())

    def test_short_shared_lines_are_not_copy_back(self):
        self.m.append("handoff/t.md", "\nNie X tun.\n")
        self.assertEqual(self.m.lint().errors, [])

    def test_size_is_only_warning(self):
        self.m.append("handoff/t.md", "\n" + ("kurz\n\n" * 3000))
        r = self.m.lint()
        self.assertEqual(r.errors, [])
        self.assertIn(("warning", "size"), {(f.level, f.check) for f in r.findings})

    def test_plaintext_password_detected(self):
        self.m.append("history/t.md", "Passwort vom Nutzer auf `geheim123` geaendert.\n")
        self.assertIn(("error", "secrets"), self.m.checks())

    def test_password_file_reference_is_fine(self):
        self.m.append("history/t.md", "Passwort liegt nur in `vm-admin-password.txt` (chmod 600).\n")
        self.assertNotIn(("error", "secrets"), self.m.checks())

    def test_api_key_detected(self):
        self.m.append("knowledge/t.md", "token=abcdefghijklmnopqrstuvwx\n")
        self.assertIn(("error", "secrets"), self.m.checks())

    def test_cli_json_exit_codes(self):
        ok = subprocess.run([sys.executable, memlint.__file__, "--root", str(self.m.root), "--no-system", "--json"],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn('"ok": true', ok.stdout)
        self.m.append("handoff/t.md", f"\n{LONG}\n")
        bad = subprocess.run([sys.executable, memlint.__file__, "--root", str(self.m.root), "--no-system"],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)


class TestSystemChecks(unittest.TestCase):
    """Backup-Abdeckung gegen eine synthetische omarchy-backup-Konfiguration."""

    def setUp(self):
        self.m = MemRoot()
        self.addCleanup(self.m.tmp.cleanup)
        self.home = Path(self.m.tmp.name) / "home"
        (self.home / ".config" / "omarchy-backup").mkdir(parents=True)
        self.orig_home = memlint.Path.home
        memlint.Path.home = classmethod(lambda cls: self.home)
        self.addCleanup(setattr, memlint.Path, "home", self.orig_home)

    def conf(self, text):
        (self.home / ".config" / "omarchy-backup" / "paths.conf").write_text(text)

    def backup_findings(self):
        return [f for f in memlint.lint(self.m.root, system=True).findings if f.check == "backup"]

    def test_included_memory_ok(self):
        self.conf(f"include {self.m.root}\nexclude *.log\n")
        self.assertEqual(self.backup_findings(), [])

    def test_memory_not_included(self):
        self.conf("include ~/.config/hypr\n")
        self.assertTrue(any("nicht per include" in f.message for f in self.backup_findings()))

    def test_excluded_filename_in_memory(self):
        self.conf(f"include {self.m.root}\nexclude *.log\n")
        self.m.write("history/alt.log", "x")
        self.m.write("knowledge/api-token-notes.md", f"# N\n\n## A\n{tag(id='n', type='gotcha')}\n")
        files = {f.file for f in self.backup_findings()}
        self.assertIn("history/alt.log", files)
        self.assertIn("knowledge/api-token-notes.md", files)

    def test_entrypoint_symlinks_reported(self):
        self.conf(f"include {self.m.root}\n")
        r = memlint.lint(self.m.root, system=True)
        self.assertTrue(any(f.check == "entrypoints" for f in r.errors))


class TestEntrypointsConf(unittest.TestCase):
    """entrypoints.conf (seit 2026-10-07): anbieterneutrale Einstiege."""

    def setUp(self):
        self.m = MemRoot()
        self.home = Path(tempfile.mkdtemp())
        (self.home / "agent-a").mkdir()
        (self.home / "agent-a" / "rules.md").write_text("eigene Datei")
        (self.home / "agent-b").mkdir()
        self.m.write("AGENTS.md", "# Regeln\n")
        self.m.write("entrypoints.conf", "\n".join([
            "# Kommentar",
            f"{self.home}/agent-a/rules.md  AGENTS.md  # vorhandene Datei",
            f"{self.home}/agent-b/AGENTS.md AGENTS.md  # Ordner da, Link fehlt",
            f"{self.home}/agent-c/AGENTS.md AGENTS.md  # Agent nicht eingerichtet",
        ]) + "\n")

    def errors_for(self):
        r = memlint.lint(self.m.root, system=True)
        return {f.file for f in r.errors if f.check == "entrypoints"}

    def test_conf_is_parsed(self):
        self.assertEqual(len(memlint.load_entrypoints(self.m.root)), 3)

    def test_missing_agent_dir_is_skipped(self):
        errs = self.errors_for()
        self.assertIn(f"{self.home}/agent-b/AGENTS.md", errs)
        self.assertIn(f"{self.home}/agent-a/rules.md", errs)  # Datei statt Link
        self.assertNotIn(f"{self.home}/agent-c/AGENTS.md", errs)

    def test_link_creates_but_never_replaces(self):
        done = memlint.link_entrypoints(self.m.root)
        self.assertEqual(len(done), 1)
        self.assertTrue((self.home / "agent-b" / "AGENTS.md").is_symlink())
        self.assertEqual((self.home / "agent-a" / "rules.md").read_text(), "eigene Datei")
        self.assertFalse((self.home / "agent-c").exists())
        self.assertNotIn(f"{self.home}/agent-b/AGENTS.md", self.errors_for())


class TestPatternsAndSkills(unittest.TestCase):
    """Loesungsmuster brauchen Beleg; Skills einen gueltigen Kopf (seit 2026-10-16)."""

    def setUp(self):
        self.m = MemRoot()

    def checks(self, check):
        return [f for f in self.m.lint().errors if f.check == check]

    def test_pattern_with_history_evidence_ok(self):
        self.m.append("knowledge/t.md", f"\n## Muster\n{tag(id='t-p', type='pattern', ref='history:t-ep')}\nSo geht es.\n")
        self.assertEqual(self.checks("pattern"), [])

    def test_pattern_without_evidence(self):
        self.m.append("knowledge/t.md", f"\n## Muster\n{tag(id='t-p', type='pattern', ref='mem:t-gotcha')}\nIdee.\n")
        self.assertEqual(len(self.checks("pattern")), 1)

    def test_pattern_outside_knowledge(self):
        self.m.append("handoff/t.md", f"\n## Muster\n{tag(id='t-p', type='pattern', ref='commit:abc1234')}\nX.\n")
        self.assertEqual(len(self.checks("pattern")), 1)

    def skill(self, name, text):
        (self.m.root / "skills" / name).mkdir(parents=True)
        self.m.write(f"skills/{name}/SKILL.md", text)

    def test_valid_skill(self):
        self.skill("gut", "---\nname: gut\ndescription: Wann und wofuer.\n---\n# Gut\n")
        self.assertEqual(self.checks("skills"), [])

    def test_skill_name_mismatch_and_missing_file(self):
        self.skill("falsch", "---\nname: anders\ndescription: x\n---\n")
        (self.m.root / "skills" / "leer").mkdir()
        self.assertEqual(len(self.checks("skills")), 2)

    def test_skill_links_per_agent_dir(self):
        home = Path(tempfile.mkdtemp())
        (home / "agent-a" / "skills").mkdir(parents=True)
        self.skill("gut", "---\nname: gut\ndescription: x\n---\n")
        self.m.write("entrypoints.conf", f"{home}/agent-a/skills/  skills/\n{home}/agent-b/skills/  skills/\n")
        self.assertEqual(memlint.expand_entrypoints(self.m.root),
                         {f"{home}/agent-a/skills/gut": "skills/gut", f"{home}/agent-b/skills/gut": "skills/gut"})
        (home / "agent-c").mkdir()
        self.m.append("entrypoints.conf", f"{home}/agent-c/skills/  skills/\n")
        self.assertEqual(len(memlint.link_entrypoints(self.m.root)), 2)
        self.assertEqual((home / "agent-a" / "skills" / "gut").resolve(), (self.m.root / "skills" / "gut").resolve())
        self.assertTrue((home / "agent-c" / "skills" / "gut").is_symlink())  # skills/ neu angelegt
        self.assertFalse((home / "agent-b").exists())

    def test_default_skill_dirs_without_conf_lines(self):
        self.skill("gut", "---\nname: gut\ndescription: x\n---\n")
        links = memlint.expand_entrypoints(self.m.root)
        self.assertEqual({k for k in links if k.endswith("/gut")}, {d + "gut" for d in memlint.SKILL_DIRS})


class TestRealMemory(unittest.TestCase):
    """Das echte Gedaechtnis muss fehlerfrei sein (Warnungen erlaubt)."""

    def test_real_memory_has_no_errors(self):
        if not memlint.DEFAULT_ROOT.is_dir():
            self.skipTest("kein Maschinengedaechtnis auf diesem Rechner")
        r = memlint.lint(memlint.DEFAULT_ROOT, system=True)
        self.assertEqual([f"{f.check} {f.file}: {f.message}" for f in r.errors], [])


if __name__ == "__main__":
    unittest.main()
