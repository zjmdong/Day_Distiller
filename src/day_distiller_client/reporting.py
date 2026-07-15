from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .domain import ComicPanel, DayReport


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
        inline_images = {
            f"panel-{index}": Path(panel.image_path)
            for index, panel in enumerate(report.panels, start=1)
            if panel.image_path and Path(panel.image_path).is_file()
        }
        local_sources = {key: value.resolve().as_uri() for key, value in inline_images.items()}
        email_sources = {key: f"cid:{key}" for key in inline_images}
        local_html = self._document(report, local_sources)
        email_html = self._document(report, email_sources)
        html_path = output_dir / "daily_report.html"
        pdf_path = output_dir / "daily_report.pdf"
        html_path.write_text(local_html, encoding="utf-8")
        self._write_pdf(local_html, pdf_path)
        return RenderedReport(
            html_path=html_path,
            pdf_path=pdf_path,
            email_html=email_html,
            plain_text=self._plain_text(report),
            inline_images=inline_images,
        )

    @staticmethod
    def _document(report: DayReport, image_sources: dict[str, str]) -> str:
        panel_blocks: list[str] = []
        for index, panel in enumerate(report.panels, start=1):
            source = image_sources.get(f"panel-{index}")
            image = f'<img src="{html.escape(source)}" alt="漫画第 {index} 格">' if source else ""
            panel_blocks.append(
                '<section class="panel">'
                f"{image}<div class=\"panel-copy\"><div class=\"time\">{html.escape(panel.time_label)}</div>"
                f"<p>{html.escape(panel.caption)}</p></div></section>"
            )
        timeline_rows = "".join(
            "<tr>"
            f"<td>{html.escape(str(item.get('time_label', '')))}</td>"
            f"<td>{html.escape(str(item.get('location', '') or ''))}</td>"
            f"<td>{html.escape(str(item.get('summary', '')))}</td>"
            f"<td>{float(item.get('confidence', 0)):.0%}</td>"
            "</tr>"
            for item in report.timeline
        )
        narrative = "<br>".join(html.escape(report.narrative).splitlines())
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(report.title)}</title>
<style>
@page {{ size: A4; margin: 14mm; }}
body {{ background:#f4f0e8; color:#2f2b27; font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif; margin:0; }}
.page {{ max-width:920px; margin:0 auto; padding:32px; }}
header {{ background:#293241; color:white; padding:28px; border-radius:20px; }}
h1 {{ margin:0 0 8px; font-size:30px; }} h2 {{ margin-top:32px; }}
.summary {{ color:#f6bd60; font-size:17px; }}
.panel {{ background:white; border-radius:16px; margin:22px 0; overflow:hidden; page-break-inside:avoid; }}
.panel img {{ width:100%; display:block; }} .panel-copy {{ padding:16px 20px; }}
.time {{ color:#d26a4a; font-weight:bold; }}
table {{ width:100%; border-collapse:collapse; background:white; }} th,td {{ padding:10px; border-bottom:1px solid #ddd; text-align:left; }}
.notice {{ color:#6b625b; font-size:12px; margin-top:28px; }}
</style></head><body><main class="page">
<header><h1>{html.escape(report.title)}</h1><div class="summary">{html.escape(report.one_sentence_summary)}</div></header>
<h2>今日漫画</h2>{''.join(panel_blocks)}
<h2>一天小结</h2><p>{narrative}</p>
<h2>完整记录时间线</h2><table><thead><tr><th>时间</th><th>地点</th><th>片段</th><th>证据置信度</th></tr></thead><tbody>{timeline_rows}</tbody></table>
<p class="notice">本报告只描述设备实际记录到的短片段；谨慎措辞表示证据不足，不代表采样间隔内持续发生。</p>
</main></body></html>"""

    @staticmethod
    def _plain_text(report: DayReport) -> str:
        timeline = "\n".join(
            f"- {item.get('time_label', '')} {item.get('summary', '')}" for item in report.timeline
        )
        return f"{report.title}\n{report.one_sentence_summary}\n\n{report.narrative}\n\n完整时间线\n{timeline}"

    @staticmethod
    def _write_pdf(document_html: str, destination: Path) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QGuiApplication, QPageLayout, QPageSize, QPdfWriter, QTextDocument

        application = QGuiApplication.instance()
        if application is None:
            application = QGuiApplication([])
        writer = QPdfWriter(str(destination))
        writer.setResolution(144)
        writer.setPageLayout(
            QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait, QMarginsF(12, 12, 12, 12))
        )
        document = QTextDocument()
        document.setHtml(document_html)
        document.setPageSize(writer.pageLayout().paintRectPixels(writer.resolution()).size())
        document.print_(writer)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("PDF rendering did not produce an output file")


def day_report_from_json(value: dict[str, object]) -> DayReport:
    return DayReport(
        report_id=str(value["report_id"]),
        job_id=str(value["job_id"]),
        report_date=date.fromisoformat(str(value["report_date"])),
        title=str(value["title"]),
        one_sentence_summary=str(value["one_sentence_summary"]),
        narrative=str(value["narrative"]),
        timeline=list(value.get("timeline", [])),
        panels=[ComicPanel(**panel) for panel in value.get("panels", [])],
        model_versions=dict(value.get("model_versions", {})),
        created_at=datetime.fromisoformat(str(value["created_at"])),
    )
