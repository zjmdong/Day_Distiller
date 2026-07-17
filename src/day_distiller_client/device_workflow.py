from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

from .device import DeviceProfile, PortCandidate, UsbLinkDevice, device_profile, find_device
from .export_adapter import MaintenanceKeepAlive, select_export_adapter
from .legacy_import import CachedRecord, delete_verified_source_records, sync_legacy_cache
from .pipeline import DistillationPipeline, PipelineResult
from .protocol import ProtocolError
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
class ExportTransaction:
    export_id: str
    target_date: date
    manifest_sha256: str
    record_count: int
    manifest_path: str
    state: str = "prepared"


@dataclass(frozen=True)
class SyncInventory:
    """Verified local cache contents discovered during one device mount."""

    cache_root: Path
    days: tuple[SyncedDate, ...]
    adapter_name: str = "legacy_msc_v1"
    device_profile: DeviceProfile | None = None
    exports: tuple[ExportTransaction, ...] = ()

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
        self.adapter_name = "legacy_msc_v1"
        self.connected_profile: DeviceProfile | None = None

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
        profile = device_profile(hello, port)
        self.connected_profile = profile
        self.adapter_name = select_export_adapter(profile).adapter_name
        transactions: tuple[ExportTransaction, ...] = ()
        export_ids: tuple[str, ...] = ()
        if profile.is_firmware_v2:
            self._emit(
                f"检测到固件 {profile.display_firmware}，启用 transactional_export_v2"
            )
            with UsbLinkDevice(port.device, timeout=1.2) as device:
                summaries = self._list_v2_record_dates(device)
                if len(summaries) > 32:
                    self._emit("设备记录超过 32 个日期，本次安全回退 legacy_msc_v1")
                    self.adapter_name = "legacy_msc_v1"
                else:
                    transactions = tuple(
                        self._prepare_v2_export(device, profile, item)
                        for item in summaries
                    )
                    export_ids = tuple(item.export_id for item in transactions)

        before = drive_letters()
        self._emit("以只读模式挂载 TF 卡")
        self._enter_msc_when_ready(port.device, "ro", export_ids=export_ids)
        self._emit("等待 Windows 完成设备卷挂载")
        drive = wait_for_ready_new_drive(before, timeout=30)
        if drive is None:
            try:
                self._exit_msc(next_mode="maintenance" if profile.is_firmware_v2 else None)
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
            inventory = SyncInventory(
                Path(cache_root).resolve(),
                days,
                adapter_name=self.adapter_name,
                device_profile=profile,
                exports=transactions,
            )
            self._emit(
                f"本地校验完成：共 {inventory.total_records} 条，"
                f"其中新增或更新 {inventory.changed_records} 条"
            )
        finally:
            self._eject_and_exit(
                drive.letter,
                drive.root,
                next_mode="maintenance" if profile.is_firmware_v2 else None,
            )
        return inventory

    def _list_v2_record_dates(self, device: UsbLinkDevice) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        cursor = 0
        while True:
            page = device.list_record_dates(cursor=cursor, limit=15)
            raw_items = page.get("items")
            if not isinstance(raw_items, list):
                raise RuntimeError("固件 LIST_RECORD_DATES 返回格式无效")
            for item in raw_items:
                if not isinstance(item, dict):
                    raise RuntimeError("固件日期列表包含无效条目")
                date.fromisoformat(str(item.get("date", "")))
                items.append(item)
            next_cursor = page.get("next_cursor")
            if next_cursor is None:
                return items
            cursor = int(next_cursor)

    def _active_v2_exports(self, device: UsbLinkDevice) -> dict[str, dict[str, object]]:
        active: dict[str, dict[str, object]] = {}
        cursor = 0
        while True:
            page = device.get_export_status(cursor=cursor, limit=8)
            raw_items = page.get("items")
            if not isinstance(raw_items, list):
                return active
            for item in raw_items:
                if isinstance(item, dict) and item.get("date"):
                    active[str(item["date"])] = item
            next_cursor = page.get("next_cursor")
            if next_cursor is None:
                return active
            cursor = int(next_cursor)

    def _prepare_v2_export(
        self,
        device: UsbLinkDevice,
        profile: DeviceProfile,
        summary: dict[str, object],
    ) -> ExportTransaction:
        target_date = date.fromisoformat(str(summary["date"]))
        fingerprint = (
            f"{profile.device_id}|{target_date.isoformat()}|"
            f"{int(summary.get('record_count', 0))}|{int(summary.get('total_bytes', 0))}"
        )
        request_id = "desktop-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:40]
        try:
            response = device.begin_export(target_date.isoformat(), request_id)
        except ProtocolError as exc:
            if "DATE_ALREADY_PREPARED" not in str(exc).upper():
                raise
            active = self._active_v2_exports(device).get(target_date.isoformat())
            if not active or not active.get("export_id"):
                raise
            response = device.get_export_status(str(active["export_id"]))
        transaction = ExportTransaction(
            export_id=str(response["export_id"]),
            target_date=target_date,
            manifest_sha256=str(response["manifest_sha256"]),
            record_count=int(response["record_count"]),
            manifest_path=str(response["manifest_path"]),
            state=str(response.get("state", "prepared")),
        )
        if transaction.state != "prepared":
            raise RuntimeError(
                f"日期 {target_date.isoformat()} 的导出事务状态不是 prepared：{transaction.state}"
            )
        return transaction

    def prepare_days(
        self,
        inventory: SyncInventory,
        target_dates: Iterable[date],
        device_id: str = "legacy-device",
        provider_mode: str = "mock",
    ) -> list[SyncedDay]:
        """Create recoverable jobs from already verified local cache days."""

        available = {item.target_date: item for item in inventory.days}
        exports = {item.target_date: item for item in inventory.exports}
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
                inventory.device_profile.device_id
                if inventory.device_profile
                else device_id,
                provider_mode,
            )
            transaction = exports.get(target_date)
            if transaction:
                self._attach_export_transaction(job_id, transaction)
            prepared.append(SyncedDay(job_id, day.record_names, target_date))
        return prepared

    def _attach_export_transaction(
        self,
        job_id: str,
        transaction: ExportTransaction,
    ) -> None:
        job = self.pipeline.database.get_job(job_id)
        if not job.manifest_path:
            raise RuntimeError("任务缺少本地导入清单")
        manifest_path = Path(job.manifest_path)
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        value["adapter"] = "transactional_export_v2"
        transaction_value = asdict(transaction)
        transaction_value["target_date"] = transaction.target_date.isoformat()
        value["device_export"] = transaction_value
        temporary = manifest_path.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(manifest_path)

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
        manifest_path = Path(job.manifest_path)
        transaction = self._load_export_transaction(manifest_path)
        if transaction:
            deleted = self._commit_export_transaction(transaction)
            self.pipeline.database.set_cleanup_state(job_id, "completed")
            return deleted
        deleted: list[str] = []

        def cleanup(_job_id: str, manifest: Path) -> None:
            nonlocal deleted
            deleted = self._mount_rw_delete_and_exit(manifest)

        cleanup(job_id, manifest_path)
        self.pipeline.database.set_cleanup_state(job_id, "completed")
        return deleted

    def _cleanup_after_delivery(self, _job_id: str, manifest_path: Path) -> None:
        if self._keepalive:
            self._keepalive.stop()
        transaction = self._load_export_transaction(manifest_path)
        if transaction:
            self._commit_export_transaction(transaction)
            return
        self._mount_rw_delete_and_exit(manifest_path)

    def _load_export_transaction(self, manifest_path: Path) -> ExportTransaction | None:
        try:
            value = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if value.get("adapter") != "transactional_export_v2":
            return None
        item = value.get("device_export")
        if not isinstance(item, dict):
            raise RuntimeError("事务化导入清单缺少 device_export")
        return ExportTransaction(
            export_id=str(item["export_id"]),
            target_date=date.fromisoformat(str(item["target_date"])),
            manifest_sha256=str(item["manifest_sha256"]),
            record_count=int(item["record_count"]),
            manifest_path=str(item["manifest_path"]),
            state=str(item.get("state", "prepared")),
        )

    def _commit_export_transaction(self, transaction: ExportTransaction) -> list[str]:
        port, status = self._wait_for_device()
        profile = device_profile(status, port)
        if not profile.is_firmware_v2:
            raise RuntimeError("当前连接设备不支持 transactional_export_v2，已保留原始记录")
        self.adapter_name = profile.adapter_name
        self.connected_profile = profile
        self._emit(
            f"邮件已被服务器接受；正在提交 {transaction.target_date.isoformat()} 的设备清理事务"
        )
        with UsbLinkDevice(port.device, timeout=1.2) as device:
            result = device.commit_export_delete(
                transaction.export_id,
                transaction.manifest_sha256,
                transaction.record_count,
            )
        deleted_count = int(result.get("deleted_count", transaction.record_count))
        self._emit(f"设备已按固件清单删除 {deleted_count} 条记录")
        return [transaction.export_id] * deleted_count

    def release_unselected(
        self,
        inventory: SyncInventory,
        selected_dates: Iterable[date],
    ) -> None:
        selected = set(selected_dates)
        to_abort = [item for item in inventory.exports if item.target_date not in selected]
        if not to_abort:
            return
        port, status = self._wait_for_device()
        if not device_profile(status, port).is_firmware_v2:
            return
        with UsbLinkDevice(port.device, timeout=1.2) as device:
            for transaction in to_abort:
                device.abort_export(transaction.export_id)
                self._emit(f"已保留未选择日期 {transaction.target_date.isoformat()} 的设备记录")

    def end_session(self) -> None:
        port, status = self._wait_for_device()
        if not device_profile(status, port).is_firmware_v2:
            return
        with UsbLinkDevice(port.device, timeout=1.2) as device:
            device.end_session()

    def _mount_rw_delete_and_exit(self, manifest_path: Path) -> list[str]:
        port, status = self._wait_for_device()
        profile = device_profile(status, port)
        before = drive_letters()
        self._emit("邮件已被服务器接受；正在以读写模式重新挂载以执行精确清理")
        self._enter_msc_when_ready(port.device, "rw")
        drive = wait_for_ready_new_drive(before, timeout=30)
        if drive is None:
            try:
                self._exit_msc(next_mode="maintenance" if profile.is_firmware_v2 else None)
            finally:
                raise RuntimeError("清理阶段未检测到可读取的稳定设备盘符")
        try:
            close_explorer_windows_for_drive(drive.letter)
            deleted = delete_verified_source_records(Path(drive.root), manifest_path)
            self._emit(f"已按清单删除 {len(deleted)} 个设备记录目录")
            return deleted
        finally:
            self._eject_and_exit(
                drive.letter,
                drive.root,
                next_mode="maintenance" if profile.is_firmware_v2 else None,
            )

    def _eject_and_exit(
        self,
        letter: str,
        root: str,
        next_mode: str | None = None,
    ) -> None:
        errors: list[str] = []
        self._emit(f"数据读取已结束，正在安全弹出设备卷 {root}")
        try:
            safe_eject(letter)
        except Exception as exc:
            errors.append(f"安全弹出失败：{exc}")
        try:
            self._exit_msc(next_mode=next_mode)
        except Exception as exc:
            errors.append(f"退出 MSC 失败：{exc}")
        if errors:
            raise RuntimeError("；".join(errors))

    def _exit_msc(self, next_mode: str | None = None) -> None:
        port, _status = self._wait_for_device()
        with UsbLinkDevice(port.device) as device:
            device.exit_msc(force=False, next_mode=next_mode)

    def _enter_msc_when_ready(
        self,
        port_name: str,
        access: str,
        timeout: float = 20.0,
        export_ids: tuple[str, ...] = (),
    ) -> None:
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
                        device.enter_msc(access, export_ids=export_ids)
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
