import io
import unittest
import zipfile
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from conftest_helpers import FakeLlm, TempProject, fast_config, make_change

from polaris_curator import documents_source, office_extract, pipeline, state as state_mod
from test_documents import MINIMAL_PDF

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R_NS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
PR_NS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
A_NS = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            # Fester Zeitstempel: sonst sind zwei Aufrufe nur in derselben
            # Sekunde byte-gleich (machte den Index-Test zufaellig rot).
            zf.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), content)
    return buf.getvalue()


def make_docx() -> bytes:
    return _zip({
        "word/document.xml": (
            f"<w:document {W_NS}><w:body>"
            "<w:p><w:r><w:t>Anleitung Stuecklisten-Export</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Feld</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Wert</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            "</w:body></w:document>"
        )
    })


def make_xlsx() -> bytes:
    return _zip({
        "xl/workbook.xml": (
            f"<workbook {S_NS} {R_NS}><sheets>"
            '<sheet name="Kennzahlen" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<Relationships {PR_NS}><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/sharedStrings.xml": f"<sst {S_NS}><si><t>Monat</t></si><si><t>Umsatz</t></si></sst>",
        "xl/styles.xml": (
            f'<styleSheet {S_NS}><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>'
        ),
        "xl/worksheets/sheet1.xml": (
            f"<worksheet {S_NS}><sheetData>"
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" s="1"><v>46266</v></c><c r="B2"><v>1234.5</v></c></row>'
            "</sheetData></worksheet>"
        ),
    })


def make_pptx() -> bytes:
    return _zip({
        "ppt/slides/slide2.xml": f"<p:sld xmlns:p='x' {A_NS}><a:p><a:r><a:t>Zweite Folie</a:t></a:r></a:p></p:sld>",
        "ppt/slides/slide1.xml": f"<p:sld xmlns:p='x' {A_NS}><a:p><a:r><a:t>Erste Folie</a:t></a:r></a:p></p:sld>",
    })


def _attach(msg, data: bytes, filename: str, maintype="application", subtype="octet-stream"):
    part = MIMEApplication(data, _subtype=subtype)
    part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(part)


def make_mail(*, small_logo=True, big_image=False, extra=()) -> bytes:
    msg = MIMEMultipart("mixed")
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    msg["Subject"] = "Anhaenge"
    msg["Date"] = "Mon, 21 Sep 2026 10:00:00 +0000"
    msg.attach(MIMEText("Siehe Anhang.", "plain", "utf-8"))
    if small_logo:
        logo = MIMEImage(b"\x89PNG\r\n\x1a\n" + b"0" * 2000, _subtype="png")
        logo.add_header("Content-Disposition", "inline", filename="image001.png")
        msg.attach(logo)
    if big_image:
        shot = MIMEImage(b"\x89PNG\r\n\x1a\n" + b"1" * 400_000, _subtype="png")
        shot.add_header("Content-Disposition", "attachment", filename="Screenshot.png")
        msg.attach(shot)
    for data, name in extra:
        _attach(msg, data, name)
    return msg.as_bytes()


class TestOfficeExtract(unittest.TestCase):
    def test_docx(self):
        text = office_extract.extract_docx(make_docx())
        self.assertIn("Anleitung Stuecklisten-Export", text)
        self.assertIn("Feld | Wert", text)

    def test_xlsx_with_shared_strings_and_date(self):
        text = office_extract.extract_xlsx(make_xlsx())
        self.assertIn("## Tabellenblatt: Kennzahlen", text)
        self.assertIn("Monat | Umsatz", text)
        self.assertIn("2026-09-01 | 1234.5", text)

    def test_pptx_slide_order(self):
        text = office_extract.extract_pptx(make_pptx())
        self.assertLess(text.index("Erste Folie"), text.index("Zweite Folie"))


class TestMailAttachments(unittest.TestCase):
    def _load(self, project, cfg=None):
        return documents_source.load_new_documents(project, state_mod.CuratorState(), cfg)

    def test_office_and_pdf_attachments_extracted(self):
        with TempProject() as project:
            (project / "inbox" / "mail" / "m.eml").write_bytes(make_mail(extra=[
                (make_docx(), "Anleitung.docx"),
                (make_xlsx(), "Kennzahlen.xlsx"),
                (MINIMAL_PDF, "Angebot.pdf"),
            ]))
            [doc] = self._load(project)
            self.assertIn("Siehe Anhang.", doc.text)
            self.assertIn("--- Anhang: Anleitung.docx ---", doc.text)
            self.assertIn("Anleitung Stuecklisten-Export", doc.text)
            self.assertIn("Monat | Umsatz", doc.text)
            self.assertIn("Hallo PDF", doc.text)
            self.assertEqual(
                [a.filename for a in doc.attachments], ["Anleitung.docx", "Kennzahlen.xlsx", "Angebot.pdf"]
            )

    def test_small_images_ignored_big_images_sent(self):
        with TempProject() as project:
            (project / "inbox" / "mail" / "m.eml").write_bytes(make_mail(big_image=True))
            [doc] = self._load(project)
            self.assertNotIn("image001.png", doc.text)
            self.assertEqual([a.filename for a in doc.attachments], ["Screenshot.png"])
            self.assertEqual([name for name, _ in doc.images], ["Screenshot.png"])

    def test_image_threshold_configurable(self):
        with TempProject() as project:
            (project / "inbox" / "mail" / "m.eml").write_bytes(make_mail())
            [doc] = self._load(project, fast_config(min_image_bytes=1000))
            self.assertEqual([name for name, _ in doc.images], ["image001.png"])

    def test_unsupported_attachment_stored_not_evaluated(self):
        with TempProject() as project:
            (project / "inbox" / "mail" / "m.eml").write_bytes(
                make_mail(extra=[(b"\xd0\xcf\x11\xe0binary", "Projektplan.mpp")])
            )
            [doc] = self._load(project)
            self.assertIn("Format nicht auswertbar, nur abgelegt): Projektplan.mpp", doc.text)
            self.assertEqual(doc.attachments[0].handling, "stored")

    def test_broken_attachment_does_not_block_mail(self):
        with TempProject() as project:
            (project / "inbox" / "mail" / "m.eml").write_bytes(
                make_mail(extra=[(b"kein zip", "kaputt.docx")])
            )
            [doc] = self._load(project)
            self.assertIn("Siehe Anhang.", doc.text)
            self.assertIn("(nicht lesbar): kaputt.docx", doc.text)

    def test_rfc2047_filename_and_path_stripped(self):
        with TempProject() as project:
            (project / "inbox" / "mail" / "m.eml").write_bytes(make_mail(extra=[
                (b"a;b", "=?utf-8?q?St=C3=BCckliste.csv?="),
                (b"x", "../../boese.txt"),
            ]))
            [doc] = self._load(project)
            names = [a.filename for a in doc.attachments]
            self.assertIn("Stückliste.csv", names)
            self.assertTrue(all("/" not in n for n in names))

    def test_document_text_capped_to_batch_budget(self):
        with TempProject() as project:
            big = ("Zeile mit Inhalt\n" * 5000).encode()
            (project / "inbox" / "mail" / "m.eml").write_bytes(make_mail(extra=[(big, "gross.txt")]))
            [doc] = self._load(project, fast_config(max_input_chars_per_batch=10_000, max_parts_per_document=1))
            self.assertLessEqual(len(doc.text), 20_100)  # einmalig bis 2x Limit, dann gekuerzt
            self.assertIn("abgeschnitten", doc.text)
            # Standard seit 2026-10-07: in Teile zerlegt, jeder Teil im Budget
            parts = self._load(project, fast_config(max_input_chars_per_batch=10_000))
            self.assertGreater(len(parts), 1)
            self.assertTrue(all(len(d.text) <= 10_000 for d in parts))


class TestDocumentCapNeverExceedsRunBudget(unittest.TestCase):
    def test_cap_uses_smaller_of_batch_and_run_budget(self):
        with TempProject() as project:
            (project / "inbox" / "documents" / "gross.txt").write_text("x" * 50_000)
            cfg = fast_config(max_input_chars_per_batch=100_000, max_total_input_chars_per_run=20_000)
            docs = documents_source.load_new_documents(project, state_mod.CuratorState(), cfg)
            self.assertEqual(len(docs), 3)  # 50 000 Zeichen in Teilen <= 20 000
            self.assertTrue(all(len(d.text) <= 20_000 for d in docs))


class TestInboxDocumentsOffice(unittest.TestCase):
    def test_xlsx_docx_pptx_in_documents_inbox(self):
        with TempProject() as project:
            (project / "inbox" / "documents" / "a.xlsx").write_bytes(make_xlsx())
            (project / "inbox" / "documents" / "b.docx").write_bytes(make_docx())
            (project / "inbox" / "documents" / "c.pptx").write_bytes(make_pptx())
            docs = documents_source.load_new_documents(project, state_mod.CuratorState())
            self.assertEqual(len(docs), 3)

    def test_unsupported_file_is_logged(self):
        with TempProject() as project:
            (project / "inbox" / "documents" / "alt.xls").write_bytes(b"x")
            with self.assertLogs("polaris_curator", level="WARNING") as logs:
                docs = documents_source.load_new_documents(project, state_mod.CuratorState())
            self.assertEqual(docs, [])
            self.assertIn("alt.xls", logs.output[0])


class TestPipelineAttachments(unittest.TestCase):
    def test_attachments_stored_and_images_passed_to_llm(self):
        with TempProject() as project:
            (project / "inbox" / "mail" / "Mail A.eml").write_bytes(
                make_mail(big_image=True, extra=[(make_docx(), "Anleitung.docx")])
            )
            fake = FakeLlm([make_change()])
            report = pipeline.run_project(project, fast_config(), llm_fn=fake)

            self.assertEqual(report.errors, [])
            self.assertEqual(report.documents_processed, ["mail/Mail A.eml"])
            self.assertIn("Anleitung Stuecklisten-Export", fake.calls[0])
            self.assertIn("Bildanhaenge als Bilder beigefuegt: Screenshot.png", fake.calls[0])
            self.assertEqual([n for n, _ in fake.images[0]], ["Screenshot.png"])

            att_dir = project / "inbox" / "mail" / "archive" / "attachments" / "Mail A"
            self.assertEqual(sorted(p.name for p in att_dir.iterdir()), ["Anleitung.docx", "Screenshot.png"])
            self.assertTrue((project / "inbox" / "mail" / "archive" / "processed" / "Mail A.eml").is_file())

            st = state_mod.load_state(project)
            sig = st.file_signature("mail:Mail A.eml")
            self.assertEqual(
                sig["attachments"],
                [{"file": "Screenshot.png", "handling": "image"}, {"file": "Anleitung.docx", "handling": "text"}],
            )

    def test_mail_without_relevant_attachments_calls_llm_without_images(self):
        with TempProject() as project:
            (project / "inbox" / "mail" / "m.eml").write_bytes(make_mail())
            fake = FakeLlm([make_change()])
            pipeline.run_project(project, fast_config(), llm_fn=fake)
            self.assertEqual(fake.images, [[]])
            self.assertFalse((project / "inbox" / "mail" / "archive" / "attachments").exists())

    def test_store_attachments_never_overwrites(self):
        with TempProject() as project:
            target = project / "att"
            A = documents_source.Attachment
            documents_source.store_attachments([A("x.txt", "text/plain", b"1", "text")], target)
            documents_source.store_attachments(
                [A("x.txt", "text/plain", b"1", "text"), A("x.txt", "text/plain", b"2", "text")], target
            )
            self.assertEqual(sorted(p.name for p in target.iterdir()), ["x (2).txt", "x.txt"])
            self.assertEqual((target / "x.txt").read_bytes(), b"1")


class TestProvenancePointsToArchive(unittest.TestCase):
    def test_provenance_uses_relative_archive_path(self):
        with TempProject() as project:
            (project / "inbox" / "mail" / "Mail A.eml").write_bytes(make_mail())
            change = make_change(knowledge_updates=[{"section": "Status", "content": "Neu", "mode": "append"}])
            pipeline.run_project(project, fast_config(), llm_fn=FakeLlm([change]))
            knowledge = (project / "knowledge.md").read_text(encoding="utf-8")
            self.assertIn("path=inbox/mail/archive/processed/Mail A.eml", knowledge)
            self.assertNotIn(str(project), knowledge)
            self.assertTrue((project / "inbox" / "mail" / "archive" / "processed" / "Mail A.eml").is_file())


class TestDocumentsIndex(unittest.TestCase):
    def test_index_lists_mail_attachments_and_documents(self):
        from polaris_curator import docs_index

        with TempProject() as project:
            (project / "inbox" / "mail" / "Mail A.eml").write_bytes(
                make_mail(big_image=True, extra=[(make_docx(), "Anleitung.docx"), (b"x", "Plan.mpp")])
            )
            (project / "inbox" / "documents" / "b.docx").write_bytes(make_docx())
            fake = FakeLlm([make_change(), make_change()])
            pipeline.run_project(project, fast_config(), llm_fn=fake)

            index = docs_index.index_path(project).read_text(encoding="utf-8")
            self.assertIn("### Anhaenge", index)  # Betreff der Testmail
            knowledge = (project / "knowledge.md").read_text(encoding="utf-8") if (project / "knowledge.md").exists() else ""
            self.assertNotIn(str(project / "inbox" / "mail" / "Mail A.eml"), knowledge)
            self.assertIn("a@example.com", index)
            self.assertIn("- Mail: `inbox/mail/archive/processed/Mail A.eml`", index)
            # Anleitung.docx ist byte-gleich mit b.docx (beide aus make_docx) und
            # wird deshalb nur dort ausgewertet (seit 2026-10-07).
            self.assertIn("`inbox/mail/archive/attachments/Mail A/Anleitung.docx` – identisch mit `inbox/documents/b.docx`, dort ausgewertet", index)
            self.assertIn("`inbox/mail/archive/attachments/Mail A/Screenshot.png` – als Bild ausgewertet", index)
            self.assertIn("`inbox/mail/archive/attachments/Mail A/Plan.mpp` – nur abgelegt", index)
            self.assertIn("- `inbox/documents/archive/processed/b.docx`", index)
            self.assertFalse(docs_index.write_index(project), "unveraenderter Index wird nicht neu geschrieben")

    def test_index_covers_mails_archived_before_the_index_existed(self):
        from polaris_curator import docs_index

        with TempProject() as project:
            processed = project / "inbox" / "mail" / "archive" / "processed"
            processed.mkdir(parents=True)
            (processed / "alt.eml").write_bytes(make_mail())
            self.assertTrue(docs_index.write_index(project))
            index = docs_index.index_path(project).read_text(encoding="utf-8")
            self.assertIn("inbox/mail/archive/processed/alt.eml", index)


if __name__ == "__main__":
    unittest.main()


class TestDuplicateAttachments(unittest.TestCase):
    """2026-10-07: Anhaenge, die als eigenes Dokument vorliegen, nicht doppelt kuratieren."""

    def _mail(self, project, data: bytes):
        msg = MIMEMultipart()
        msg["Subject"] = "Wochenstatistik"
        msg.attach(MIMEText("Hallo, anbei die Zahlen.", "plain"))
        att = MIMEApplication(data, Name="stat.xlsx")
        att["Content-Disposition"] = 'attachment; filename="stat.xlsx"'
        msg.attach(att)
        (project / "inbox" / "mail" / "stat.eml").write_bytes(msg.as_bytes())

    def test_attachment_identical_to_pending_document_is_only_referenced(self):
        with TempProject() as project:
            data = make_xlsx()
            (project / "inbox" / "documents" / "stat.xlsx").write_bytes(data)
            self._mail(project, data)
            docs = {d.path.name: d for d in documents_source.load_new_documents(project, state_mod.CuratorState())}
            mail = docs["stat.eml"]
            self.assertIn("identisch mit inbox/documents/stat.xlsx", mail.text)
            self.assertNotIn(docs["stat.xlsx"].text.strip()[:40], mail.text)
            self.assertEqual(mail.attachments[0].handling, "duplicate")

    def test_attachment_identical_to_curated_document_is_only_referenced(self):
        import hashlib

        with TempProject() as project:
            data = make_xlsx()
            st = state_mod.CuratorState()
            st.processed_files["documents:alt.xlsx"] = {"hash": hashlib.sha256(data).hexdigest()}
            self._mail(project, data)
            mail = documents_source.load_new_documents(project, st)[0]
            self.assertIn("identisch mit inbox/documents/alt.xlsx", mail.text)

    def test_unknown_attachment_is_still_extracted(self):
        with TempProject() as project:
            self._mail(project, make_xlsx())
            mail = documents_source.load_new_documents(project, state_mod.CuratorState())[0]
            self.assertNotIn("identisch mit", mail.text)
            self.assertEqual(mail.attachments[0].handling, "text")
