from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class JobStage(StrEnum):
    IMPORTING = "importing"
    VALIDATING = "validating"
    PREPROCESSING = "preprocessing"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    RENDERING = "rendering"
    EMAILING = "emailing"
    CLEANUP = "cleanup"
    COMPLETED = "completed"
    FAILED = "failed"


JOB_FLOW: tuple[JobStage, ...] = (
    JobStage.IMPORTING,
    JobStage.VALIDATING,
    JobStage.PREPROCESSING,
    JobStage.ANALYZING,
    JobStage.GENERATING,
    JobStage.RENDERING,
    JobStage.EMAILING,
    JobStage.CLEANUP,
    JobStage.COMPLETED,
)


@dataclass(frozen=True)
class FileDigest:
    relative_path: str
    size: int
    sha256: str


@dataclass
class CaptureRecord:
    record_id: str
    record_name: str
    captured_at: datetime
    source_dir: Path
    local_dir: Path
    video_path: Path | None = None
    audio_path: Path | None = None
    imu_path: Path | None = None
    meta_path: Path | None = None
    schema_version: int = 1
    valid: bool = True
    validation_error: str | None = None
    files: list[FileDigest] = field(default_factory=list)

    @property
    def local_date(self) -> date:
        return self.captured_at.date()


@dataclass(frozen=True)
class MotionAssessment:
    placement: str
    placement_confidence: float
    activity: str
    activity_confidence: float
    duration_s: float
    sample_rate_hz: float
    features: dict[str, float]
    classifier_version: str = "heuristic-v1"


@dataclass(frozen=True)
class EvidenceClaim:
    claim: str
    evidence_type: str
    evidence_ids: list[str]
    confidence: float


@dataclass
class SceneEvidence:
    record_id: str
    captured_at: datetime
    summary: str
    transcript: str
    location_candidate: str | None
    location_confidence: float
    visual_activity: str
    visual_activity_confidence: float
    motion: MotionAssessment | None
    importance: float
    claims: list[EvidenceClaim] = field(default_factory=list)
    privacy_flags: list[str] = field(default_factory=list)
    frame_paths: list[Path] = field(default_factory=list)
    visual_signature: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["captured_at"] = self.captured_at.isoformat()
        value["frame_paths"] = [str(path) for path in self.frame_paths]
        return value


@dataclass(frozen=True)
class ComicPanel:
    record_ids: list[str]
    time_label: str
    caption: str
    image_prompt: str
    image_path: str | None = None


@dataclass
class DayReport:
    report_id: str
    job_id: str
    report_date: date
    title: str
    one_sentence_summary: str
    narrative: str
    timeline: list[dict[str, Any]]
    panels: list[ComicPanel]
    model_versions: dict[str, str]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_json_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["report_date"] = self.report_date.isoformat()
        value["created_at"] = self.created_at.isoformat()
        return value
