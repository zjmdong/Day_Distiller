from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from .database import JobDatabase
from .domain import CaptureRecord, ComicPanel, DayReport, EvidenceClaim, FileDigest, JobStage, SceneEvidence
from .imu_analysis import analyze_imu
from .legacy_import import (
    ImportVerificationError,
    delete_verified_source_records,
    import_legacy_day,
    sha256_file,
)
from .media import MediaPreprocessor, PreprocessedMedia, image_average_hash
from .paths import AppPaths
from .providers.base import BatchMultimodalProvider, ImageProvider, MailProvider, SceneProvider, StoryProvider
from .reporting import RenderedReport, ReportRenderer, day_report_from_json


ProgressCallback = Callable[[JobStage, float, str], None]
CleanupHandler = Callable[[str, Path], None]


@dataclass(frozen=True)
class PipelineResult:
    job_id: str
    report: DayReport
    rendered: RenderedReport
    cleanup_state: str


class DistillationPipeline:
    def __init__(
        self,
        paths: AppPaths,
        database: JobDatabase,
        scene_provider: SceneProvider,
        story_provider: StoryProvider,
        batch_provider: BatchMultimodalProvider,
        image_provider: ImageProvider,
        mail_provider: MailProvider,
        media_preprocessor: MediaPreprocessor | None = None,
        report_renderer: ReportRenderer | None = None,
        motion_model_path: Path | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.paths = paths.ensure()
        self.database = database
        self.scene_provider = scene_provider
        self.story_provider = story_provider
        self.batch_provider = batch_provider
        self.image_provider = image_provider
        self.mail_provider = mail_provider
        self.media = media_preprocessor or MediaPreprocessor()
        self.renderer = report_renderer or ReportRenderer()
        self.motion_model_path = motion_model_path
        self.progress = progress

    def import_legacy(
        self,
        source_root: Path,
        target_date: date,
        device_id: str = "legacy-device",
        provider_mode: str = "mock",
    ) -> str:
        job = self.database.create_job(target_date, provider_mode)
        try:
            records, manifest_path = import_legacy_day(
                source_root,
                target_date,
                self.paths.imports,
                job.id,
                device_id,
                progress=lambda done, total, text: self._notify(
                    JobStage.IMPORTING, done / max(1, total), text
                ),
            )
            if not records:
                raise ImportVerificationError(f"{target_date.isoformat()} 没有找到可导入的记录")
            for record in records:
                self.database.upsert_record(job.id, record)
            self.database.set_manifest_path(job.id, manifest_path)
            self.database.set_stage(job.id, JobStage.VALIDATING, 0.1)
            return job.id
        except Exception as exc:
            self.database.fail_job(job.id, str(exc))
            raise

    def process(
        self,
        job_id: str,
        cleanup_source_root: Path | None = None,
        avatar_references: list[Path] | None = None,
        cleanup_handler: CleanupHandler | None = None,
    ) -> PipelineResult:
        job = self.database.get_job(job_id)
        if job.stage == JobStage.FAILED:
            job = self.database.retry_job(job_id)
        if job.stage == JobStage.COMPLETED:
            raise ValueError("job is already completed")
        try:
            records = self._records(job_id)
            self._validate_local(records)
            self._advance(job_id, JobStage.VALIDATING, JobStage.PREPROCESSING, 0.15, "本地哈希校验完成")

            preprocessed: dict[str, PreprocessedMedia] = {}
            for index, record in enumerate(records, start=1):
                preprocessed[record.record_id] = self.media.preprocess_record(record, self.paths.cache / job_id / "frames")
                self._notify(JobStage.PREPROCESSING, index / len(records), f"已预处理 {record.record_name}")
            self._advance(job_id, JobStage.PREPROCESSING, JobStage.ANALYZING, 0.3, "媒体预处理完成")

            evidence = self._analyze(records, preprocessed)
            self._advance(job_id, JobStage.ANALYZING, JobStage.GENERATING, 0.55, "逐片证据分析完成")

            report = self._generate_report(job_id, job.target_date, evidence, avatar_references or [])
            self.database.save_report(report)
            self._advance(job_id, JobStage.GENERATING, JobStage.RENDERING, 0.72, "漫画生成完成")

            rendered = self.renderer.render(report, self.paths.reports / job.target_date.isoformat() / job_id)
            self.database.save_report(report, rendered.html_path, rendered.pdf_path)
            self._advance(job_id, JobStage.RENDERING, JobStage.EMAILING, 0.84, "HTML 与 PDF 排版完成")

            message_id = f"<day-distiller-{job_id}@local>"
            if not self.database.accepted_delivery(job_id, message_id):
                delivery = self.mail_provider.send(
                    report.title,
                    rendered.plain_text,
                    rendered.email_html,
                    rendered.pdf_path,
                    rendered.inline_images,
                    message_id,
                )
                self.database.save_delivery(job_id, delivery.message_id, delivery.accepted, delivery.response)
                if not delivery.accepted:
                    raise RuntimeError(f"邮件服务器未接受日报：{delivery.response}")
            self._advance(job_id, JobStage.EMAILING, JobStage.CLEANUP, 0.94, "邮件服务器已接受日报")

            cleanup_state = self._cleanup(job_id, cleanup_source_root, cleanup_handler)
            self._advance(job_id, JobStage.CLEANUP, JobStage.COMPLETED, 1.0, "每日蒸馏完成")
            return PipelineResult(job_id, report, rendered, cleanup_state)
        except Exception as exc:
            if self.database.get_job(job_id).stage != JobStage.COMPLETED:
                self.database.fail_job(job_id, str(exc))
            raise

    def _records(self, job_id: str) -> list[CaptureRecord]:
        records: list[CaptureRecord] = []
        for row in self.database.list_records(job_id):
            files = [FileDigest(**item) for item in json.loads(row["files_json"])]
            records.append(
                CaptureRecord(
                    record_id=row["id"],
                    record_name=row["record_name"],
                    captured_at=datetime.fromisoformat(row["captured_at"]),
                    source_dir=Path(row["source_dir"]),
                    local_dir=Path(row["local_dir"]),
                    video_path=Path(row["video_path"]) if row["video_path"] else None,
                    audio_path=Path(row["audio_path"]) if row["audio_path"] else None,
                    imu_path=Path(row["imu_path"]) if row["imu_path"] else None,
                    meta_path=Path(row["meta_path"]) if row["meta_path"] else None,
                    schema_version=int(row["schema_version"]),
                    valid=bool(row["valid"]),
                    validation_error=row["validation_error"],
                    files=files,
                )
            )
        if not records:
            raise RuntimeError("任务没有可处理的记录")
        return records

    @staticmethod
    def _validate_local(records: list[CaptureRecord]) -> None:
        for record in records:
            for digest in record.files:
                path = (record.local_dir / digest.relative_path).resolve()
                if not path.is_relative_to(record.local_dir.resolve()) or not path.is_file():
                    raise ImportVerificationError(f"本地导入文件丢失：{record.record_name}/{digest.relative_path}")
                if path.stat().st_size != digest.size or sha256_file(path) != digest.sha256:
                    raise ImportVerificationError(f"本地导入文件校验失败：{record.record_name}/{digest.relative_path}")

    def _analyze(
        self, records: list[CaptureRecord], preprocessed: dict[str, PreprocessedMedia]
    ) -> list[SceneEvidence]:
        values: list[SceneEvidence] = []
        for index, record in enumerate(records, start=1):
            media = preprocessed[record.record_id]
            motion = None
            if record.imu_path:
                try:
                    motion = analyze_imu(record.imu_path, self.motion_model_path)
                except Exception:
                    motion = None
            batch = self.batch_provider.analyze_batch(media.frames, record.audio_path)
            ranked_frames = _rank_frames(media.frames, batch.frame_scores)
            analysis = self.scene_provider.analyze_scene(
                record.record_id, record.captured_at, ranked_frames, batch, motion
            )
            visual_signature = f"{image_average_hash(media.frames[0]):016x}" if media.frames else None
            remembered_place = self.database.match_place(visual_signature) if visual_signature else None
            location_candidate = analysis.location_candidate
            location_confidence = analysis.location_confidence
            if remembered_place and location_confidence < 0.8:
                location_candidate = remembered_place
                location_confidence = 0.75
            motion_value = 0.2
            if motion:
                motion_value = motion.activity_confidence * (0.25 if motion.activity == "stationary" else 1.0)
            importance = max(
                0.0,
                min(
                    1.0,
                    0.30 * analysis.semantic_significance
                    + 0.20 * analysis.novelty
                    + 0.15 * motion_value
                    + 0.15 * analysis.audio_value
                    + 0.10 * analysis.memory_relevance
                    + 0.10 * analysis.media_quality,
                ),
            )
            evidence = SceneEvidence(
                record_id=record.record_id,
                captured_at=record.captured_at,
                summary=analysis.summary,
                transcript=batch.transcript,
                location_candidate=location_candidate,
                location_confidence=location_confidence,
                visual_activity=analysis.visual_activity,
                visual_activity_confidence=analysis.visual_activity_confidence,
                motion=motion,
                importance=importance,
                claims=[EvidenceClaim(**claim.model_dump()) for claim in analysis.claims],
                privacy_flags=analysis.privacy_flags,
                frame_paths=media.frames,
                visual_signature=visual_signature,
                ambient_sounds=analysis.ambient_sounds or batch.ambient_sounds,
                ocr_text=analysis.ocr_text or batch.ocr_text,
            )
            self.database.save_scene_evidence(evidence)
            values.append(evidence)
            self._notify(JobStage.ANALYZING, index / len(records), f"已分析 {record.record_name}")
        return values

    def _generate_report(
        self, job_id: str, report_date: date, evidence: list[SceneEvidence], avatar_references: list[Path]
    ) -> DayReport:
        scene_payloads = [
            {
                "record_id": item.record_id,
                "captured_at": item.captured_at.isoformat(),
                "time_label": item.captured_at.strftime("%H:%M"),
                "summary": item.summary,
                "transcript": item.transcript,
                "ambient_sounds": item.ambient_sounds,
                "ocr_text": item.ocr_text,
                "location_candidate": item.location_candidate,
                "location_confidence": item.location_confidence,
                "visual_activity": item.visual_activity,
                "visual_activity_confidence": item.visual_activity_confidence,
                "motion": item.motion.activity if item.motion else "unknown",
                "motion_confidence": item.motion.activity_confidence if item.motion else 0,
                "importance": item.importance,
                "visual_signature": item.visual_signature,
                "claims": [claim.__dict__ for claim in item.claims],
            }
            for item in evidence
        ]
        synthesis = self.story_provider.synthesize_day(report_date, scene_payloads)
        panel_dir = self.paths.reports / report_date.isoformat() / job_id / "panels"
        panels: list[ComicPanel] = []
        for index, plan in enumerate(synthesis.panels, start=1):
            destination = panel_dir / f"panel_{index:02d}.jpg"
            self.image_provider.generate_panel(plan.image_prompt, destination, avatar_references)
            panels.append(
                ComicPanel(
                    record_ids=plan.record_ids,
                    time_label=plan.time_label,
                    caption=plan.caption,
                    image_prompt=plan.image_prompt,
                    image_path=str(destination),
                )
            )
            self._notify(JobStage.GENERATING, index / len(synthesis.panels), f"已生成漫画第 {index} 格")
        timeline = [
            {
                "record_id": item.record_id,
                "time_label": item.captured_at.strftime("%H:%M"),
                "captured_at": item.captured_at.isoformat(),
                "summary": item.summary,
                "location": item.location_candidate if item.location_confidence >= 0.6 else None,
                "location_confidence": item.location_confidence,
                "visual_signature": item.visual_signature,
                "confidence": max(
                    item.visual_activity_confidence,
                    item.motion.activity_confidence if item.motion else 0,
                ),
                "importance": item.importance,
            }
            for item in evidence
        ]
        scene_settings = getattr(self.scene_provider, "settings", None)
        daily_settings = getattr(self.story_provider, "settings", None)
        image_settings = getattr(self.image_provider, "settings", None)
        model_versions = {
            "keyframe_vision": getattr(scene_settings, "keyframe_model", "mock-v1"),
            "batch_omni": getattr(scene_settings, "omni_model", "mock-v1"),
            "daily": getattr(daily_settings, "daily_model", "mock-v1"),
            "image": getattr(image_settings, "image_model", "mock-v1"),
            "motion": "heuristic-v1" if not self.motion_model_path else self.motion_model_path.name,
        }
        return DayReport(
            report_id=str(uuid.uuid4()),
            job_id=job_id,
            report_date=report_date,
            title=synthesis.title,
            one_sentence_summary=synthesis.one_sentence_summary,
            narrative=synthesis.narrative,
            timeline=timeline,
            panels=panels,
            model_versions=model_versions,
        )

    def regenerate(self, job_id: str, avatar_references: list[Path] | None = None) -> tuple[DayReport, RenderedReport]:
        job = self.database.get_job(job_id)
        evidence = [self._evidence_from_json(item) for item in self.database.list_scene_evidence(job_id)]
        if not evidence:
            raise RuntimeError("任务没有已保存的场景证据")
        for item in evidence:
            remembered = self.database.match_place(item.visual_signature) if item.visual_signature else None
            if remembered:
                item.location_candidate = remembered
                item.location_confidence = 1.0
        report = self._generate_report(job_id, job.target_date, evidence, avatar_references or [])
        rendered = self.renderer.render(report, self.paths.reports / job.target_date.isoformat() / job_id)
        self.database.save_report(report, rendered.html_path, rendered.pdf_path)
        return report, rendered

    def resend(self, job_id: str) -> RenderedReport:
        row = self.database.get_report(job_id)
        if row is None:
            raise RuntimeError("任务没有可发送的日报")
        report_json = json.loads(row["report_json"])
        report = day_report_from_json(report_json)
        rendered = self.renderer.render(report, self.paths.reports / report.report_date.isoformat() / job_id)
        revision = hashlib.sha256(json.dumps(report_json, sort_keys=True).encode()).hexdigest()[:12]
        message_id = f"<day-distiller-{job_id}-manual-{revision}@local>"
        delivery = self.mail_provider.send(
            report.title,
            rendered.plain_text,
            rendered.email_html,
            rendered.pdf_path,
            rendered.inline_images,
            message_id,
        )
        self.database.save_delivery(job_id, delivery.message_id, delivery.accepted, delivery.response)
        if not delivery.accepted:
            raise RuntimeError(f"邮件服务器未接受日报：{delivery.response}")
        return rendered

    @staticmethod
    def _evidence_from_json(value: dict[str, object]) -> SceneEvidence:
        motion_value = value.get("motion")
        motion = None
        if isinstance(motion_value, dict):
            from .domain import MotionAssessment

            motion = MotionAssessment(**motion_value)
        return SceneEvidence(
            record_id=str(value["record_id"]),
            captured_at=datetime.fromisoformat(str(value["captured_at"])),
            summary=str(value.get("summary", "")),
            transcript=str(value.get("transcript", "")),
            location_candidate=str(value["location_candidate"]) if value.get("location_candidate") else None,
            location_confidence=float(value.get("location_confidence", 0)),
            visual_activity=str(value.get("visual_activity", "unknown")),
            visual_activity_confidence=float(value.get("visual_activity_confidence", 0)),
            motion=motion,
            importance=float(value.get("importance", 0)),
            claims=[EvidenceClaim(**item) for item in value.get("claims", [])],
            privacy_flags=list(value.get("privacy_flags", [])),
            frame_paths=[Path(path) for path in value.get("frame_paths", [])],
            visual_signature=str(value["visual_signature"]) if value.get("visual_signature") else None,
            ambient_sounds=list(value.get("ambient_sounds", [])),
            ocr_text=list(value.get("ocr_text", [])),
        )

    def _cleanup(
        self,
        job_id: str,
        source_root: Path | None,
        cleanup_handler: CleanupHandler | None,
    ) -> str:
        job = self.database.get_job(job_id)
        if not job.manifest_path:
            self.database.set_cleanup_state(job_id, "pending_cleanup", "导入清单路径缺失")
            return "pending_cleanup"
        if source_root is None and cleanup_handler is None:
            self.database.set_cleanup_state(job_id, "pending_cleanup", "等待设备重新以读写模式连接")
            return "pending_cleanup"
        try:
            if cleanup_handler:
                cleanup_handler(job_id, Path(job.manifest_path))
            else:
                assert source_root is not None
                delete_verified_source_records(source_root, Path(job.manifest_path))
        except Exception as exc:
            self.database.set_cleanup_state(job_id, "pending_cleanup", str(exc))
            return "pending_cleanup"
        self.database.set_cleanup_state(job_id, "completed")
        return "completed"

    def _advance(
        self,
        job_id: str,
        expected: JobStage,
        next_stage: JobStage,
        progress: float,
        message: str,
    ) -> None:
        current = self.database.get_job(job_id).stage
        if current == expected:
            self.database.set_stage(job_id, next_stage, progress)
        elif current != next_stage and current != JobStage.COMPLETED:
            # Retried jobs may recompute prerequisite artifacts before reaching their resume stage.
            if current.value not in {stage.value for stage in JobStage}:
                raise RuntimeError(f"未知任务状态：{current}")
        self._notify(next_stage, progress, message)

    def _notify(self, stage: JobStage, progress: float, message: str) -> None:
        if self.progress:
            self.progress(stage, progress, message)


def _rank_frames(paths: list[Path], scores: list[float], limit: int = 6) -> list[Path]:
    """Honor Omni's relevance ranking without losing deterministic fallback behavior."""
    if len(scores) != len(paths):
        return paths[:limit]
    ranked = sorted(enumerate(paths), key=lambda item: (-scores[item[0]], item[0]))
    return [path for _, path in ranked[:limit]]
