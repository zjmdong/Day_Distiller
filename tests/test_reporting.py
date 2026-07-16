import tempfile
import unittest
from datetime import date, datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path

from PIL import Image

from day_distiller_client.domain import ComicPanel, DayReport
from day_distiller_client.providers.mock import MockMailProvider
from day_distiller_client.reporting import POSTER_CONTENT_ID, ReportRenderer


class ReportingTests(unittest.TestCase):
    def test_minimal_html_pdf_and_email_embed_one_poster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            poster = root / "daily_poster.jpg"
            Image.new("RGB", (600, 800), "#1652a3").save(poster, quality=90)
            report = DayReport(
                report_id="report-1",
                job_id="job-1",
                report_date=date(2026, 7, 16),
                title="忙碌之后的小确幸",
                one_sentence_summary="不应出现在邮件里的内部摘要",
                warm_message="今天辛苦了，洗个热水澡，好好睡一觉吧。",
                narrative="不应出现在邮件里的长篇日报",
                timeline=[{"time_label": "18:00", "summary": "不应显示的时间线"}],
                panels=[
                    ComicPanel(
                        record_ids=["r1", "r2"],
                        time_label="今日",
                        caption="不应显示的海报说明",
                        image_prompt="poster",
                        image_path=str(poster),
                    )
                ],
                model_versions={},
                created_at=datetime.now(timezone.utc),
            )
            rendered = ReportRenderer().render(report, root / "report")
            local_html = rendered.html_path.read_text(encoding="utf-8")

            self.assertIn(report.title, local_html)
            self.assertIn(report.warm_message, local_html)
            self.assertNotIn(report.narrative, local_html)
            self.assertNotIn("不应显示的时间线", local_html)
            self.assertIn(f"cid:{POSTER_CONTENT_ID}", rendered.email_html)
            self.assertTrue(rendered.pdf_path.read_bytes().startswith(b"%PDF"))

            outbox = root / "outbox"
            MockMailProvider(outbox).send(
                report.title,
                rendered.plain_text,
                rendered.email_html,
                rendered.pdf_path,
                rendered.inline_images,
                "<poster-test@local>",
            )
            email = BytesParser(policy=policy.default).parsebytes(next(outbox.glob("*.eml")).read_bytes())
            self.assertEqual(email["Subject"], report.title)
            attachments = {part.get_filename() for part in email.iter_attachments()}
            self.assertEqual(attachments, {"daily_poster.jpg", "daily_report.pdf"})
            inline = next(
                part for part in email.walk() if part.get("Content-ID") == f"<{POSTER_CONTENT_ID}>"
            )
            self.assertEqual(inline.get_content_disposition(), "inline")


if __name__ == "__main__":
    unittest.main()
