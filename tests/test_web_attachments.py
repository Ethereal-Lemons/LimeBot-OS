import base64
import io
import shutil
import unittest
from pathlib import Path

from channels.web import WebChannel
from core.loop import AgentLoop


class TestWebAttachments(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        shutil.rmtree(Path("temp") / "web_uploads" / "chat_test", ignore_errors=True)
        shutil.rmtree(Path("temp") / "web_uploads" / "legacy_doc", ignore_errors=True)

    async def test_normalize_web_attachments_extracts_docx_text(self):
        try:
            from docx import Document
        except Exception:
            raise unittest.SkipTest("Missing dependencies (python-docx).")

        buffer = io.BytesIO()
        doc = Document()
        doc.add_paragraph("Quarterly revenue memo")
        doc.save(buffer)

        payload = {
            "name": "memo.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "data_url": "data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii"),
        }

        channel = WebChannel.__new__(WebChannel)
        attachments, image_data = await channel._normalize_web_attachments(
            "chat/test", [payload]
        )

        self.assertIsNone(image_data)
        self.assertEqual(len(attachments), 1)
        attachment = attachments[0]
        self.assertEqual(attachment["kind"], "document")
        self.assertIn("Quarterly revenue memo", attachment["extracted_text"])
        self.assertEqual(
            attachment["url"],
            f"/temp/{Path(attachment['path']).relative_to(Path('temp')).as_posix()}",
        )
        self.assertTrue(Path(attachment["path"]).exists())

    async def test_normalize_web_attachments_marks_legacy_doc_as_non_extractable(self):
        payload = {
            "name": "memo.doc",
            "mimeType": "application/msword",
            "data_url": "data:application/msword;base64,"
            + base64.b64encode(b"fake-binary-doc").decode("ascii"),
        }

        channel = WebChannel.__new__(WebChannel)
        attachments, image_data = await channel._normalize_web_attachments(
            "legacy/doc", [payload]
        )

        self.assertIsNone(image_data)
        self.assertEqual(len(attachments), 1)
        self.assertIn("Legacy .doc files", attachments[0]["extraction_note"])


class TestDocumentAttachmentContext(unittest.TestCase):
    def test_build_document_attachment_context_includes_path_and_text(self):
        context = AgentLoop._build_document_attachment_context(
            [
                {
                    "kind": "document",
                    "name": "memo.docx",
                    "path": "temp/web_uploads/chat_test/memo.docx",
                    "extracted_text": "Quarterly revenue memo",
                }
            ]
        )

        self.assertIn("memo.docx", context)
        self.assertIn("temp/web_uploads/chat_test/memo.docx", context)
        self.assertIn("Quarterly revenue memo", context)
