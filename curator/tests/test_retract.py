import json
import unittest

from conftest_helpers import TempProject

from polaris_curator import layout, retract, state as state_mod

IDS_A = ["aaaaaaaa-0000-0000-0000-000000000001", "aaaaaaaa-0000-0000-0000-000000000002"]
IDS_B = ["bbbbbbbb-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"]
BAD = "cccccccc-0000-0000-0000-00000000000f"


def src(ids):
    return f"<!-- polaris-source: type=chatgpt conversation_id=c1 message_ids={','.join(ids)} captured_at=x -->"


DOC = "<!-- polaris-source: type=document path=inbox/documents/archive/processed/a b.pdf hash=123 -->"


def raw(tp, *ids):
    for i in ids:
        tp.write_message(i)


def write(project, knowledge="", decisions="", loops=""):
    if knowledge:
        layout.knowledge_path(project).write_text(knowledge, encoding="utf-8")
    if decisions:
        layout.decisions_path(project).write_text(decisions, encoding="utf-8")
    if loops:
        layout.open_loops_path(project).write_text(loops, encoding="utf-8")


KNOWLEDGE = f"""# Wissen

## Status
- **[Fakt]** Nur aus der fremden Nachricht
  - Scope: Projekt
  {src([BAD])}
- **[Fakt]** Gemischter Batch mit fremder Nachricht
  {src(IDS_A + [BAD])}
- **[Fakt]** Sauberer Batch
  {src(IDS_B)}
- **[Fakt]** Gemischt plus Dokument
  {src(IDS_A)}
  {DOC}
"""

DECISIONS = f"""# Entscheidungen

## 2026-09-29 – Fremd
**Entscheidung**
X

**Quelle**
{src([BAD])}

## 2026-09-29 – Sauber
**Entscheidung**
Y

**Quelle**
{src(IDS_B)}
"""

LOOPS = f"""# Offene Punkte

## Offen
- **[Sonstiges]** Punkt aus gemischtem Batch
{src(IDS_A + [BAD])}
- **[Sonstiges]** Sauberer Punkt
{src(IDS_B)}
"""


class TestRetract(unittest.TestCase):
    def test_dry_run_classifies_and_writes_nothing(self):
        with TempProject() as project:
            write(project, KNOWLEDGE, DECISIONS, LOOPS)
            before = layout.knowledge_path(project).read_text()

            res = retract.retract(project, [BAD])

            self.assertFalse(res.written)
            self.assertEqual(len(res.full), 2)  # Wissen + Entscheidung
            self.assertEqual(len(res.partial), 2)  # gemischter Wissens- und Loop-Eintrag
            self.assertEqual(layout.knowledge_path(project).read_text(), before)
            self.assertFalse((layout.polaris_dir(project) / "retracted").exists())

    def test_apply_removes_only_pure_entries_with_backup(self):
        with TempProject() as project:
            write(project, KNOWLEDGE, DECISIONS, LOOPS)

            res = retract.retract(project, [BAD], apply=True)

            knowledge = layout.knowledge_path(project).read_text()
            decisions = layout.decisions_path(project).read_text()
            self.assertTrue(res.written)
            self.assertNotIn("Nur aus der fremden Nachricht", knowledge)
            self.assertIn("Gemischter Batch", knowledge)
            self.assertIn("Sauberer Batch", knowledge)
            self.assertNotIn("Fremd", decisions)
            self.assertIn("Sauber", decisions)
            self.assertEqual(len(res.backups), 2)
            with open(res.retracted_file, encoding="utf-8") as f:
                archived = f.read()
            self.assertIn("Nur aus der fremden Nachricht", archived)
            self.assertIn(BAD, archived)

    def test_rebuild_keeps_entries_it_could_not_rebuild(self):
        # "Gemischter Batch" nennt IDS_A, die auch der stehen bleibende
        # Dokument-Eintrag nennt: IDS_A wird nicht neu eingestellt (Dublette),
        # also darf auch der gemischte Eintrag nicht verschwinden - er bleibt
        # stehen und wird zur Handpruefung gemeldet (Review Runde 2).
        tp = TempProject()
        with tp as project:
            write(project, KNOWLEDGE, DECISIONS, LOOPS)
            raw(tp, *IDS_A, *IDS_B)
            st = state_mod.CuratorState()
            st.mark_messages_processed("2026-09-29", IDS_A + IDS_B + [BAD])
            state_mod.save_state(project, st)

            res = retract.retract(project, [BAD], apply=True, rebuild=True)

            knowledge = layout.knowledge_path(project).read_text()
            self.assertNotIn("Nur aus der fremden Nachricht", knowledge)
            # verunreinigt -> immer weg, auch wenn der saubere Anteil nicht
            # automatisch zurueckkommt (wird gemeldet)
            self.assertNotIn("Gemischter Batch", knowledge)
            self.assertTrue(any("Gemischter Batch" in m for m in res.not_rebuilt))
            self.assertIn("Sauberer Batch", knowledge)
            self.assertIn("Gemischt plus Dokument", knowledge)
            self.assertEqual(res.requeued_ids, 0)
            st2 = state_mod.load_state(project)
            self.assertIn(BAD, st2.processed_message_ids)  # nie wieder kuratieren

    def test_rebuild_never_removes_what_is_no_longer_in_raw(self):
        tp = TempProject()
        with tp as project:
            write(project, KNOWLEDGE.replace("  {DOC}\n".format(DOC=DOC), ""))
            # IDS_A fehlen im Rohbestand -> Eintrag aus IDS_A+BAD nicht neu aufbaubar,
            # aber verunreinigt: weg, und so gemeldet
            res = retract.retract(project, [BAD], apply=True, rebuild=True)
            self.assertNotIn("Gemischter Batch", layout.knowledge_path(project).read_text())
            self.assertTrue(res.not_rebuilt)
            self.assertEqual(res.requeued_ids, 0)

    def test_ids_still_in_raw_are_skipped_unless_forced(self):
        tp = TempProject()
        with tp as project:
            write(project, KNOWLEDGE, DECISIONS, LOOPS)
            tp.write_message(BAD)  # liegt noch legitim im Projekt

            res = retract.retract(project, [BAD], apply=True)
            self.assertEqual(res.still_in_raw, [BAD])
            self.assertFalse(res.written)
            self.assertIn("Nur aus der fremden Nachricht", layout.knowledge_path(project).read_text())

            forced = retract.retract(project, [BAD], apply=True, force=True)
            self.assertTrue(forced.written)
            self.assertNotIn("Nur aus der fremden Nachricht", layout.knowledge_path(project).read_text())

    def test_prefix_must_be_long_enough(self):
        with TempProject() as project:
            with self.assertRaises(retract.RetractError):
                retract.retract(project, ["abc"])

    def test_ids_from_dir(self):
        with TempProject() as project:
            d = project / "q" / "ABC" / "messages"
            d.mkdir(parents=True)
            (d / "x.json").write_text(json.dumps({"message": {"id": BAD}}), encoding="utf-8")
            (d / "broken.json").write_text("{", encoding="utf-8")
            self.assertEqual(retract.ids_from_dir(project / "q"), [BAD])

    def test_nothing_to_do_writes_nothing(self):
        with TempProject() as project:
            write(project, KNOWLEDGE)
            res = retract.retract(project, ["dddddddd-0000-0000-0000-000000000000"], apply=True)
            self.assertFalse(res.written)
            self.assertFalse((layout.polaris_dir(project) / "retracted").exists())



class TestRetractWithRealRenderers(unittest.TestCase):
    """Rundlauf mit dem, was merge.py und compact.py wirklich schreiben (die
    Tests oben nutzen handgeschriebenes Markdown)."""

    def _prov(self, ids):
        from polaris_curator import merge
        return merge.build_provenance("chatgpt", conversation_id="c1", message_ids=",".join(ids))

    def test_rebuild_on_merge_output_removes_only_affected_batch(self):
        from conftest_helpers import make_change
        from polaris_curator import merge
        tp = TempProject()
        with tp as project:
            merge.apply_change_object(project, make_change(
                knowledge_updates=[{"section": "Status", "content": "Aus sauberem Batch"}],
                decisions_add=[{"title": "Sauber", "decision": "A", "rationale": "B", "date": "2026-09-01"}],
                open_loops_add=[{"text": "Sauberer Punkt", "loop_type": "other"}],
            ), self._prov(IDS_B))
            merge.apply_change_object(project, make_change(
                knowledge_updates=[{"section": "Status", "content": "Aus gemischtem Batch"}],
                decisions_add=[{"title": "Gemischt", "decision": "C", "rationale": "D", "date": "2026-09-02"}],
                open_loops_add=[{"text": "Gemischter Punkt", "loop_type": "other"}],
            ), self._prov(IDS_A + [BAD]))
            for i in IDS_A + IDS_B:
                tp.write_message(i)

            res = retract.retract(project, [BAD], apply=True, rebuild=True)

            k = layout.knowledge_path(project).read_text()
            d = layout.decisions_path(project).read_text()
            o = layout.open_loops_path(project).read_text()
            self.assertIn("Aus sauberem Batch", k)
            self.assertNotIn("Aus gemischtem Batch", k)
            self.assertIn("Sauber", d)
            self.assertNotIn("Gemischt", d)
            self.assertIn("Sauberer Punkt", o)
            self.assertNotIn("Gemischter Punkt", o)
            self.assertEqual(res.requeued_ids, 2)

    def test_archive_headings_stay_with_their_entries(self):
        from polaris_curator import compact
        with TempProject() as project:
            text = (
                "# Projektwissen\n\n## Status\n"
                f"- **[Fakt]** Eins\n  {self._prov([BAD])}\n"
                f"- **[Fakt]** Zwei\n  {self._prov(IDS_B)}\n"
            )
            _pre, _s, entries = compact.parse_entries(text)
            archived = [(e, {"reason": "ueberholt"}) for e in entries]
            (project / compact.ARCHIVE_NAME).write_text(compact._archive_text(project, archived, {}), encoding="utf-8")

            retract.retract(project, [BAD], apply=True)

            archive = (project / compact.ARCHIVE_NAME).read_text()
            self.assertNotIn("Eins", archive)
            self.assertIn("Zwei", archive)
            # Ueberschrift des zweiten Eintrags steht noch unmittelbar davor
            lines = archive.splitlines()
            i = next(n for n, ln in enumerate(lines) if "Zwei" in ln)
            self.assertTrue(lines[i - 1].startswith("### Abschnitt"), lines[i - 3:i + 1])

    def test_manual_entry_messages_are_not_requeued(self):
        tp = TempProject()
        with tp as project:
            write(project, KNOWLEDGE)
            raw(tp, *IDS_A, *IDS_B)
            res = retract.retract(project, [BAD], apply=True, rebuild=True)
            self.assertTrue(any("Gemischt plus Dokument" in m for m in res.manual))
            # IDS_A steckt auch im stehen bleibenden Dokument-Eintrag
            self.assertEqual(res.requeued_ids, 0)

    def test_refuses_duplicate_decision_headings(self):
        tp = TempProject()
        with tp as project:
            dup = DECISIONS.replace("## 2026-09-29 – Sauber", "## 2026-09-29 – Fremd")
            write(project, decisions=dup)
            raw(tp, *IDS_B)
            with self.assertRaises(retract.RetractError):
                retract.retract(project, [BAD], apply=True, rebuild=True)


if __name__ == "__main__":
    unittest.main()
