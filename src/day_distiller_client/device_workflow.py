from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

from .device import PortCandidate, UsbLinkDevice, find_device
from .export_adapter import MaintenanceKeepAlive
from .legacy_import import CachedRecord, delete_verified_source_records, sync_legacy_cache
from .pipeline import DistillationPipeline, PipelineResult
from .windows import (
    close_explorer_windows_for_drive,
    drive_letters,
    safe_eject,
    wait_for_ready_new_drive,
)


StatusCallback = Callable[[str], None]
SyncProgressCallback = Callable[[float, str], None]


@dataclass(frozen=True)
class SyncedDay:
    """A verified local copy that is ready for the cloud distillation stages."""

    job_id: str
    record_names: tuple[str, ...]
    target_date: date | None = None


@dataclass(frozen=True)
class SyncedDate:
    target_date: date
    record_names: tuple[str, ...]
    changed_record_names: tuple[str, ...]


@dataclass(frozen=True)
class SyncInventory:
    """Verified local cache contents discovered during one device mount."""

    cache_root: Path
    days: tuple[SyncedDate, ...]

    @property
    def total_records(self) -> int:
        return sum(len(day.record_names) for day in self.days)

    @property
    def changed_records(self) -> int:
        return sum(len(day.changed_record_names) for day in self.days)


class LegacyDeviceWorkflow:
    """End-to-end protocol-v1 workflow with verified, post-delivery deletion."""

    def __init__(
        self,
        pipeline: DistillationPipeline,
        status: StatusCallback | None = None,
        sync_progress: SyncProgressCallback | None = None,
        discovery_timeout: float = 35.0,
    ) -> None:
        self.pipeline = pipeline
        self.status = status
        self.sync_progress = sync_progress
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
        """Compatibility wrapper for callers that still request one day."""
        inventory = self.sync_all(self.pipeline.paths.imports, device_id=device_id)
        prepared = self.prepare_days(
            inventory,
            (target_date,),
            device_id=device_id,
            provider_mode=provider_mode,
        )
        return prepared[0]

    def sync_all(
        self,
        cache_root: Path,
        device_id: str = "legacy-device",
    ) -> SyncInventory:
        """Mount once, mirror every missing/changed record, verify, then eject."""

        self._emit("正在发现设备并执行 HELLO / GET_STATUS")
        port, hello = self._wait_for_device()
        capabilities = set(hello.get("capabilities", [])) if isinstance(hello.get("capabilities"), list) else set()
        adapter = "transactional_export_v2" if "transactional_export_v2" in capabilities else "legacy_msc_v1"
        if adapter != "legacy_msc_v1":
            self._emit("检测到 v2 能力；当前版本仍使用兼容的 legacy_msc_v1 路径")

        before = drive_letters()
        self._emit("以只读模式挂载 TF 卡")
        self._enter_msc_when_ready(port.device, "ro")
        self._emit("等待 Windows 完成设备卷挂载")
        drive = wait_for_ready_new_drive(before, timeout=30)
        if drive is None:
            try:
                self._exit_msc()
            finally:
                raise RuntimeError("设备已进入 MSC，但 Windows 未分配可读取的稳定盘符")
        source_root = Path(drive.root)
        try:
            close_explorer_windows_for_drive(drive.letter)
            self._emit("设备卷已就绪，正在扫描全部记录并与本地缓存比对")
            result = sync_legacy_cache(
                source_root,
                cache_root,
                progress=lambda done, total, message: self._sync_notify(done, total, message),
            )
            if not result.records:
                raise RuntimeError("设备中没有找到可同步的 REC_XXXX_YYMMDD_HHMMSS 记录")
            grouped: dict[date, list[CachedRecord]] = {}
            for record in result.records:
                grouped.setdefault(record.captured_at.date(), []).append(record)
            days = tuple(
                SyncedDate(
                    target_date=target_date,
                    record_names=tuple(record.record_name for record in records),
                    changed_record_names=tuple(
                        record.record_name
                        for record in records
                        if record.was_new or record.changed_files
                    ),
                )
                for target_date, records in sorted(grouped.items(), reverse=True)
            )
            inventory = SyncInventory(Path(cache_root).resolve(), days)
            self._emit(
                f"本地校验完成：共 {inventory.total_records} 条，"
                f"其中新增或更新 {inventory.changed_records} 条"
            )
        finally:
            self._eject_and_exit(drive.letter, drive.root)
        return inventory

    def prepare_days(
        self,
        inventory: SyncInventory,
        target_dates: Iterable[date],
        device_id: str = "legacy-device",
        provider_mode: str = "mock",
    ) -> list[SyncedDay]:
        """Create recoverable jobs from already verified local cache days."""

        available = {item.target_date: item for item in inventory.days}
        requested = list(dict.fromkeys(target_dates))
        if not requested:
            raise ValueError("请至少选择一个需要蒸馏的日期")
        prepared: list[SyncedDay] = []
        for target_date in requested:
            day = available.get(target_date)
            if day is None:
                raise ValueError(f"{target_date.isoformat()} 不在本次同步结果中")
            source_root = inventory.cache_root / target_date.isoformat()
            job_id = self.pipeline.import_legacy(
                source_root,
                target_date,
                device_id,
                provider_mode,
            )
            prepared.append(SyncedDay(job_id, day.record_names, target_date))
        return prepared

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
        self._enter_msc_when_ready(port.device, "rw")
        drive = wait_for_ready_new_drive(before, timeout=30)
        if drive is None:
            try:
                self._exit_msc()
            finally:
                raise RuntimeError("清理阶段未检测到可读取的稳定设备盘符")
        try:
            close_explorer_windows_for_drive(drive.letter)
            deleted = delete_verified_source_records(Path(drive.root), manifest_path)
            self._emit(f"已按清单删除 {len(deleted)} 个设备记录目录")
            return deleted
        finally:
            self._eject_and_exit(drive.letter, drive.root)

    def _eject_and_exit(self, letter: str, root: str) -> None:
        errors: list[str] = []
        self._emit(f"数据读取已结束，正在安全弹出设备卷 {root}")
        try:
            safe_eject(letter)
        except Exception as exc:
            errors.append(f"安全弹出失败：{exc}")
        try:
            self._exit_msc()
        except Exception as exc:
            errors.append(f"退出 MSC 失败：{exc}")
        if errors:
            raise RuntimeError("；".join(errors))

    def _exit_msc(self) -> None:
        port, _status = self._wait_for_device()
        with UsbLinkDevice(port.device) as device:
            device.exit_msc(force=False)

    def _enter_msc_when_ready(self, port_name: str, access: str, timeout: float = 20.0) -> None:
        """Retry the short firmware transition window reported as BUSY."""

        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with UsbLinkDevice(port_name) as device:
                    status = device.get_status()
                    if bool(status.get("recording")):
                        last_error = RuntimeError("设备正在完成当前录制片段")
                    else:
                        device.enter_msc(access)
                        return
            except Exception as exc:
                last_error = exc
                if "BUSY" not in str(exc).upper():
                    raise
                self._emit("设备正在完成上一次 USB 状态切换，稍候重试")
                try:
                    with UsbLinkDevice(port_name) as device:
                        device.exit_msc(force=False)
                except Exception:
                    pass
                time.sleep(1.25)
                found = find_device(timeout_per_port=0.7)
                if found:
                    port_name = found[0].device
                continue
            self._emit("设备正在完成当前录制片段，结束后将自动继续")
            time.sleep(0.75)
        raise RuntimeError(f"设备长时间处于 BUSY 状态，无法进入 MSC：{last_error}")

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

    def _sync_notify(self, done: int, total: int, message: str) -> None:
        if self.sync_progress:
            self.sync_progress(done / max(1, total), message)
        self._emit(message)
