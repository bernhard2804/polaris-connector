import unittest

from conftest_helpers import TempProject

from polaris_curator import chatgpt_source, state as state_mod


class TestChatgptSource(unittest.TestCase):
    def test_local_date_conversion_and_filtering(self):
        tp = TempProject()
        with tp as project:
            tp.write_message("m1", captured_at="2026-09-01T10:00:00.000Z")
            tp.write_message("m2", captured_at="2026-09-02T10:00:00.000Z")

            st = state_mod.CuratorState()
            messages = chatgpt_source.load_new_messages(project, st)
            self.assertEqual({m.message_id for m in messages}, {"m1", "m2"})
            for m in messages:
                self.assertRegex(m.local_date, r"^\d{4}-\d{2}-\d{2}$")

            st.mark_messages_processed(messages[0].local_date, ["m1"])
            remaining = chatgpt_source.load_new_messages(project, st)
            self.assertEqual({m.message_id for m in remaining}, {"m2"})

    def test_malformed_event_is_skipped(self):
        tp = TempProject()
        with tp as project:
            bad = project / "raw" / "chatgpt" / "messages" / "broken.json"
            bad.write_text("{not valid json", encoding="utf-8")
            tp.write_message("m1")

            st = state_mod.CuratorState()
            messages = chatgpt_source.load_new_messages(project, st)
            self.assertEqual([m.message_id for m in messages], ["m1"])


class TestUploadNames(unittest.TestCase):
    def test_upload_names_are_appended_to_content(self):
        import json as _json
        from conftest_helpers import TempProject
        from polaris_curator import chatgpt_source

        with TempProject() as tp:
            path = TempProject.write_message(type("T", (), {"path": tp})(), "m1", role="user", content="Siehe Mail")
            data = _json.loads(path.read_text())
            data["message"]["attachments"] = ["WG_ Freigabe Terminals und Chips.eml"]
            path.write_text(_json.dumps(data))
            msg = chatgpt_source.load_message(path)
            self.assertIn("Siehe Mail", msg.content)
            self.assertIn("[Im Chat hochgeladene Datei(en): WG_ Freigabe Terminals und Chips.eml]", msg.content)


class TestClaudeSource(unittest.TestCase):
    def test_claude_messages_are_read_and_labelled(self):
        import json as _json
        from conftest_helpers import TempProject, fast_config
        from polaris_curator import chatgpt_source, state as state_mod, prompts, batching

        with TempProject() as tp:
            helper = type("T", (), {"path": tp})()
            TempProject.write_message(helper, "g1", content="Aus ChatGPT")
            cdir = tp / "raw" / "claude" / "messages"
            cdir.mkdir(parents=True)
            src = tp / "raw" / "chatgpt" / "messages" / "g1.json"
            event = _json.loads(src.read_text())
            event.update(source="claude")
            event["message"] = dict(event["message"], id="c1", content="Aus Claude")
            (cdir / "c1.json").write_text(_json.dumps(event))
            msgs = chatgpt_source.load_new_messages(tp, state_mod.CuratorState())
            self.assertEqual(sorted((m.message_id, m.source) for m in msgs), [("c1", "claude"), ("g1", "chatgpt")])
            batch = batching.build_day_batches(msgs, fast_config())[0]
            text = prompts.build_chat_batch_prompt(batch, "", "", "")
            self.assertIn("quelle=Claude", text)
            self.assertIn("quelle=ChatGPT", text)



class TestRevisions(unittest.TestCase):
    def test_only_latest_revision_enters_batch(self):
        import json as _json
        from conftest_helpers import TempProject
        from polaris_curator import chatgpt_source, state as state_mod
        tp = TempProject()
        with tp as project:
            base = tp.write_message("m1", content="teil")
            rev = base.with_name("m1.rev2.json")
            event = _json.loads(base.read_text())
            event["message"]["content"] = "final"
            rev.write_text(_json.dumps(event))
            msgs = chatgpt_source.load_new_messages(project, state_mod.CuratorState())
            self.assertEqual([(m.message_id, m.content) for m in msgs], [("m1", "final")])


class TestContinuations(unittest.TestCase):
    def _rev(self, base, n, content):
        import json as _json
        p = base.with_name(f"{base.stem}.rev{n}.json")
        e = _json.loads(base.read_text())
        e["message"]["content"] = content
        p.write_text(_json.dumps(e))

    def test_substantial_continuation_is_curated_once_with_only_the_new_part(self):
        from conftest_helpers import FakeLlm, TempProject, fast_config, make_change
        from polaris_curator import chatgpt_source, pipeline, state as state_mod
        tp = TempProject()
        with tp as project:
            base = tp.write_message("m1", content="Teil eins")
            cfg = fast_config()
            pipeline.run_project(project, cfg, llm_fn=FakeLlm([make_change()]))
            self._rev(base, 2, "Teil eins " + "und der ganze Rest der Antwort. " * 10)
            fake = FakeLlm([make_change()])
            pipeline.run_project(project, cfg, llm_fn=fake)
            self.assertEqual(len(fake.calls), 1)
            self.assertIn("nur diese neuen", fake.calls[0])
            self.assertNotIn("Teil eins und", fake.calls[0].split("QUELLINHALT")[-1])
            # kein weiterer Lauf fuer dieselbe Revision
            fake2 = FakeLlm([])
            pipeline.run_project(project, cfg, llm_fn=fake2)
            self.assertEqual(fake2.calls, [])
            self.assertEqual(state_mod.load_state(project).processed_revisions["m1"], 2)

    def test_cosmetic_revision_is_ignored(self):
        from conftest_helpers import FakeLlm, TempProject, fast_config, make_change
        from polaris_curator import pipeline
        tp = TempProject()
        with tp as project:
            base = tp.write_message("m1", content="Siehe Quelle und Text")
            pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([make_change()]))
            self._rev(base, 2, "Siehe [Quelle](https://x.example) und **Text**")
            fake = FakeLlm([])
            pipeline.run_project(project, fast_config(), llm_fn=fake)
            self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
