from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Protocol

from .device import UsbLinkDevice
from .legacy_import import delete_verified_source_records, import_legacy_day
from .protocol import Command


class DeviceExportAdapter(Protocol):
    adapter_name: str

    def capabilities(self) -> set[str]: ...


class LegacyMscV1Adapter:
    adapter_name = "legacy_msc_v1"

    def capabilities(self) -> set[str]:
        return {"legacy_msc", "host_manifest", "verified_host_cleanup"}

    def import_day(self, *args, **kwargs):
        return import_legacy_day(*args, **kwargs)

    def cleanup(self, source_root: Path, manifest_path: Path) -> list[str]:
        return delete_verified_source_records(source_root, manifest_path)


class MaintenanceKeepAlive:
    def __init__(
        self,
        device_factory: Callable[[], UsbLinkDevice],
        interval_seconds: float = 30.0,
    ) -> None:
        self.device_factory = device_factory
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: Exception | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="usb-maintenance-ping", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(2.0, self.interval_seconds + 1.0))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                with self.device_factory() as device:
                    device.request(Command.PING)
            except Exception as exc:
                self.last_error = exc
