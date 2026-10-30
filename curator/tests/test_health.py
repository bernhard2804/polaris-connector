import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from conftest_helpers import TempProject

from polaris_curator import health


def _age(path: Path, days: float) -> None:
    t = time.time() - days * 86400
    os.utime(path, (t, t))


class TestHealth(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="polaris-health-"))
        self.patches = [
            patch.object(health, "BRIDGE_INBOX", self.tmp / "inbox"),
            patch.object(health, "BRIDGE_PENDING", self.tmp / "pending"),
            patch.object(health, "BRIDGE_FAILED", self.tmp / "failed"),
            patch.object(health, "BRIDGE_LOG", self.tmp / "polaris.log"),
            patch.object(health, "BRIDGE_RETRY", self.tmp / "retry.json"),
            patch.object(health, "CAPTURE_STATUS", self.tmp / "capture-status.json"),
            patch.object(health, "HELD_BACK", self.tmp / "held-back.json"),
            patch.object(health, "BRIDGE_CONFLICTS", self.tmp / "conflicts"),
            patch.object(health, "NOTIFY_STAMP", self.tmp / "stamp.json"),
        ]
        for p in self.patches:
            p.start()
        for d in ("inbox", "pending", "failed"):
            (self.tmp / d).mkdir()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_no_message_at_all_warns(self):
        with TempProject() as a:
            h = health.collect({"a": a}, 3)
            self.assertIn("Noch keine ChatGPT-Nachricht archiviert.", h["warnings"])

    def test_claude_only_does_not_warn(self):
        with TempProject() as a:
            d = a / "raw" / "claude" / "messages"
            d.mkdir(parents=True)
            (d / "m1.json").write_text("{}", encoding="utf-8")
            h = health.collect({"a": a}, 3)
            self.assertEqual(h["warnings"], [])

    def test_zero_days_disables_chatgpt_check(self):
        with TempProject() as a, TempProject() as b:
            self.assertEqual(health.collect({"a": a}, 0)["warnings"], [])
            _age(self._msg(b, "m1"), 20)
            self.assertEqual(health.collect({"b": b}, 0)["warnings"], [])

    def test_recent_message_in_one_project_is_healthy_even_if_others_idle(self):
        with TempProject() as a, TempProject() as b:
            _age(self._msg(a, "m1"), 0.5)
            _age(self._msg(b, "m2"), 20)  # ruhendes Projekt
            h = health.collect({"a": a, "b": b}, 3)
            self.assertEqual(h["warnings"], [])
            self.assertLess(h["days_since_chatgpt_message"], 1)

    def test_no_message_anywhere_for_too_long_warns(self):
        with TempProject() as a:
            _age(self._msg(a, "m1"), 11)
            h = health.collect({"a": a}, 3)
            self.assertEqual(len(h["warnings"]), 1)
            self.assertIn("Seit 11 Tagen", h["warnings"][0])

    def test_bridge_backlog_and_errors_warn(self):
        with TempProject() as a:
            self._msg(a, "m1")
            stale = self.tmp / "inbox" / "x.json"
            stale.write_text("{}")
            _age(stale, 1)
            (self.tmp / "inbox" / "fresh.json").write_text("{}")
            (self.tmp / "pending" / "g-p-1").mkdir()
            (self.tmp / "pending" / "g-p-1" / "p.json").write_text("{}")
            (self.tmp / "failed" / "f.json").write_text("{}")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            old = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
            (self.tmp / "polaris.log").write_text(f"{old},1 ERROR alt\n{now},1 ERROR neu\n{now},1 INFO ok\n")
            h = health.collect({"a": a}, 3)
            self.assertEqual(h["bridge"], {"inbox_stale": 1, "pending": 1, "failed_last_7_days": 1, "log_errors_last_24h": 1})
            self.assertEqual(len(h["warnings"]), 4)

    def test_log_errors_of_cleaned_up_failures_no_longer_count(self):
        # 2026-09-29: nach dem Aufraeumen von failed/ blieb der Check 24 h gelb.
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        (self.tmp / "failed" / "still.json").write_text("{}")
        (self.tmp / "polaris.log").write_text(
            f"{now},1 ERROR failed file=gone.json reason=invalid-schema: conversation.id fehlt\n"
            f"{now},1 ERROR failed file=still.json reason=conflict: message_id=m1 bereits archiviert\n"
            f"{now},1 ERROR etwas anderes\n"
        )
        self.assertEqual(health._recent_log_errors(datetime.now() - timedelta(hours=1)), 2)

    def test_stuck_retry_is_reported(self):
        with TempProject() as a:
            self._msg(a, "m1")
            (self.tmp / "inbox" / "x.json").write_text("{}")
            old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
            (self.tmp / "retry.json").write_text(json.dumps({
                "x.json": {"first": old, "count": 9, "last_error": "OSError: Read-only file system"},
                "weg.json": {"first": old, "count": 1, "last_error": "x"},
            }))
            h = health.collect({"a": a}, 3)
            self.assertTrue(any("lassen sich seit über einer Stunde nicht ablegen" in w and "Read-only" in w
                                for w in h["warnings"]))

    def test_notification_only_once_per_day_and_finding(self):
        calls = []
        with patch.dict(os.environ, {"POLARIS_NO_NOTIFY": ""}), patch.object(health.shutil, "which", return_value="/usr/bin/notify-send"), \
             patch.object(health.subprocess, "run", side_effect=lambda *a, **k: calls.append(a)):
            h = {"warnings": ["X"]}
            self.assertTrue(health.notify_once_per_day(h))
            self.assertFalse(health.notify_once_per_day(h))
            self.assertTrue(health.notify_once_per_day({"warnings": ["Y"]}))
            self.assertFalse(health.notify_once_per_day({"warnings": []}))
        self.assertEqual(len(calls), 2)

    def _msg(self, project: Path, name: str) -> Path:
        p = project / "raw" / "chatgpt" / "messages" / f"{name}.json"
        p.write_text(json.dumps({"id": name}))
        return p



class TestCaptureErrorFreshness(unittest.TestCase):
    def _check(self, err_age_h, ok_age_h=None):
        import json, tempfile
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
        d = Path(tempfile.mkdtemp()) / "capture-status.json"
        data = {"last_error": {"at": (now - timedelta(hours=err_age_h)).isoformat(), "error": "TypeError: NetworkError"}}
        if ok_age_h is not None:
            data["last_ok"] = (now - timedelta(hours=ok_age_h)).isoformat()
        d.write_text(json.dumps(data))
        with patch.object(health, "CAPTURE_STATUS", d):
            return health._capture_problem(now)

    def test_fresh_error_warns(self):
        self.assertIn("nicht nutzbar", self._check(1, ok_age_h=2))

    def test_old_single_error_is_not_reported(self):
        self.assertIsNone(self._check(16, ok_age_h=17))

    def test_error_followed_by_success_is_fine(self):
        self.assertIsNone(self._check(1, ok_age_h=0.5))

if __name__ == "__main__":
    unittest.main()
