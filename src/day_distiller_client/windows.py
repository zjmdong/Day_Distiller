from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class DriveInfo:
    letter: str
    label: str
    filesystem: str
    size: int | None
    free: int | None

    @property
    def root(self) -> str:
        return f"{self.letter}:\\"


def _wmi_service():
    import win32com.client

    return win32com.client.GetObject("winmgmts:")


def list_removable_drives() -> list[DriveInfo]:
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


def safe_eject(letter: str) -> None:
    letter = letter.rstrip(":\\").upper()
    if not letter or len(letter) != 1:
        raise ValueError("drive letter must be a single letter")

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
                return
        item.InvokeVerb("Eject")
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
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or str(exc)
            raise RuntimeError(f"failed to eject {letter}: {detail}") from exc
