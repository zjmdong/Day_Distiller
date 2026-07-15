from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from ..domain import MotionAssessment


class ClaimOutput(BaseModel):
    claim: str
    evidence_type: str
    evidence_ids: list[str]
    confidence: float = Field(ge=0, le=1)


class SceneAnalysis(BaseModel):
    summary: str
    location_candidate: str | None = None
    location_confidence: float = Field(ge=0, le=1)
    visual_activity: str
    visual_activity_confidence: float = Field(ge=0, le=1)
    semantic_significance: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    audio_value: float = Field(ge=0, le=1)
    memory_relevance: float = Field(ge=0, le=1)
    media_quality: float = Field(ge=0, le=1)
    claims: list[ClaimOutput]
    privacy_flags: list[str]


class PanelPlan(BaseModel):
    record_ids: list[str]
    time_label: str
    caption: str
    image_prompt: str


class DailySynthesis(BaseModel):
    title: str
    one_sentence_summary: str
    narrative: str
    panels: list[PanelPlan] = Field(min_length=1, max_length=8)


@dataclass(frozen=True)
class DeliveryResult:
    accepted: bool
    message_id: str
    response: str


class TranscriptionProvider(Protocol):
    def transcribe(self, audio_path: Path) -> str: ...


class StoryProvider(Protocol):
    def analyze_scene(
        self,
        record_id: str,
        captured_at: datetime,
        frame_paths: list[Path],
        transcript: str,
        motion: MotionAssessment | None,
    ) -> SceneAnalysis: ...

    def synthesize_day(self, report_date: date, scenes: list[dict[str, object]]) -> DailySynthesis: ...


class ImageProvider(Protocol):
    def generate_panel(
        self,
        prompt: str,
        destination: Path,
        reference_images: list[Path] | None = None,
    ) -> Path: ...


class MailProvider(Protocol):
    def send(
        self,
        subject: str,
        plain_text: str,
        html: str,
        pdf_path: Path,
        inline_images: dict[str, Path],
        message_id: str,
    ) -> DeliveryResult: ...

