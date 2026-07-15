from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict
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


def parse_record_datetime(name: str) -> datetime:
    match = RECORD_NAME.fullmatch(name)
    if not match:
        raise ValueError(f"invalid legacy record directory: {name}")
    value = datetime.strptime(match.group("date") + match.group("time"), "%y%m%d%H%M%S")
    return value.astimezone()


def scan_record_directories(root: Path, target_date: date | None = None) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    records: list[tuple[datetime, Path]] = []
    for candidate in root.iterdir():
        if not candidate.is_dir() or not RECORD_NAME.fullmatch(candidate.name):
            continue
        captured_at = parse_record_datetime(candidate.name)
        if target_date is not None and captured_at.date() != target_date:
            continue
        records.append((captured_at, candidate))
    return [path for _captured_at, path in sorted(records, key=lambda item: (item[0], item[1].name))]


def available_record_dates(root: Path) -> list[date]:
    return sorted({parse_record_datetime(path.name).date() for path in scan_record_directories(root)}, reverse=True)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_verified(source: Path, destination: Path) -> FileDigest:
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected_size = source.stat().st_size
    if destination.is_file() and destination.stat().st_size == expected_size:
        existing_hash = sha256_file(destination)
        if existing_hash == sha256_file(source):
            return FileDigest(destination.name, expected_size, existing_hash)

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
    return FileDigest(destination.name, expected_size, destination_hash)


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
        record_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"day-distiller:{device_id}:{source_dir.name}"))
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
    manifest_path = day_dir / "import_manifest.json"
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

