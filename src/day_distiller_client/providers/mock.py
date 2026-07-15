from __future__ import annotations

import hashlib
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

from PIL import Image, ImageDraw

from ..domain import MotionAssessment
from .base import ClaimOutput, DailySynthesis, DeliveryResult, PanelPlan, SceneAnalysis


class MockAIProvider:
    """Deterministic offline provider used for development and acceptance tests."""

    def transcribe(self, audio_path: Path) -> str:
        return ""

    def analyze_scene(
        self,
        record_id: str,
        captured_at: datetime,
        frame_paths: list[Path],
        transcript: str,
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
            audio_value=0.2 if transcript else 0,
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
        )

    def synthesize_day(self, report_date: date, scenes: list[dict[str, object]]) -> DailySynthesis:
        ordered = sorted(scenes, key=lambda item: str(item.get("captured_at", "")))
        ranked = sorted(ordered, key=lambda item: float(item.get("importance", 0)), reverse=True)
        selected = ranked[: min(6, len(ranked))]
        panels = [
            PanelPlan(
                record_ids=[str(scene["record_id"])],
                time_label=str(scene.get("time_label", "")),
                caption=str(scene.get("summary", "这一天的一个瞬间")),
                image_prompt=(
                    "温暖电影感日记漫画，第一人称生活记录，清晰线稿与柔和色彩，"
                    f"表现这个场景：{scene.get('summary', '')}。画面中不要出现文字。"
                ),
            )
            for scene in sorted(selected, key=lambda item: str(item.get("captured_at", "")))
        ]
        summary = "今天留下了若干真实而安静的生活片段。" if ordered else "今天没有可用记录。"
        return DailySynthesis(
            title=f"{report_date:%Y年%m月%d日} · 每日蒸馏",
            one_sentence_summary=summary,
            narrative="\n".join(str(item.get("summary", "")) for item in ordered),
            panels=panels or [
                PanelPlan(record_ids=[], time_label="今日", caption=summary, image_prompt="温暖日记漫画空镜，不要文字")
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
        image = Image.new("RGB", (1024, 768), (digest[0], digest[1], digest[2]))
        draw = ImageDraw.Draw(image)
        for index in range(8):
            inset = 35 + index * 35
            color = (digest[(index * 3) % 32], digest[(index * 3 + 1) % 32], digest[(index * 3 + 2) % 32])
            draw.rounded_rectangle((inset, inset, 1024 - inset, 768 - inset), radius=24, outline=color, width=8)
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
        message.add_attachment(pdf_path.read_bytes(), maintype="application", subtype="pdf", filename=pdf_path.name)
        destination = self.outbox / f"{message_id.strip('<>').replace('@', '_')}.eml"
        destination.write_bytes(message.as_bytes())
        return DeliveryResult(True, message_id, f"mock accepted: {destination}")

