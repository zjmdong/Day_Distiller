from __future__ import annotations

import subprocess
import time
import platform
import plistlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .subprocess_utils import hidden_window_options


@dataclass(frozen=True)
class DriveInfo:
    letter: str
    label: str
    filesystem: str
    size: int | None
    free: int | None

    @property
    def root(self) -> str:
        if platform.system() == "Windows":
            return f"{self.letter}:\\"
        return self.letter


def _wmi_service():
    import win32com.client

    return win32com.client.GetObject("winmgmts:")


def list_removable_drives() -> list[DriveInfo]:
    if platform.system() == "Darwin":
        return _list_macos_removable_drives()
    if platform.system() != "Windows":
        return []
    try:
        svc = _wmi_service()
        drives = []
        for disk in svc.ExecQuery("SELECT DeviceID, VolumeName, FileSystem, Size, FreeSpace FROM Win32_LogicalDisk WHERE DriveType=2"):
            letter = str(disk.DeviceID).rstrip(":").upper()
            drives.append(
                DriveInfo(
                    letter=letter,
                    label=str(disk.VolumeName or ""),
                    filesystem=str(disk.FileSystem or ""),
                    size=int(disk.Size) if disk.Size is not None else None,
                    free=int(disk.FreeSpace) if disk.FreeSpace is not None else None,
                )
            )
        return sorted(drives, key=lambda drive: drive.letter)
    except Exception:
        return []


def _list_macos_removable_drives() -> list[DriveInfo]:
    drives: list[DriveInfo] = []
    volumes = Path("/Volumes")
    if not volumes.is_dir():
        return drives
    for mount in volumes.iterdir():
        try:
            result = subprocess.run(
                ["diskutil", "info", "-plist", str(mount)],
                capture_output=True,
                check=False,
                timeout=4,
            )
            if result.returncode != 0:
                continue
            info = plistlib.loads(result.stdout)
            if not bool(info.get("RemovableMedia") or info.get("Ejectable") or info.get("Internal") is False):
                continue
            drives.append(
                DriveInfo(
                    letter=str(mount),
                    label=str(info.get("VolumeName") or mount.name),
                    filesystem=str(info.get("FilesystemName") or info.get("FilesystemType") or ""),
                    size=int(info["TotalSize"]) if info.get("TotalSize") is not None else None,
                    free=int(info["FreeSpace"]) if info.get("FreeSpace") is not None else None,
                )
            )
        except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError):
            continue
    return sorted(drives, key=lambda drive: drive.letter)


def drive_letters(drives: list[DriveInfo] | None = None) -> set[str]:
    return {drive.letter for drive in (drives if drives is not None else list_removable_drives())}


def wait_for_new_drive(before: set[str], timeout: float = 20.0) -> DriveInfo | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        drives = list_removable_drives()
        for drive in drives:
            if drive.letter not in before:
                return drive
        time.sleep(0.5)
    return None


def wait_for_ready_new_drive(before: set[str], timeout: float = 30.0) -> DriveInfo | None:
    """Wait until a newly assigned removable drive is readable and stable.

    Windows can publish the drive letter a little before the filesystem is
    ready. Requiring two matching directory snapshots prevents the importer
    from racing that mount transition.
    """

    deadline = time.monotonic() + timeout
    previous: dict[str, tuple[tuple[object, ...], int]] = {}
    while time.monotonic() < deadline:
        for drive in list_removable_drives():
            if drive.letter in before:
                continue
            try:
                root = Path(drive.root)
                names = tuple(sorted(item.name for item in root.iterdir()))
                signature: tuple[object, ...] = (
                    drive.label,
                    drive.filesystem,
                    drive.size,
                    names,
                )
            except OSError:
                continue
            old_signature, count = previous.get(drive.letter, ((), 0))
            count = count + 1 if old_signature == signature else 1
            previous[drive.letter] = (signature, count)
            if count >= 2:
                return drive
        time.sleep(0.4)
    return None


def close_explorer_windows_for_drive(letter: str) -> int:
    """Close AutoPlay Explorer windows that point at the device volume."""

    if platform.system() != "Windows":
        return 0

    normalized = letter.rstrip(":\\").upper()
    if len(normalized) != 1:
        return 0
    closed = 0
    try:
        import win32com.client

        shell = win32com.client.Dispatch("Shell.Application")
        for window in list(shell.Windows()):
            try:
                location_url = str(getattr(window, "LocationURL", "") or "")
                parsed = urlparse(location_url)
                location_path = unquote(parsed.path).lstrip("/").replace("/", "\\")
                if parsed.scheme.lower() != "file" or not location_path.upper().startswith(f"{normalized}:\\"):
                    continue
                window.Quit()
                closed += 1
            except Exception:
                continue
    except Exception:
        return closed
    return closed


def _wait_for_drive_removal(letter: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if letter not in drive_letters():
            return True
        time.sleep(0.25)
    return letter not in drive_letters()


def safe_eject(letter: str) -> None:
    if platform.system() == "Darwin":
        mount = str(Path(letter))
        completed = subprocess.run(
            ["diskutil", "eject", mount],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"failed to eject {mount}: {detail}")
        return
    if platform.system() != "Windows":
        raise RuntimeError("safe eject is supported only on Windows and macOS")
    letter = letter.rstrip(":\\").upper()
    if not letter or len(letter) != 1:
        raise ValueError("drive letter must be a single letter")

    if close_explorer_windows_for_drive(letter):
        time.sleep(0.25)

    try:
        import win32com.client

        shell = win32com.client.Dispatch("Shell.Application")
        item = shell.Namespace(17).ParseName(f"{letter}:\\")
        if item is None:
            raise RuntimeError(f"drive {letter}: not found")
        for verb in item.Verbs():
            name = str(verb.Name).replace("&", "").lower()
            if "eject" in name or "弹出" in name:
                verb.DoIt()
                _wait_for_drive_removal(letter, timeout=2.0)
                return
        item.InvokeVerb("Eject")
        _wait_for_drive_removal(letter, timeout=2.0)
        return
    except Exception as exc:
        script = (
            "$shell = New-Object -ComObject Shell.Application; "
            f"$item = $shell.NameSpace(17).ParseName('{letter}:\\'); "
            "if ($null -eq $item) { exit 2 }; "
            "$item.InvokeVerb('Eject')"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            **hidden_window_options(),
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or str(exc)
            raise RuntimeError(f"failed to eject {letter}: {detail}") from exc
