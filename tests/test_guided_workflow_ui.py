from __future__ import annotations

import os
import tempfile
from datetime import date
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from day_distiller_client.app import MainWindow
from day_distiller_client.device import PortCandidate
from day_distiller_client.device_workflow import SyncInventory, SyncedDate


APPLICATION = QApplication.instance() or QApplication([])


def _window() -> tuple[MainWindow, tempfile.TemporaryDirectory[str]]:
    temporary = tempfile.TemporaryDirectory()
    environment = patch.dict(os.environ, {"DAY_DISTILLER_DATA_DIR": temporary.name})
    credentials = patch("day_distiller_client.app.CredentialStore")
    environment.start()
    credential_mock = credentials.start()
    credential_mock.return_value.get.return_value = None
    window = MainWindow()
    window._test_patches = (environment, credentials)  # type: ignore[attr-defined]
    return window, temporary


def _close(window: MainWindow, temporary: tempfile.TemporaryDirectory[str]) -> None:
    window.discovery_timer.stop()
    window.sync_countdown_timer.stop()
    window.header_device_timer.stop()
    window.timer.stop()
    window.window.close()
    environment, credentials = window._test_patches  # type: ignore[attr-defined]
    credentials.stop()
    environment.stop()
    temporary.cleanup()


def test_multi_day_sync_requires_explicit_selection_and_supports_select_all() -> None:
    window, temporary = _window()
    try:
        inventory = SyncInventory(
            window.paths.imports,
            (
                SyncedDate(date(2026, 7, 17), ("REC_0002_260717_090000",), ()),
                SyncedDate(
                    date(2026, 7, 16),
                    ("REC_0001_260716_080000",),
                    ("REC_0001_260716_080000",),
                ),
            ),
        )
        window._handle_result("guided_sync", (object(), inventory))

        assert window.guided_records_list.count() == 2
        assert not window.sync_countdown_timer.isActive()
        assert window.guided_start_distill_button.isEnabled() is False
        assert all(
            window.guided_records_list.item(index).checkState() == Qt.CheckState.Unchecked
            for index in range(2)
        )

        window._guided_select_all_dates()
        assert len(window._selected_guided_dates()) == 2
        assert window.guided_start_distill_button.isEnabled()
        assert "2" in window.guided_start_distill_button.text()
    finally:
        _close(window, temporary)


def test_device_search_timeout_exposes_retry_and_home_return() -> None:
    window, temporary = _window()
    try:
        window.guided_phase = "discovering"
        window.connect_spinner.start()
        window._guided_discovery_timeout()
        assert window.guided_phase == "discovery_timeout"
        assert not window.guided_retry_connection_button.isHidden()
        assert "120" in window.guided_connect_copy.text()

        window._return_guided_home()
        assert window.home_stack.currentIndex() == 0
        assert window.guided_phase == "idle"
    finally:
        _close(window, temporary)


def test_landing_intro_uses_ordered_title_subtitle_button_footer_timeline() -> None:
    window, temporary = _window()
    try:
        window._play_landing_intro()
        timeline = window._landing_animation_groups[-1]

        assert timeline.animationCount() == 4
        assert timeline.animationAt(0).duration() == 1100

        subtitle = timeline.animationAt(1)
        button = timeline.animationAt(2)
        footer = timeline.animationAt(3)
        assert subtitle.animationAt(0).duration() == 520
        assert button.animationAt(0).duration() == 520
        assert subtitle.animationAt(1).duration() == 700
        assert button.animationAt(1).duration() == 700
        assert footer.animationAt(0).duration() == 880
        assert footer.animationAt(1).duration() == 280
        assert footer.duration() <= button.duration()
        assert timeline.duration() == 1220
        all_copy = "\n".join(label.text() for label in window.window.findChildren(QLabel))
        assert "隐私与费用" not in all_copy
    finally:
        _close(window, temporary)


def test_firmware_and_serial_are_shown_and_v2_features_are_gated() -> None:
    window, temporary = _window()
    try:
        port = PortCandidate(
            "COM9",
            "USB 串行设备",
            "USB VID:PID=303A:4020 MI_02",
            0x303A,
            0x4020,
            True,
            serial_number="DD-USB-TEST",
            interface_number=2,
            role="protocol",
        )
        window._apply_status(
            {
                "protocol": 1,
                "firmware_version": "2.0.0",
                "device": "Day Distiller",
                "device_id": "DD-AABBCCDDEEFF",
                "mode": "serial",
                "capabilities": [
                    "transactional_export_v2",
                    "list_record_dates",
                    "get_export_status",
                    "end_session",
                    "msc_rw",
                ],
                "storage": {"ready": True},
            },
            port,
        )

        assert window.firmware_version_label.text() == "2.0.0"
        assert window.device_serial_label.text() == "DD-USB-TEST"
        assert "2.0.0" in window.guided_device_info.text()
        assert "DD-USB-TEST" in window.guided_sync_device_info.text()
        assert window.v2_dates_button.isEnabled()
        assert window.v2_exports_button.isEnabled()
        assert window.v2_end_session_button.isEnabled()
        assert "已连接" in window.header_device_state_label.text()
        assert window.header_restart_button.isEnabled()
        rw_index = window.access_combo.findData("rw")
        assert window.access_combo.model().item(rw_index).isEnabled()
        window.access_combo.setCurrentIndex(rw_index)
        assert window.access_combo.currentData() == "rw"
        assert window.battery_status_label.text() == "当前固件未通过 USB 提供"
        assert window.rtc_status_label.text() == "当前固件未通过 USB 提供"
    finally:
        _close(window, temporary)
