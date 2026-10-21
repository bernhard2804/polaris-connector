import unittest

from conftest_helpers import TempProject

from polaris_curator import documents_source, state as state_mod

# Minimale, gueltige PDF-Datei mit dem sichtbaren Text "Hallo PDF".
MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
5 0 obj<</Length 44>>
stream
BT /F1 12 Tf 10 50 Td (Hallo PDF) Tj ET
endstream
endobj
xref
0 6
trailer<</Size 6/Root 1 0 R>>
startxref
0
%%EOF
"""


class TestDocuments(unittest.TestCase):
    def test_txt_extraction(self):
        with TempProject() as project:
            path = project / "inbox" / "documents" / "note.txt"
            path.write_text("Hallo Text", encoding="utf-8")
            st = state_mod.CuratorState()
            docs = documents_source.load_new_documents(project, st)
            self.assertEqual(len(docs), 1)
            self.assertIn("Hallo Text", docs[0].text)
            self.assertEqual(docs[0].kind, "documents")

    def test_md_extraction(self):
        with TempProject() as project:
            path = project / "inbox" / "notes" / "note.md"
            path.write_text("# Titel\nInhalt", encoding="utf-8")
            st = state_mod.CuratorState()
            docs = documents_source.load_new_documents(project, st)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0].kind, "notes")

    def test_eml_extraction_plain_text(self):
        with TempProject() as project:
            eml_content = (
                "From: a@example.com\r\n"
                "To: b@example.com\r\n"
                "Subject: Testmail\r\n"
                "Date: Mon, 1 Sep 2026 10:00:00 +0000\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                "\r\n"
                "Hallo aus der Mail.\r\n"
            )
            path = project / "inbox" / "mail" / "test.eml"
            path.write_bytes(eml_content.encode("utf-8"))
            st = state_mod.CuratorState()
            docs = documents_source.load_new_documents(project, st)
            self.assertEqual(len(docs), 1)
            self.assertIn("Hallo aus der Mail.", docs[0].text)
            self.assertIn("Testmail", docs[0].text)
            self.assertEqual(docs[0].kind, "mail")

    def test_pdf_extraction(self):
        with TempProject() as project:
            path = project / "inbox" / "documents" / "test.pdf"
            path.write_bytes(MINIMAL_PDF)
            st = state_mod.CuratorState()
            docs = documents_source.load_new_documents(project, st)
            self.assertEqual(len(docs), 1)
            self.assertIn("Hallo PDF", docs[0].text)

    def test_unchanged_file_not_reprocessed(self):
        with TempProject() as project:
            path = project / "inbox" / "documents" / "note.txt"
            path.write_text("Version 1", encoding="utf-8")
            st = state_mod.CuratorState()
            docs = documents_source.load_new_documents(project, st)
            self.assertEqual(len(docs), 1)

            st.mark_file_processed(docs[0].state_key, docs[0].signature)
            docs2 = documents_source.load_new_documents(project, st)
            self.assertEqual(len(docs2), 0)

    def test_changed_file_is_reprocessed(self):
        with TempProject() as project:
            path = project / "inbox" / "documents" / "note.txt"
            path.write_text("Version 1", encoding="utf-8")
            st = state_mod.CuratorState()
            docs = documents_source.load_new_documents(project, st)
            st.mark_file_processed(docs[0].state_key, docs[0].signature)

            path.write_text("Version 2 - komplett anderer Inhalt", encoding="utf-8")
            docs2 = documents_source.load_new_documents(project, st)
            self.assertEqual(len(docs2), 1)
            self.assertIn("Version 2", docs2[0].text)

    def test_unsupported_suffix_ignored(self):
        with TempProject() as project:
            path = project / "inbox" / "documents" / "image.png"
            path.write_bytes(b"\x89PNG\r\n")
            st = state_mod.CuratorState()
            docs = documents_source.load_new_documents(project, st)
            self.assertEqual(len(docs), 0)


if __name__ == "__main__":
    unittest.main()


class TestTruncationNoteOnlyWhenCurated(unittest.TestCase):
    """2026-10-07: Budgetbedingt wartende Dokumente erschienen faelschlich als
    'gekuerzt kuratiert'. Der Hinweis gehoert nur zu wirklich kuratierten."""

    def test_note_only_for_processed_document(self):
        from conftest_helpers import FakeLlm, fast_config, make_change
        from polaris_curator import pipeline

        with TempProject() as project:
            for name in ("a-gross.txt", "b-gross.txt"):
                (project / "inbox" / "documents" / name).write_text("x" * 5000, encoding="utf-8")
            cfg = fast_config(max_input_chars_per_batch=1000, max_batches_per_run=1, max_llm_calls_per_run=1,
                              max_parts_per_document=1)
            docs = documents_source.load_new_documents(project, state_mod.CuratorState(), cfg)
            self.assertTrue(all(d.truncation_note for d in docs))
            report = pipeline.run_project(project, cfg, llm_fn=FakeLlm([make_change()]))
            notes = [w for w in report.warnings if "gekuerzt kuratiert" in w]
            self.assertEqual(len(notes), 1, report.warnings)


class TestDocumentParts(unittest.TestCase):
    """Grosse Dokumente in Teilen statt gekuerzt (seit 2026-10-07)."""

    def _cfg(self, **kw):
        from conftest_helpers import fast_config
        base = dict(max_input_chars_per_batch=2000, max_total_input_chars_per_run=100000,
                    max_batches_per_run=2, max_llm_calls_per_run=2)
        base.update(kw)
        return fast_config(**base)

    def test_parts_across_runs_archive_only_after_last(self):
        from conftest_helpers import FakeLlm, make_change
        from polaris_curator import pipeline

        with TempProject() as project:
            doc = project / "inbox" / "documents" / "gross.txt"
            doc.write_text("".join(f"Zeile {i} mit etwas Inhalt\n" for i in range(250)), encoding="utf-8")
            cfg = self._cfg()
            parts = documents_source.load_new_documents(project, state_mod.CuratorState(), cfg)
            n = len(parts)
            self.assertGreaterEqual(n, 3)
            self.assertEqual([d.part for d in parts], list(range(1, n + 1)))
            self.assertTrue(all(len(d.text) <= 2000 for d in parts))
            self.assertIn(f"Teil 2 von {n}", parts[1].text)
            joined = "".join(d.text.split("]\n\n", 1)[1] for d in parts)
            self.assertEqual(joined, doc.read_text(encoding="utf-8"))  # nichts verloren, nichts doppelt

            fake = FakeLlm([make_change() for _ in range(n)])
            r1 = pipeline.run_project(project, cfg, llm_fn=fake)
            self.assertEqual(r1.documents_processed, [f"documents/gross.txt (Teil {i}/{n})" for i in (1, 2)])
            self.assertTrue(doc.exists())  # noch nicht archiviert
            st = state_mod.load_state(project)
            self.assertEqual(st.document_parts["documents:gross.txt"]["done"], 2)
            self.assertIsNone(st.file_signature("documents:gross.txt"))

            reports = [r1]
            while doc.exists() and len(reports) < 10:
                reports.append(pipeline.run_project(project, cfg, llm_fn=fake))
            done = [lbl for r in reports for lbl in r.documents_processed]
            self.assertEqual(done, [f"documents/gross.txt (Teil {i}/{n})" for i in range(1, n + 1)])
            st = state_mod.load_state(project)
            self.assertNotIn("documents:gross.txt", st.document_parts)
            self.assertIsNotNone(st.file_signature("documents:gross.txt"))
            self.assertEqual(len(fake.calls), n)
            self.assertFalse(any("gekuerzt" in w for r in reports for w in r.warnings))

    def test_more_parts_than_allowed_is_truncated_and_reported(self):
        with TempProject() as project:
            (project / "inbox" / "documents" / "riesig.txt").write_text("x\n" * 5000, encoding="utf-8")
            parts = documents_source.load_new_documents(project, state_mod.CuratorState(),
                                                        self._cfg(max_parts_per_document=2))
            self.assertEqual(len(parts), 2)
            self.assertIsNone(parts[0].truncation_note)
            self.assertIn("gekuerzt kuratiert", parts[1].truncation_note)

    def test_changed_file_restarts_from_first_part(self):
        with TempProject() as project:
            doc = project / "inbox" / "documents" / "gross.txt"
            doc.write_text("a\n" * 3000, encoding="utf-8")
            st = state_mod.CuratorState()
            st.document_parts["documents:gross.txt"] = {"hash": "veraltet", "done": 2, "parts": 3}
            parts = documents_source.load_new_documents(project, st, self._cfg())
            self.assertEqual(parts[0].part, 1)


class TestOversizeSingleCall(unittest.TestCase):
    def test_up_to_factor_one_call_full_text(self):
        from conftest_helpers import fast_config

        with TempProject() as project:
            body = "y" * 3500
            (project / "inbox" / "documents" / "mittel.txt").write_text(body, encoding="utf-8")
            cfg = fast_config(max_input_chars_per_batch=2000, max_total_input_chars_per_run=100000)
            [doc] = documents_source.load_new_documents(project, state_mod.CuratorState(), cfg)
            self.assertEqual((doc.part, doc.parts), (1, 1))
            self.assertEqual(doc.text, body)
            self.assertIsNone(doc.truncation_note)
            cfg.document_oversize_factor = 1.0  # aus: dann Teile
            parts = documents_source.load_new_documents(project, state_mod.CuratorState(), cfg)
            self.assertGreaterEqual(len(parts), 2)
