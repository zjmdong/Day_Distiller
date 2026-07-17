from __future__ import annotations

import sys
import shutil
import platform
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Resolve files in source checkouts, Nuitka bundles and legacy builds."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root).joinpath(*parts)
    candidates: list[Path] = []
    compiled = globals().get("__compiled__")
    containing_dir = getattr(compiled, "containing_dir", None)
    if containing_dir:
        candidates.append(Path(containing_dir))
    executable = Path(sys.executable).resolve()
    candidates.extend(
        (
            executable.parent,
            executable.parent.parent / "Resources",
            Path(__file__).resolve().parents[2],
        )
    )
    for root in candidates:
        candidate = root.joinpath(*parts)
        if candidate.exists():
            return candidate
    return candidates[0].joinpath(*parts) if candidates else Path(*parts)


def bundled_ffmpeg_paths() -> tuple[Path, Path]:
    root = resource_path("resources", "ffmpeg")
    suffix = ".exe" if platform.system() == "Windows" else ""
    ffmpeg = root / f"ffmpeg{suffix}"
    ffprobe = root / f"ffprobe{suffix}"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        system_ffmpeg = shutil.which("ffmpeg")
        system_ffprobe = shutil.which("ffprobe")
        if system_ffmpeg and system_ffprobe and not getattr(sys, "frozen", False):
            return Path(system_ffmpeg), Path(system_ffprobe)
        raise FileNotFoundError("内置 FFmpeg 缺失，请重新安装 Day Distiller。")
    return ffmpeg, ffprobe
