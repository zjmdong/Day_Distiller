from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from .device import PortCandidate, UsbLinkDevice, find_device
from .export_adapter import MaintenanceKeepAlive
from .legacy_import import delete_verified_source_records
from .pipeline import DistillationPipeline, PipelineResult
from .windows import drive_letters, safe_eject, wait_for_new_drive


StatusCallback = Callable[[str], None]


@dataclass(frozen=True)
class SyncedDay:
    """A verified local copy that is ready for the cloud distillation stages."""

    job_id: str
    record_names: tuple[str, ...]


class LegacyDeviceWorkflow:
    """End-to-end protocol-v1 workflow with verified, post-delivery deletion."""

    def __init__(
        self,
        pipeline: DistillationPipeline,
        status: StatusCallback | None = None,
        discovery_timeout: float = 35.0,
    ) -> None:
        self.pipeline = pipeline
        self.status = status
        self.discovery_timeout = discovery_timeout
        self._keepalive: MaintenanceKeepAlive | None = None

    def run_day(
        self,
        target_date: date,
        device_id: str = "legacy-device",
        provider_mode: str = "mock",
        avatar_references: list[Path] | None = None,
    ) -> PipelineResult:
        synced = self.sync_day(target_date, device_id=device_id, provider_mode=provider_mode)
        return self.process_synced(synced.job_id, avatar_references=avatar_references)

    def sync_day(
        self,
        target_date: date,
        device_id: str = "legacy-device",
        provider_mode: str = "mock",
    ) -> SyncedDay:
        """Mount read-only, import and verify one day, then safely leave MSC.

        This deliberately stops before AI processing so the production UI can
        show the user exactly what was copied and offer a short cancel/resync
        window before billable providers are called.
        """
        self._emit("正在发现设备并执行 HELLO / GET_STATUS")
        port, hello = self._wait_for_device()
        capabilities = set(hello.get("capabilities", [])) if isinstance(hello.get("capabilities"), list) else set()
        adapter = "transactional_export_v2" if "transactional_export_v2" in capabilities else "legacy_msc_v1"
        if adapter != "legacy_msc_v1":
            self._emit("检测到 v2 能力；当前版本仍使用兼容的 legacy_msc_v1 路径")

        before = drive_letters()
        self._emit("以只读模式挂载 TF 卡")
        with UsbLinkDevice(port.device) as device:
            device.get_status()
            device.enter_msc("ro")
        drive = wait_for_new_drive(before, timeout=30)
        if drive is None:
            raise RuntimeError("设备已进入 MSC，但 Windows 未分配新的盘符")
        source_root = Path(drive.root)
        try:
            job_id = self.pipeline.import_legacy(source_root, target_date, device_id, provider_mode)
        finally:
            self._emit(f"正在安全弹出只读卷 {drive.root}")
            safe_eject(drive.letter)
            self._exit_msc()

        record_names = tuple(
            str(row["record_name"]) for row in self.pipeline.database.list_records(job_id)
        )
        return SyncedDay(job_id=job_id, record_names=record_names)

    def process_synced(
        self,
        job_id: str,
        avatar_references: list[Path] | None = None,
    ) -> PipelineResult:
        """Run analysis, generation, delivery and verified device cleanup."""
        serial_port, _status = self._wait_for_device()
        self._keepalive = MaintenanceKeepAlive(lambda: UsbLinkDevice(serial_port.device), interval_seconds=30)
        self._keepalive.start()
        try:
            return self.pipeline.process(
                job_id,
                avatar_references=avatar_references,
                cleanup_handler=self._cleanup_after_delivery,
            )
        finally:
            if self._keepalive:
                self._keepalive.stop()
                self._keepalive = None

    def retry_pending_cleanup(self, job_id: str) -> list[str]:
        job = self.pipeline.database.get_job(job_id)
        if not job.manifest_path:
            raise RuntimeError("任务缺少导入清单")
        deleted: list[str] = []

        def cleanup(_job_id: str, manifest: Path) -> None:
            nonlocal deleted
            deleted = self._mount_rw_delete_and_exit(manifest)

        cleanup(job_id, Path(job.manifest_path))
        self.pipeline.database.set_cleanup_state(job_id, "completed")
        return deleted

    def _cleanup_after_delivery(self, _job_id: str, manifest_path: Path) -> None:
        if self._keepalive:
            self._keepalive.stop()
        self._mount_rw_delete_and_exit(manifest_path)

    def _mount_rw_delete_and_exit(self, manifest_path: Path) -> list[str]:
        port, _status = self._wait_for_device()
        before = drive_letters()
        self._emit("邮件已被服务器接受；正在以读写模式重新挂载以执行精确清理")
        with UsbLinkDevice(port.device) as device:
            device.get_status()
            device.enter_msc("rw")
        drive = wait_for_new_drive(before, timeout=30)
        if drive is None:
            raise RuntimeError("清理阶段未检测到设备盘符")
        try:
            deleted = delete_verified_source_records(Path(drive.root), manifest_path)
            self._emit(f"已按清单删除 {len(deleted)} 个设备记录目录")
            return deleted
        finally:
            safe_eject(drive.letter)
            self._exit_msc()

    def _exit_msc(self) -> None:
        port, _status = self._wait_for_device()
        with UsbLinkDevice(port.device) as device:
            device.exit_msc(force=False)

    def _wait_for_device(self) -> tuple[PortCandidate, dict[str, object]]:
        deadline = time.monotonic() + self.discovery_timeout
        while time.monotonic() < deadline:
            found = find_device(timeout_per_port=0.7)
            if found:
                return found
            time.sleep(0.75)
        raise RuntimeError("在等待时间内没有找到 Day Distiller 协议串口")

    def _emit(self, message: str) -> None:
        if self.status:
            self.status(message)
