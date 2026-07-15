from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .domain import JOB_FLOW, CaptureRecord, DayReport, JobStage, SceneEvidence


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    target_date TEXT NOT NULL,
    stage TEXT NOT NULL,
    resume_stage TEXT,
    provider_mode TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    manifest_path TEXT,
    report_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    record_name TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    source_dir TEXT NOT NULL,
    local_dir TEXT NOT NULL,
    video_path TEXT,
    audio_path TEXT,
    imu_path TEXT,
    meta_path TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    valid INTEGER NOT NULL DEFAULT 1,
    validation_error TEXT,
    files_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(job_id, record_name)
);

CREATE TABLE IF NOT EXISTS scene_evidence (
    record_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
    evidence_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    report_json TEXT NOT NULL,
    html_path TEXT,
    pdf_path TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_receipts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    response TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cleanup_tasks (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS place_memories (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    signature TEXT NOT NULL,
    visit_dates_json TEXT NOT NULL,
    user_verified INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class JobRow:
    id: str
    target_date: date
    stage: JobStage
    provider_mode: str
    progress: float
    error: str | None
    manifest_path: str | None
    report_path: str | None
    created_at: datetime
    updated_at: datetime


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobDatabase:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def create_job(self, target_date: date, provider_mode: str = "mock") -> JobRow:
        job_id = str(uuid.uuid4())
        now = _utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO jobs (id,target_date,stage,provider_mode,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (job_id, target_date.isoformat(), JobStage.IMPORTING.value, provider_mode, now, now),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobRow:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._job_from_row(row)

    def list_jobs(self, limit: int = 100) -> list[JobRow]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def set_stage(self, job_id: str, stage: JobStage, progress: float | None = None) -> None:
        current = self.get_job(job_id)
        if current.stage == JobStage.FAILED:
            raise ValueError("retry a failed job before advancing it")
        if stage == JobStage.FAILED:
            raise ValueError("use fail_job")
        if current.stage != stage:
            current_index = JOB_FLOW.index(current.stage)
            expected = JOB_FLOW[min(current_index + 1, len(JOB_FLOW) - 1)]
            if stage != expected:
                raise ValueError(f"invalid stage transition: {current.stage} -> {stage}")
        values: list[Any] = [stage.value, _utc_now(), job_id]
        sql = "UPDATE jobs SET stage = ?, error = NULL, updated_at = ? WHERE id = ?"
        if progress is not None:
            sql = "UPDATE jobs SET stage = ?, error = NULL, progress = ?, updated_at = ? WHERE id = ?"
            values = [stage.value, max(0.0, min(1.0, progress)), _utc_now(), job_id]
        with self.connect() as connection:
            connection.execute(sql, values)

    def set_progress(self, job_id: str, progress: float) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?",
                (max(0.0, min(1.0, progress)), _utc_now(), job_id),
            )

    def fail_job(self, job_id: str, error: str) -> None:
        current = self.get_job(job_id)
        resume = current.stage.value if current.stage != JobStage.FAILED else None
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET resume_stage = COALESCE(?, resume_stage), stage = ?, error = ?, updated_at = ? WHERE id = ?",
                (resume, JobStage.FAILED.value, error, _utc_now(), job_id),
            )

    def retry_job(self, job_id: str) -> JobRow:
        with self.connect() as connection:
            row = connection.execute("SELECT stage,resume_stage FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["stage"] != JobStage.FAILED.value or not row["resume_stage"]:
                raise ValueError("job is not retryable")
            connection.execute(
                "UPDATE jobs SET stage = resume_stage, resume_stage = NULL, error = NULL, updated_at = ? WHERE id = ?",
                (_utc_now(), job_id),
            )
        return self.get_job(job_id)

    def set_manifest_path(self, job_id: str, path: Path) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET manifest_path = ?, updated_at = ? WHERE id = ?",
                (str(path), _utc_now(), job_id),
            )

    def upsert_record(self, job_id: str, record: CaptureRecord) -> None:
        files_json = json.dumps([asdict(item) for item in record.files], ensure_ascii=False)
        values = (
            record.record_id,
            job_id,
            record.record_name,
            record.captured_at.isoformat(),
            str(record.source_dir),
            str(record.local_dir),
            str(record.video_path) if record.video_path else None,
            str(record.audio_path) if record.audio_path else None,
            str(record.imu_path) if record.imu_path else None,
            str(record.meta_path) if record.meta_path else None,
            record.schema_version,
            int(record.valid),
            record.validation_error,
            files_json,
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  valid=excluded.valid,
                  validation_error=excluded.validation_error,
                  files_json=excluded.files_json
                """,
                values,
            )

    def list_records(self, job_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM records WHERE job_id = ? ORDER BY captured_at", (job_id,)
            ).fetchall()

    def list_scene_evidence(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT scene_evidence.evidence_json
                FROM scene_evidence
                JOIN records ON records.id = scene_evidence.record_id
                WHERE records.job_id = ?
                ORDER BY records.captured_at
                """,
                (job_id,),
            ).fetchall()
        return [json.loads(row["evidence_json"]) for row in rows]

    def save_scene_evidence(self, evidence: SceneEvidence) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO scene_evidence (record_id,evidence_json,updated_at) VALUES (?,?,?)
                ON CONFLICT(record_id) DO UPDATE SET evidence_json=excluded.evidence_json,updated_at=excluded.updated_at
                """,
                (evidence.record_id, json.dumps(evidence.to_json_dict(), ensure_ascii=False), _utc_now()),
            )

    def save_report(self, report: DayReport, html_path: Path | None = None, pdf_path: Path | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO reports (id,job_id,report_json,html_path,pdf_path,created_at) VALUES (?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                  id=excluded.id,report_json=excluded.report_json,html_path=excluded.html_path,pdf_path=excluded.pdf_path
                """,
                (
                    report.report_id,
                    report.job_id,
                    json.dumps(report.to_json_dict(), ensure_ascii=False),
                    str(html_path) if html_path else None,
                    str(pdf_path) if pdf_path else None,
                    report.created_at.isoformat(),
                ),
            )
            if html_path:
                connection.execute(
                    "UPDATE jobs SET report_path = ?, updated_at = ? WHERE id = ?",
                    (str(html_path), _utc_now(), report.job_id),
                )

    def get_report(self, job_id: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM reports WHERE job_id = ?", (job_id,)).fetchone()

    def save_delivery(self, job_id: str, message_id: str, accepted: bool, response: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO delivery_receipts VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), job_id, message_id, int(accepted), response, _utc_now()),
            )

    def accepted_delivery(self, job_id: str, message_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM delivery_receipts WHERE job_id = ? AND message_id = ? AND accepted = 1 LIMIT 1",
                (job_id, message_id),
            ).fetchone()
        return row is not None

    def list_cleanup_tasks(self, state: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM cleanup_tasks"
        parameters: tuple[Any, ...] = ()
        if state is not None:
            sql += " WHERE state = ?"
            parameters = (state,)
        sql += " ORDER BY updated_at"
        with self.connect() as connection:
            return connection.execute(sql, parameters).fetchall()

    def remember_place(self, label: str, signature: str, visit_date: date) -> None:
        if not label.strip() or not signature:
            return
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id,visit_dates_json FROM place_memories WHERE signature = ?", (signature,)
            ).fetchone()
            visits = set(json.loads(row["visit_dates_json"])) if row else set()
            visits.add(visit_date.isoformat())
            if row:
                connection.execute(
                    "UPDATE place_memories SET label=?,visit_dates_json=?,user_verified=1,updated_at=? WHERE id=?",
                    (label.strip(), json.dumps(sorted(visits)), _utc_now(), row["id"]),
                )
            else:
                connection.execute(
                    "INSERT INTO place_memories VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), label.strip(), signature, json.dumps(sorted(visits)), 1, _utc_now()),
                )

    def match_place(self, signature: str, max_hamming_distance: int = 8) -> str | None:
        try:
            candidate = int(signature, 16)
        except ValueError:
            return None
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT label,signature FROM place_memories WHERE user_verified = 1"
            ).fetchall()
        best: tuple[int, str] | None = None
        for row in rows:
            try:
                distance = (candidate ^ int(row["signature"], 16)).bit_count()
            except ValueError:
                continue
            if distance <= max_hamming_distance and (best is None or distance < best[0]):
                best = (distance, str(row["label"]))
        return best[1] if best else None

    def set_cleanup_state(self, job_id: str, state: str, error: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO cleanup_tasks (job_id,state,attempts,error,updated_at) VALUES (?,?,1,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                  state=excluded.state,attempts=cleanup_tasks.attempts+1,error=excluded.error,updated_at=excluded.updated_at
                """,
                (job_id, state, error, _utc_now()),
            )

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), _utc_now()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            row = connection.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["value_json"])

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> JobRow:
        return JobRow(
            id=row["id"],
            target_date=date.fromisoformat(row["target_date"]),
            stage=JobStage(row["stage"]),
            provider_mode=row["provider_mode"],
            progress=float(row["progress"]),
            error=row["error"],
            manifest_path=row["manifest_path"],
            report_path=row["report_path"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
