from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .domain import CaptureRecord


class MediaProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaProbe:
    path: Path
    duration_s: float
    format_name: str
    streams: list[dict[str, Any]]


@dataclass
class PreprocessedMedia:
    record_id: str
    frames: list[Path] = field(default_factory=list)
    video_probe: MediaProbe | None = None
    audio_probe: MediaProbe | None = None
    warnings: list[str] = field(default_factory=list)


def resolve_binary(name: str, configured: str | Path | None = None) -> Path:
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        raise FileNotFoundError(candidate)
    environment_name = f"DAY_DISTILLER_{name.upper()}"
    if os.environ.get(environment_name):
        return resolve_binary(name, os.environ[environment_name])
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} was not found in PATH or {environment_name}")
    return Path(found).resolve()


class MediaPreprocessor:
    def __init__(self, ffmpeg: str | Path | None = None, ffprobe: str | Path | None = None) -> None:
        self.ffmpeg = resolve_binary("ffmpeg", ffmpeg)
        self.ffprobe = resolve_binary("ffprobe", ffprobe)

    def probe(self, path: Path) -> MediaProbe:
        completed = subprocess.run(
            [
                str(self.ffprobe),
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise MediaProcessingError(f"ffprobe failed for {path.name}: {detail}")
        try:
            value = json.loads(completed.stdout)
            info = value.get("format", {})
            duration = float(info.get("duration", 0.0))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise MediaProcessingError(f"invalid ffprobe response for {path.name}") from exc
        if duration <= 0:
            raise MediaProcessingError(f"media has no positive duration: {path.name}")
        return MediaProbe(
            path=Path(path),
            duration_s=duration,
            format_name=str(info.get("format_name", "")),
            streams=list(value.get("streams", [])),
        )

    def preprocess_record(self, record: CaptureRecord, output_root: Path) -> PreprocessedMedia:
        result = PreprocessedMedia(record_id=record.record_id)
        output_dir = Path(output_root) / record.record_id
        output_dir.mkdir(parents=True, exist_ok=True)
        if record.video_path and record.video_path.is_file():
            result.video_probe = self.probe(record.video_path)
            result.frames = self.extract_keyframes(record.video_path, result.video_probe.duration_s, output_dir)
        else:
            result.warnings.append("video missing")
        if record.audio_path and record.audio_path.is_file():
            result.audio_probe = self.probe(record.audio_path)
        else:
            result.warnings.append("audio missing")
        return result

    def extract_keyframes(self, video_path: Path, duration_s: float, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        requested = (0.5, 2.5, 4.5)
        timestamps = sorted({max(0.0, min(duration_s - 0.05, value)) for value in requested if duration_s > 0.05})
        baseline: list[Path] = []
        for index, timestamp in enumerate(timestamps, start=1):
            destination = output_dir / f"frame_{index:02d}.jpg"
            self._run_ffmpeg(
                [
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale='min(1280,iw)':-2",
                    "-q:v",
                    "2",
                    "-y",
                    str(destination),
                ]
            )
            if destination.is_file() and destination.stat().st_size:
                baseline.append(destination)

        scene_pattern = output_dir / "scene_%02d.jpg"
        try:
            self._run_ffmpeg(
                [
                    "-i",
                    str(video_path),
                    "-vf",
                    "select='gt(scene,0.30)',scale='min(1280,iw)':-2",
                    "-fps_mode",
                    "vfr",
                    "-frames:v",
                    "3",
                    "-q:v",
                    "2",
                    "-y",
                    str(scene_pattern),
                ]
            )
        except MediaProcessingError:
            pass
        candidates = baseline + sorted(output_dir.glob("scene_*.jpg"))
        return _deduplicate_images(candidates, limit=6)

    def _run_ffmpeg(self, arguments: list[str]) -> None:
        completed = subprocess.run(
            [str(self.ffmpeg), "-hide_banner", "-loglevel", "error", *arguments],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise MediaProcessingError(f"ffmpeg failed: {detail}")


def image_average_hash(path: Path) -> int:
    from PIL import Image

    with Image.open(path) as image:
        pixels = list(image.convert("L").resize((8, 8)).tobytes())
    average = sum(pixels) / len(pixels)
    value = 0
    for pixel in pixels:
        value = (value << 1) | int(pixel >= average)
    return value


def image_sharpness(path: Path) -> float:
    from PIL import Image, ImageFilter, ImageStat

    with Image.open(path) as image:
        edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
        return float(ImageStat.Stat(edges).var[0])


def _deduplicate_images(paths: list[Path], limit: int) -> list[Path]:
    selected: list[tuple[Path, int, float]] = []
    for path in paths:
        try:
            fingerprint = image_average_hash(path)
            sharpness = image_sharpness(path)
        except Exception:
            continue
        duplicate_index = next(
            (
                index
                for index, (_existing, existing_hash, _quality) in enumerate(selected)
                if (fingerprint ^ existing_hash).bit_count() <= 4
            ),
            None,
        )
        if duplicate_index is None:
            selected.append((path, fingerprint, sharpness))
        elif sharpness > selected[duplicate_index][2]:
            selected[duplicate_index] = (path, fingerprint, sharpness)
    return [path for path, _hash, _quality in selected[:limit]]
