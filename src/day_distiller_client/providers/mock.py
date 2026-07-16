from __future__ import annotations

import hashlib
import mimetypes
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

from PIL import Image, ImageDraw

from ..domain import MotionAssessment
from .base import BatchAnalysis, ClaimOutput, DailySynthesis, DeliveryResult, PanelPlan, SceneAnalysis


class MockAIProvider:
    """Deterministic offline provider used for development and acceptance tests."""

    def analyze_batch(self, frame_paths: list[Path], audio_path: Path | None) -> BatchAnalysis:
        return BatchAnalysis(
            transcript="",
            frame_scores=[1.0 - index * 0.05 for index, _ in enumerate(frame_paths)],
            media_quality=0.8 if frame_paths else 0.2,
        )

    def analyze_scene(
        self,
        record_id: str,
        captured_at: datetime,
        frame_paths: list[Path],
        batch: BatchAnalysis,
        motion: MotionAssessment | None,
    ) -> SceneAnalysis:
        activity = motion.activity if motion else "unknown"
        activity_cn = {
            "stationary": "短暂停留",
            "walking": "正在步行",
            "running": "正在跑动",
            "vehicle_like": "可能正在乘车",
            "handling": "正在操作设备",
            "unknown": "记录了眼前的场景",
        }.get(activity, "记录了眼前的场景")
        confidence = motion.activity_confidence if motion else 0.5
        summary = f"{captured_at:%H:%M}，设备{activity_cn}。"
        return SceneAnalysis(
            summary=summary,
            location_candidate=None,
            location_confidence=0,
            visual_activity=activity,
            visual_activity_confidence=confidence,
            semantic_significance=min(1.0, 0.45 + confidence * 0.3),
            novelty=0.5,
            audio_value=0.2 if batch.transcript or batch.ambient_sounds else 0,
            memory_relevance=0,
            media_quality=0.8 if frame_paths else 0.2,
            claims=[
                ClaimOutput(
                    claim=summary,
                    evidence_type="sensor" if motion else "timestamp",
                    evidence_ids=[record_id],
                    confidence=confidence,
                )
            ],
            privacy_flags=[],
            ocr_text=batch.ocr_text,
            ambient_sounds=batch.ambient_sounds,
            selected_frame_indices=list(range(min(3, len(frame_paths)))),
        )

    def synthesize_day(self, report_date: date, scenes: list[dict[str, object]]) -> DailySynthesis:
        ordered = sorted(scenes, key=lambda item: str(item.get("captured_at", "")))
        ranked = sorted(ordered, key=lambda item: float(item.get("importance", 0)), reverse=True)
        selected = ranked[: min(3, len(ranked))]
        selected_ordered = sorted(selected, key=lambda item: str(item.get("captured_at", "")))
        summary = "今天留下了若干真实而安静的生活片段。" if ordered else "今天没有可用记录。"
        return DailySynthesis(
            title="值得收藏的平凡一天",
            one_sentence_summary=summary,
            warm_message="今天也辛苦了，把这些小小的闪光收好，然后安心休息吧。",
            narrative="\n".join(str(item.get("summary", "")) for item in ordered),
            panels=[
                PanelPlan(
                    record_ids=[str(scene["record_id"]) for scene in selected_ordered],
                    time_label="今日",
                    caption="；".join(str(scene.get("summary", "这一刻")) for scene in selected_ordered) or summary,
                    image_prompt=(
                        "Fuse the selected real moments into one vertical 3:4 flat-vector memoir poster; "
                        "use royal blue, coral orange, amber yellow and deep navy; no text."
                    ),
                )
            ],
        )

    def generate_panel(
        self,
        prompt: str,
        destination: Path,
        reference_images: list[Path] | None = None,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        image = Image.new("RGB", (996, 1332), (digest[0], digest[1], digest[2]))
        draw = ImageDraw.Draw(image)
        for index in range(8):
            inset = 35 + index * 35
            color = (digest[(index * 3) % 32], digest[(index * 3 + 1) % 32], digest[(index * 3 + 2) % 32])
            draw.rounded_rectangle((inset, inset, 996 - inset, 1332 - inset), radius=24, outline=color, width=8)
        image.save(destination, "JPEG", quality=90)
        return destination


class MockMailProvider:
    def __init__(self, outbox: Path) -> None:
        self.outbox = Path(outbox)

    def send(
        self,
        subject: str,
        plain_text: str,
        html: str,
        pdf_path: Path,
        inline_images: dict[str, Path],
        message_id: str,
    ) -> DeliveryResult:
        self.outbox.mkdir(parents=True, exist_ok=True)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = "mock@day-distiller.local"
        message["To"] = "user@example.invalid"
        message["Message-ID"] = message_id
        message.set_content(plain_text)
        message.add_alternative(html, subtype="html")
        html_part = message.get_payload()[-1]
        for content_id, image_path in inline_images.items():
            mime, _encoding = mimetypes.guess_type(image_path.name)
            maintype, subtype = (mime or "image/jpeg").split("/", 1)
            html_part.add_related(
                image_path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                cid=f"<{content_id}>",
                disposition="inline",
                filename=image_path.name,
            )
            related = html_part.get_payload()[-1]
            related["Content-Location"] = image_path.name
            message.add_attachment(
                image_path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=image_path.name,
            )
        message.add_attachment(pdf_path.read_bytes(), maintype="application", subtype="pdf", filename=pdf_path.name)
        destination = self.outbox / f"{message_id.strip('<>').replace('@', '_')}.eml"
        destination.write_bytes(message.as_bytes())
        return DeliveryResult(True, message_id, f"mock accepted: {destination}")
