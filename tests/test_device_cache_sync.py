from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from day_distiller_client.device_workflow import (
    LegacyDeviceWorkflow,
    SyncInventory,
    SyncedDate,
)
from day_distiller_client.legacy_import import sync_legacy_cache


def _record(root: Path, name: str, payload: bytes) -> None:
    record = root / name
    record.mkdir(parents=True)
    (record / "video.avi").write_bytes(payload)
    (record / "imu.json").write_text("[]", encoding="utf-8")


def test_cache_sync_is_multi_day_verified_and_differential(tmp_path: Path) -> None:
    device = tmp_path / "device"
    cache = tmp_path / "cache"
    device.mkdir()
    _record(device, "REC_0001_260716_080000", b"day-one")
    _record(device, "REC_0002_260717_090000", b"day-two")
    updates: list[tuple[int, int, str]] = []

    first = sync_legacy_cache(
        device,
        cache,
        progress=lambda done, total, message: updates.append((done, total, message)),
    )
    assert first.changed_record_count == 2
    assert len(first.records) == 2
    assert updates[-1][0] == updates[-1][1] == 4
    assert json.loads(first.manifest_path.read_text(encoding="utf-8"))["records"]

    second = sync_legacy_cache(device, cache)
    assert second.changed_record_count == 0
    assert all(not record.was_new and record.changed_files == 0 for record in second.records)

    (device / "REC_0002_260717_090000" / "video.avi").write_bytes(b"day-two-updated")
    third = sync_legacy_cache(device, cache)
    assert third.changed_record_count == 1
    assert (
        cache / "2026-07-17" / "REC_0002_260717_090000" / "video.avi"
    ).read_bytes() == b"day-two-updated"


class _PreparingPipeline:
    def __init__(self, cache_root: Path) -> None:
        self.calls: list[tuple[Path, date, str]] = []

        class _Paths:
            imports = cache_root

        self.paths = _Paths()

    def import_legacy(
        self,
        source_root: Path,
        target_date: date,
        _device_id: str,
        provider_mode: str,
    ) -> str:
        self.calls.append((source_root, target_date, provider_mode))
        return f"job-{target_date.isoformat()}"


def test_prepare_days_creates_jobs_only_for_selected_dates(tmp_path: Path) -> None:
    first = date(2026, 7, 16)
    second = date(2026, 7, 17)
    inventory = SyncInventory(
        tmp_path,
        (
            SyncedDate(second, ("REC_0002_260717_090000",), ("REC_0002_260717_090000",)),
            SyncedDate(first, ("REC_0001_260716_080000",), ()),
        ),
    )
    pipeline = _PreparingPipeline(tmp_path)
    workflow = LegacyDeviceWorkflow(pipeline)  # type: ignore[arg-type]

    prepared = workflow.prepare_days(
        inventory,
        (first, second, first),
        provider_mode="mainland",
    )

    assert [item.target_date for item in prepared] == [first, second]
    assert [call[1] for call in pipeline.calls] == [first, second]
    assert all(call[2] == "mainland" for call in pipeline.calls)
