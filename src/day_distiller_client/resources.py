from __future__ import annotations

import sys
import shutil
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Resolve files in source checkouts and PyInstaller one-folder builds."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root).joinpath(*parts)
    return Path(__file__).resolve().parents[2].joinpath(*parts)


def bundled_ffmpeg_paths() -> tuple[Path, Path]:
    root = resource_path("resources", "ffmpeg")
    ffmpeg = root / "ffmpeg.exe"
    ffprobe = root / "ffprobe.exe"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        system_ffmpeg = shutil.which("ffmpeg")
        system_ffprobe = shutil.which("ffprobe")
        if system_ffmpeg and system_ffprobe and not getattr(sys, "frozen", False):
            return Path(system_ffmpeg), Path(system_ffprobe)
        raise FileNotFoundError("Bundled FFmpeg is missing. Reinstall the application.")
    return ffmpeg, ffprobe
