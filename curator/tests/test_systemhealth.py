import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest.mock
from unittest.mock import patch

import conftest_helpers  # noqa: F401 - sys.path

from polaris_curator import systemhealth as sh


class TestExtensionCheck(unittest.TestCase):
    def _profile(self, addon, granted=None):
        d = Path(tempfile.mkdtemp()) / "abc.default"
        d.mkdir()
        (d / "extensions.json").write_text(json.dumps({"addons": [addon] if addon else []}))
        if granted is not None:
            (d / "extension-preferences.json").write_text(json.dumps(
                {sh.EXTENSION_ID: {"permissions": [], "origins": granted, "data_collection": []}}))
        return d.parent

    def _check(self, addon, granted=None):
        with patch.object(sh, "FIREFOX_DIR", self._profile(addon, granted)):
            return sh.check_extension()

    def base(self, **kw):
        a = {"id": sh.EXTENSION_ID, "version": "0.1.2", "active": True, "visible": True,
             "userDisabled": False, "userPermissions": {"origins": ["https://chatgpt.com/*"]}}
        a.update(kw)
        return a

    def test_ok(self):
        self.assertEqual(self._check(self.base())["level"], "ok")

    def test_missing_permission_is_error(self):
        c = self._check(self.base(userPermissions={"origins": []}))
        self.assertEqual(c["level"], "error")
        self.assertIn("chatgpt.com", c["detail"])

    def test_old_version_warns(self):
        self.assertEqual(self._check(self.base(version="0.1.0"))["level"], "warn")

    def test_temporarily_overridden_warns(self):
        c = self._check(self.base(version="0.1.0", active=False, visible=False))
        self.assertEqual(c["level"], "warn")
        self.assertIn("temporär", c["detail"])

    def test_user_disabled_is_error(self):
        self.assertEqual(self._check(self.base(active=False, userDisabled=True))["level"], "error")

    def test_020_without_claude_permission_warns(self):
        c = self._check(self.base(version="0.2.0"))
        self.assertEqual(c["level"], "warn")
        self.assertIn("claude.ai", c["detail"])

    def test_same_folder_mapped_twice_counts_once(self):
        now = datetime.now(timezone.utc)
        proj = Path(tempfile.mkdtemp())
        (proj / "raw" / "claude" / "messages").mkdir(parents=True)
        (proj / "raw" / "claude" / "messages" / "m.json").write_text("{}")
        c = sh.check_claude_archive({"g-p-1": proj, "claude-p-1": proj, "claude-p-2": proj}, 7, now)
        self.assertIn("(1 Projekt(e)", c["detail"])
        ok = self._check(self.base(version="0.2.0", userPermissions={"origins": ["https://chatgpt.com/*", "https://claude.ai/*"]}))
        self.assertEqual(ok["level"], "ok")

    def test_origins_granted_later_come_from_extension_preferences(self):
        c = self._check(self.base(version="0.2.0", userPermissions={"origins": []}),
                        granted=["https://chatgpt.com/*", "https://claude.ai/*"])
        self.assertEqual(c["level"], "ok")
        self.assertIn("claude.ai", c["detail"])

    def test_not_installed_is_error(self):
        self.assertEqual(self._check(None)["level"], "error")


class TestNativeHostCheck(unittest.TestCase):
    def test_missing_and_ok(self):
        tmp = Path(tempfile.mkdtemp())
        manifest = tmp / "polaris_bridge.json"
        with patch.object(sh, "NATIVE_MANIFEST", manifest):
            self.assertEqual(sh.check_native_host()["level"], "warn")
            host = tmp / "host"
            host.write_text("#!/bin/sh\n")
            os.chmod(host, 0o755)
            manifest.write_text(json.dumps({"path": str(host)}))
            self.assertEqual(sh.check_native_host()["level"], "ok")


class TestBackupCheck(unittest.TestCase):
    def _check(self, snaps, doctor="HEALTHY", result="success"):
        f = Path(tempfile.mkdtemp()) / "state.json"
        f.write_text(json.dumps({"snapshots": snaps, "last_doctor_status": doctor}))
        with patch.object(sh, "BACKUP_STATE", f), patch.object(sh, "_systemctl_user", return_value=result):
            return sh.check_backup(datetime.now(timezone.utc))

    def _snap(self, days, pushed=True):
        return {"created_at": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), "remote_pushed": pushed}

    def test_levels(self):
        self.assertEqual(self._check([self._snap(0.5)])["level"], "ok")
        self.assertEqual(self._check([self._snap(3)])["level"], "warn")
        self.assertEqual(self._check([self._snap(0.5, pushed=False), self._snap(10)])["level"], "error")
        self.assertEqual(self._check([self._snap(0.5)], doctor="DEGRADED")["level"], "error")
        self.assertEqual(self._check([self._snap(0.5)], result="exit-code")["level"], "warn")


class TestChatgptArchive(unittest.TestCase):
    def test_without_chatgpt_message_is_neutral_when_check_is_off(self):
        with tempfile.TemporaryDirectory() as t:
            c = sh.check_archive({"a": Path(t)}, 0)
            self.assertEqual(c["level"], "ok")
            self.assertIn("noch keine", c["detail"])


class TestClaudeArchive(unittest.TestCase):
    def test_levels(self):
        import time
        now = datetime.now(timezone.utc)
        proj = Path(tempfile.mkdtemp())
        self.assertEqual(sh.check_claude_archive({"p": proj}, 7, now)["level"], "ok")  # nie genutzt
        d = proj / "raw" / "claude" / "messages"
        d.mkdir(parents=True)
        f = d / "m.json"
        f.write_text("{}")
        self.assertEqual(sh.check_claude_archive({"p": proj}, 7, now)["level"], "ok")
        old = time.time() - 10 * 86400
        os.utime(f, (old, old))
        c = sh.check_claude_archive({"p": proj}, 7, now)
        self.assertEqual(c["level"], "warn")
        self.assertIn("claude.ai", c["detail"])


class TestMemoryCheck(unittest.TestCase):
    def _run(self, report=None, stdout=None):
        f = Path(tempfile.mkdtemp()) / "memlint.py"
        f.write_text("# stub")
        out = stdout if stdout is not None else json.dumps(report)
        done = unittest.mock.Mock(stdout=out, returncode=0)
        with patch.object(sh, "MEMLINT", f), patch.object(sh.subprocess, "run", return_value=done):
            return sh.check_memory()

    def test_clean_is_ok(self):
        c = self._run({"findings": [], "stats": {"tags": 12}})
        self.assertEqual(c["level"], "ok")
        self.assertIn("12", c["detail"])

    def test_size_warning_stays_ok(self):
        c = self._run({"findings": [{"level": "warning", "check": "size", "file": "handoff/x.md"}], "stats": {}})
        self.assertEqual(c["level"], "ok")
        self.assertIn("Hinweis", c["detail"])

    def test_structural_error_warns(self):
        c = self._run({"findings": [{"level": "error", "check": "duplicate", "file": "handoff/x.md"}]})
        self.assertEqual(c["level"], "warn")

    def test_secret_or_backup_error_is_red(self):
        for check in ("secrets", "backup", "entrypoints"):
            c = self._run({"findings": [{"level": "error", "check": check, "file": "f"}]})
            self.assertEqual(c["level"], "error", check)

    def test_missing_or_broken_memlint_warns(self):
        with patch.object(sh, "MEMLINT", Path("/nonexistent/memlint.py")):
            self.assertEqual(sh.check_memory()["level"], "warn")
        self.assertEqual(self._run(stdout="kein json")["level"], "warn")

    def test_real_memlint_if_present(self):
        if not sh.MEMLINT.is_file():
            self.skipTest("kein Maschinengedaechtnis")
        self.assertIn(sh.check_memory()["level"], ("ok", "warn", "error"))


class TestGrouping(unittest.TestCase):
    def c(self, cid, level="ok", detail="gut"):
        return {"id": cid, "label": cid, "level": level, "detail": detail}

    def test_groups_reduce_rows_and_show_only_problems(self):
        checks = [self.c("chatgpt-archive", detail="letzte Nachricht vor 1 h"), self.c("claude-archive"),
                  self.c("extension", "error", "Berechtigung fehlt"), self.c("native-host"),
                  self.c("separation"), self.c("curator"), self.c("journal"),
                  self.c("mount"), self.c("backup", detail="Snapshot vor 2 h"), self.c("memory"),
                  self.c("neu", "warn", "unbekannt")]
        rows = sh.group_components(checks)
        self.assertEqual([r["id"] for r in rows], ["capture", "separation", "curator", "storage", "memory", "neu"])
        capture = rows[0]
        self.assertEqual(capture["level"], "error")
        self.assertEqual(capture["detail"], "Erweiterung: Berechtigung fehlt")
        self.assertEqual(len(capture["parts"]), 4)
        storage = rows[3]
        self.assertEqual(storage["detail"], "Projektordner, Backup in Ordnung – Snapshot vor 2 h")


class TestUploadBacklog(unittest.TestCase):
    def test_dirty_old_files_are_reported(self):
        import os, tempfile, time
        d = Path(tempfile.mkdtemp())
        (d / "a").write_text('{"Dirty": true}')
        (d / "b").write_text('{"Dirty": false}')
        (d / "c").write_text('{"Dirty": true}')
        old = time.time() - 3 * 3600
        os.utime(d / "a", (old, old)); os.utime(d / "b", (old, old))
        with patch.object(sh, "RCLONE_VFS_META", d):
            self.assertEqual(sh._upload_backlog(), (1, 3))


class TestSeparation(unittest.TestCase):
    def _finding(self, kind):
        from polaris_curator import integrity
        return integrity.Finding(kind, "m1", ["ABC"], "x")

    def test_levels(self):
        with patch.object(sh.integrity, "scan", return_value=[]):
            self.assertEqual(sh.check_separation({})["level"], "ok")
        with patch.object(sh.integrity, "scan", return_value=[self._finding("duplicate_id")]):
            c = sh.check_separation({})
            self.assertEqual(c["level"], "error")
            self.assertIn("ABC", c["detail"])
        with patch.object(sh.integrity, "scan", return_value=[self._finding("bridge_conflict")]):
            self.assertEqual(sh.check_separation({})["level"], "error")
        with patch.object(sh.integrity, "scan", return_value=[self._finding("index_mismatch"), self._finding("unreadable")]):
            c = sh.check_separation({})
            self.assertEqual(c["level"], "warn")
            self.assertIn("Chat-Verzeichnis", c["detail"])
            self.assertIn("unlesbare", c["detail"])


class TestCollect(unittest.TestCase):
    def test_overall_level_is_worst(self):
        ok = {"id": "a", "label": "A", "level": "ok", "detail": ""}
        err = dict(ok, level="error")
        with patch.object(sh, "check_archive", return_value=ok), patch.object(sh, "check_extension", return_value=err), \
             patch.object(sh, "check_native_host", return_value=ok), patch.object(sh, "check_curator_timer", return_value=ok), \
             patch.object(sh, "check_backup", return_value=ok), patch.object(sh, "check_mount", return_value=ok), \
             patch.object(sh, "check_system", return_value=[ok]), \
             patch.object(sh, "check_claude_archive", return_value=ok), \
             patch.object(sh, "check_separation", return_value=ok), \
             patch.object(sh, "check_journals", return_value=ok), \
             patch.object(sh, "check_memory", return_value=ok), \
             patch.object(sh, "check_llm", return_value=ok), \
             patch.object(sh, "check_widget", return_value=None):
            self.assertEqual(sh.collect({}, 3)["level"], "error")


class TestWidgetCheck(unittest.TestCase):
    def _dirs(self):
        base = Path(tempfile.mkdtemp())
        src, active = base / "src", base / "active"
        for d in (src, active):
            (d / "bin").mkdir(parents=True)
            (d / "Widget.qml").write_text("a")
            (d / "bin" / "run").write_text("b")
        return src, active

    def test_identical_is_ok(self):
        src, active = self._dirs()
        self.assertEqual(sh.check_widget(src, active)["level"], "ok")

    def test_changed_extra_or_missing_file_is_warn(self):
        src, active = self._dirs()
        (src / "Widget.qml").write_text("neu")
        (active / "alt.qml").write_text("x")
        (src / "bin" / "run").unlink()
        c = sh.check_widget(src, active)
        self.assertEqual(c["level"], "warn")
        for name in ("Widget.qml", "alt.qml", "bin/run"):
            self.assertIn(name, c["detail"])
        self.assertIn("install.sh", c["detail"])

    def test_not_installed_gives_no_line(self):
        src, active = self._dirs()
        self.assertIsNone(sh.check_widget(src, active.parent / "fehlt"))


if __name__ == "__main__":
    unittest.main()


class TestMountCheck(unittest.TestCase):
    """Projektordner-Check (seit 2026-10-07 beliebige project_roots statt fest
    ~/Projekte als Google-Drive-Mount)."""

    def _check(self, roots, projects=None, fstype="ext4"):
        cfg = Path(tempfile.mkdtemp())
        import yaml

        (cfg / "config.yaml").write_text(yaml.safe_dump({"project_roots": [str(r) for r in roots]}))
        (cfg / "projects.yaml").write_text(yaml.safe_dump({"projects": projects or {}}))
        with patch.object(sh, "POLARIS_CONFIG_DIR", cfg), patch.object(sh, "_fstype", return_value=fstype), \
                patch.object(sh, "_upload_backlog", return_value=None):
            return sh.check_mount()

    def test_no_roots_warns(self):
        self.assertEqual(self._check([])["level"], "warn")

    def test_local_folder_ok(self):
        root = Path(tempfile.mkdtemp())
        (root / "A").mkdir()
        c = self._check([root], {"g-p-1": {"local_path": str(root / "A"), "status": "ok"}})
        self.assertEqual(c["level"], "ok")
        self.assertEqual(c["detail"], "erreichbar")

    def test_missing_root_is_error(self):
        self.assertEqual(self._check([Path("/nonexistent/polaris-root")])["level"], "error")

    def test_dropped_mount_is_error(self):
        # Mount weg: leerer Mountpunkt existiert, zugeordnete Ordner fehlen.
        root = Path(tempfile.mkdtemp())
        c = self._check([root], {"g-p-1": {"local_path": str(root / "A"), "status": "ok"}})
        self.assertEqual(c["level"], "error")
        self.assertIn("fehlt", c["detail"])

    def test_rclone_ok(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(self._check([root], fstype="fuse.rclone")["detail"], "rclone eingehängt")


class TestOptionalParts(unittest.TestCase):
    """Andere Rechner (seit 2026-10-07): fehlendes omarchy-backup bzw.
    Maschinengedaechtnis ist ein Hinweis, kein Fehler."""

    def test_backup_not_installed(self):
        missing = Path(tempfile.mkdtemp()) / "state.json"
        with patch.object(sh, "BACKUP_STATE", missing), patch.object(sh.shutil, "which", return_value=None):
            c = sh.check_backup(datetime.now(timezone.utc))
        self.assertEqual((c["level"], c["detail"]), ("ok", "nicht eingerichtet (optional)"))

    def test_backup_installed_without_snapshot_warns(self):
        missing = Path(tempfile.mkdtemp()) / "state.json"
        with patch.object(sh, "BACKUP_STATE", missing), \
                patch.object(sh.shutil, "which", return_value="/usr/bin/omarchy-backup"):
            self.assertEqual(sh.check_backup(datetime.now(timezone.utc))["level"], "warn")

    def test_memory_not_set_up(self):
        base = Path(tempfile.mkdtemp())
        with patch.object(sh, "MEMLINT", base / "ai-agents" / "tools" / "memlint.py"):
            self.assertEqual(sh.check_memory()["detail"], "nicht eingerichtet (optional)")
        (base / "ai-agents").mkdir()
        with patch.object(sh, "MEMLINT", base / "ai-agents" / "tools" / "memlint.py"):
            self.assertEqual(sh.check_memory()["level"], "warn")


class TestLlmCheck(unittest.TestCase):
    def _check(self, which, **kw):
        from polaris_curator import config as config_mod
        from polaris_curator.config import CuratorConfig

        with patch.object(config_mod, "load_config", return_value=CuratorConfig(**kw)), \
                patch("shutil.which", side_effect=which):
            return sh.check_llm()

    def test_main_cli_missing_is_error(self):
        c = self._check(lambda b: None, runtime="codex")
        self.assertEqual(c["level"], "error")
        self.assertIn("codex fehlt", c["detail"])

    def test_claude_runtime_ok(self):
        c = self._check(lambda b: "/x/claude" if b == "claude" else None, runtime="claude", model="opus",
                        fallback_model=None)
        self.assertEqual((c["level"], c["detail"]), ("ok", "claude opus"))

    def test_missing_fallback_cli_warns(self):
        c = self._check(lambda b: "/x/codex" if b == "codex" else None, runtime="codex",
                        claude_fallback_model="opus")
        self.assertEqual(c["level"], "warn")


class TestCrashes(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def _dir(self, *ages_by_comm):
        d = Path(tempfile.mkdtemp())
        for comm, hours in ages_by_comm:
            usec = int((self.NOW - timedelta(hours=hours)).timestamp() * 1e6)
            (d / f"core.{comm}.1000.b17d110629314b6692fc6566117385a8.{1000 + int(hours * 10)}.{usec}.zst").write_bytes(b"")
        (d / "unrelated.txt").write_text("x")
        return d

    def _check(self, d):
        with patch.object(sh, "COREDUMP_DIR", d):
            return sh.check_crashes(self.NOW)

    def test_recent_crashes_warn_with_counts(self):
        c = self._check(self._dir(("chromium", 1), ("chromium", 2), ("Web.Content", 3), ("old", 48)))
        self.assertEqual(c["level"], "warn")
        self.assertIn("3 seit 24 h", c["detail"])
        self.assertIn("chromium (2×)", c["detail"])
        self.assertIn("Web.Content", c["detail"])
        self.assertNotIn("old", c["detail"])

    def test_old_crashes_ok_with_last(self):
        c = self._check(self._dir(("opennox-hd", 72)))
        self.assertEqual(c["level"], "ok")
        self.assertIn("opennox-hd", c["detail"])
        self.assertIn("vor 3 Tag", c["detail"])

    def test_empty_and_missing_dir(self):
        self.assertEqual(self._check(Path(tempfile.mkdtemp()))["detail"], "keine")
        self.assertIsNone(self._check(Path("/nonexistent/coredump")))

    def test_parse_uncompressed_name(self):
        comm, ts = sh._parse_coredump_name("core.bash.1000.abc.42.1790587698000000")
        self.assertEqual(comm, "bash")
        self.assertEqual(ts.year, 2026)


class TestDiskCauses(unittest.TestCase):
    def _cands(self):
        root = Path(tempfile.mkdtemp())
        big, small = root / "big", root / "small"
        big.mkdir(); small.mkdir()
        return root, [("Groß", big, "aufräumen"), ("Klein", small, ""), ("Fehlt", root / "nope", "")]

    def test_reclaimable_sorted_and_filtered(self):
        root, cands = self._cands()
        sizes = {"big": 3 * 2**30, "small": 2**20}
        with patch.object(sh, "RECLAIM_CANDIDATES", cands), \
             patch.object(sh, "_backing_device", return_value="/dev/x"), \
             patch.object(sh, "_du_bytes", side_effect=lambda p: sizes[p.name]):
            self.assertEqual(sh.reclaimable(str(root)), [("Groß", 3 * 2**30, "aufräumen")])

    def _usage(self, pct):
        total = 100 * 2**30
        return unittest.mock.Mock(total=total, used=total * pct // 100, free=total * (100 - pct) // 100)

    def test_full_disk_names_causes_once(self):
        with patch.object(sh.shutil, "disk_usage", return_value=self._usage(90)), \
             patch.object(sh, "reclaimable", return_value=[("pacman-Cache", 12 * 2**30, "paccache -rk2")]) as rec, \
             patch.object(sh, "check_crashes", return_value=None), \
             patch.object(sh.subprocess, "run", return_value=unittest.mock.Mock(stdout="")), \
             patch.object(sh, "_backing_device", return_value="/dev/mapper/root"):
            items = sh.check_system()
        disks = [i for i in items if i["id"].startswith("disk")]
        self.assertEqual(disks[0]["level"], "warn")
        self.assertIn("pacman-Cache 12 GB (paccache -rk2)", disks[0]["detail"])
        self.assertNotIn("größte", disks[1]["detail"])  # gleiches Dateisystem nur einmal
        rec.assert_called_once()

    def test_ok_disk_skips_measuring(self):
        with patch.object(sh.shutil, "disk_usage", return_value=self._usage(40)), \
             patch.object(sh, "reclaimable") as rec, \
             patch.object(sh, "check_crashes", return_value=None), \
             patch.object(sh.subprocess, "run", return_value=unittest.mock.Mock(stdout="")):
            sh.check_system()
        rec.assert_not_called()

    def test_fmt_size(self):
        self.assertEqual(sh._fmt_size(int(0.8 * 2**30)), "0,8 GB")
        self.assertEqual(sh._fmt_size(12 * 2**30), "12 GB")

    def test_backing_device_btrfs_subvolumes_share_device(self):
        info = Path(tempfile.mkdtemp()) / "mountinfo"
        info.write_text(
            "26 1 0:25 /@ / rw - btrfs /dev/mapper/root rw\n"
            "40 26 0:30 /@pkg /var/cache/pacman/pkg rw - btrfs /dev/mapper/root rw\n"
            "41 26 0:31 / /home rw - btrfs /dev/mapper/home rw\n"
            "42 41 0:32 / /home/u/My\\040Drive rw - fuse.rclone gdrive: rw\n")
        with patch.object(sh, "MOUNTINFO", info), patch.object(sh.os.path, "realpath", side_effect=lambda p: str(p)):
            self.assertEqual(sh._backing_device("/var/cache/pacman/pkg/x"), "/dev/mapper/root")
            self.assertEqual(sh._backing_device("/var/lib/systemd/coredump"), "/dev/mapper/root")
            self.assertEqual(sh._backing_device("/home/u/.cache/yay"), "/dev/mapper/home")
            self.assertEqual(sh._backing_device("/home/u/My Drive/a"), "gdrive:")
            self.assertEqual(sh._backing_device("/homework"), "/dev/mapper/root")


class TestMemoryMirror(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def _status(self, configured=True, state=None):
        d = Path(tempfile.mkdtemp())
        f = d / "memory-mirror.json"
        if state is not None:
            f.write_text(json.dumps(state))
        with patch.object(sh, "_polaris_config_value", return_value="~/X" if configured else None), \
             patch.object(sh, "MEMORY_MIRROR_STATE", f):
            return sh.mirror_status(self.NOW)

    def test_not_configured_is_silent(self):
        self.assertEqual(self._status(configured=False), ("ok", ""))

    def test_never_ran_warns(self):
        level, text = self._status()
        self.assertEqual(level, "warn")
        self.assertIn("noch nie", text)

    def test_fresh_ok(self):
        level, text = self._status(state={"last_success": "2026-10-08T11:00:00+00:00", "last_result": "ok"})
        self.assertEqual(level, "ok")
        self.assertIn("gespiegelt vor 1 h", text)

    def test_stale_warns_with_reason(self):
        level, text = self._status(state={"last_success": "2026-10-05T11:00:00+00:00",
                                          "last_result": "unreachable", "detail": "Polaris nicht erreichbar"})
        self.assertEqual(level, "warn")
        self.assertIn("vor 3 Tag", text)
        self.assertIn("nicht erreichbar", text)

    def test_mirror_newer_warns_immediately(self):
        level, text = self._status(state={"last_success": "2026-10-08T11:00:00+00:00",
                                          "last_result": "mirror_newer"})
        self.assertEqual(level, "warn")
        self.assertIn("neuer als lokal", text)



class TestUpdateCheck(unittest.TestCase):
    """Update-Zeile (seit 2026-10-23): liest den Zustand von update.sh --check,
    holt ihn hoechstens einmal am Tag neu, ohne git-Checkout keine Zeile."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.script = self.repo / "update.sh"
        self.state = self.tmp / "update-check.json"
        self.now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

    def _check(self, run=None):
        with patch.object(sh, "UPDATE_SCRIPT", self.script), patch.object(sh, "UPDATE_STATE", self.state), \
                patch.object(sh.subprocess, "run", run or unittest.mock.Mock()) as mocked:
            return sh.check_update(self.now), mocked

    def _write(self, hours_ago, behind, local="0.4.4", remote="0.4.5"):
        at = (self.now - timedelta(hours=hours_ago)).isoformat()
        self.state.write_text(json.dumps({"checked_at": at, "behind": behind, "local": local, "remote": remote}))

    def test_no_git_checkout_no_line(self):
        (self.repo / ".git").rmdir()
        result, mocked = self._check()
        self.assertIsNone(result)
        mocked.assert_not_called()

    def test_fresh_state_no_fetch(self):
        self._write(2, 0, "0.4.4", "0.4.4")
        result, mocked = self._check()
        mocked.assert_not_called()
        self.assertEqual(result["level"], "ok")

    def test_update_available_is_warn(self):
        self._write(2, 1)
        result, _ = self._check()
        self.assertEqual(result["level"], "warn")
        self.assertIn("0.4.4 → 0.4.5", result["detail"])

    def test_stale_state_triggers_check(self):
        self._write(30, 0, "0.4.4", "0.4.4")

        def fake_run(*args, **kwargs):
            self._write(0, 2, "0.4.4", "0.4.6")

        result, _ = self._check(run=fake_run)
        self.assertEqual(result["level"], "warn")
        self.assertIn("0.4.6", result["detail"])

    def test_offline_is_ok(self):
        result, _ = self._check(run=unittest.mock.Mock(side_effect=OSError("offline")))
        self.assertEqual(result["level"], "ok")
