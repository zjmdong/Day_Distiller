from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .domain import ComicPanel, DayReport


POSTER_CONTENT_ID = "daily-poster@day-distiller.local"


@dataclass(frozen=True)
class RenderedReport:
    html_path: Path
    pdf_path: Path
    email_html: str
    plain_text: str
    inline_images: dict[str, Path]


class ReportRenderer:
    def render(self, report: DayReport, output_dir: Path) -> RenderedReport:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        poster_path = self._poster_path(report)
        inline_images = {POSTER_CONTENT_ID: poster_path} if poster_path else {}
        local_source = poster_path.resolve().as_uri() if poster_path else None
        local_html = self._document(report, local_source)
        email_html = self._document(
            report,
            f"cid:{POSTER_CONTENT_ID}" if poster_path else None,
        )
        html_path = output_dir / "daily_report.html"
        pdf_path = output_dir / "daily_report.pdf"
        html_path.write_text(local_html, encoding="utf-8")
        self._write_pdf(report, poster_path, pdf_path)
        return RenderedReport(
            html_path=html_path,
            pdf_path=pdf_path,
            email_html=email_html,
            plain_text=self._plain_text(report),
            inline_images=inline_images,
        )

    @staticmethod
    def _poster_path(report: DayReport) -> Path | None:
        for panel in report.panels:
            if panel.image_path:
                path = Path(panel.image_path)
                if path.is_file():
                    return path
        return None

    @staticmethod
    def _document(report: DayReport, poster_source: str | None) -> str:
        poster = (
            f'<img class="poster" src="{html.escape(poster_source, quote=True)}" '
            f'alt="{html.escape(report.title, quote=True)}">'
            if poster_source
            else ""
        )
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(report.title)}</title><style>
body {{ margin:0; padding:0; background:#f7f4ee; color:#172033; font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif; }}
.wrap {{ width:100%; max-width:720px; margin:0 auto; padding:36px 18px 48px; box-sizing:border-box; text-align:center; }}
h1 {{ margin:0; font-size:32px; line-height:1.35; font-weight:750; letter-spacing:.02em; }}
.warm {{ margin:14px auto 28px; max-width:620px; color:#596174; font-size:17px; line-height:1.75; }}
.poster {{ display:block; width:100%; height:auto; margin:0 auto; border:0; border-radius:20px; box-shadow:0 14px 38px rgba(23,32,51,.16); }}
@media (max-width:520px) {{ .wrap {{ padding:26px 12px 36px; }} h1 {{ font-size:26px; }} .warm {{ font-size:16px; }} .poster {{ border-radius:14px; }} }}
</style></head><body><main class="wrap"><h1>{html.escape(report.title)}</h1>
<p class="warm">{html.escape(report.warm_message)}</p>{poster}</main></body></html>"""

    @staticmethod
    def _plain_text(report: DayReport) -> str:
        return f"{report.title}\n\n{report.warm_message}"

    @staticmethod
    def _write_pdf(report: DayReport, poster_path: Path | None, destination: Path) -> None:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
        from reportlab.platypus import Paragraph

        font_name = "DayDistillerCJK"
        bold_font_name = "DayDistillerCJKBold"
        try:
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(
                    TTFont(font_name, str(_windows_font("msyh.ttc")), subfontIndex=0)
                )
            if bold_font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(
                    TTFont(bold_font_name, str(_windows_font("msyhbd.ttc")), subfontIndex=0)
                )
        except Exception:
            font_name = bold_font_name = "STSong-Light"
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(UnicodeCIDFont(font_name))

        page_width, page_height = A4
        pdf = canvas.Canvas(str(destination), pagesize=A4, pageCompression=1)
        pdf.setTitle(report.title)
        pdf.setAuthor("Day Distiller")
        pdf.setFillColor(HexColor("#F7F4EE"))
        pdf.rect(0, 0, page_width, page_height, stroke=0, fill=1)

        margin_x = 18 * mm
        top = page_height - 17 * mm
        usable_width = page_width - 2 * margin_x
        title_style = ParagraphStyle(
            "PosterTitle",
            fontName=bold_font_name,
            fontSize=22,
            leading=29,
            alignment=TA_CENTER,
            textColor=HexColor("#172033"),
            spaceAfter=0,
        )
        warm_style = ParagraphStyle(
            "WarmMessage",
            fontName=font_name,
            fontSize=11.5,
            leading=19,
            alignment=TA_CENTER,
            textColor=HexColor("#596174"),
            spaceAfter=0,
        )
        title = Paragraph(html.escape(report.title), title_style)
        title_width, title_height = title.wrap(usable_width, 50 * mm)
        title.drawOn(pdf, margin_x + (usable_width - title_width) / 2, top - title_height)
        cursor_y = top - title_height - 5 * mm

        warm = Paragraph(html.escape(report.warm_message), warm_style)
        warm_width, warm_height = warm.wrap(usable_width, 38 * mm)
        warm.drawOn(pdf, margin_x + (usable_width - warm_width) / 2, cursor_y - warm_height)
        cursor_y -= warm_height + 7 * mm

        if poster_path and poster_path.is_file():
            image = ImageReader(str(poster_path))
            image_width, image_height = image.getSize()
            max_height = cursor_y - 14 * mm
            scale = min(usable_width / image_width, max_height / image_height)
            draw_width = image_width * scale
            draw_height = image_height * scale
            x = (page_width - draw_width) / 2
            y = cursor_y - draw_height
            pdf.drawImage(
                image,
                x,
                y,
                width=draw_width,
                height=draw_height,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )

        pdf.showPage()
        pdf.save()
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("PDF rendering did not produce an output file")


def _windows_font(filename: str) -> Path:
    font = Path("C:/Windows/Fonts") / filename
    if not font.is_file():
        raise FileNotFoundError(font)
    return font


def day_report_from_json(value: dict[str, object]) -> DayReport:
    summary = str(value.get("one_sentence_summary", ""))
    return DayReport(
        report_id=str(value["report_id"]),
        job_id=str(value["job_id"]),
        report_date=date.fromisoformat(str(value["report_date"])),
        title=str(value["title"]),
        one_sentence_summary=summary,
        warm_message=str(value.get("warm_message") or summary),
        narrative=str(value.get("narrative", "")),
        timeline=list(value.get("timeline", [])),
        panels=[ComicPanel(**panel) for panel in value.get("panels", [])],
        model_versions=dict(value.get("model_versions", {})),
        created_at=datetime.fromisoformat(str(value["created_at"])),
    )
