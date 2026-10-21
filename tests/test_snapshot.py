"""Chat-Abbilder (Architektur seit 2026-09-29): rohes Unterhaltungs-JSON der
ChatGPT-Web-Schnittstelle -> Turns, nur in der Bridge interpretiert."""

import json
import unittest

from conftest_helpers import TempHome, make_event

from polaris import config, mapping, pipeline, snapshot, storage

T0 = 1790000000


def msg(mid, role, text=None, *, ctype="text", recipient="all", status="finished_successfully",
        end_turn=None, hidden=False, t=0, attachments=None):
    m = {"id": mid, "author": {"role": role}, "create_time": T0 + t, "recipient": recipient,
         "status": status, "content": {"content_type": ctype, "parts": [text] if text is not None else []},
         "metadata": {"is_visually_hidden_from_conversation": hidden}}
    if end_turn is not None:
        m["end_turn"] = end_turn
    if attachments:
        m["metadata"]["attachments"] = [{"name": n} for n in attachments]
    return m


def conv(messages, conv_id="c-1", gizmo="g-p-abc1-abc", title="Chat"):
    mapping_ = {"root": {"id": "root", "parent": None, "children": [], "message": None}}
    parent = "root"
    for m in messages:
        mapping_[m["id"]] = {"id": m["id"], "parent": parent, "children": [], "message": m}
        parent = m["id"]
    return {"conversation_id": conv_id, "title": title, "current_node": parent,
            "mapping": mapping_, **({"gizmo_id": gizmo} if gizmo else {})}


def snap(c, hint_project="g-p-abc1", hint_conv=None, captured_at="2026-09-29T12:00:00+00:00"):
    return {"polaris_snapshot": 1, "source": "chatgpt", "conversation": c,
            "hint": {"project_id": hint_project, "conversation_id": hint_conv or c["conversation_id"]},
            "captured_at": captured_at}


def now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def put(s, name="snapshot__x.json"):
    (config.INBOX_DIR / name).write_text(json.dumps(s), encoding="utf-8")


class TestTurns(unittest.TestCase):
    def test_grouping_ids_completeness(self):
        c = conv([
            msg("u1", "user", "Frage", t=1),
            msg("th", "assistant", None, ctype="thoughts", t=2),
            msg("call", "assistant", "search()", recipient="browser", t=3),
            msg("tool", "tool", "Ergebnis-Rohdaten", t=4),
            msg("a2", "assistant", "Antwort", t=5),
            msg("u2", "user", None, t=6, attachments=["Plan.pdf"]),
            msg("a3", "assistant", "Ich schaue nach", t=7),
            msg("call2", "assistant", "search()", recipient="browser", t=8),
        ])
        events = snapshot.events_from_conversation(c, "x")
        got = [(e["message"]["id"], e["message"]["role"], e["message"]["content"]) for e in events]
        # Turn-ID = frueheste Nachricht; letzter Turn laeuft noch (Werkzeugschritt) -> fehlt
        self.assertEqual(got, [("u1", "user", "Frage"), ("th", "assistant", "Antwort"), ("u2", "user", "")])
        self.assertEqual(events[1]["message"]["member_ids"], ["th", "call", "tool", "a2"])
        self.assertEqual(events[2]["message"]["attachments"], ["Plan.pdf"])
        self.assertEqual(events[0]["project"]["id"], "g-p-abc1")

    def test_last_turn_in_progress_or_end_turn_false_is_held_back(self):
        for last in (msg("a1", "assistant", "Hal", status="in_progress", t=2),
                     msg("a1", "assistant", "Hal", end_turn=False, t=2)):
            events = snapshot.events_from_conversation(conv([msg("u1", "user", "F", t=1), last]), "x")
            self.assertEqual([e["message"]["id"] for e in events], ["u1"])
        # andere Endzustaende (z. B. vom Nutzer gestoppt) gelten als fertig
        done = msg("a1", "assistant", "Halb", status="finished_partial_completion", t=2)
        events = snapshot.events_from_conversation(conv([msg("u1", "user", "F", t=1), done]), "x")
        self.assertEqual([e["message"]["id"] for e in events], ["u1", "a1"])

    def test_no_project_no_events(self):
        self.assertEqual(snapshot.events_from_conversation(conv([msg("u1", "user", "F")], gizmo=None), "x"), [])


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.h = TempHome()
        self.h.__enter__()
        self.root = self.h.make_project_root("ABC", "Falke")
        mapping.manual_map("g-p-abc1", self.root / "ABC", "ABC")
        mapping.manual_map("g-p-lau1", self.root / "Falke", "Falke")

    def tearDown(self):
        self.h.__exit__(None, None, None)

    def files(self, folder="ABC"):
        d = self.root / folder / "raw" / "chatgpt" / "messages"
        return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []

    def test_snapshot_is_stored_under_provider_project(self):
        put(snap(conv([msg("u1", "user", "F", t=1), msg("a1", "assistant", "A", t=2)])))
        pipeline.process_inbox()
        self.assertEqual(self.files(), ["a1.json", "u1.json"])
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_expansion_never_writes_into_the_watched_inbox(self):
        # 2026-09-29: jede Datei im Inbox-Ordner startet die Path-Unit; viele
        # zerlegte Turns legten Service und Path-Unit per Start-Limit still.
        from unittest.mock import patch
        written = []
        real = pipeline.storage.atomic_write_json

        def spy(path, data):
            written.append(path)
            return real(path, data)

        put(snap(conv([msg(f"u{i}", "user", "F", t=i) for i in range(5)])))
        with patch.object(pipeline.storage, "atomic_write_json", side_effect=spy):
            pipeline.process_inbox()
        self.assertFalse([p for p in written if p.parent == config.INBOX_DIR])
        self.assertEqual(len(self.files()), 5)

    def test_hint_mismatch_stores_nothing(self):
        put(snap(conv([msg("u1", "user", "F")]), hint_project="g-p-lau1"))
        counts = pipeline.process_inbox()
        self.assertEqual(counts.get("failed"), 1)
        self.assertEqual(self.files(), [])
        self.assertEqual(self.files("Falke"), [])

    def test_incomplete_answer_arrives_with_next_snapshot(self):
        base = [msg("u1", "user", "F", t=1)]
        put(snap(conv(base + [msg("a1", "assistant", "Hal", status="in_progress", t=2)])), "s1.json")
        pipeline.process_inbox()
        self.assertEqual(self.files(), ["u1.json"])
        put(snap(conv(base + [msg("a1", "assistant", "Hallo Welt", t=2)])), "s2.json")
        pipeline.process_inbox()
        self.assertEqual(self.files(), ["a1.json", "u1.json"])
        stored = json.loads((self.root / "ABC" / "raw" / "chatgpt" / "messages" / "a1.json").read_text())
        self.assertEqual(stored["message"]["content"], "Hallo Welt")

    def test_turn_archived_by_dom_under_other_member_id_is_not_duplicated(self):
        # DOM hat den Turn frueher unter "a2" (erste ID der Such-Einheit) archiviert.
        dom = make_event(project_id="g-p-abc1", project_name="ABC", conversation_id="c-1",
                         message_id="a2", role="assistant", content="Antwort")
        (config.INBOX_DIR / "dom.json").write_text(json.dumps(dom), encoding="utf-8")
        pipeline.process_inbox()
        put(snap(conv([msg("u1", "user", "F", t=1), msg("th", "assistant", None, ctype="thoughts", t=2),
                       msg("a2", "assistant", "Antwort", t=3)])))
        pipeline.process_inbox()
        self.assertEqual(self.files(), ["a2.json", "u1.json"])  # kein th.json
        conv_file = json.loads((self.root / "ABC" / "raw" / "chatgpt" / "conversations" / "c-1.json").read_text())
        self.assertEqual(conv_file["members"]["th"], "a2")

    def test_member_id_archived_in_other_project_is_a_conflict(self):
        other = make_event(project_id="g-p-lau1", project_name="Falke", conversation_id="c-9",
                           message_id="a2", role="assistant", content="Antwort")
        (config.INBOX_DIR / "l.json").write_text(json.dumps(other), encoding="utf-8")
        pipeline.process_inbox()
        put(snap(conv([msg("u1", "user", "F", t=1), msg("th", "assistant", None, ctype="thoughts", t=2),
                       msg("a2", "assistant", "Antwort", t=3)])))
        counts = pipeline.process_inbox()
        self.assertEqual(counts.get("conflict"), 1)
        self.assertEqual(self.files(), ["u1.json"])

    def test_non_project_chat_is_ignored(self):
        put(snap(conv([msg("u1", "user", "F")], gizmo=None), hint_project=None))
        pipeline.process_inbox()
        self.assertEqual(self.files(), [])
        self.assertEqual(self.files("Falke"), [])

    def test_only_latest_snapshot_per_chat_is_kept(self):
        for i in range(3):
            put(snap(conv([msg("u1", "user", "F", t=1)])), f"s{i}.json")
            pipeline.process_inbox()
        kept = sorted(p.name for p in config.PROCESSED_DIR.glob("snapshot__*"))
        self.assertEqual(kept, ["snapshot__c-1.latest.json"])


    def test_dom_fallback_rejected_after_provider_said_no_project(self):
        """Architektur-Review 2026-09-29: aus dem Projekt genommener Chat, die
        Seite zeigt noch die alte Projekt-Adresse -> nicht archivieren."""
        c = conv([msg("u1", "user", "F")], gizmo=None)
        c["gizmo_id"] = None  # Anbieter meldet ausdruecklich: kein Projekt
        put(snap(c, hint_project=None))
        pipeline.process_inbox()
        (config.INBOX_DIR / "dom.json").write_text(json.dumps(
            make_event(project_id="g-p-abc1", project_name="ABC", conversation_id="c-1", message_id="u1")))
        counts = pipeline.process_inbox()
        self.assertEqual(counts.get("failed"), 1)
        self.assertEqual(self.files(), [])
        # Wieder in einem Projekt (Abbild mit gizmo_id): Sperre aufgehoben.
        put(snap(conv([msg("u1", "user", "F", t=1), msg("a1", "assistant", "A", t=2)])))
        pipeline.process_inbox()
        self.assertEqual(self.files(), ["a1.json", "u1.json"])

    def test_format_change_keeps_dom_fallback_open(self):
        """Fehlt gizmo_id ganz (Formatwechsel), bleibt der Seiten-Ersatzweg
        offen (Review 2026-09-29, H1)."""
        put(snap(conv([msg("u1", "user", "F")], gizmo=None)))  # Seite: Projekt, Antwort: kein Feld
        counts = pipeline.process_inbox()
        self.assertEqual(counts.get("failed"), 1)  # snapshot-no-project, sichtbar
        (config.INBOX_DIR / "dom.json").write_text(json.dumps(
            make_event(project_id="g-p-abc1", project_name="ABC", conversation_id="c-1", message_id="u1")))
        pipeline.process_inbox()
        self.assertEqual(self.files(), ["u1.json"])


    def _domx(self, name, project, conv_id, mid):
        (config.INBOX_DIR / name).write_text(json.dumps(
            make_event(project_id=project, project_name="x", conversation_id=conv_id, message_id=mid)))

    def test_stale_dom_quarantined_when_provider_evidences_both_sides(self):
        """2026-09-30: HTTP 429 -> Seiten-Ersatzweg nach Chatwechsel; die Seite
        zeigte noch den ABC-Chat unter der Falke-Adresse."""
        put(snap(conv([msg("u1", "user", "Aktivitaeten", t=1), msg("a1", "assistant", "MS 6727.01", t=2)]),
                 captured_at="2026-09-30T12:00:00+00:00"))
        pipeline.process_inbox()
        put(snap(conv([msg("x1", "user", "Falke", t=3)], conv_id="c-lau", gizmo="g-p-lau1-l"), hint_project="g-p-lau1",
                 captured_at="2026-09-30T12:05:00+00:00"), name="snapshot__lau.json")
        pipeline.process_inbox()
        for mid in ("u1", "a1"):
            self._domx(f"dom-{mid}.json", "g-p-lau1", "c-lau", mid)
        counts = pipeline.process_inbox()
        self.assertEqual(counts.get("stale-dom"), 2)
        self.assertFalse(counts.get("conflict"))
        self.assertEqual(self.files("Falke"), ["x1.json"])
        self.assertEqual(self.files(), ["a1.json", "u1.json"])

    def test_without_fresh_snapshot_of_claimed_chat_it_stays_a_conflict(self):
        put(snap(conv([msg("u1", "user", "Aktivitaeten", t=1)])))
        pipeline.process_inbox()
        self._domx("dom.json", "g-p-lau1", "c-lau", "u1")
        self.assertEqual(pipeline.process_inbox().get("conflict"), 1)

    def test_wrong_existing_dom_copy_is_never_confirmed(self):
        """Review HIGH: eine schon falsch archivierte Seiten-Kopie darf die
        richtige nicht verdraengen."""
        self._domx("wrong.json", "g-p-lau1", "c-lau", "m1")   # falsch unter Falke
        pipeline.process_inbox()
        put(snap(conv([msg("z1", "user", "ABC alt", t=1)], conv_id="c-abc"), captured_at="2026-09-30T12:05:00+00:00"))
        pipeline.process_inbox()                              # ABC-Abbild ohne m1 (aelter als die Nachricht)
        self._domx("right.json", "g-p-abc1", "c-abc", "m1")
        self.assertEqual(pipeline.process_inbox().get("conflict"), 1)

    def test_moved_chat_and_branch_stay_conflicts(self):
        put(snap(conv([msg("u1", "user", "A", t=1)], conv_id="c-a"), captured_at="2026-09-30T12:00:00+00:00"))
        pipeline.process_inbox()
        self._domx("moved.json", "g-p-lau1", "c-a", "u1")     # derselbe Chat, jetzt Falke
        self.assertEqual(pipeline.process_inbox().get("conflict"), 1)
        # Branch: neuer Chat mit geteilter ID, sein Abbild enthaelt sie
        put(snap(conv([msg("u1", "user", "A", t=1), msg("b1", "user", "B", t=5)], conv_id="c-b", gizmo="g-p-lau1-l"),
                 hint_project="g-p-lau1", captured_at="2026-09-30T12:10:00+00:00"), name="snapshot__b.json")
        counts = pipeline.process_inbox()
        self.assertFalse(counts.get("stale-dom"))

    def test_dom_conflict_resolved_by_later_snapshot_of_the_claimed_chat(self):
        self._domx("a.json", "g-p-abc1", "c-abc", "m1")
        pipeline.process_inbox()
        self._domx("b.json", "g-p-lau1", "c-lau", "m1")
        self.assertEqual(pipeline.process_inbox().get("conflict"), 1)   # noch kein Beleg -> Konflikt
        put(snap(conv([msg("m1", "user", "ABC", t=1)], conv_id="c-abc"), captured_at="2026-09-30T12:00:00+00:00"))
        pipeline.process_inbox()                                         # ABC bestaetigt m1
        self.assertEqual(len(list(config.CONFLICTS_DIR.glob("*.json"))), 1)
        put(snap(conv([msg("x1", "user", "Falke-Frage", t=1)], conv_id="c-lau", gizmo="g-p-lau1-falke"),
                 hint_project="g-p-lau1", captured_at="2026-09-30T12:05:00+00:00"), name="snapshot__l.json")
        pipeline.process_inbox()                                         # Falke ohne m1 -> loest sich
        self.assertEqual(list(config.CONFLICTS_DIR.glob("*.json")), [])
        self.assertEqual(self.files("Falke"), ["x1.json"])

    def test_stale_dom_copy_already_archived_is_replaced_by_api_copy(self):
        """2026-09-30 14:28/14:30: "bitte für heute" kam per Seite unter
        Falke an (ABC hatte die ID noch nicht), dann per Abbild fuer ABC."""
        self._domx("d.json", "g-p-lau1", "c-lau", "m1")               # 12:28 veraltete Seite -> Falke
        pipeline.process_inbox()
        self.assertEqual(self.files("Falke"), ["m1.json"])
        put(snap(conv([msg("x1", "user", "Falke", t=1)], conv_id="c-lau", gizmo="g-p-lau1-l"), hint_project="g-p-lau1",
                 captured_at="2099-01-01T00:00:00+00:00"), name="snapshot__l.json")   # juenger, ohne m1
        pipeline.process_inbox()
        put(snap(conv([msg("m1", "user", "bitte fuer heute", t=2)], conv_id="c-abc"),
                 captured_at="2099-01-01T00:01:00+00:00"))
        counts = pipeline.process_inbox()
        self.assertFalse(counts.get("conflict"))
        self.assertEqual(self.files("Falke"), ["x1.json"])
        self.assertEqual(self.files(), ["m1.json"])
        idx = json.loads((self.root / "Falke" / "raw" / "chatgpt" / "conversations" / "c-lau.json").read_text())
        self.assertNotIn("m1", idx.get("known_message_ids", []))

    def test_already_curated_stale_copy_stays_a_conflict(self):
        self._domx("d.json", "g-p-lau1", "c-lau", "m1")
        pipeline.process_inbox()
        pol = self.root / "Falke" / ".polaris"
        pol.mkdir(parents=True, exist_ok=True)
        (pol / "curator-state.json").write_text(json.dumps({"processed_message_ids": ["m1"]}))
        put(snap(conv([msg("x1", "user", "Falke", t=1)], conv_id="c-lau", gizmo="g-p-lau1-l"), hint_project="g-p-lau1",
                 captured_at="2099-01-01T00:00:00+00:00"), name="snapshot__l.json")
        pipeline.process_inbox()
        put(snap(conv([msg("m1", "user", "x", t=2)], conv_id="c-abc"), captured_at="2099-01-01T00:01:00+00:00"))
        self.assertEqual(pipeline.process_inbox().get("conflict"), 1)   # sichtbar, mit Ruecknahme-Hinweis beim Klaeren
        self.assertIn("m1.json", self.files("Falke"))

    def test_existing_dom_copy_kept_without_fresh_evidence(self):
        self._domx("d.json", "g-p-lau1", "c-lau", "m1")
        pipeline.process_inbox()
        put(snap(conv([msg("m1", "user", "x", t=2)], conv_id="c-abc")))   # kein Falke-Abbild
        self.assertEqual(pipeline.process_inbox().get("conflict"), 1)
        self.assertEqual(self.files("Falke"), ["m1.json"])

    def test_dom_vs_dom_still_a_conflict(self):
        (config.INBOX_DIR / "a.json").write_text(json.dumps(
            make_event(project_id="g-p-abc1", project_name="ABC", conversation_id="c-abc", message_id="m1")))
        pipeline.process_inbox()
        (config.INBOX_DIR / "b.json").write_text(json.dumps(
            make_event(project_id="g-p-lau1", project_name="Falke", conversation_id="c-lau", message_id="m1")))
        counts = pipeline.process_inbox()
        self.assertEqual(counts.get("conflict"), 1)


class TestRound3(TestIngest):
    """Review Runde 3 (2026-09-29)."""

    def _dom(self, mid, content, conv_id="c-1", role="assistant", members=None, name="dom.json"):
        e = make_event(project_id="g-p-abc1", project_name="ABC", conversation_id=conv_id,
                       message_id=mid, role=role, content=content)
        if members is not None:
            e["message"]["member_ids"] = members
        (config.INBOX_DIR / name).write_text(json.dumps(e), encoding="utf-8")

    def test_h1_dom_single_id_after_snapshot_is_not_duplicated(self):
        put(snap(conv([msg("u1", "user", "F", t=1), msg("th", "assistant", None, ctype="thoughts", t=2),
                       msg("a2", "assistant", "Antwort", t=3)])))
        pipeline.process_inbox()
        self._dom("a2", "Antwort", members=["a2"])
        pipeline.process_inbox()
        self.assertEqual(self.files(), ["th.json", "u1.json"])

    def test_h2_import_after_dom_is_not_duplicated(self):
        import zipfile, tempfile
        from polaris.export_import import import_export
        self._dom("a2", "Antwort")
        pipeline.process_inbox()
        c = conv([msg("u1", "user", "F", t=1), msg("th", "assistant", None, ctype="thoughts", t=2),
                  msg("a2", "assistant", "Antwort", t=3)])
        z = Path(tempfile.mkdtemp()) / "e.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("conversations.json", json.dumps([c]))
        import_export(z)
        self.assertEqual(self.files(), ["a2.json", "u1.json"])

    def test_h3_dom_member_ids_never_steer_canonical_ids(self):
        # DOM-Einheit fasst (hypothetisch) zwei Turns zusammen
        self._dom("a1", "A eins", members=["a1", "a3"])
        pipeline.process_inbox()
        put(snap(conv([msg("u1", "user", "F", t=1), msg("a1", "assistant", "A eins", t=2),
                       msg("u2", "user", "G", t=3), msg("th3", "assistant", None, ctype="thoughts", t=4),
                       msg("a3", "assistant", "A drei", t=5)])))
        pipeline.process_inbox()
        self.assertIn("th3.json", self.files())
        self.assertNotIn("a1.rev2.json", self.files())

    def test_m1_idle_chat_completes_last_turn(self):
        import time
        c = conv([msg("u1", "user", "F", t=1), msg("a1", "assistant", "Bild", t=2),
                  msg("img", "tool", "bild.png", t=3)])
        c["update_time"] = time.time() - 600
        put(snap(c, captured_at=now_iso()))  # beim Erfassen seit 10 min ruhig
        pipeline.process_inbox()
        self.assertIn("a1.json", self.files())

    def test_m2_canvas_and_code_are_content(self):
        import time
        canvas = msg("cv", "assistant", json.dumps({"name": "Plan", "content": "Schritt 1\nSchritt 2"}),
                     recipient="canmore.create_textdoc", t=2)
        code = msg("cd", "assistant", None, t=3)
        code["content"] = {"content_type": "code", "text": "print(1)"}
        c = conv([msg("u1", "user", "F", t=1), canvas, code, msg("a1", "assistant", "Fertig", t=4)])
        c["update_time"] = time.time() - 600
        events = snapshot.events_from_conversation(c, "x")
        body = events[1]["message"]["content"]
        self.assertIn("[Canvas: Plan]", body)
        self.assertIn("Schritt 2", body)
        self.assertIn("print(1)", body)

    def test_m3_active_branch_is_recorded(self):
        put(snap(conv([msg("u1", "user", "F", t=1), msg("a1", "assistant", "A", t=2)])))
        pipeline.process_inbox()
        put(snap(conv([msg("u1", "user", "F", t=1), msg("a1", "assistant", "A", t=2)])), "s2.json")
        pipeline.process_inbox()
        data = json.loads((self.root / "ABC" / "raw" / "chatgpt" / "conversations" / "c-1.json").read_text())
        self.assertEqual(data["active_message_ids"], ["u1", "a1"])

    def test_m4_native_host_reports_rejected_snapshot(self):
        from polaris import native_host
        reply = native_host.handle({"id": 1, "type": "export-snapshot",
                                    "snapshot": snap(conv([msg("u1", "user", "F")]), hint_project="g-p-lau1")})
        self.assertFalse(reply["ok"])
        self.assertIn("snapshot-mismatch", reply["error"])

    def test_m5_citation_marks_do_not_create_revision(self):
        self._dom("a1", "Laut Quelle gilt X.")
        pipeline.process_inbox()
        put(snap(conv([msg("u1", "user", "F", t=1),
                       msg("a1", "assistant", "Laut Quelle gilt X. \ue200cite\ue202turn0search0\ue201", t=2)])))
        pipeline.process_inbox()
        self.assertNotIn("a1.rev2.json", self.files())

    def test_l1_processed_stays_bounded(self):
        for i in range(3):
            put(snap(conv([msg(f"u{j}", "user", "F", t=j) for j in range(10)])), f"s{i}.json")
            pipeline.process_inbox()
        self.assertEqual(len(list(config.PROCESSED_DIR.glob("*.json"))), 1)

    def test_audit_no_gizmo_but_project_hint_is_rejected_visibly(self):
        put(snap(conv([msg("u1", "user", "F")], gizmo=None)))
        counts = pipeline.process_inbox()
        self.assertEqual(counts.get("failed"), 1)

    def test_audit_unknown_answer_shape_is_rejected_visibly(self):
        import time
        weird = msg("a1", "assistant", None, t=2)
        weird["content"] = {"content_type": "neues_format", "blocks": ["Antwort"]}
        c = conv([msg("u1", "user", "F", t=1), weird])
        c["update_time"] = time.time() - 600
        put(snap(c, captured_at=now_iso()))
        counts = pipeline.process_inbox()
        self.assertEqual(counts.get("failed"), 1)
        self.assertEqual(self.files(), [])

    def test_corrupt_conversation_index_means_retry(self):
        self._dom("a1", "A")
        pipeline.process_inbox()
        (self.root / "ABC" / "raw" / "chatgpt" / "conversations" / "c-1.json").write_text("{kaputt", encoding="utf-8")
        self._dom("a9", "B", name="d2.json")
        counts = pipeline.process_inbox()
        self.assertEqual(counts.get("retry"), 1)

    def test_r5_late_processing_never_releases_a_running_answer(self):
        # Review Runde 5, F1: mitten in der Antwort erfasst, spaet verarbeitet -
        # darf NICHT als fertig gelten (Ruhe wird am Erfassungszeitpunkt gemessen).
        import time
        c = conv([msg("u1", "user", "F", t=1), msg("a1", "assistant", "Teil 1", status="in_progress", t=2)])
        c["update_time"] = time.time() - 3600
        put(snap(c, captured_at=now_iso()))  # selbst "ruhig beim Erfassen": in_progress bleibt offen
        pipeline.process_inbox()
        self.assertEqual(self.files(), ["u1.json"])
        c2 = conv([msg("u1", "user", "F", t=1), msg("a1", "assistant", "Ich suche", t=2),
                   msg("call", "assistant", "x()", recipient="browser", t=3)])
        c2["conversation_id"] = "c-2"
        c2["update_time"] = time.time() - 10
        put(snap(c2, captured_at=now_iso()), "s2.json")  # mitten im Werkzeugschritt erfasst
        pipeline.process_inbox()
        self.assertNotIn("a1.rev2.json", self.files())

    def test_r5_held_back_answer_is_recorded_and_cleared(self):
        import time
        c = conv([msg("u1", "user", "F", t=1), msg("a1", "assistant", "Bild", t=2), msg("img", "tool", "x", t=3)])
        c["update_time"] = time.time() - 5
        put(snap(c, captured_at=now_iso()))
        pipeline.process_inbox()
        held = json.loads((config.STATE_DIR / "held-back.json").read_text())
        self.assertIn("g-p-abc1/c-1", held)
        c["update_time"] = time.time() - 600  # Extension holt nach 190 s Ruhe neu
        put(snap(c, captured_at=now_iso()), "s2.json")
        pipeline.process_inbox()
        self.assertIn("a1.json", self.files())
        self.assertEqual(json.loads((config.STATE_DIR / "held-back.json").read_text()), {})
    def test_r6_held_back_cleared_when_chat_leaves_project_and_pending_question_recorded(self):
        import time
        # Frage gestellt, Antwort noch nicht gespeichert -> vermerkt
        c = conv([msg("u1", "user", "F", t=1)])
        c["update_time"] = time.time() - 5
        put(snap(c, captured_at=now_iso()))
        pipeline.process_inbox()
        held = json.loads((config.STATE_DIR / "held-back.json").read_text())
        self.assertIn("g-p-abc1/c-1", held)
        # Chat aus dem Projekt genommen (kein gizmo_id, kein Hinweis) -> Eintrag weg
        c2 = conv([msg("u1", "user", "F", t=1)], gizmo=None)
        put(snap(c2, hint_project=None, captured_at=now_iso()), "s2.json")
        pipeline.process_inbox()
        self.assertEqual(json.loads((config.STATE_DIR / "held-back.json").read_text()), {})

    def test_r6_stale_stream_releases_visible_part(self):
        import time
        c = conv([msg("u1", "user", "F", t=1), msg("a1", "assistant", "Sichtbarer Teil", status="in_progress", t=2)])
        c["update_time"] = time.time() - 2 * 86400
        put(snap(c, captured_at=now_iso()))
        pipeline.process_inbox()
        self.assertIn("a1.json", self.files())

    def test_r6_held_back_cli_clear(self):
        from polaris import cli
        import time
        c = conv([msg("u1", "user", "F", t=1)])
        c["update_time"] = time.time() - 5
        put(snap(c, captured_at=now_iso()))
        pipeline.process_inbox()
        self.assertEqual(cli.main(["held-back", "--clear", "all"]), 0)
        self.assertEqual(json.loads((config.STATE_DIR / "held-back.json").read_text()), {})


from pathlib import Path  # noqa: E402

if __name__ == "__main__":
    unittest.main()
