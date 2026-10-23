"""Speicherwaechter fuer Firefox-Tabs (bridge/polaris/tab_memory.py, 2026-10-23).
Laeuft gegen ein nachgebautes /proc in einem Temp-Verzeichnis."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from conftest_helpers import TempHome

from polaris import config, native_host, tab_memory

GIB = tab_memory.GIB


class FakeProc:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="polaris-proc-"))

    def add(self, pid, ppid, cmdline, rss_kb=0, swap_kb=0, comm="firefox"):
        d = self.root / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} 1 1 0 -1\n")
        # Wie Firefox: Argumente als eine Zeichenkette mit Leerzeichen.
        (d / "cmdline").write_bytes(cmdline.encode() + b"\0")
        (d / "status").write_text(f"Name:\t{comm}\nVmRSS:\t{rss_kb} kB\nVmSwap:\t{swap_kb} kB\n")


def firefox_tree(tab_sizes_kb):
    """Hauptprozess 100, Forkserver 101, Tabs ab 200 (RSS, Swap in kB)."""
    p = FakeProc()
    p.add(100, 1, "/usr/lib/firefox/firefox")
    p.add(101, 100, "/usr/lib/firefox/firefox -contentproc -ipcHandle 0", comm="forkserver")
    p.add(150, 100, "python3 -m polaris.native_host", comm="python3")
    for i, (rss, swap) in enumerate(tab_sizes_kb):
        p.add(200 + i, 101, f"/usr/lib/firefox/firefox -contentproc -isForBrowser -appDir x {i + 1} tab",
              rss_kb=rss, swap_kb=swap, comm="Isolated Web Co")
    # Nicht-Tab-Prozess und fremder Firefox
    p.add(300, 101, "/usr/lib/firefox/firefox -contentproc -appDir x 9 socket", rss_kb=9 * 1024 ** 2)
    p.add(400, 999, "/usr/lib/firefox/firefox -contentproc -appDir x 1 tab", rss_kb=50 * 1024 ** 2)
    return p


class TestTabProcesses(unittest.TestCase):
    def test_finds_tabs_under_forkserver_and_counts_swap(self):
        p = firefox_tree([(1024 ** 2, 0), (2 * 1024 ** 2, 3 * 1024 ** 2)])
        with mock.patch.object(tab_memory, "PROC", p.root):
            procs = tab_memory.tab_processes(100)
        self.assertEqual([x["pid"] for x in procs], [201, 200])
        self.assertEqual(procs[0]["bytes"], 5 * GIB)

    def test_levels(self):
        self.assertEqual(tab_memory.level_for(4 * GIB - 1), "ok")
        self.assertEqual(tab_memory.level_for(4 * GIB), "warn")
        self.assertEqual(tab_memory.level_for(8 * GIB), "critical")


class TestNotifyRules(unittest.TestCase):
    def test_rules(self):
        n = tab_memory.should_notify
        self.assertFalse(n({}, 1, "ok", 0))
        self.assertTrue(n({}, 1, "warn", 0))
        self.assertFalse(n({"pid": 1, "level": "warn", "at": 0}, 1, "warn", 9999))
        self.assertTrue(n({"pid": 1, "level": "warn", "at": 0}, 1, "critical", 1))
        self.assertTrue(n({"pid": 1, "level": "warn", "at": 0}, 2, "warn", 1))
        self.assertFalse(n({"pid": 1, "level": "critical", "at": 0}, 1, "critical", 599))
        self.assertTrue(n({"pid": 1, "level": "critical", "at": 0}, 1, "critical", 600))


class TestCheck(unittest.TestCase):
    def test_warns_once_then_resets(self):
        with TempHome():
            state = config.STATE_DIR / "tab-memory.json"
            big = firefox_tree([(5 * 1024 ** 2, 0)])
            with mock.patch.object(tab_memory, "PROC", big.root), \
                    mock.patch.object(tab_memory, "_notify", return_value=True) as notify:
                r1 = tab_memory.check(browser_pid=100, now=1000)
                r2 = tab_memory.check(browser_pid=100, now=1060)
            self.assertEqual((r1["level"], r1["notified"]), ("warn", True))
            self.assertEqual((r2["level"], r2["notified"]), ("warn", False))
            self.assertEqual(notify.call_count, 1)
            self.assertEqual(json.loads(state.read_text())["pid"], 200)
            small = firefox_tree([(1024 ** 2, 0)])
            with mock.patch.object(tab_memory, "PROC", small.root):
                r3 = tab_memory.check(browser_pid=100, now=1120)
            self.assertEqual(r3["level"], "ok")
            self.assertFalse(state.exists())

    def test_native_host_message(self):
        p = firefox_tree([(9 * 1024 ** 2, 0)])
        with TempHome(), mock.patch.object(tab_memory, "PROC", p.root), \
                mock.patch.object(tab_memory.os, "getppid", return_value=100):
            reply = native_host.handle({"id": 4, "type": "tab-memory"})
        self.assertEqual(reply["id"], 4)
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["level"], "critical")
        self.assertEqual(reply["largest"], {"pid": 200, "bytes": 9 * GIB})
        self.assertFalse(reply["notified"])  # POLARIS_NO_NOTIFY in Tests


if __name__ == "__main__":
    unittest.main()
