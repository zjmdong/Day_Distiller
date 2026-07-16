from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .domain import CaptureRecord, FileDigest


RECORD_NAME = re.compile(
    r"^REC_(?P<sequence>\d{4})_(?P<date>\d{6})_(?P<time>\d{6})$"
)
KNOWN_FILES = ("video.avi", "audio.wav", "imu.json", "meta.json")
ProgressCallback = Callable[[int, int, str], None]


class ImportVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CachedRecord:
    """One device record verified into the long-lived local material cache."""

    record_name: str
    captured_at: datetime
    local_dir: Path
    files: tuple[FileDigest, ...]
    was_new: bool
    changed_files: int


@dataclass(frozen=True)
class CacheSyncResult:
    records: tuple[CachedRecord, ...]
    manifest_path: Path

    @property
    def changed_record_count(self) -> int:
        return sum(1 for record in self.records if record.was_new or record.changed_files)


def parse_record_datetime(name: str) -> datetime:
    match = RECORD_NAME.fullmatch(name)
    if not match:
        raise ValueError(f"invalid legacy record directory: {name}")
    value = datetime.strptime(match.group("date") + match.group("time"), "%y%m%d%H%M%S")
    # On Windows, converting a naive pre-1970 datetime with astimezone() can
    # route through a negative C timestamp and raise OSError(22). The device
    # directory format uses two-digit years, so old RTC test captures can hit
    # that path. Attaching the host's local timezone directly is deterministic
    # and does not perform the unsupported timestamp conversion.
    local_timezone = datetime.now().astimezone().tzinfo
    return value.replace(tzinfo=local_timezone)


def scan_record_directories(root: Path, target_date: date | None = None) -> list[Path]:
    root = Path(root)
    try:
        if not root.is_dir():
            raise FileNotFoundError(f"记录根目录不存在或不可访问：{root}")
    except OSError as exc:
        raise FileNotFoundError(f"记录根目录无效或已断开：{root}") from exc
    records: list[tuple[datetime, Path]] = []
    try:
        for candidate in root.iterdir():
            if not candidate.is_dir() or not RECORD_NAME.fullmatch(candidate.name):
                continue
            captured_at = parse_record_datetime(candidate.name)
            if target_date is not None and captured_at.date() != target_date:
                continue
            records.append((captured_at, candidate))
    except OSError as exc:
        raise FileNotFoundError(f"无法读取记录根目录（设备可能已断开）：{root}") from exc
    return [path for _captured_at, path in sorted(records, key=lambda item: (item[0], item[1].name))]


def normalize_source_root(value: str) -> Path:
    """Normalize a path pasted from Windows Explorer without resolving a missing drive."""
    raw = value.strip().strip('"').strip("'").strip()
    if not raw:
        raise ValueError("请先选择记录根目录")
    if "\x00" in raw:
        raise ValueError("记录根目录包含无效字符，请重新选择目录")
    if len(raw) == 2 and raw[0].isalpha() and raw[1] == ":":
        raw += "\\"
    root = Path(raw).expanduser()
    try:
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"记录根目录不存在或设备已断开：{root}")
    except OSError as exc:
        raise ValueError(f"记录根目录无效：{root}") from exc
    return root


def available_record_dates(root: Path) -> list[date]:
    return sorted({parse_record_datetime(path.name).date() for path in scan_record_directories(root)}, reverse=True)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_verified_with_status(source: Path, destination: Path) -> tuple[FileDigest, bool]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected_size = source.stat().st_size
    if destination.is_file() and destination.stat().st_size == expected_size:
        existing_hash = sha256_file(destination)
        if existing_hash == sha256_file(source):
            return FileDigest(destination.name, expected_size, existing_hash), False

    partial = destination.with_name(destination.name + ".part")
    partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    with source.open("rb") as src, partial.open("wb") as dst:
        while chunk := src.read(1024 * 1024):
            dst.write(chunk)
            digest.update(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    if partial.stat().st_size != expected_size:
        partial.unlink(missing_ok=True)
        raise ImportVerificationError(f"size mismatch while copying {source}")
    source_hash = digest.hexdigest()
    partial.replace(destination)
    destination_hash = sha256_file(destination)
    if destination_hash != source_hash:
        destination.unlink(missing_ok=True)
        raise ImportVerificationError(f"hash mismatch while copying {source}")
    shutil.copystat(source, destination)
    return FileDigest(destination.name, expected_size, destination_hash), True


def _copy_verified(source: Path, destination: Path) -> FileDigest:
    digest, _copied = _copy_verified_with_status(source, destination)
    return digest


def _record_files(record_dir: Path) -> list[Path]:
    resolved_root = record_dir.resolve()
    files: list[Path] = []
    for path in record_dir.rglob("*"):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(resolved_root):
            raise ImportVerificationError(f"record contains an unsafe path: {path}")
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(record_dir).as_posix())


def sync_legacy_cache(
    source_root: Path,
    destination_root: Path,
    progress: ProgressCallback | None = None,
) -> CacheSyncResult:
    """Differentially mirror every verified device record into the local cache.

    The cache is organized by ISO calendar day. Existing files are reused only
    after size and SHA-256 match, so reconnecting every day copies only new or
    changed material while still detecting corruption.
    """

    source_root = Path(source_root).resolve()
    destination_root = Path(destination_root).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    manifest_path = destination_root / "device_sync_manifest.json"
    previous_files: dict[tuple[str, str], dict[str, object]] = {}
    if manifest_path.is_file():
        try:
            previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if previous_manifest.get("adapter") == "legacy_msc_v1_cache":
                for record in previous_manifest.get("records", []):
                    if not isinstance(record, dict):
                        continue
                    record_name = str(record.get("record_name", ""))
                    for item in record.get("files", []):
                        if isinstance(item, dict):
                            previous_files[(record_name, str(item.get("relative_path", "")))] = item
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            previous_files = {}
    selected = scan_record_directories(source_root)
    total_files = sum(len(_record_files(record_dir)) for record_dir in selected)
    completed_files = 0
    cached_records: list[CachedRecord] = []
    manifest_records: list[dict[str, object]] = []

    for source_dir in selected:
        captured_at = parse_record_datetime(source_dir.name)
        local_dir = destination_root / captured_at.date().isoformat() / source_dir.name
        was_new = not local_dir.is_dir()
        changed_files = 0
        digests: list[FileDigest] = []
        manifest_files: list[dict[str, object]] = []
        for source_file in _record_files(source_dir):
            relative = source_file.relative_to(source_dir)
            relative_text = relative.as_posix()
            destination = local_dir / relative
            source_stat = source_file.stat()
            previous = previous_files.get((source_dir.name, relative_text))
            try:
                can_reuse = bool(
                    previous
                    and int(previous.get("size", -1)) == source_stat.st_size
                    and int(previous.get("source_mtime_ns", -1)) == source_stat.st_mtime_ns
                    and destination.is_file()
                    and destination.stat().st_size == source_stat.st_size
                )
            except (TypeError, ValueError):
                can_reuse = False
            if can_reuse and sha256_file(destination) == str(previous.get("sha256", "")):
                digest = FileDigest(destination.name, source_stat.st_size, str(previous["sha256"]))
                copied = False
            else:
                digest, copied = _copy_verified_with_status(source_file, destination)
            normalized = FileDigest(relative.as_posix(), digest.size, digest.sha256)
            digests.append(normalized)
            manifest_files.append(
                {
                    **asdict(normalized),
                    "source_mtime_ns": source_stat.st_mtime_ns,
                }
            )
            changed_files += int(copied)
            completed_files += 1
            if progress:
                progress(
                    completed_files,
                    total_files,
                    f"正在同步 {source_dir.name}/{relative.as_posix()}",
                )

        cached = CachedRecord(
            record_name=source_dir.name,
            captured_at=captured_at,
            local_dir=local_dir,
            files=tuple(digests),
            was_new=was_new,
            changed_files=changed_files,
        )
        cached_records.append(cached)
        manifest_records.append(
            {
                "record_name": cached.record_name,
                "captured_at": cached.captured_at.isoformat(),
                "local_dir": str(cached.local_dir),
                "was_new": cached.was_new,
                "changed_files": cached.changed_files,
                "files": manifest_files,
            }
        )

    manifest = {
        "schema_version": 1,
        "adapter": "legacy_msc_v1_cache",
        "source_root": str(source_root),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "records": manifest_records,
    }
    temporary = manifest_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    return CacheSyncResult(tuple(cached_records), manifest_path)


def import_legacy_day(
    source_root: Path,
    target_date: date,
    destination_root: Path,
    job_id: str,
    device_id: str = "legacy-device",
    progress: ProgressCallback | None = None,
) -> tuple[list[CaptureRecord], Path]:
    source_root = Path(source_root).resolve()
    destination_root = Path(destination_root).resolve()
    selected = scan_record_directories(source_root, target_date)
    day_dir = destination_root / target_date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    total_files = sum(len(_record_files(path)) for path in selected)
    completed_files = 0
    records: list[CaptureRecord] = []
    manifest_records: list[dict[str, object]] = []

    for source_dir in selected:
        local_dir = day_dir / source_dir.name
        digests: list[FileDigest] = []
        for source_file in _record_files(source_dir):
            relative = source_file.relative_to(source_dir)
            destination = local_dir / relative
            digest = _copy_verified(source_file, destination)
            digest = FileDigest(relative.as_posix(), digest.size, digest.sha256)
            digests.append(digest)
            completed_files += 1
            if progress:
                progress(completed_files, total_files, f"Copying {source_dir.name}/{relative.as_posix()}")

        captured_at = parse_record_datetime(source_dir.name)
        schema_version = 1
        meta_path = local_dir / "meta.json"
        if meta_path.is_file():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                schema_version = int(metadata.get("schema_version", 1))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                schema_version = 1
        # A physical recording can be distilled more than once. Record rows are
        # job-owned, so include the job id while keeping retries of the same job
        # deterministic.
        record_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"day-distiller:{job_id}:{device_id}:{source_dir.name}",
            )
        )
        record = CaptureRecord(
            record_id=record_id,
            record_name=source_dir.name,
            captured_at=captured_at,
            source_dir=source_dir,
            local_dir=local_dir,
            video_path=(local_dir / "video.avi") if (local_dir / "video.avi").is_file() else None,
            audio_path=(local_dir / "audio.wav") if (local_dir / "audio.wav").is_file() else None,
            imu_path=(local_dir / "imu.json") if (local_dir / "imu.json").is_file() else None,
            meta_path=meta_path if meta_path.is_file() else None,
            schema_version=schema_version,
            files=digests,
        )
        records.append(record)
        manifest_records.append(
            {
                "record_id": record.record_id,
                "record_name": record.record_name,
                "captured_at": record.captured_at.isoformat(),
                "files": [asdict(item) for item in digests],
            }
        )

    manifest = {
        "schema_version": 1,
        "adapter": "legacy_msc_v1",
        "job_id": job_id,
        "device_id": device_id,
        "target_date": target_date.isoformat(),
        "source_root": str(source_root),
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "records": manifest_records,
    }
    # Never overwrite another job's cleanup proof for the same calendar day.
    manifest_path = day_dir / f"import_manifest_{job_id}.json"
    temporary = manifest_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    return records, manifest_path


def load_manifest(path: Path) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("adapter") != "legacy_msc_v1" or not isinstance(value.get("records"), list):
        raise ImportVerificationError("not a legacy import manifest")
    return value


def verify_manifest_source(source_root: Path, manifest_path: Path) -> list[Path]:
    source_root = Path(source_root).resolve()
    manifest = load_manifest(manifest_path)
    verified: list[Path] = []
    for record in manifest["records"]:
        if not isinstance(record, dict):
            raise ImportVerificationError("invalid record entry in manifest")
        record_name = str(record.get("record_name", ""))
        if not RECORD_NAME.fullmatch(record_name):
            raise ImportVerificationError(f"unsafe record name in manifest: {record_name}")
        record_dir = (source_root / record_name).resolve()
        if not record_dir.is_relative_to(source_root) or not record_dir.is_dir():
            raise ImportVerificationError(f"record no longer exists: {record_name}")
        expected_files = record.get("files")
        if not isinstance(expected_files, list):
            raise ImportVerificationError(f"missing file list: {record_name}")
        actual_relative = {path.relative_to(record_dir).as_posix() for path in _record_files(record_dir)}
        declared_relative = {str(item.get("relative_path", "")) for item in expected_files if isinstance(item, dict)}
        if actual_relative != declared_relative:
            raise ImportVerificationError(f"file set changed after import: {record_name}")
        for item in expected_files:
            if not isinstance(item, dict):
                raise ImportVerificationError(f"invalid digest entry: {record_name}")
            relative = Path(str(item["relative_path"]))
            source_file = (record_dir / relative).resolve()
            if not source_file.is_relative_to(record_dir) or not source_file.is_file():
                raise ImportVerificationError(f"unsafe or missing source file: {record_name}/{relative}")
            if source_file.stat().st_size != int(item["size"]):
                raise ImportVerificationError(f"source size changed: {record_name}/{relative}")
            if sha256_file(source_file) != item["sha256"]:
                raise ImportVerificationError(f"source hash changed: {record_name}/{relative}")
        verified.append(record_dir)
    return verified


def delete_verified_source_records(source_root: Path, manifest_path: Path) -> list[str]:
    source_root = Path(source_root).resolve()
    verified = verify_manifest_source(source_root, manifest_path)
    deleted: list[str] = []
    for record_dir in verified:
        resolved = record_dir.resolve()
        if not resolved.is_relative_to(source_root) or resolved.parent != source_root:
            raise ImportVerificationError(f"refusing recursive delete outside source root: {resolved}")
        shutil.rmtree(resolved)
        deleted.append(resolved.name)
    return deleted
