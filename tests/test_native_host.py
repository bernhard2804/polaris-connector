import io
import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from conftest_helpers import BRIDGE_ROOT, TempHome, make_event

from polaris import config, native_host


def frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return struct.pack("=I", len(body)) + body


def unframe_all(data: bytes) -> list[dict]:
    out, pos = [], 0
    while pos < len(data):
        (n,) = struct.unpack("=I", data[pos:pos + 4])
        out.append(json.loads(data[pos + 4:pos + 4 + n]))
        pos += 4 + n
    return out


class TestFraming(unittest.TestCase):
    def test_roundtrip(self):
        buf = io.BytesIO()
        native_host.write_message(buf, {"id": 1, "text": "Grüße"})
        buf.seek(0)
        self.assertEqual(native_host.read_message(buf, timeout=None), {"id": 1, "text": "Grüße"})

    def test_eof_returns_none(self):
        self.assertIsNone(native_host.read_message(io.BytesIO(b""), timeout=None))

    def test_oversized_message_is_rejected(self):
        buf = io.BytesIO(struct.pack("=I", native_host.MAX_MESSAGE_BYTES + 1))
        with self.assertRaises(ValueError):
            native_host.read_message(buf, timeout=None)


class TestHandle(unittest.TestCase):
    def test_ping(self):
        self.assertEqual(native_host.handle({"id": 7, "type": "ping"}), {"id": 7, "ok": True, "version": 1})

    def test_unknown_type(self):
        self.assertFalse(native_host.handle({"id": 1, "type": "rm -rf"})["ok"])

    def test_export_events_lands_in_project(self):
        with TempHome() as h:
            root = h.make_project_root("Musterfirma")
            event = make_event(project_id="proj_1", project_name="Musterfirma")
            reply = native_host.handle({"id": 3, "type": "export-events", "events": [event]})
            self.assertTrue(reply["ok"])
            self.assertEqual(reply["accepted"], [event["message"]["id"]])
            self.assertEqual(reply["counts"]["processed"], 1)
            msg = root / "Musterfirma" / "raw" / "chatgpt" / "messages" / f"{event['message']['id']}.json"
            self.assertTrue(msg.is_file())
            self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [], "Inbox ist danach leer")

    def test_invalid_event_is_not_lost_silently(self):
        with TempHome():
            reply = native_host.handle({"id": 4, "type": "export-events", "events": [{"kaputt": True}]})
            # Wird angenommen (liegt als Datei vor) und von der Pipeline als failed einsortiert.
            self.assertEqual(reply["counts"]["failed"], 1)
            self.assertEqual(len(list(config.FAILED_DIR.glob("*.json"))), 1)

    def test_filename_cannot_escape_inbox(self):
        with TempHome():
            event = make_event(project_id="../../etc", project_name="x")
            event["conversation"]["id"] = "../../evil"
            native_host._write_to_inbox(event)
            written = list(config.INBOX_DIR.glob("*.json"))
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0].parent, config.INBOX_DIR)


class TestProjectMapping(unittest.TestCase):
    def test_list_and_map_with_pending_reprocessing(self):
        with TempHome() as h:
            root = h.make_project_root("MusterFirma", "Falke")
            cfg = config.load_config()
            cfg.suggested_folder_for_claude = "MusterFirma"
            config.save_config(cfg)
            event = make_event(project_id="claude-p-x", project_name="COO")
            event["source"] = "claude"
            reply = native_host.handle({"id": 1, "type": "export-events", "events": [event]})
            self.assertEqual(reply["counts"]["pending"], 1)

            listing = native_host.handle({"id": 2, "type": "list-folders", "projectId": "claude-p-x"})
            self.assertEqual(listing["folders"], ["Falke", "MusterFirma"])
            self.assertIsNone(listing["current"])
            self.assertEqual(listing["suggested"], "MusterFirma")
            self.assertEqual(listing["pending"], 1)

            mapped = native_host.handle({"id": 3, "type": "map-project", "projectId": "claude-p-x",
                                         "projectName": "COO", "folder": "MusterFirma"})
            self.assertTrue(mapped["ok"])
            self.assertEqual(mapped["counts"]["processed"], 1)
            msg = root / "MusterFirma" / "raw" / "claude" / "messages" / f"{event['message']['id']}.json"
            self.assertTrue(msg.is_file())
            again = native_host.handle({"id": 4, "type": "list-folders", "projectId": "claude-p-x"})
            self.assertEqual((again["current"], again["suggested"], again["pending"]), ("MusterFirma", None, 0))

    def test_export_snapshot_lands_in_project(self):
        from test_snapshot import conv, msg, snap
        with TempHome() as h:
            root = h.make_project_root("ABC")
            from polaris import mapping
            mapping.manual_map("g-p-abc1", root / "ABC", "ABC")
            reply = native_host.handle({"id": 7, "type": "export-snapshot",
                                        "snapshot": snap(conv([msg("u1", "user", "F")]))})
            self.assertTrue(reply["ok"])
            self.assertTrue((root / "ABC" / "raw" / "chatgpt" / "messages" / "u1.json").is_file())
            bad = native_host.handle({"id": 8, "type": "export-snapshot", "snapshot": {"x": 1}})
            self.assertFalse(bad["ok"])

    def test_remap_and_shared_folder_need_confirmation(self):
        with TempHome() as h:
            root = h.make_project_root("ABC", "Falke")
            ok = native_host.handle({"id": 1, "type": "map-project", "projectId": "g-p-a", "folder": "ABC"})
            self.assertTrue(ok["ok"])
            remap = native_host.handle({"id": 2, "type": "map-project", "projectId": "g-p-a", "folder": "Falke"})
            self.assertFalse(remap["ok"])
            self.assertTrue(remap["needs_confirm"])
            self.assertIn("ABC", remap["error"])
            share = native_host.handle({"id": 3, "type": "map-project", "projectId": "g-p-b", "folder": "ABC"})
            self.assertTrue(share["needs_confirm"])
            forced = native_host.handle({"id": 4, "type": "map-project", "projectId": "g-p-a", "folder": "Falke", "confirm": True})
            self.assertTrue(forced["ok"])
            from polaris import mapping
            self.assertEqual(Path(mapping.load_store().get("g-p-a").local_path).name, "Falke")

    def test_map_rejects_unknown_or_escaping_folder(self):
        with TempHome() as h:
            h.make_project_root("MusterFirma")
            for folder in ("../../etc", "/tmp", "Gibtsnicht", ""):
                r = native_host.handle({"id": 1, "type": "map-project", "projectId": "claude-p-x", "folder": folder})
                self.assertFalse(r["ok"], folder)


class TestRealProcess(unittest.TestCase):
    def test_wrapper_speaks_native_messaging_over_stdio_and_exits_on_eof(self):
        home = tempfile.mkdtemp(prefix="polaris-native-")
        wrapper = BRIDGE_ROOT / "bin" / "polaris-native-host"
        proc = subprocess.run(
            [str(wrapper), "/fake/manifest.json", "polaris-chatgpt-connector@mstarchy.local"],
            input=frame({"id": 1, "type": "ping"}) + frame({"id": 2, "type": "nope"}),
            capture_output=True,
            env={**os.environ, "HOME": home},
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        replies = unframe_all(proc.stdout)
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertTrue(replies[0]["ok"])
        self.assertFalse(replies[1]["ok"])

    def test_second_message_is_answered_while_port_stays_open(self):
        # Firefox haelt den Port offen; zwei direkt aufeinander folgende
        # Nachrichten muessen beide beantwortet werden, ohne dass stdin endet.
        import select
        home = tempfile.mkdtemp(prefix="polaris-native-")
        wrapper = BRIDGE_ROOT / "bin" / "polaris-native-host"
        proc = subprocess.Popen(
            [str(wrapper), "/fake/manifest.json", "polaris-chatgpt-connector@mstarchy.local"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "HOME": home},
        )
        try:
            proc.stdin.write(frame({"id": 1, "type": "ping"}) + frame({"id": 2, "type": "ping"}))
            proc.stdin.flush()
            data = b""
            deadline = 20
            while len(unframe_all_safe(data)) < 2 and deadline > 0:
                ready, _, _ = select.select([proc.stdout], [], [], 1)
                deadline -= 1
                if ready:
                    data += os.read(proc.stdout.fileno(), 65536)
            self.assertEqual([r["id"] for r in unframe_all_safe(data)], [1, 2])
        finally:
            proc.stdin.close()
            proc.wait(timeout=30)
            proc.stdout.close()
            proc.stderr.close()


def unframe_all_safe(data: bytes) -> list[dict]:
    out, pos = [], 0
    while pos + 4 <= len(data):
        (n,) = struct.unpack("=I", data[pos:pos + 4])
        if pos + 4 + n > len(data):
            break
        out.append(json.loads(data[pos + 4:pos + 4 + n]))
        pos += 4 + n
    return out


if __name__ == "__main__":
    unittest.main()
