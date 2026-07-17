from __future__ import annotations

import os
import tempfile
from datetime import date
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from day_distiller_client.app import MainWindow
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
        sequence = window._landing_animation_groups[-1]

        assert sequence.animationCount() == 3
        assert sequence.animationAt(0).duration() == 1250

        subtitle_and_button = sequence.animationAt(1)
        assert subtitle_and_button.animationCount() == 2
        assert subtitle_and_button.animationAt(0).duration() == 800
        delayed_button = subtitle_and_button.animationAt(1)
        assert delayed_button.animationAt(0).duration() == 400
        assert delayed_button.animationAt(1).duration() == 800

        assert sequence.animationAt(2).duration() == 800
        assert sequence.duration() == 3250
    finally:
        _close(window, temporary)
