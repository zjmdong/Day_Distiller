from __future__ import annotations

import queue
import shutil
import threading
import time
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .art_styles import (
    ART_STYLES,
    CUSTOM_ART_STYLE_ID,
    DEFAULT_ART_STYLE_ID,
    MAX_CUSTOM_STYLE_CHARS,
    get_art_style,
    resolve_art_style_prompt,
    validate_custom_style_prompt,
)
from .credentials import CredentialName, CredentialStore
from .database import JobDatabase
from .device import (
    DeviceProfile,
    PortCandidate,
    UsbLinkDevice,
    device_profile,
    find_device,
    list_serial_ports,
)
from .device_workflow import LegacyDeviceWorkflow, SyncInventory, SyncedDay
from .domain import JobStage
from .legacy_import import available_record_dates, normalize_source_root, scan_record_directories
from .media import MediaPreprocessor
from .paths import AppPaths
from .pipeline import DistillationPipeline
from .providers import (
    DeepSeekSettings,
    DeepSeekStoryProvider,
    MockAIProvider,
    MockMailProvider,
    QwenEvidenceProvider,
    QwenSettings,
    SeedreamImageProvider,
    SeedreamSettings,
    SmtpMailProvider,
    SmtpSettings,
)
from .reporting import ReportRenderer, day_report_from_json
from .resources import bundled_ffmpeg_paths, resource_path
from .windows import drive_letters, list_removable_drives, safe_eject, wait_for_new_drive
from .ui_theme import APP_STYLESHEET


DEFAULT_POSTER_IMAGE_SIZE = "864x1152"


def _validate_base_url(value: str, provider: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlparse(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or any(token in candidate for token in ("{", "}", " "))
    ):
        raise ValueError(f"{provider} Base URL格式无效，请填写完整的 http(s) 地址")
    return candidate


def _validate_image_size(value: str) -> str:
    candidate = value.strip().upper()
    match = re.fullmatch(r"(\d{3,5})X(\d{3,5})", candidate)
    if not match:
        raise ValueError("海报尺寸应填写明确的宽x高像素值，例如 864x1152")
    width, height = map(int, match.groups())
    if min(width, height) < 512 or width >= height:
        raise ValueError("海报必须使用竖版尺寸")
    if abs(width / height - 0.75) > 0.02:
        raise ValueError("海报必须接近 3:4 竖版比例")
    if width * height >= 2_200_000:
        raise ValueError("海报总像素数必须少于220万，以避免进入更高的 Seedream 计费档位")
    return candidate.lower()


class Worker:
    def __init__(self, done: Callable[[tuple[str, Any, Exception | None]], None]) -> None:
        self._done = done

    def run(self, name: str, operation: Callable[[], Any]) -> None:
        def target() -> None:
            try:
                self._done((name, operation(), None))
            except Exception as exc:
                self._done((name, None, exc))

        threading.Thread(target=target, name=f"day-distiller-{name}", daemon=True).start()


class Spinner:
    """Small native Qt spinner used by the guided workflow and style preview."""

    def __init__(self, parent=None, diameter: int = 34, color: str = "#0099ff") -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QWidget

        self.widget = QWidget(parent)
        self.widget.setFixedSize(diameter, diameter)
        self.widget.paintEvent = self._paint_event
        self._angle = 0
        self._color = color
        self._timer = QTimer(self.widget)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._advance)

    def _advance(self) -> None:
        self._angle = (self._angle + 24) % 360
        self.widget.update()

    def _paint_event(self, _event) -> None:
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self.widget)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(self._color), 3.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        inset = 4.5
        rect = QRectF(inset, inset, self.widget.width() - inset * 2, self.widget.height() - inset * 2)
        painter.drawArc(rect, int((90 - self._angle) * 16), int(-285 * 16))

    def start(self) -> None:
        self.widget.show()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.widget.hide()


class FlatSuccessIcon:
    """Font-independent flat success mark matching the production UI."""

    def __init__(self, parent=None, diameter: int = 38) -> None:
        from PySide6.QtWidgets import QWidget

        self.widget = QWidget(parent)
        self.widget.setFixedSize(diameter, diameter)
        self.widget.paintEvent = self._paint_event

    def _paint_event(self, _event) -> None:
        from PySide6.QtCore import QPointF, QRectF, Qt
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self.widget)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        diameter = min(self.widget.width(), self.widget.height())
        inset = max(1.0, diameter * 0.035)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#22c875"))
        painter.drawEllipse(QRectF(inset, inset, diameter - inset * 2, diameter - inset * 2))
        pen = QPen(QColor("#ffffff"), max(2.2, diameter * 0.075))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        first = QPointF(diameter * 0.28, diameter * 0.52)
        middle = QPointF(diameter * 0.44, diameter * 0.68)
        last = QPointF(diameter * 0.73, diameter * 0.36)
        painter.drawLine(first, middle)
        painter.drawLine(middle, last)

    def show(self) -> None:
        self.widget.show()

    def hide(self) -> None:
        self.widget.hide()


class ClickableImageLabel:
    """A QLabel facade with a clicked signal without introducing custom QSS types."""

    def __init__(self, text: str = "") -> None:
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtWidgets import QLabel

        class ClickRelay(QObject):
            clicked = Signal()

        self.widget = QLabel(text)
        self._relay = ClickRelay(self.widget)
        self.clicked = self._relay.clicked
        self.widget.mouseReleaseEvent = self._mouse_release

    def _mouse_release(self, event) -> None:
        from PySide6.QtCore import Qt

        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        event.accept()


class NavigationStack:
    """Horizontal-text sidebar navigation backed by a QStackedWidget."""

    def __init__(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QStackedWidget, QVBoxLayout, QWidget

        self.widget = QWidget()
        self.widget.setAutoFillBackground(True)
        layout = QHBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sideBar")
        sidebar.setAutoFillBackground(True)
        sidebar.setFixedWidth(184)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 22, 14, 18)
        sidebar_layout.setSpacing(10)
        label = QLabel("WORKSPACE")
        label.setProperty("muted", True)
        label.setStyleSheet("font-size:10px; font-weight:600; padding-left:12px;")
        self.navigation = QListWidget()
        self.navigation.setObjectName("sideNav")
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setSpacing(1)
        sidebar_layout.addWidget(label)
        sidebar_layout.addWidget(self.navigation, 1)

        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)

    def addTab(self, widget, title: str) -> int:
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QListWidgetItem

        item = QListWidgetItem(title)
        item.setSizeHint(QSize(150, 46))
        self.navigation.addItem(item)
        index = self.stack.addWidget(widget)
        if self.navigation.currentRow() < 0:
            self.navigation.setCurrentRow(0)
        return index

    def count(self) -> int:
        return self.stack.count()

    def tabText(self, index: int) -> str:
        item = self.navigation.item(index)
        return item.text() if item else ""

    def setCurrentIndex(self, index: int) -> None:
        self.navigation.setCurrentRow(index)

    def currentIndex(self) -> int:
        return self.stack.currentIndex()


def main() -> int:
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    application = QApplication([])
    application.setApplicationName("Day Distiller")
    application.setStyle("Fusion")
    installed_fonts = set(QFontDatabase.families())
    ui_family = next(
        (
            candidate
            for candidate in ("Inter", "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI")
            if candidate in installed_fonts
        ),
        QApplication.font().family(),
    )
    application.setFont(QFont(ui_family, 10))
    window = MainWindow()
    window.show()
    return application.exec()


class MainWindow:
    def __init__(self) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QMainWindow

        self.window = QMainWindow()
        self.window.setWindowTitle("Day Distiller")
        icon_path = resource_path("assets", "day-distiller.svg")
        if icon_path.is_file():
            self.window.setWindowIcon(QIcon(str(icon_path)))
        self.window.resize(1280, 820)
        self.window.setMinimumSize(1060, 700)
        self.window.setStyleSheet(APP_STYLESHEET)
        self.paths = AppPaths.default().ensure()
        self.database = JobDatabase(self.paths.database)
        self.credentials = CredentialStore()
        self.events: queue.Queue[tuple[str, Any, Exception | None]] = queue.Queue()
        self.worker = Worker(self.events.put)
        self.device: UsbLinkDevice | None = None
        self.connected_device_profile: DeviceProfile | None = None
        self.ports: list[PortCandidate] = []
        self.last_drive_letter: str | None = None
        self.active_job_id: str | None = None
        self.scanned_source_root: Path | None = None
        self.scanned_target_date: date | None = None
        self.scanned_record_count = 0
        self.guided_discovery_inflight = False
        self.guided_sync_workflow: LegacyDeviceWorkflow | None = None
        self.guided_synced_day: SyncedDay | None = None
        self.guided_sync_inventory: SyncInventory | None = None
        self.guided_synced_days: list[SyncedDay] = []
        self.guided_countdown = 0
        self.guided_started_at = 0.0
        self.guided_discovery_deadline = 0.0
        self.guided_backgrounded = False
        self.guided_operation_active = False
        self.guided_completed_days = 0
        self.guided_total_days = 0
        self.guided_phase = "idle"
        self.style_preview_path: Path | None = None
        self._landing_reveal_targets: list[tuple[object, object]] = []
        self._landing_animation_groups: list[object] = []

        self.discovery_timer = QTimer()
        self.discovery_timer.setInterval(1000)
        self.discovery_timer.timeout.connect(self._guided_discovery_tick)
        self.sync_countdown_timer = QTimer()
        self.sync_countdown_timer.setInterval(1000)
        self.sync_countdown_timer.timeout.connect(self._guided_countdown_tick)

        self._build_ui()
        self._load_cloud_settings()
        self._load_avatar_profile()
        self.refresh_ports()
        self.refresh_history()
        # Let the native Windows window-opening animation finish before the
        # landing content begins its own reveal.
        QTimer.singleShot(500, self._play_landing_intro)

        self.timer = QTimer()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._drain_events)
        self.timer.start()

    def show(self) -> None:
        self.window.show()

    def _build_ui(self) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

        self.tabs = NavigationStack()
        self.tabs.addTab(self._guide_page(), "开始")
        self.tabs.addTab(self._history_page(), "回忆")
        self.tabs.addTab(self._avatar_page(), "形象与风格")
        self.tabs.addTab(self._settings_hub_page(), "设置")

        root = QWidget()
        root.setObjectName("appRoot")
        root.setAutoFillBackground(True)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("appHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 15, 24, 15)
        brand_box = QVBoxLayout()
        brand_box.setSpacing(1)
        brand = QLabel("Day Distiller")
        brand.setObjectName("brandTitle")
        brand_subtitle = QLabel("把散落的瞬间，变成一张值得收藏的今日海报")
        brand_subtitle.setObjectName("brandSubtitle")
        brand_box.addWidget(brand)
        brand_box.addWidget(brand_subtitle)
        header_layout.addLayout(brand_box)
        header_layout.addStretch(1)
        root_layout.addWidget(header)
        root_layout.addWidget(self.tabs.widget, 1)
        self.window.setCentralWidget(root)

        def repaint_after_navigation(_index: int) -> None:
            item = self.tabs.navigation.item(_index)
            if item and item.text() == "回忆":
                self.refresh_history()
            if item and item.text() == "设置":
                self.settings_stack.setCurrentIndex(0)
            # On Windows, a stacked-page switch can leave unchanged child widgets
            # waiting for the next native paint event. Repaint the complete visible
            # tree so the persistent header/sidebar never appear temporarily blank.
            def repaint_visible_tree() -> None:
                root.repaint()
                for child in root.findChildren(QWidget):
                    if child.isVisible():
                        child.repaint()
                QApplication.processEvents()

            QTimer.singleShot(0, repaint_visible_tree)
            QTimer.singleShot(40, repaint_visible_tree)

        self.tabs.navigation.currentRowChanged.connect(repaint_after_navigation)

    @staticmethod
    def _page_header(title: str, subtitle: str):
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        copy = QLabel(subtitle)
        copy.setObjectName("pageSubtitle")
        copy.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(copy)
        return widget

    @staticmethod
    def _mark_button(button, role: str = "primary"):
        button.setProperty("role", role)
        return button

    def _guide_page(self):
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
        from PySide6.QtWidgets import (
            QFrame,
            QGraphicsBlurEffect,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QPlainTextEdit,
            QProgressBar,
            QPushButton,
            QStackedWidget,
            QVBoxLayout,
            QWidget,
        )

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.home_stack = QStackedWidget()
        self.home_stack.setObjectName("homeWorkflow")
        layout.addWidget(self.home_stack)

        def centered_page() -> tuple[QWidget, QVBoxLayout]:
            widget = QWidget()
            box = QVBoxLayout(widget)
            box.setContentsMargins(54, 34, 54, 28)
            box.setSpacing(16)
            return widget, box

        def step_label(text: str) -> QLabel:
            label = QLabel(text)
            label.setProperty("stepBadge", True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedWidth(92)
            label.setFixedHeight(28)
            return label

        class RoundedProgressBar(QProgressBar):
            """Paints both the track and fill through the same rounded clip."""

            def paintEvent(self, _event) -> None:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                track = QRectF(0.75, 0.75, self.width() - 1.5, self.height() - 1.5)
                radius = track.height() / 2
                path = QPainterPath()
                path.addRoundedRect(track, radius, radius)
                painter.fillPath(path, QColor("#151515"))
                painter.setPen(QPen(QColor("#2b2b2b"), 1))
                painter.drawPath(path)
                span = self.maximum() - self.minimum()
                ratio = 0.0 if span <= 0 else (self.value() - self.minimum()) / span
                if ratio > 0:
                    fill = QRectF(
                        track.left(),
                        track.top(),
                        track.width() * min(1.0, ratio),
                        track.height(),
                    )
                    fill_radius = min(radius, fill.width() / 2)
                    fill_path = QPainterPath()
                    fill_path.addRoundedRect(fill, fill_radius, fill_radius)
                    painter.fillPath(fill_path, QColor("#0099ff"))
                painter.setPen(QColor("#e6e6e6"))
                painter.drawText(track, Qt.AlignmentFlag.AlignCenter, f"{round(ratio * 100)}%")

        class RevealEffect(QGraphicsBlurEffect):
            """One render pass for both soft focus and opacity on Windows."""

            from PySide6.QtCore import Property as _Property

            def __init__(self, parent=None) -> None:
                super().__init__(parent)
                self._reveal_opacity = 0.0

            def _get_reveal_opacity(self) -> float:
                return self._reveal_opacity

            def _set_reveal_opacity(self, value: float) -> None:
                self._reveal_opacity = max(0.0, min(1.0, float(value)))
                self.update()

            revealOpacity = _Property(
                float,
                _get_reveal_opacity,
                _set_reveal_opacity,
            )

            def draw(self, painter) -> None:
                painter.save()
                painter.setOpacity(self._reveal_opacity)
                super().draw(painter)
                painter.restore()

        def workflow_header(text: str) -> QHBoxLayout:
            row = QHBoxLayout()
            back = QPushButton("←  返回首页")
            back.setObjectName("workflowBackButton")
            back.setFixedWidth(112)
            back.clicked.connect(self._return_guided_home)
            row.addWidget(back)
            row.addStretch(1)
            row.addWidget(step_label(text))
            row.addStretch(1)
            row.addSpacing(112)
            return row

        def reveal_container(content: QWidget) -> QWidget:
            outer = QWidget()
            outer_layout = QVBoxLayout(outer)
            outer_layout.setContentsMargins(0, 0, 0, 0)
            outer_layout.addWidget(content)
            effect = RevealEffect(content)
            effect.setBlurRadius(16.0)
            effect.setProperty("revealOpacity", 0.0)
            content.setGraphicsEffect(effect)
            self._landing_reveal_targets.append((content, effect))
            return outer

        landing, landing_layout = centered_page()
        landing_layout.addStretch(2)
        hero = QLabel("让今天，成为值得收藏的一张海报")
        hero.setObjectName("heroTitle")
        hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.setWordWrap(True)

        hero_copy = QLabel("连接设备后，Day Distiller 会自动同步、理解、创作并发送。")
        hero_copy.setObjectName("heroSubtitle")
        hero_copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hero_start_button = self._mark_button(QPushButton("开始"))
        self.hero_start_button.setObjectName("heroStartButton")
        self.hero_start_button.setFixedSize(310, 62)
        self.hero_start_button.clicked.connect(self.start_guided_workflow)
        start_row = QHBoxLayout()
        start_row.addStretch(1)
        start_row.addWidget(self.hero_start_button)
        start_row.addStretch(1)
        button_holder = QWidget()
        button_layout = QVBoxLayout(button_holder)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addLayout(start_row)
        self.guided_home_status = QLabel("")
        self.guided_home_status.setObjectName("homeStatus")
        self.guided_home_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button_layout.addWidget(self.guided_home_status)

        # Keep each visual level independent so the intro can follow a clear,
        # deliberate title -> subtitle/button -> footer timeline.
        landing_layout.addWidget(reveal_container(hero))
        landing_layout.addWidget(reveal_container(hero_copy))
        landing_layout.addWidget(reveal_container(button_holder))
        landing_layout.addStretch(3)
        first_use = QPushButton("首次使用？先完成配置")
        first_use.setProperty("role", "link")
        first_use.clicked.connect(lambda: self._select_tab("设置"))
        first_use_row = QHBoxLayout()
        first_use_row.addStretch(1)
        first_use_row.addWidget(first_use)
        first_use_row.addStretch(1)
        privacy = QLabel("隐私与费用：IMU 与地点记忆在本地处理；仅必要的关键帧、音频与证据会发送至已配置服务。")
        privacy.setObjectName("privacyFootnote")
        privacy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        privacy.setWordWrap(True)
        footer_holder = QWidget()
        footer_layout = QVBoxLayout(footer_holder)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(6)
        footer_layout.addLayout(first_use_row)
        footer_layout.addWidget(privacy)
        landing_layout.addWidget(reveal_container(footer_holder))
        self.home_stack.addWidget(landing)

        connect_page, connect_layout = centered_page()
        connect_layout.addLayout(workflow_header("步骤 1 / 3"))
        connect_title = QLabel("连接设备")
        connect_title.setObjectName("workflowTitle")
        connect_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.guided_connect_copy = QLabel("请重启 Day Distiller 设备，并使用 USB 连接至电脑。")
        self.guided_connect_copy.setObjectName("workflowSubtitle")
        self.guided_connect_copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.guided_connect_copy.setWordWrap(True)
        connect_layout.addWidget(connect_title)
        connect_layout.addWidget(self.guided_connect_copy)
        connect_layout.addSpacing(26)
        status_card = QFrame()
        status_card.setObjectName("workflowStatusCard")
        status_row = QHBoxLayout(status_card)
        status_row.setContentsMargins(24, 22, 24, 22)
        self.connect_spinner = Spinner(status_card, 34)
        self.connect_success_icon = FlatSuccessIcon(status_card, 38)
        self.connect_success_icon.hide()
        self.guided_connection_status = QLabel("正在寻找设备")
        self.guided_connection_status.setObjectName("workflowStatusText")
        status_row.addStretch(1)
        status_row.addWidget(self.connect_spinner.widget)
        status_row.addWidget(self.connect_success_icon.widget)
        status_row.addSpacing(10)
        status_row.addWidget(self.guided_connection_status)
        status_row.addStretch(1)
        status_card.setMaximumWidth(580)
        connect_layout.addWidget(status_card, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.guided_retry_connection_button = self._mark_button(QPushButton("重新寻找设备"))
        self.guided_retry_connection_button.setFixedWidth(180)
        self.guided_retry_connection_button.clicked.connect(self.start_guided_workflow)
        self.guided_retry_connection_button.hide()
        connect_layout.addWidget(
            self.guided_retry_connection_button,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        connect_layout.addStretch(1)
        self.guided_device_info = QLabel("")
        self.guided_device_info.setObjectName("deviceMetaLabel")
        self.guided_device_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.guided_device_info.hide()
        connect_layout.addWidget(self.guided_device_info)
        self.home_stack.addWidget(connect_page)

        sync_page, sync_layout = centered_page()
        sync_layout.addLayout(workflow_header("步骤 2 / 3"))
        sync_title = QLabel("正在同步数据")
        sync_title.setObjectName("workflowTitle")
        sync_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.guided_sync_subtitle = QLabel("正在安全读取设备内今天的记录，请稍等。")
        self.guided_sync_subtitle.setObjectName("workflowSubtitle")
        self.guided_sync_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sync_layout.addWidget(sync_title)
        sync_layout.addWidget(self.guided_sync_subtitle)
        self.guided_sync_progress = RoundedProgressBar()
        self.guided_sync_progress.setRange(0, 100)
        self.guided_sync_progress.setValue(0)
        self.guided_sync_progress.setFormat("%p%")
        sync_layout.addWidget(self.guided_sync_progress)
        self.guided_records_list = QListWidget()
        self.guided_records_list.setObjectName("guidedRecordsList")
        self.guided_records_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.guided_records_list.itemChanged.connect(self._guided_date_selection_changed)
        self.guided_records_list.setMinimumHeight(250)
        self.guided_records_list.hide()
        sync_layout.addWidget(self.guided_records_list, 1)
        self.guided_sync_actions = QWidget()
        sync_actions = QHBoxLayout(self.guided_sync_actions)
        sync_actions.setContentsMargins(0, 0, 0, 0)
        self.guided_select_all_button = QPushButton("全选日期")
        self.guided_select_all_button.clicked.connect(self._guided_select_all_dates)
        self.guided_resync_button = QPushButton("重新同步")
        self.guided_start_distill_button = self._mark_button(QPushButton("开始蒸馏（3s）"))
        self.guided_resync_button.clicked.connect(self._guided_start_sync)
        self.guided_start_distill_button.clicked.connect(self._guided_start_distillation)
        sync_actions.addStretch(1)
        sync_actions.addWidget(self.guided_select_all_button)
        sync_actions.addWidget(self.guided_resync_button)
        sync_actions.addWidget(self.guided_start_distill_button)
        sync_actions.addStretch(1)
        self.guided_sync_actions.hide()
        sync_layout.addWidget(self.guided_sync_actions)
        self.guided_sync_device_info = QLabel("")
        self.guided_sync_device_info.setObjectName("deviceMetaLabel")
        self.guided_sync_device_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.guided_sync_device_info.hide()
        sync_layout.addWidget(self.guided_sync_device_info)
        self.home_stack.addWidget(sync_page)

        distill_page, distill_layout = centered_page()
        distill_layout.addLayout(workflow_header("步骤 3 / 3"))
        distill_title = QLabel("蒸馏进行中")
        distill_title.setObjectName("workflowTitle")
        distill_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        distill_copy = QLabel("去喝杯茶吧，很快就好。")
        distill_copy.setObjectName("workflowSubtitle")
        distill_copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        distill_layout.addWidget(distill_title)
        distill_layout.addWidget(distill_copy)
        progress_row = QHBoxLayout()
        self.guided_distill_progress = RoundedProgressBar()
        self.guided_distill_progress.setRange(0, 100)
        self.guided_distill_progress.setValue(0)
        self.guided_distill_progress.setFormat("%p%")
        self.guided_eta_label = QLabel("正在估算剩余时间")
        self.guided_eta_label.setObjectName("etaLabel")
        progress_row.addWidget(self.guided_distill_progress, 1)
        progress_row.addWidget(self.guided_eta_label)
        distill_layout.addLayout(progress_row)
        self.guided_live_progress = QPlainTextEdit()
        self.guided_live_progress.setObjectName("guidedLiveProgress")
        self.guided_live_progress.setReadOnly(True)
        self.guided_live_progress.setPlaceholderText("实时进度会显示在这里")
        distill_layout.addWidget(self.guided_live_progress, 1)
        self.home_stack.addWidget(distill_page)

        complete_page, complete_layout = centered_page()
        complete_layout.addStretch(1)
        done_icon = FlatSuccessIcon(complete_page, 86)
        done_title = QLabel("蒸馏已完成")
        done_title.setObjectName("workflowTitle")
        done_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.guided_complete_email = QLabel("已发送至指定邮箱")
        self.guided_complete_email.setObjectName("workflowSubtitle")
        self.guided_complete_email.setAlignment(Qt.AlignmentFlag.AlignCenter)
        finish = self._mark_button(QPushButton("完成"))
        finish.setFixedSize(260, 54)
        finish.clicked.connect(self._finish_guided_workflow)
        finish_row = QHBoxLayout()
        finish_row.addStretch(1)
        finish_row.addWidget(finish)
        finish_row.addStretch(1)
        complete_layout.addWidget(done_icon.widget, alignment=Qt.AlignmentFlag.AlignHCenter)
        complete_layout.addWidget(done_title)
        complete_layout.addWidget(self.guided_complete_email)
        complete_layout.addSpacing(22)
        complete_layout.addLayout(finish_row)
        complete_layout.addStretch(2)
        self.home_stack.addWidget(complete_page)
        return page

    def _avatar_page(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QComboBox,
            QFileDialog,
            QFrame,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPlainTextEdit,
            QPushButton,
            QStackedLayout,
            QVBoxLayout,
            QWidget,
        )

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 24, 30, 28)
        layout.setSpacing(14)
        layout.addWidget(
            self._page_header(
                "形象与艺术风格",
                "形象参考决定“画谁”，艺术风格决定“如何记住今天”。你也可以用历史日报只测试一张新风格。",
            )
        )

        columns = QHBoxLayout()
        columns.setSpacing(18)
        avatar_card = QGroupBox("我的海报形象")
        avatar_layout = QVBoxLayout(avatar_card)
        self.avatar_preview = QLabel("尚未选择参考形象")
        self.avatar_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_preview.setMinimumHeight(260)
        self.avatar_preview.setStyleSheet(
            "border:1px solid #242424; border-radius:12px; background:#050505; color:#777777;"
        )
        self.avatar_edit = QLineEdit()
        self.avatar_edit.setReadOnly(True)
        self.avatar_edit.setPlaceholderText("可上传 AI 形象，也可上传自己的实拍参考图")
        self.avatar_description_edit = QPlainTextEdit()
        self.avatar_description_edit.setPlaceholderText("简短描述发型、衣着、配饰和希望保持的角色特征")
        self.avatar_description_edit.setMaximumHeight(92)
        choose = QPushButton("选择参考形象")
        choose.clicked.connect(lambda: self._choose_avatar(QFileDialog))
        clear_avatar = QPushButton("清空参考形象")
        clear_avatar.clicked.connect(self.clear_avatar_profile)
        save = self._mark_button(QPushButton("保存形象与风格"))
        save.clicked.connect(self.save_avatar_profile)
        avatar_layout.addWidget(self.avatar_preview, 1)
        avatar_layout.addWidget(self.avatar_edit)
        avatar_layout.addWidget(self.avatar_description_edit)
        avatar_actions = QHBoxLayout()
        avatar_actions.addWidget(choose)
        avatar_actions.addWidget(clear_avatar)
        avatar_actions.addWidget(save)
        avatar_layout.addLayout(avatar_actions)
        columns.addWidget(avatar_card, 1)

        style_card = QGroupBox("海报艺术风格")
        style_layout = QVBoxLayout(style_card)
        self.art_style_combo = QComboBox()
        for style in ART_STYLES:
            self.art_style_combo.addItem(style.name, style.id)
        self.art_style_combo.addItem("自定义风格", CUSTOM_ART_STYLE_ID)
        self.art_style_combo.currentIndexChanged.connect(self._on_art_style_changed)
        self.art_style_description = QLabel()
        self.art_style_description.setWordWrap(True)
        self.art_style_description.setMinimumHeight(64)
        self.custom_style_edit = QPlainTextEdit()
        self.custom_style_edit.setPlaceholderText(
            "描述画面风格、材质、色彩与氛围。无需重复写竖版、无文字等规则。"
        )
        self.custom_style_edit.setMaximumHeight(90)
        self.custom_style_edit.textChanged.connect(self._limit_custom_style_prompt)
        self.custom_style_counter = QLabel(f"0/{MAX_CUSTOM_STYLE_CHARS}")
        self.custom_style_counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.custom_style_counter.setProperty("muted", True)

        history_label = QLabel("使用一条成功回忆预览当前风格")
        history_label.setStyleSheet("font-weight:650; margin-top:8px;")
        self.style_test_job_combo = QComboBox()
        self.style_test_job_combo.setPlaceholderText("选择历史日报")
        self.style_test_button = self._mark_button(
            QPushButton("预览当前风格"), "accent"
        )
        self.style_test_button.clicked.connect(self.test_current_art_style)
        self.style_test_status = QLabel("不会重新分析视频、音频或 IMU，也不会发送邮件。")
        self.style_test_status.setProperty("muted", True)
        self.style_test_status.setWordWrap(True)
        preview_holder = QFrame()
        preview_holder.setObjectName("stylePreviewHolder")
        preview_stack = QStackedLayout(preview_holder)
        preview_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        clickable_preview = ClickableImageLabel("新风格预览会显示在这里")
        self.style_preview = clickable_preview.widget
        self.style_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.style_preview.setMinimumHeight(240)
        self.style_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        clickable_preview.clicked.connect(self.open_style_preview)
        preview_stack.addWidget(self.style_preview)
        spinner_layer = QWidget()
        spinner_layout = QVBoxLayout(spinner_layer)
        spinner_layout.addStretch(1)
        self.style_preview_spinner = Spinner(spinner_layer, 44)
        spinner_layout.addWidget(self.style_preview_spinner.widget, alignment=Qt.AlignmentFlag.AlignCenter)
        spinner_layout.addStretch(1)
        preview_stack.addWidget(spinner_layer)
        spinner_layer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.style_preview_spinner_layer = spinner_layer
        self.style_preview_spinner.stop()
        spinner_layer.hide()
        self.style_download_button = QPushButton("下载图片")
        self.style_download_button.setProperty("role", "link")
        self.style_download_button.clicked.connect(self.download_style_preview)
        self.style_download_button.hide()
        style_layout.addWidget(self.art_style_combo)
        style_layout.addWidget(self.art_style_description)
        style_layout.addWidget(self.custom_style_edit)
        style_layout.addWidget(self.custom_style_counter)
        style_layout.addWidget(history_label)
        style_layout.addWidget(self.style_test_job_combo)
        style_layout.addWidget(self.style_test_button)
        style_layout.addWidget(self.style_test_status)
        style_layout.addWidget(preview_holder, 1)
        style_layout.addWidget(self.style_download_button, alignment=Qt.AlignmentFlag.AlignRight)
        columns.addWidget(style_card, 1)
        layout.addLayout(columns, 1)
        self._on_art_style_changed()
        return page

    def _settings_hub_page(self):
        from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.settings_stack = QStackedWidget()
        self.settings_stack.setObjectName("settingsStack")
        self.settings_stack.addWidget(self._simple_settings_page())
        self.settings_stack.addWidget(self._developer_settings_page())
        self.settings_stack.addWidget(self._developer_history_page())
        self.settings_stack.addWidget(self._manual_debug_page())
        layout.addWidget(self.settings_stack)
        return page

    def _simple_settings_page(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QFrame,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 24, 30, 28)
        layout.setSpacing(18)
        layout.addWidget(self._page_header("设置", "查看当前服务分工，并设置日报的默认收件地址。"))

        model_grid = QGridLayout()
        model_grid.setSpacing(12)
        self.model_summary_labels: dict[str, QLabel] = {}
        for index, (key, task, default) in enumerate(
            (
                ("scene", "关键帧理解", "qwen3.7-plus"),
                ("omni", "音视频与 OCR", "qwen3.5-omni-plus"),
                ("daily", "日报编排", "deepseek-v4-pro"),
                ("image", "海报生成", "doubao-seedream-5-0-pro"),
            )
        ):
            card = QFrame()
            card.setObjectName("modelCard")
            card_layout = QVBoxLayout(card)
            task_label = QLabel(task)
            task_label.setProperty("muted", True)
            model_label = QLabel(default)
            model_label.setObjectName("modelName")
            model_label.setWordWrap(True)
            card_layout.addWidget(task_label)
            card_layout.addWidget(model_label)
            self.model_summary_labels[key] = model_label
            model_grid.addWidget(card, 0, index)
        layout.addLayout(model_grid)

        mail_group = QGroupBox("日报收件地址")
        mail_layout = QVBoxLayout(mail_group)
        current_row = QHBoxLayout()
        current_row.addWidget(QLabel("当前收件地址"))
        self.current_recipient_label = QLabel("尚未设置")
        self.current_recipient_label.setObjectName("recipientValue")
        current_row.addWidget(self.current_recipient_label, 1)
        mail_layout.addLayout(current_row)
        edit_row = QHBoxLayout()
        self.simple_recipient_edit = QLineEdit()
        self.simple_recipient_edit.setPlaceholderText("name@example.com")
        self.save_recipient_button = self._mark_button(QPushButton("保存收件地址"))
        self.save_recipient_button.clicked.connect(self.save_recipient_setting)
        edit_row.addWidget(self.simple_recipient_edit, 1)
        edit_row.addWidget(self.save_recipient_button)
        mail_layout.addLayout(edit_row)
        layout.addWidget(mail_group)
        layout.addStretch(1)

        developer_link = QPushButton("进入开发者设置")
        developer_link.setProperty("role", "link")
        developer_link.clicked.connect(lambda: self.settings_stack.setCurrentIndex(1))
        layout.addWidget(developer_link, alignment=Qt.AlignmentFlag.AlignHCenter)
        return page

    def _developer_settings_page(self):
        from PySide6.QtWidgets import (
            QComboBox,
            QFormLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPushButton,
            QScrollArea,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 24, 30, 28)
        layout.setSpacing(14)
        layout.addWidget(
            self._page_header(
                "开发者设置",
                "只需首次填写。密钥保存在 Windows 凭据管理器，并按你的要求在此页明文显示。",
            )
        )
        notice = QLabel(
            "安全提醒 · 请勿截图或向他人展示此页面。Base URL、模型名和邮件参数保存在本地 SQLite。"
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(
            "background:#08131a; color:#a6a6a6; border:1px solid #17364a; "
            "border-radius:10px; padding:11px; font-weight:500;"
        )
        layout.addWidget(notice)

        self.api_key_edit = self._password_edit(QLineEdit)
        self.deepseek_key_edit = self._password_edit(QLineEdit)
        self.volcengine_key_edit = self._password_edit(QLineEdit)
        self.resend_key_edit = self._password_edit(QLineEdit)
        self.qwen_base_url_edit = QLineEdit("https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.deepseek_base_url_edit = QLineEdit("https://api.deepseek.com")
        self.volcengine_base_url_edit = QLineEdit("https://ark.cn-beijing.volces.com/api/v3")
        self.resend_host_edit = QLineEdit("smtp.resend.com")
        self.resend_port_edit = QSpinBox()
        self.resend_port_edit.setRange(1, 65535)
        self.resend_port_edit.setValue(465)
        self.resend_security_combo = QComboBox()
        self.resend_security_combo.addItem("SSL/TLS（端口465，推荐）", "ssl")
        self.resend_security_combo.addItem("STARTTLS（端口587）", "starttls")
        self.scene_model_edit = QLineEdit("qwen3.7-plus")
        self.omni_model_edit = QLineEdit("qwen3.5-omni-plus")
        self.daily_model_edit = QLineEdit("deepseek-v4-pro")
        self.image_model_edit = QLineEdit("doubao-seedream-5-0-pro")
        self.image_size_edit = QLineEdit(DEFAULT_POSTER_IMAGE_SIZE)
        self.image_size_edit.setReadOnly(True)
        self.image_size_edit.setToolTip("固定 1K 级 3:4 输出，约 99.5 万像素，避免误触更高计费档位")
        self.resend_sender_edit = QLineEdit()
        self.resend_recipient_edit = QLineEdit()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        columns = QHBoxLayout(content)
        columns.setContentsMargins(2, 2, 12, 2)
        columns.setSpacing(16)

        ai_group = QGroupBox("AI 模型")
        ai_form = QFormLayout(ai_group)
        ai_form.addRow("百炼 Base URL", self.qwen_base_url_edit)
        ai_form.addRow("百炼 API Key", self.api_key_edit)
        ai_form.addRow("关键帧理解", self.scene_model_edit)
        ai_form.addRow("音视频 / OCR", self.omni_model_edit)
        ai_form.addRow("DeepSeek Base URL", self.deepseek_base_url_edit)
        ai_form.addRow("DeepSeek API Key", self.deepseek_key_edit)
        ai_form.addRow("每日主题与文案", self.daily_model_edit)
        ai_form.addRow("火山方舟 Base URL", self.volcengine_base_url_edit)
        ai_form.addRow("火山方舟 API Key", self.volcengine_key_edit)
        ai_form.addRow("Seedream 模型 / Endpoint", self.image_model_edit)
        ai_form.addRow("海报分辨率（固定 1K）", self.image_size_edit)
        columns.addWidget(ai_group, 3)

        mail_group = QGroupBox("邮件 · Resend SMTP")
        mail_form = QFormLayout(mail_group)
        mail_form.addRow("SMTP 主机", self.resend_host_edit)
        mail_form.addRow("SMTP 端口", self.resend_port_edit)
        mail_form.addRow("安全方式", self.resend_security_combo)
        mail_form.addRow("Resend API Key", self.resend_key_edit)
        mail_form.addRow("发件地址", self.resend_sender_edit)
        mail_form.addRow("日报收件地址", self.resend_recipient_edit)
        mail_note = QLabel(
            "发件地址必须属于已在 Resend 验证的域名。SMTP 用户名固定为 resend；API Key 同时作为 SMTP 密码。"
        )
        mail_note.setWordWrap(True)
        mail_note.setProperty("muted", True)
        mail_form.addRow(mail_note)
        columns.addWidget(mail_group, 2)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        save = self._mark_button(QPushButton("保存全部设置"))
        save.clicked.connect(self.save_cloud_settings)
        bottom = QHBoxLayout()
        bottom.addWidget(save)
        self.data_usage_label = QLabel()
        self.data_usage_label.setProperty("muted", True)
        bottom.addStretch(1)
        bottom.addWidget(self.data_usage_label)
        layout.addLayout(bottom)
        developer_routes = QHBoxLayout()
        back = QPushButton("返回设置")
        back.setProperty("role", "link")
        generation_records = QPushButton("生成记录")
        manual_debug = QPushButton("手动调试")
        back.clicked.connect(lambda: self.settings_stack.setCurrentIndex(0))
        generation_records.clicked.connect(lambda: self.settings_stack.setCurrentIndex(2))
        manual_debug.clicked.connect(lambda: self.settings_stack.setCurrentIndex(3))
        developer_routes.addWidget(back)
        developer_routes.addStretch(1)
        developer_routes.addWidget(generation_records)
        developer_routes.addWidget(manual_debug)
        layout.addLayout(developer_routes)
        self.refresh_data_usage()
        return page

    def _developer_history_page(self):
        from PySide6.QtWidgets import QHBoxLayout, QListWidget, QPushButton, QVBoxLayout, QWidget

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 24, 30, 28)
        layout.setSpacing(14)
        layout.addWidget(
            self._page_header(
                "生成记录",
                "完整任务记录与恢复工具。普通用户的“回忆”页只展示已经成功完成的内容。",
            )
        )
        row = QHBoxLayout()
        back = QPushButton("返回开发者设置")
        back.setProperty("role", "link")
        refresh = QPushButton("刷新")
        self.dev_open_report_button = self._mark_button(QPushButton("打开日报"))
        self.edit_report_button = QPushButton("修改地点与文字")
        self.regenerate_button = QPushButton("重新编排并生成")
        self.dev_resend_button = QPushButton("手动再次发送")
        self.retry_job_button = QPushButton("重试失败任务")
        self.retry_cleanup_button = QPushButton("重试设备清理")
        back.clicked.connect(lambda: self.settings_stack.setCurrentIndex(1))
        for widget in (
            back,
            refresh,
            self.dev_open_report_button,
            self.edit_report_button,
            self.regenerate_button,
            self.dev_resend_button,
            self.retry_job_button,
            self.retry_cleanup_button,
        ):
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
        self.history_list = QListWidget()
        self.history_list.setObjectName("developerHistoryList")
        layout.addWidget(self.history_list, 1)
        refresh.clicked.connect(self.refresh_history)
        self.dev_open_report_button.clicked.connect(self.open_selected_report)
        self.edit_report_button.clicked.connect(self.edit_selected_report)
        self.regenerate_button.clicked.connect(self.regenerate_selected_report)
        self.dev_resend_button.clicked.connect(self.resend_selected_report)
        self.retry_job_button.clicked.connect(self.retry_selected_job)
        self.retry_cleanup_button.clicked.connect(self.retry_selected_cleanup)
        self.history_list.itemDoubleClicked.connect(lambda _item: self.open_selected_report())
        return page

    def _manual_debug_page(self):
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTabWidget, QVBoxLayout, QWidget

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 24)
        top = QHBoxLayout()
        back = QPushButton("返回开发者设置")
        back.setProperty("role", "link")
        back.clicked.connect(lambda: self.settings_stack.setCurrentIndex(1))
        top.addWidget(back)
        top.addStretch(1)
        layout.addLayout(top)
        debug_tabs = QTabWidget()
        debug_tabs.setObjectName("manualDebugTabs")
        debug_tabs.addTab(self._device_page(), "设备")
        debug_tabs.addTab(self._records_page(), "记录")
        debug_tabs.addTab(self._distillation_page(), "生成")
        layout.addWidget(debug_tabs, 1)
        return page

    @staticmethod
    def _password_edit(line_edit_class):
        edit = line_edit_class()
        edit.setEchoMode(line_edit_class.EchoMode.Normal)
        edit.setPlaceholderText("保存在本机凭据管理器；此处会明文显示")
        return edit

    def _device_page(self):
        from PySide6.QtWidgets import (
            QComboBox,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QPlainTextEdit,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 24, 30, 28)
        layout.setSpacing(14)
        layout.addWidget(
            self._page_header("连接设备", "通常只需点击“自动发现”。高级 MSC 操作仅在设备维护时使用。")
        )
        row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.refresh_button = QPushButton("刷新串口")
        self.connect_button = QPushButton("连接")
        self.auto_button = self._mark_button(QPushButton("自动发现"))
        row.addWidget(QLabel("协议 CDC"))
        row.addWidget(self.port_combo, 1)
        row.addWidget(self.refresh_button)
        row.addWidget(self.connect_button)
        row.addWidget(self.auto_button)
        layout.addLayout(row)

        group = QGroupBox("当前设备")
        grid = QGridLayout(group)
        self.connection_label = QLabel("未连接")
        self.mode_label = QLabel("-")
        self.storage_label = QLabel("-")
        self.drive_label = QLabel("-")
        self.firmware_version_label = QLabel("-")
        self.device_serial_label = QLabel("-")
        self.protocol_adapter_label = QLabel("-")
        self.battery_status_label = QLabel("-")
        self.rtc_status_label = QLabel("-")
        for index, (label, value) in enumerate(
            (
                ("连接", self.connection_label),
                ("固件版本", self.firmware_version_label),
                ("设备序列号", self.device_serial_label),
                ("协议适配器", self.protocol_adapter_label),
                ("模式", self.mode_label),
                ("电量", self.battery_status_label),
                ("RTC 时钟", self.rtc_status_label),
                ("TF 卡", self.storage_label),
                ("Windows 盘符", self.drive_label),
            )
        ):
            grid.addWidget(QLabel(label), index, 0)
            grid.addWidget(value, index, 1)
        layout.addWidget(group)

        controls = QHBoxLayout()
        self.access_combo = QComboBox()
        self.access_combo.addItem("只读", "ro")
        self.access_combo.addItem("读写", "rw")
        self.status_button = QPushButton("读取状态")
        self.enter_button = QPushButton("进入 U 盘模式")
        self.eject_button = QPushButton("安全弹出并退出")
        self.force_exit_button = QPushButton("强制退出 MSC")
        for widget in (
            QLabel("挂载权限"), self.access_combo, self.status_button, self.enter_button, self.eject_button, self.force_exit_button
        ):
            controls.addWidget(widget)
        layout.addLayout(controls)
        v2_controls = QHBoxLayout()
        self.v2_dates_button = QPushButton("读取设备日期")
        self.v2_exports_button = QPushButton("查询导出事务")
        self.v2_end_session_button = QPushButton("结束 2.0 维护会话")
        self.v2_dates_button.setEnabled(False)
        self.v2_exports_button.setEnabled(False)
        self.v2_end_session_button.setEnabled(False)
        self.v2_dates_button.clicked.connect(
            lambda: self._start("v2_dates", lambda: self._require_device().list_record_dates())
        )
        self.v2_exports_button.clicked.connect(
            lambda: self._start("v2_exports", lambda: self._require_device().get_export_status())
        )
        self.v2_end_session_button.clicked.connect(
            lambda: self._start("v2_end_session", lambda: self._require_device().end_session())
        )
        v2_controls.addWidget(QLabel("固件 2.0 功能"))
        v2_controls.addWidget(self.v2_dates_button)
        v2_controls.addWidget(self.v2_exports_button)
        v2_controls.addWidget(self.v2_end_session_button)
        v2_controls.addStretch(1)
        layout.addLayout(v2_controls)
        self.device_log = QPlainTextEdit()
        self.device_log.setReadOnly(True)
        layout.addWidget(self.device_log, 1)

        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self.connect_selected)
        self.auto_button.clicked.connect(self.auto_find)
        self.status_button.clicked.connect(lambda: self._start("status", lambda: self._require_device().get_status()))
        self.enter_button.clicked.connect(self.enter_msc)
        self.eject_button.clicked.connect(self.eject_and_exit)
        self.force_exit_button.clicked.connect(
            lambda: self._start("exit_msc", lambda: self._manual_exit_msc(force=True))
        )
        return page

    def _records_page(self):
        from PySide6.QtCore import QDate
        from PySide6.QtWidgets import (
            QDateEdit,
            QFileDialog,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QListWidget,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 24, 30, 28)
        layout.setSpacing(14)
        layout.addWidget(
            self._page_header(
                "选择今天的记录",
                "设备挂载后会自动带入路径。也可以选择本机测试文件夹，再扫描指定日期。",
            )
        )
        source_row = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("虚拟 TF 卡目录或已挂载设备盘符，例如 E:\\")
        browse = QPushButton("选择目录")
        browse.clicked.connect(
            lambda: self.source_edit.setText(QFileDialog.getExistingDirectory(self.window, "选择记录根目录") or self.source_edit.text())
        )
        source_row.addWidget(QLabel("记录根目录"))
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(browse)
        layout.addLayout(source_row)

        date_row = QHBoxLayout()
        self.target_date = QDateEdit(QDate.currentDate())
        self.target_date.setCalendarPopup(True)
        scan_button = self._mark_button(QPushButton("扫描所选日期"), "mint")
        scan_button.clicked.connect(self.scan_records)
        date_row.addWidget(QLabel("日期"))
        date_row.addWidget(self.target_date)
        date_row.addWidget(scan_button)
        date_row.addStretch(1)
        layout.addLayout(date_row)
        self.records_list = QListWidget()
        self.records_list.setObjectName("contentList")
        layout.addWidget(self.records_list, 1)
        self.records_hint = QLabel("只分析每个实际记录到的约 5 秒片段，不推断定时采样间隔内的活动。")
        self.records_hint.setWordWrap(True)
        layout.addWidget(self.records_hint)
        return page

    def _distillation_page(self):
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QHBoxLayout,
            QLabel,
            QPlainTextEdit,
            QProgressBar,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 24, 30, 28)
        layout.setSpacing(14)
        layout.addWidget(
            self._page_header(
                "生成今日海报",
                "已经扫描过记录时会直接开始；尚未选择素材时，应用会带你回到记录页。",
            )
        )
        top = QHBoxLayout()
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("中国大陆模型工作流 + Resend", "mainland")
        self.provider_combo.addItem("离线演示（零云端调用）", "mock")
        self.delete_virtual_source = QCheckBox("邮件成功后删除所选虚拟卡记录")
        self.start_folder_button = QPushButton("使用已扫描的文件夹")
        self.start_device_button = self._mark_button(QPushButton("从当前设备一键生成"))
        top.addWidget(QLabel("产出模式"))
        top.addWidget(self.provider_combo)
        top.addWidget(self.delete_virtual_source)
        top.addStretch(1)
        top.addWidget(self.start_folder_button)
        top.addWidget(self.start_device_button)
        layout.addLayout(top)
        self.stage_label = QLabel("等待开始")
        self.stage_label.setStyleSheet("font-size:18px; font-weight:600; color:#ffffff;")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.usage_label = QLabel(
            "费用边界 · 每次完整生成只调用一次 Seedream；最多输入 3 张参考图，输出固定 864×1152、约 99.5 万像素的 3:4 海报。"
        )
        self.usage_label.setWordWrap(True)
        self.distill_log = QPlainTextEdit()
        self.distill_log.setReadOnly(True)
        layout.addWidget(self.stage_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.usage_label)
        layout.addWidget(self.distill_log, 1)
        self.start_folder_button.clicked.connect(self.start_folder_distillation)
        self.start_device_button.clicked.connect(self.start_device_distillation)
        return page

    def _history_page(self):
        from PySide6.QtWidgets import QHBoxLayout, QListWidget, QPushButton, QVBoxLayout, QWidget

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 24, 30, 28)
        layout.setSpacing(14)
        layout.addWidget(self._page_header("我的回忆", "这里保存着已经完成的每日蒸馏。双击即可打开回看。"))
        row = QHBoxLayout()
        self.open_report_button = self._mark_button(QPushButton("打开回忆"))
        self.resend_button = QPushButton("再次发送")
        self.resend_other_button = QPushButton("发送到其他邮箱")
        for widget in (self.open_report_button, self.resend_button, self.resend_other_button):
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
        self.memory_list = QListWidget()
        self.memory_list.setObjectName("historyList")
        layout.addWidget(self.memory_list, 1)
        self.open_report_button.clicked.connect(lambda: self.open_selected_report(self.memory_list))
        self.resend_button.clicked.connect(lambda: self.resend_selected_report(self.memory_list))
        self.resend_other_button.clicked.connect(self.resend_selected_to_other_email)
        self.memory_list.itemDoubleClicked.connect(lambda _item: self.open_selected_report(self.memory_list))
        return page

    def _settings_page(self):
        from PySide6.QtWidgets import (
            QComboBox,
            QFileDialog,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPushButton,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )

        page = QWidget()
        layout = QVBoxLayout(page)
        notice = QLabel(
            "云端模式会把本地抽取的关键帧、音频和结构化证据发送到所选 API。Responses 请求使用 store:false，"
            "但这不等同于 Zero Data Retention。API Key 与 SMTP 密码只保存到 Windows Credential Manager。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = QFormLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.deepseek_key_edit = QLineEdit()
        self.deepseek_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.volcengine_key_edit = QLineEdit()
        self.volcengine_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("留空则保留已保存的 Key")
        self.scene_model_edit = QLineEdit("qwen3.7-plus")
        self.omni_model_edit = QLineEdit("qwen3.5-omni-plus")
        self.daily_model_edit = QLineEdit("deepseek-v4-pro")
        self.image_model_edit = QLineEdit("doubao-seedream-5-0-pro")
        self.smtp_host_edit = QLineEdit()
        self.smtp_port_edit = QSpinBox()
        self.smtp_port_edit.setRange(1, 65535)
        self.smtp_port_edit.setValue(465)
        self.smtp_security_combo = QComboBox()
        for label, value in (("SSL/TLS", "ssl"), ("STARTTLS", "starttls"), ("无加密（仅测试）", "none")):
            self.smtp_security_combo.addItem(label, value)
        self.smtp_username_edit = QLineEdit()
        self.smtp_password_edit = QLineEdit()
        self.smtp_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.smtp_password_edit.setPlaceholderText("留空则保留已保存的密码")
        self.smtp_sender_edit = QLineEdit()
        self.smtp_recipient_edit = QLineEdit()
        self.ffmpeg_edit = QLineEdit()
        self.motion_model_edit = QLineEdit()
        self.avatar_edit = QLineEdit()
        avatar_browse = QPushButton("选择头像")
        avatar_browse.clicked.connect(
            lambda: self.avatar_edit.setText(QFileDialog.getOpenFileName(self.window, "选择漫画参考形象", filter="Images (*.jpg *.jpeg *.png *.webp)")[0] or self.avatar_edit.text())
        )
        avatar_row = QHBoxLayout()
        avatar_row.addWidget(self.avatar_edit)
        avatar_row.addWidget(avatar_browse)
        form.addRow("阿里云百炼 API Key", self.api_key_edit)
        form.addRow("DeepSeek API Key", self.deepseek_key_edit)
        form.addRow("Volcengine API Key", self.volcengine_key_edit)
        form.addRow("逐片理解模型", self.scene_model_edit)
        form.addRow("批量音视频/OCR模型", self.omni_model_edit)
        form.addRow("全天综合模型", self.daily_model_edit)
        form.addRow("海报生图模型", self.image_model_edit)
        form.addRow("SMTP 主机", self.smtp_host_edit)
        form.addRow("SMTP 端口", self.smtp_port_edit)
        form.addRow("SMTP 安全", self.smtp_security_combo)
        form.addRow("SMTP 用户名", self.smtp_username_edit)
        form.addRow("SMTP 应用密码", self.smtp_password_edit)
        form.addRow("发件地址", self.smtp_sender_edit)
        form.addRow("收件地址", self.smtp_recipient_edit)
        form.addRow("FFmpeg bin 目录或 ffmpeg.exe", self.ffmpeg_edit)
        form.addRow("可选 IMU 模型 .joblib", self.motion_model_edit)
        form.addRow("漫画参考形象", avatar_row)
        layout.addLayout(form)
        save = QPushButton("保存设置")
        save.clicked.connect(self.save_settings)
        layout.addWidget(save)
        storage_row = QHBoxLayout()
        self.data_usage_label = QLabel()
        open_data = QPushButton("打开数据目录（手动清理）")
        open_data.clicked.connect(self.open_data_directory)
        storage_row.addWidget(self.data_usage_label)
        storage_row.addStretch(1)
        storage_row.addWidget(open_data)
        layout.addLayout(storage_row)
        layout.addStretch(1)
        self.refresh_data_usage()
        return page

    def refresh_ports(self) -> None:
        self.ports = list_serial_ports()
        self.port_combo.clear()
        for port in self.ports:
            role_text = {
                "protocol": "协议端口",
                "compat": "兼容接口（自动检测）",
                "log": "辅助接口（自动检测）",
                "unknown": "待检测",
            }[port.role]
            self.port_combo.addItem(
                f"{port.device} · {port.description} · {role_text}"
                f"{' · 推荐' if port.role == 'protocol' else ''}",
                port.device,
            )
        self._device_log(f"发现 {len(self.ports)} 个串口。")

    def connect_selected(self) -> None:
        port = self.port_combo.currentData()
        if not port:
            self._device_log("没有选择串口。")
            return
        def work():
            self._close_device()
            self.device = UsbLinkDevice(port)
            return self.device.hello()

        self._start("connect", work)

    def auto_find(self) -> None:
        self._start("auto_find", find_device)

    def enter_msc(self) -> None:
        access = self.access_combo.currentData()
        before = drive_letters()

        def work():
            status = self._require_device().enter_msc(access)
            self._close_device()
            drive = wait_for_new_drive(before, timeout=25)
            found = find_device(timeout_per_port=0.6)
            return {"status": found[1] if found else status, "drive": drive, "port": found[0] if found else None}

        self._start("enter_msc", work)

    def eject_and_exit(self) -> None:
        letter = self.last_drive_letter
        if not letter:
            drives = list_removable_drives()
            letter = drives[0].letter if drives else None
        if not letter:
            self._device_log("未发现可弹出的可移动卷。")
            return

        def work():
            safe_eject(letter)
            time.sleep(1.5)
            return letter

        self._start("eject", work)

    def scan_records(self) -> None:
        self.records_list.clear()
        self.scanned_source_root = None
        self.scanned_target_date = None
        self.scanned_record_count = 0
        try:
            root = normalize_source_root(self.source_edit.text())
            self.source_edit.setText(str(root))
            dates = available_record_dates(root)
            if dates:
                selected = self._selected_date()
                if selected not in dates:
                    selected = dates[0]
                    self.target_date.setDate(selected)
            else:
                selected = self._selected_date()
            records = scan_record_directories(root, selected)
            for record in records:
                files = ", ".join(path.name for path in record.iterdir() if path.is_file())
                self.records_list.addItem(f"{record.name}    {files}")
            self.records_hint.setText(f"{selected.isoformat()} 共 {len(records)} 条记录。只分析实际记录片段。")
            self.scanned_source_root = root
            self.scanned_target_date = selected
            self.scanned_record_count = len(records)
        except Exception as exc:
            self.records_hint.setText(f"扫描失败：{exc}")

    def _play_landing_intro(self) -> None:
        """Reveal the landing content through a deliberate four-part timeline."""

        from PySide6.QtCore import (
            QEasingCurve,
            QParallelAnimationGroup,
            QPropertyAnimation,
            QSequentialAnimationGroup,
        )

        self._landing_animation_groups.clear()
        if len(self._landing_reveal_targets) != 4:
            return

        for content, effect in self._landing_reveal_targets:
            effect.setBlurRadius(16.0)
            effect.setProperty("revealOpacity", 0.0)

        def reveal_group(content, effect, duration: int, parent) -> QParallelAnimationGroup:
            group = QParallelAnimationGroup(parent)
            blur_animation = QPropertyAnimation(effect, b"blurRadius", group)
            blur_animation.setStartValue(16.0)
            blur_animation.setEndValue(0.0)
            blur_animation.setDuration(duration)
            blur_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
            opacity_animation = QPropertyAnimation(effect, b"revealOpacity", group)
            opacity_animation.setStartValue(0.0)
            opacity_animation.setEndValue(1.0)
            opacity_animation.setDuration(duration)
            opacity_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
            group.finished.connect(lambda content=content: content.setGraphicsEffect(None))
            return group

        title, subtitle, button, footer = self._landing_reveal_targets
        sequence = QSequentialAnimationGroup(self.window)
        sequence.addAnimation(reveal_group(*title, 1250, sequence))

        subtitle_and_button = QParallelAnimationGroup(sequence)
        subtitle_and_button.addAnimation(
            reveal_group(*subtitle, 800, subtitle_and_button)
        )
        delayed_button = QSequentialAnimationGroup(subtitle_and_button)
        delayed_button.addPause(400)
        delayed_button.addAnimation(reveal_group(*button, 800, delayed_button))
        subtitle_and_button.addAnimation(delayed_button)
        sequence.addAnimation(subtitle_and_button)

        sequence.addAnimation(reveal_group(*footer, 800, sequence))
        self._landing_animation_groups.append(sequence)
        sequence.start()

    def start_guided_workflow(self) -> None:
        """Enter the production one-click flow and begin 1 Hz device discovery."""
        if self.guided_operation_active:
            return
        self.sync_countdown_timer.stop()
        self.guided_phase = "discovering"
        self.guided_backgrounded = False
        self.guided_discovery_inflight = False
        self.guided_sync_workflow = None
        self.guided_synced_day = None
        self.guided_synced_days = []
        self.guided_sync_inventory = None
        self.guided_completed_days = 0
        self.guided_total_days = 0
        self.guided_discovery_deadline = time.monotonic() + 120.0
        self.connect_success_icon.hide()
        self.connect_spinner.start()
        self.guided_device_info.hide()
        self.guided_sync_device_info.hide()
        self.guided_retry_connection_button.hide()
        self.guided_connect_copy.setText("请重启 Day Distiller 设备，并使用 USB 连接至电脑。")
        self.guided_connection_status.setText("正在寻找设备")
        self.guided_home_status.setText("")
        self.home_stack.setCurrentIndex(1)
        self.discovery_timer.start()
        self._guided_discovery_tick()

    def _guided_discovery_tick(self) -> None:
        if self.guided_phase != "discovering" or self.guided_discovery_inflight:
            return
        if time.monotonic() >= self.guided_discovery_deadline:
            self._guided_discovery_timeout()
            return
        self.guided_discovery_inflight = True
        self.worker.run("guided_discover", find_device)

    def _guided_discovery_timeout(self) -> None:
        self.discovery_timer.stop()
        self.guided_phase = "discovery_timeout"
        self.connect_spinner.stop()
        self.connect_success_icon.hide()
        self.guided_connection_status.setText("暂未找到设备")
        self.guided_connect_copy.setText(
            "已等待 120 秒。请检查 USB 连接与设备状态，重启设备后再试一次。"
        )
        self.guided_retry_connection_button.show()

    def _return_guided_home(self) -> None:
        self.discovery_timer.stop()
        self.sync_countdown_timer.stop()
        self.connect_spinner.stop()
        active = self.guided_phase in {"syncing", "distilling"}
        if active:
            self.guided_backgrounded = True
            self.hero_start_button.setDisabled(True)
            activity = "同步" if self.guided_phase == "syncing" else "蒸馏"
            self.guided_home_status.setText(f"{activity}仍在后台安全进行，完成前请勿断开设备。")
        else:
            self.guided_phase = "idle"
            self.guided_backgrounded = False
            self.hero_start_button.setDisabled(False)
        self.home_stack.setCurrentIndex(0)
        self._select_tab("开始")

    def _guided_start_sync(self) -> None:
        if self.guided_phase not in {"discovering", "sync_error", "synced"}:
            return
        if self.guided_operation_active:
            return
        self.discovery_timer.stop()
        self.sync_countdown_timer.stop()
        self.guided_phase = "syncing"
        self.guided_operation_active = True
        self.home_stack.setCurrentIndex(2)
        self.guided_sync_progress.setValue(2)
        self.guided_sync_subtitle.setText("正在挂载设备并比对本地缓存，请稍等。")
        self.guided_records_list.clear()
        self.guided_records_list.hide()
        self.guided_sync_actions.hide()

        def work():
            pipeline = self._create_pipeline("mock")
            workflow = LegacyDeviceWorkflow(
                pipeline,
                status=lambda message: self.events.put(("guided_message", message, None)),
                sync_progress=lambda progress, message: self.events.put(
                    ("guided_sync_progress", (progress, message), None)
                ),
            )
            inventory = workflow.sync_all(self.paths.imports)
            return workflow, inventory

        self.worker.run("guided_sync", work)

    def _guided_countdown_tick(self) -> None:
        self.guided_countdown -= 1
        if self.guided_countdown <= 0:
            self.sync_countdown_timer.stop()
            self._guided_start_distillation()
            return
        self.guided_start_distill_button.setText(f"开始蒸馏（{self.guided_countdown}s）")

    def _guided_select_all_dates(self) -> None:
        from PySide6.QtCore import Qt

        for index in range(self.guided_records_list.count()):
            self.guided_records_list.item(index).setCheckState(Qt.CheckState.Checked)

    def _guided_date_selection_changed(self, _item=None) -> None:
        selected = self._selected_guided_dates()
        self.guided_start_distill_button.setDisabled(not selected)
        if self.guided_sync_inventory and len(self.guided_sync_inventory.days) > 1:
            self.guided_start_distill_button.setText(
                f"开始蒸馏（{len(selected)} 天）" if selected else "请选择日期"
            )

    def _selected_guided_dates(self) -> list[date]:
        from PySide6.QtCore import Qt

        selected: list[date] = []
        for index in range(self.guided_records_list.count()):
            item = self.guided_records_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(date.fromisoformat(str(item.data(Qt.ItemDataRole.UserRole))))
        return selected

    def _guided_start_distillation(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        self.sync_countdown_timer.stop()
        if self.guided_sync_inventory is None:
            QMessageBox.information(self.window, "尚未同步", "请先等待设备记录同步完成。")
            return
        selected_dates = self._selected_guided_dates()
        if not selected_dates:
            QMessageBox.information(self.window, "请选择日期", "请勾选一个或多个需要蒸馏的日期。")
            return
        self.guided_phase = "distilling"
        self.guided_operation_active = True
        self.guided_backgrounded = False
        self.guided_device_info.hide()
        self.guided_sync_device_info.hide()
        self._set_busy(True)
        self.home_stack.setCurrentIndex(3)
        self.guided_distill_progress.setValue(1)
        self.guided_live_progress.clear()
        self.guided_live_progress.appendPlainText("本地数据已校验，正在准备分析…")
        self.guided_eta_label.setText("正在估算剩余时间")
        self.guided_started_at = time.monotonic()
        provider_mode = str(self.provider_combo.currentData() or "mainland")
        inventory = self.guided_sync_inventory
        self.guided_total_days = len(selected_dates)
        self.guided_completed_days = 0

        def work():
            preparation_pipeline = self._create_pipeline(
                "mock",
                progress_callback=lambda _stage, _progress, message: self.events.put(
                    ("guided_message", message, None)
                ),
            )
            preparation_workflow = LegacyDeviceWorkflow(preparation_pipeline)
            preparation_workflow.release_unselected(inventory, selected_dates)
            synced_days = preparation_workflow.prepare_days(
                inventory,
                selected_dates,
                provider_mode=provider_mode,
            )
            self.guided_synced_days = synced_days
            results = []
            for index, synced in enumerate(synced_days):
                day_label = synced.target_date.isoformat() if synced.target_date else "所选日期"
                self.events.put(
                    ("guided_day_start", (index, len(synced_days), day_label), None)
                )

                def progress_callback(stage, progress, message, index=index):
                    self.events.put(
                        (
                            "guided_batch_progress",
                            (index, len(synced_days), stage, progress, message),
                            None,
                        )
                    )

                pipeline = self._create_pipeline(
                    provider_mode,
                    progress_callback=progress_callback,
                )
                workflow = LegacyDeviceWorkflow(
                    pipeline,
                    status=lambda message: self.events.put(("guided_message", message, None)),
                )
                self.guided_sync_workflow = workflow
                results.append(
                    workflow.process_synced(
                        synced.job_id,
                        avatar_references=self._avatar_references(),
                    )
                )
                self.events.put(
                    ("guided_day_complete", (index + 1, len(synced_days), day_label), None)
                )
            if inventory.adapter_name == "transactional_export_v2" and self.guided_sync_workflow:
                try:
                    self.guided_sync_workflow.end_session()
                except Exception as exc:
                    self.events.put(
                        ("guided_message", f"设备维护会话将在超时后自动结束：{exc}", None)
                    )
            return results

        self.worker.run("guided_distill", work)

    def _guided_progress_value(self, stage: JobStage, progress: float) -> int:
        ranges = {
            JobStage.VALIDATING: (3, 8),
            JobStage.PREPROCESSING: (8, 34),
            JobStage.ANALYZING: (34, 64),
            JobStage.GENERATING: (64, 82),
            JobStage.RENDERING: (82, 89),
            JobStage.EMAILING: (89, 95),
            JobStage.CLEANUP: (95, 99),
            JobStage.COMPLETED: (100, 100),
        }
        low, high = ranges.get(stage, (1, 99))
        return max(low, min(high, round(low + (high - low) * max(0.0, min(1.0, progress)))))

    def _update_guided_eta(self, percent: int) -> None:
        if percent < 3 or not self.guided_started_at:
            self.guided_eta_label.setText("正在估算剩余时间")
            return
        elapsed = max(1.0, time.monotonic() - self.guided_started_at)
        remaining = elapsed * (100 - percent) / max(1, percent)
        if remaining >= 90:
            text = f"预计剩余 {max(2, round(remaining / 60))} 分钟"
        else:
            text = f"预计剩余 {max(1, round(remaining))} 秒"
        self.guided_eta_label.setText(text)

    def _finish_guided_workflow(self) -> None:
        self.discovery_timer.stop()
        self.sync_countdown_timer.stop()
        self.guided_phase = "idle"
        self.guided_operation_active = False
        self.guided_backgrounded = False
        self.guided_sync_workflow = None
        self.guided_synced_day = None
        self.guided_synced_days = []
        self.guided_sync_inventory = None
        self.hero_start_button.setDisabled(False)
        self.guided_home_status.setText("")
        self.home_stack.setCurrentIndex(0)
        self._select_tab("开始")

    def start_folder_distillation(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        try:
            source = normalize_source_root(self.source_edit.text())
        except (ValueError, FileNotFoundError) as exc:
            QMessageBox.information(self.window, "请选择记录", str(exc))
            self._select_tab("记录")
            return
        target = self._selected_date()
        if (
            self.scanned_source_root != source
            or self.scanned_target_date != target
            or self.scanned_record_count < 1
        ):
            QMessageBox.information(
                self.window,
                "请先扫描记录",
                "请在“记录”页选择记录根目录和日期，并确认扫描到了至少一条记录。",
            )
            self._select_tab("记录")
            return
        provider_mode = str(self.provider_combo.currentData())
        delete_source = self.delete_virtual_source.isChecked()
        self._set_busy(True)
        self._select_tab("生成")

        def work():
            pipeline = self._create_pipeline(provider_mode)
            job_id = pipeline.import_legacy(source, target, provider_mode=provider_mode)
            self.active_job_id = job_id
            cleanup = source if delete_source else None
            return pipeline.process(job_id, cleanup_source_root=cleanup, avatar_references=self._avatar_references())

        self._start("distill", work)

    def start_device_distillation(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        target = self._selected_date()
        if (
            self.scanned_source_root is not None
            and self.scanned_target_date == target
            and self.scanned_record_count > 0
        ):
            # A mounted device card and a user-selected fixture are both valid
            # record sources. Reuse the confirmed selection instead of forcing
            # the user through device discovery again.
            self.start_folder_distillation()
            return
        if self.device is None and not self.port_combo.currentData():
            QMessageBox.information(self.window, "请先连接设备", "尚未选择设备串口，请先在“设备”页连接或自动发现设备。")
            self._select_tab("设备")
            return
        provider_mode = str(self.provider_combo.currentData())
        self._set_busy(True)
        self._select_tab("生成")

        def work():
            pipeline = self._create_pipeline(provider_mode)
            workflow = LegacyDeviceWorkflow(
                pipeline, status=lambda message: self.events.put(("progress_message", message, None))
            )
            result = workflow.run_day(
                target, provider_mode=provider_mode, avatar_references=self._avatar_references()
            )
            self.active_job_id = result.job_id
            return result

        self._start("distill", work)

    def retry_selected_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        provider_mode = self.database.get_job(job_id).provider_mode
        self._set_busy(True)
        self._select_tab("生成")
        self._start(
            "distill",
            lambda: self._create_pipeline(provider_mode).process(
                job_id, avatar_references=self._avatar_references()
            ),
        )

    def retry_selected_cleanup(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        provider_mode = self.database.get_job(job_id).provider_mode
        self._set_busy(True)
        self._start(
            "cleanup_retry",
            lambda: LegacyDeviceWorkflow(self._create_pipeline(provider_mode)).retry_pending_cleanup(job_id),
        )

    def refresh_history(self) -> None:
        from PySide6.QtCore import Qt

        self.history_list.clear()
        self.memory_list.clear()
        if hasattr(self, "style_test_job_combo"):
            self.style_test_job_combo.clear()
        for job in self.database.list_jobs():
            report_row = self.database.get_report(job.id)
            report_title = ""
            if report_row is not None:
                try:
                    report_title = str(json.loads(report_row["report_json"]).get("title", ""))
                except (json.JSONDecodeError, TypeError):
                    report_title = ""
            item_text = f"{job.target_date.isoformat()}  ·  {report_title or job.stage.value}"
            if job.error:
                item_text += f"  ·  {job.error}"
            self.history_list.addItem(item_text)
            self.history_list.item(self.history_list.count() - 1).setData(Qt.ItemDataRole.UserRole, job.id)
            if job.stage == JobStage.COMPLETED and report_row is not None:
                memory_text = f"{job.target_date.isoformat()}  ·  {report_title or '今日回忆'}"
                self.memory_list.addItem(memory_text)
                self.memory_list.item(self.memory_list.count() - 1).setData(
                    Qt.ItemDataRole.UserRole, job.id
                )
            if (
                job.stage == JobStage.COMPLETED
                and report_row is not None
                and hasattr(self, "style_test_job_combo")
            ):
                self.style_test_job_combo.addItem(
                    f"{job.target_date.isoformat()}  ·  {report_title or '已完成日报'}", job.id
                )
        if hasattr(self, "data_usage_label"):
            self.refresh_data_usage()

    def open_selected_report(self, list_widget=None) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        job_id = self._selected_job_id(list_widget)
        if not job_id:
            return
        job = self.database.get_job(job_id)
        if job.report_path and Path(job.report_path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(job.report_path))

    def edit_selected_report(self) -> None:
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLineEdit,
            QPlainTextEdit,
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
        )

        job_id = self._selected_job_id()
        if not job_id:
            return
        row = self.database.get_report(job_id)
        if row is None:
            return
        report = day_report_from_json(json.loads(row["report_json"]))
        dialog = QDialog(self.window)
        dialog.setWindowTitle("修改日报地点与文字")
        dialog.resize(900, 620)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        title = QLineEdit(report.title)
        summary = QLineEdit(report.one_sentence_summary)
        warm_message = QLineEdit(report.warm_message)
        narrative = QPlainTextEdit(report.narrative)
        form.addRow("标题", title)
        form.addRow("一句话总结", summary)
        form.addRow("给用户的暖心话", warm_message)
        form.addRow("一天小结", narrative)
        layout.addLayout(form)
        table = QTableWidget(len(report.timeline), 3)
        table.setHorizontalHeaderLabels(["时间", "地点（会记住）", "片段描述"])
        table.horizontalHeader().setStretchLastSection(True)
        for index, item in enumerate(report.timeline):
            table.setItem(index, 0, QTableWidgetItem(str(item.get("time_label", ""))))
            table.setItem(index, 1, QTableWidgetItem(str(item.get("location", "") or "")))
            table.setItem(index, 2, QTableWidgetItem(str(item.get("summary", ""))))
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        report.title = title.text().strip() or report.title
        report.one_sentence_summary = summary.text().strip()
        report.warm_message = warm_message.text().strip() or report.warm_message
        report.narrative = narrative.toPlainText().strip()
        for index, item in enumerate(report.timeline):
            location = table.item(index, 1).text().strip() if table.item(index, 1) else ""
            description = table.item(index, 2).text().strip() if table.item(index, 2) else ""
            item["location"] = location or None
            item["summary"] = description
            self.database.update_scene_correction(str(item.get("record_id", "")), description, location or None)
            signature = str(item.get("visual_signature", ""))
            if location and signature:
                self.database.remember_place(location, signature, report.report_date)
        rendered = ReportRenderer().render(
            report, self.paths.reports / report.report_date.isoformat() / job_id
        )
        self.database.save_report(report, rendered.html_path, rendered.pdf_path)
        self.refresh_history()

    def regenerate_selected_report(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        provider_mode = self.database.get_job(job_id).provider_mode
        self._set_busy(True)
        self._start(
            "manual_regenerate",
            lambda: self._create_pipeline(provider_mode).regenerate(job_id, self._avatar_references()),
        )

    def resend_selected_report(self, list_widget=None, recipient: str | None = None) -> None:
        job_id = self._selected_job_id(list_widget)
        if not job_id:
            return
        provider_mode = self.database.get_job(job_id).provider_mode
        self._set_busy(True)
        self._start(
            "manual_resend",
            lambda: self._create_pipeline(provider_mode, recipient_override=recipient).resend(job_id),
        )

    def resend_selected_to_other_email(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        job_id = self._selected_job_id(self.memory_list)
        if not job_id:
            QMessageBox.information(self.window, "选择回忆", "请先选择一条已经完成的回忆。")
            return
        value, accepted = QInputDialog.getText(self.window, "发送到其他邮箱", "收件人邮箱")
        recipient = value.strip()
        if not accepted:
            return
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", recipient):
            QMessageBox.warning(self.window, "邮箱格式无效", "请输入完整的收件人邮箱地址。")
            return
        self.resend_selected_report(self.memory_list, recipient)

    def save_settings(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        settings = {
            "models": {
                "scene": self.scene_model_edit.text().strip(),
                "omni": self.omni_model_edit.text().strip(),
                "daily": self.daily_model_edit.text().strip(),
                "image": self.image_model_edit.text().strip(),
            },
            "smtp": {
                "host": self.smtp_host_edit.text().strip(),
                "port": self.smtp_port_edit.value(),
                "security": self.smtp_security_combo.currentData(),
                "username": self.smtp_username_edit.text().strip(),
                "sender": self.smtp_sender_edit.text().strip(),
                "recipient": self.smtp_recipient_edit.text().strip(),
            },
            "ffmpeg": self.ffmpeg_edit.text().strip(),
            "motion_model": self.motion_model_edit.text().strip(),
            "avatar": self.avatar_edit.text().strip(),
        }
        self.database.set_setting("desktop_v2", settings)
        try:
            for field, credential in (
                (self.api_key_edit, CredentialName.QWEN_API_KEY),
                (self.deepseek_key_edit, CredentialName.DEEPSEEK_API_KEY),
                (self.volcengine_key_edit, CredentialName.VOLCENGINE_API_KEY),
            ):
                if field.text():
                    self.credentials.set(credential, field.text())
                    field.clear()
            if self.smtp_password_edit.text():
                self.credentials.set(CredentialName.SMTP_PASSWORD, self.smtp_password_edit.text())
                self.smtp_password_edit.clear()
        except Exception as exc:
            QMessageBox.warning(self.window, "凭据保存失败", str(exc))
            return
        QMessageBox.information(self.window, "设置", "设置已保存；凭据未写入数据库、日志或 Git。")

    def open_data_directory(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths.root)))

    def refresh_data_usage(self) -> None:
        total = sum(path.stat().st_size for path in self.paths.root.rglob("*") if path.is_file())
        self.data_usage_label.setText(f"本地数据占用：{total / (1024 ** 3):.2f} GiB · {self.paths.root}")

    def _load_settings(self) -> None:
        settings = self.database.get_setting("desktop_v2", {})
        models = settings.get("models", {})
        smtp = settings.get("smtp", {})
        self.scene_model_edit.setText(models.get("scene", self.scene_model_edit.text()))
        self.omni_model_edit.setText(models.get("omni", self.omni_model_edit.text()))
        self.daily_model_edit.setText(models.get("daily", self.daily_model_edit.text()))
        self.image_model_edit.setText(models.get("image", self.image_model_edit.text()))
        self.smtp_host_edit.setText(smtp.get("host", ""))
        self.smtp_port_edit.setValue(int(smtp.get("port", 465)))
        index = self.smtp_security_combo.findData(smtp.get("security", "ssl"))
        self.smtp_security_combo.setCurrentIndex(max(0, index))
        self.smtp_username_edit.setText(smtp.get("username", ""))
        self.smtp_sender_edit.setText(smtp.get("sender", ""))
        self.smtp_recipient_edit.setText(smtp.get("recipient", ""))
        self.ffmpeg_edit.setText(settings.get("ffmpeg", ""))
        self.motion_model_edit.setText(settings.get("motion_model", ""))
        self.avatar_edit.setText(settings.get("avatar", ""))

    def _legacy_create_pipeline(self, provider_mode: str) -> DistillationPipeline:
        settings = self.database.get_setting("desktop_v2", {})
        models = settings.get("models", {})
        if provider_mode == "mock":
            scene = story = batch = image = MockAIProvider()
            mail = MockMailProvider(self.paths.root / "mock_outbox")
        else:
            qwen_key = self.credentials.get(CredentialName.QWEN_API_KEY)
            deepseek_key = self.credentials.get(CredentialName.DEEPSEEK_API_KEY)
            volcengine_key = self.credentials.get(CredentialName.VOLCENGINE_API_KEY)
            if not all((qwen_key, deepseek_key, volcengine_key)):
                raise RuntimeError("请先在设置页保存 OpenAI API Key")
            scene = QwenEvidenceProvider(
                api_key=qwen_key,
                settings=QwenSettings(
                    keyframe_model=models.get("scene", "qwen3.7-plus"),
                    omni_model=models.get("omni", "qwen3.5-omni-plus"),
                ),
            )
            batch = scene
            story = DeepSeekStoryProvider(
                api_key=deepseek_key,
                settings=DeepSeekSettings(daily_model=models.get("daily", "deepseek-v4-pro")),
            )
            image = SeedreamImageProvider(
                api_key=volcengine_key,
                settings=SeedreamSettings(
                    image_model=models.get("image", "doubao-seedream-5-0-pro")
                ),
            )
            smtp = settings.get("smtp", {})
            smtp_settings = SmtpSettings(
                host=smtp.get("host", ""),
                port=int(smtp.get("port", 465)),
                security=smtp.get("security", "ssl"),
                username=smtp.get("username", ""),
                sender=smtp.get("sender", ""),
                recipient=smtp.get("recipient", ""),
            )
            mail = SmtpMailProvider(
                smtp_settings, self.credentials.get(CredentialName.SMTP_PASSWORD) or ""
            )
        ffmpeg, ffprobe = self._ffmpeg_paths(settings.get("ffmpeg", ""))
        return DistillationPipeline(
            self.paths,
            self.database,
            scene,
            story,
            batch,
            image,
            mail,
            media_preprocessor=MediaPreprocessor(ffmpeg, ffprobe),
            motion_model_path=Path(settings["motion_model"]) if settings.get("motion_model") else None,
            progress=lambda stage, progress, message: self.events.put(
                ("progress", (stage, progress, message), None)
            ),
        )

    @staticmethod
    def _ffmpeg_paths(configured: str) -> tuple[Path | None, Path | None]:
        if not configured:
            return None, None
        path = Path(configured)
        if path.is_dir():
            return path / "ffmpeg.exe", path / "ffprobe.exe"
        return path, path.with_name("ffprobe.exe")

    def _legacy_avatar_references(self) -> list[Path]:
        value = self.database.get_setting("desktop_v2", {}).get("avatar", "")
        path = Path(value) if value else None
        return [path] if path and path.is_file() else []

    def _choose_avatar(self, file_dialog) -> None:
        selected, _ = file_dialog.getOpenFileName(
            self.window,
            "选择海报参考形象",
            filter="Images (*.jpg *.jpeg *.png *.webp)",
        )
        if selected:
            self.avatar_edit.setText(selected)
            self.avatar_edit.setCursorPosition(0)
            self._update_avatar_preview(Path(selected))

    def clear_avatar_profile(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        settings = self.database.get_setting("desktop_v2", {})
        settings["avatar"] = ""
        settings["avatar_description"] = ""
        self.database.set_setting("desktop_v2", settings)
        self.avatar_edit.clear()
        self.avatar_description_edit.clear()
        self.avatar_preview.clear()
        self.avatar_preview.setText("未设置参考形象，生成时由模型自主决定")
        QMessageBox.information(self.window, "参考形象", "参考形象设置已清空。")

    def save_avatar_profile(self) -> None:
        from PIL import Image
        from PySide6.QtWidgets import QMessageBox

        source = Path(self.avatar_edit.text().strip()) if self.avatar_edit.text().strip() else None
        description = self.avatar_description_edit.toPlainText().strip()
        try:
            style_id, style_name, style_prompt, custom_prompt = self._current_art_style()
        except ValueError as exc:
            QMessageBox.warning(self.window, "艺术风格", str(exc))
            return
        if source is not None and not source.is_file():
            QMessageBox.warning(self.window, "参考形象", "所选参考图片已经不存在，请重新选择。")
            return
        if source is not None and not description:
            QMessageBox.warning(self.window, "参考形象", "请填写一段简短的形象描述。")
            return
        try:
            settings = self.database.get_setting("desktop_v2", {})
            destination = source
            if source is not None:
                with Image.open(source) as image:
                    image.verify()
                profile_dir = self.paths.root / "profile"
                profile_dir.mkdir(parents=True, exist_ok=True)
                destination = profile_dir / ("reference" + source.suffix.lower())
                if source.resolve() != destination.resolve():
                    shutil.copy2(source, destination)
                settings["avatar"] = str(destination)
                settings["avatar_description"] = description
            settings["art_style_id"] = style_id
            settings["art_style_name"] = style_name
            settings["art_style_prompt"] = style_prompt
            settings["custom_art_style_prompt"] = custom_prompt
            self.database.set_setting("desktop_v2", settings)
            if destination is not None:
                self.avatar_edit.setText(str(destination))
                self.avatar_edit.setCursorPosition(0)
                self._update_avatar_preview(destination)
        except Exception as exc:
            QMessageBox.warning(self.window, "参考形象保存失败", str(exc))
            return
        saved = "参考形象、描述和艺术风格" if source is not None else "艺术风格"
        QMessageBox.information(self.window, "形象与风格", f"{saved}已保存在本机。")

    def _current_art_style(self) -> tuple[str, str, str, str]:
        style_id = str(self.art_style_combo.currentData() or DEFAULT_ART_STYLE_ID)
        custom_prompt = validate_custom_style_prompt(self.custom_style_edit.toPlainText())
        style_name, style_prompt = resolve_art_style_prompt(style_id, custom_prompt)
        return style_id, style_name, style_prompt, custom_prompt

    def _save_art_style_settings(self) -> tuple[str, str, str, str]:
        style_id, style_name, style_prompt, custom_prompt = self._current_art_style()
        settings = self.database.get_setting("desktop_v2", {})
        settings.update(
            {
                "art_style_id": style_id,
                "art_style_name": style_name,
                "art_style_prompt": style_prompt,
                "custom_art_style_prompt": custom_prompt,
            }
        )
        self.database.set_setting("desktop_v2", settings)
        return style_id, style_name, style_prompt, custom_prompt

    def _on_art_style_changed(self, _index: int | None = None) -> None:
        style_id = str(self.art_style_combo.currentData() or DEFAULT_ART_STYLE_ID)
        custom = style_id == CUSTOM_ART_STYLE_ID
        self.custom_style_edit.setVisible(custom)
        self.custom_style_counter.setVisible(custom)
        if custom:
            description = "用不超过 200 字定义独有的色彩、材质、笔触和情绪。系统仍会强制保持竖版、无文字与事实一致。"
        else:
            style = get_art_style(style_id)
            description = f"{style.description}\n{style.tagline}"
        self.art_style_description.setText(description)
        self.art_style_description.setStyleSheet(
            "background:#07131b; color:#d7d7d7; border:1px solid #17405b; "
            "border-radius:10px; padding:12px; font-weight:500;"
        )

    def _limit_custom_style_prompt(self) -> None:
        text = self.custom_style_edit.toPlainText()
        if len(text) > MAX_CUSTOM_STYLE_CHARS:
            self.custom_style_edit.blockSignals(True)
            self.custom_style_edit.setPlainText(text[:MAX_CUSTOM_STYLE_CHARS])
            cursor = self.custom_style_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.custom_style_edit.setTextCursor(cursor)
            self.custom_style_edit.blockSignals(False)
            text = text[:MAX_CUSTOM_STYLE_CHARS]
        self.custom_style_counter.setText(f"{len(text)}/{MAX_CUSTOM_STYLE_CHARS}")

    def test_current_art_style(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        job_id = self.style_test_job_combo.currentData()
        if not job_id:
            QMessageBox.information(self.window, "选择历史日报", "请先选择一条已经成功生成的历史日报。")
            return
        try:
            style_id, style_name, style_prompt, _custom = self._save_art_style_settings()
        except ValueError as exc:
            QMessageBox.warning(self.window, "艺术风格", str(exc))
            return
        self._set_busy(True)
        self.style_test_status.setText(f"正在用“{style_name}”生成 1K 试片…")
        self.style_preview_spinner_layer.show()
        self.style_preview_spinner.start()
        self.style_download_button.hide()
        self._start(
            "style_test",
            lambda: self._create_style_test_pipeline().regenerate_poster_only(
                str(job_id),
                self._avatar_references(),
                style_fingerprint=style_id + ":" + style_prompt,
            ),
        )

    def _update_style_preview(self, path: Path) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap

        pixmap = QPixmap(str(path))
        self.style_preview_spinner.stop()
        self.style_preview_spinner_layer.hide()
        if pixmap.isNull():
            self.style_preview.setText("图片已经生成，但预览加载失败")
            return
        self.style_preview_path = path
        self.style_preview.setPixmap(
            pixmap.scaled(
                self.style_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.style_download_button.show()

    def open_style_preview(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout

        path = self.style_preview_path
        if not path or not path.is_file():
            return
        dialog = QDialog(self.window)
        dialog.setWindowTitle("风格预览")
        dialog.resize(840, 900)
        layout = QVBoxLayout(dialog)
        image = QLabel()
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setPixmap(QPixmap(str(path)))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(image)
        layout.addWidget(scroll)
        dialog.exec()

    def download_style_preview(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        source = self.style_preview_path
        if not source or not source.is_file():
            return
        destination, _ = QFileDialog.getSaveFileName(
            self.window,
            "保存风格预览",
            source.name,
            "Images (*.jpg *.jpeg *.png *.webp)",
        )
        if not destination:
            return
        try:
            shutil.copy2(source, Path(destination))
        except Exception as exc:
            QMessageBox.warning(self.window, "保存失败", str(exc))
            return
        QMessageBox.information(self.window, "已保存", f"图片已保存到：{destination}")

    def _update_avatar_preview(self, path: Path) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.avatar_preview.setText("无法预览该图片")
            return
        self.avatar_preview.setPixmap(
            pixmap.scaled(
                self.avatar_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def save_cloud_settings(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        try:
            self.image_size_edit.setText(DEFAULT_POSTER_IMAGE_SIZE)
            _validate_base_url(self.qwen_base_url_edit.text(), "百炼")
            _validate_base_url(self.deepseek_base_url_edit.text(), "DeepSeek")
            _validate_base_url(self.volcengine_base_url_edit.text(), "火山方舟")
            if not self.resend_host_edit.text().strip():
                raise ValueError("Resend SMTP主机不能为空")
            _validate_image_size(self.image_size_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self.window, "设置无效", str(exc))
            return

        settings = self.database.get_setting("desktop_v2", {})
        settings.update(
            {
                "models": {
                    "scene": self.scene_model_edit.text().strip(),
                    "omni": self.omni_model_edit.text().strip(),
                    "daily": self.daily_model_edit.text().strip(),
                    "image": self.image_model_edit.text().strip(),
                    "image_size": self.image_size_edit.text().strip(),
                },
                "endpoints": {
                    "qwen": self.qwen_base_url_edit.text().strip(),
                    "deepseek": self.deepseek_base_url_edit.text().strip(),
                    "volcengine": self.volcengine_base_url_edit.text().strip(),
                },
                "resend": {
                    "host": self.resend_host_edit.text().strip(),
                    "port": self.resend_port_edit.value(),
                    "security": self.resend_security_combo.currentData(),
                    "sender": self.resend_sender_edit.text().strip(),
                    "recipient": self.resend_recipient_edit.text().strip(),
                },
            }
        )
        self.database.set_setting("desktop_v2", settings)
        self._refresh_simple_settings_summary(settings)
        try:
            for field, credential in (
                (self.api_key_edit, CredentialName.QWEN_API_KEY),
                (self.deepseek_key_edit, CredentialName.DEEPSEEK_API_KEY),
                (self.volcengine_key_edit, CredentialName.VOLCENGINE_API_KEY),
                (self.resend_key_edit, CredentialName.RESEND_API_KEY),
            ):
                if field.text():
                    self.credentials.set(credential, field.text())
        except Exception as exc:
            QMessageBox.warning(self.window, "凭据保存失败", str(exc))
            return
        QMessageBox.information(self.window, "设置", "模型、Endpoint和Resend设置已保存。")

    def save_recipient_setting(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        recipient = self.simple_recipient_edit.text().strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", recipient):
            QMessageBox.warning(self.window, "邮箱格式无效", "请输入完整的日报收件邮箱。")
            return
        settings = self.database.get_setting("desktop_v2", {})
        resend = dict(settings.get("resend", {}))
        resend["recipient"] = recipient
        settings["resend"] = resend
        self.database.set_setting("desktop_v2", settings)
        self.resend_recipient_edit.setText(recipient)
        self._refresh_simple_settings_summary(settings)
        QMessageBox.information(self.window, "收件地址", "日报收件地址已保存。")

    def _refresh_simple_settings_summary(self, settings: dict[str, Any]) -> None:
        models = settings.get("models", {})
        for key, label in self.model_summary_labels.items():
            defaults = {
                "scene": "qwen3.7-plus",
                "omni": "qwen3.5-omni-plus",
                "daily": "deepseek-v4-pro",
                "image": "doubao-seedream-5-0-pro",
            }
            label.setText(str(models.get(key, defaults[key])))
        recipient = str(settings.get("resend", {}).get("recipient", "")).strip()
        self.current_recipient_label.setText(recipient or "尚未设置")
        self.simple_recipient_edit.setText(recipient)

    def _load_cloud_settings(self) -> None:
        settings = self.database.get_setting("desktop_v2", {})
        models = settings.get("models", {})
        endpoints = settings.get("endpoints", {})
        resend = settings.get("resend", {})
        self.scene_model_edit.setText(models.get("scene", self.scene_model_edit.text()))
        self.omni_model_edit.setText(models.get("omni", self.omni_model_edit.text()))
        self.daily_model_edit.setText(models.get("daily", self.daily_model_edit.text()))
        self.image_model_edit.setText(models.get("image", self.image_model_edit.text()))
        # v2.1 intentionally migrates every older canvas setting to the fixed
        # 1K cost-safe output. The field remains visible for transparency.
        self.image_size_edit.setText(DEFAULT_POSTER_IMAGE_SIZE)
        self.qwen_base_url_edit.setText(endpoints.get("qwen", self.qwen_base_url_edit.text()))
        self.deepseek_base_url_edit.setText(endpoints.get("deepseek", self.deepseek_base_url_edit.text()))
        self.volcengine_base_url_edit.setText(endpoints.get("volcengine", self.volcengine_base_url_edit.text()))
        self.resend_host_edit.setText(resend.get("host", self.resend_host_edit.text()))
        self.resend_port_edit.setValue(int(resend.get("port", self.resend_port_edit.value())))
        index = self.resend_security_combo.findData(resend.get("security", "ssl"))
        if index >= 0:
            self.resend_security_combo.setCurrentIndex(index)
        self.resend_sender_edit.setText(resend.get("sender", ""))
        self.resend_recipient_edit.setText(resend.get("recipient", ""))
        self._refresh_simple_settings_summary(settings)
        for field, credential in (
            (self.api_key_edit, CredentialName.QWEN_API_KEY),
            (self.deepseek_key_edit, CredentialName.DEEPSEEK_API_KEY),
            (self.volcengine_key_edit, CredentialName.VOLCENGINE_API_KEY),
            (self.resend_key_edit, CredentialName.RESEND_API_KEY),
        ):
            value = self.credentials.get(credential)
            field.setText(value or "")

    def _load_avatar_profile(self) -> None:
        settings = self.database.get_setting("desktop_v2", {})
        self.avatar_edit.setText(settings.get("avatar", ""))
        self.avatar_edit.setCursorPosition(0)
        self.avatar_description_edit.setPlainText(settings.get("avatar_description", ""))
        path = Path(self.avatar_edit.text()) if self.avatar_edit.text() else None
        if path and path.is_file():
            self._update_avatar_preview(path)
        style_id = settings.get("art_style_id", DEFAULT_ART_STYLE_ID)
        index = self.art_style_combo.findData(style_id)
        self.art_style_combo.setCurrentIndex(index if index >= 0 else 0)
        self.custom_style_edit.setPlainText(settings.get("custom_art_style_prompt", ""))
        self._on_art_style_changed()

    def _create_pipeline(
        self,
        provider_mode: str,
        recipient_override: str | None = None,
        progress_callback: Callable[[JobStage, float, str], None] | None = None,
    ) -> DistillationPipeline:
        settings = self.database.get_setting("desktop_v2", {})
        models = settings.get("models", {})
        endpoints = settings.get("endpoints", {})
        if provider_mode == "mock":
            scene = story = batch = image = MockAIProvider()
            mail = MockMailProvider(self.paths.root / "mock_outbox")
        else:
            _validate_base_url(endpoints.get("qwen", QwenSettings.base_url), "百炼")
            _validate_base_url(endpoints.get("deepseek", DeepSeekSettings.base_url), "DeepSeek")
            _validate_base_url(endpoints.get("volcengine", SeedreamSettings.base_url), "火山方舟")
            poster_image_size = _validate_image_size(DEFAULT_POSTER_IMAGE_SIZE)
            qwen_key = self.credentials.get(CredentialName.QWEN_API_KEY)
            deepseek_key = self.credentials.get(CredentialName.DEEPSEEK_API_KEY)
            volcengine_key = self.credentials.get(CredentialName.VOLCENGINE_API_KEY)
            resend_key = self.credentials.get(CredentialName.RESEND_API_KEY)
            if not all((qwen_key, deepseek_key, volcengine_key, resend_key)):
                raise RuntimeError("请先在设置页保存百炼、DeepSeek、火山方舟和Resend API Key")
            scene = QwenEvidenceProvider(
                api_key=qwen_key,
                settings=QwenSettings(
                    base_url=endpoints.get("qwen", QwenSettings.base_url),
                    keyframe_model=models.get("scene", "qwen3.7-plus"),
                    omni_model=models.get("omni", "qwen3.5-omni-plus"),
                ),
            )
            batch = scene
            story = DeepSeekStoryProvider(
                api_key=deepseek_key,
                settings=DeepSeekSettings(
                    base_url=endpoints.get("deepseek", DeepSeekSettings.base_url),
                    daily_model=models.get("daily", "deepseek-v4-pro"),
                ),
            )
            image = SeedreamImageProvider(
                api_key=volcengine_key,
                settings=SeedreamSettings(
                    base_url=endpoints.get("volcengine", SeedreamSettings.base_url),
                    image_model=models.get("image", "doubao-seedream-5-0-pro"),
                    image_size=poster_image_size,
                    character_description=settings.get("avatar_description", ""),
                    art_style_name=settings.get("art_style_name", get_art_style(DEFAULT_ART_STYLE_ID).name),
                    art_style_prompt=settings.get("art_style_prompt", get_art_style(DEFAULT_ART_STYLE_ID).prompt),
                ),
            )
            resend = settings.get("resend", {})
            mail = SmtpMailProvider(
                SmtpSettings(
                    host=resend.get("host", "smtp.resend.com"),
                    port=int(resend.get("port", 465)),
                    security=resend.get("security", "ssl"),
                    username="resend",
                    sender=resend.get("sender", ""),
                    recipient=recipient_override or resend.get("recipient", ""),
                ),
                resend_key,
            )
        ffmpeg, ffprobe = bundled_ffmpeg_paths()
        return DistillationPipeline(
            self.paths,
            self.database,
            scene,
            story,
            batch,
            image,
            mail,
            media_preprocessor=MediaPreprocessor(ffmpeg, ffprobe),
            motion_model_path=None,
            progress=progress_callback
            or (
                lambda stage, progress, message: self.events.put(
                    ("progress", (stage, progress, message), None)
                )
            ),
        )

    def _create_style_test_pipeline(self) -> DistillationPipeline:
        settings = self.database.get_setting("desktop_v2", {})
        models = settings.get("models", {})
        endpoints = settings.get("endpoints", {})
        base_url = _validate_base_url(
            endpoints.get("volcengine", SeedreamSettings.base_url), "火山方舟"
        )
        image_size = _validate_image_size(DEFAULT_POSTER_IMAGE_SIZE)
        volcengine_key = self.credentials.get(CredentialName.VOLCENGINE_API_KEY)
        if not volcengine_key:
            raise RuntimeError("请先在设置页保存火山方舟 API Key")
        mock = MockAIProvider()
        image = SeedreamImageProvider(
            api_key=volcengine_key,
            settings=SeedreamSettings(
                base_url=base_url,
                image_model=models.get("image", "doubao-seedream-5-0-pro"),
                image_size=image_size,
                character_description=settings.get("avatar_description", ""),
                art_style_name=settings.get("art_style_name", get_art_style(DEFAULT_ART_STYLE_ID).name),
                art_style_prompt=settings.get("art_style_prompt", get_art_style(DEFAULT_ART_STYLE_ID).prompt),
            ),
        )
        ffmpeg, ffprobe = bundled_ffmpeg_paths()
        return DistillationPipeline(
            self.paths,
            self.database,
            mock,
            mock,
            mock,
            image,
            MockMailProvider(self.paths.root / "mock_outbox"),
            media_preprocessor=MediaPreprocessor(ffmpeg, ffprobe),
            motion_model_path=None,
        )

    def _avatar_references(self) -> list[Path]:
        value = self.database.get_setting("desktop_v2", {}).get("avatar", "")
        path = Path(value) if value else None
        return [path] if path and path.is_file() else []

    def _selected_date(self) -> date:
        value = self.target_date.date()
        return date(value.year(), value.month(), value.day())

    def _selected_job_id(self, list_widget=None) -> str | None:
        from PySide6.QtCore import Qt

        source = list_widget or self.history_list
        item = source.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _select_tab(self, title: str) -> None:
        title = {
            "用户指引": "开始",
            "今日记录": "记录",
            "蒸馏进度": "生成",
            "报告历史": "回忆",
            "参考形象": "形象与风格",
        }.get(title, title)
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == title:
                self.tabs.setCurrentIndex(index)
                return

    def _start(self, name: str, operation: Callable[[], Any]) -> None:
        if name in {"distill", "cleanup_retry", "manual_regenerate", "manual_resend"}:
            self.distill_log.appendPlainText(f"{name}...")
        elif name == "style_test":
            self.style_test_status.setText("正在连接 Seedream，仅生成最终海报试片…")
        else:
            self._device_log(f"{name}...")
        self.worker.run(name, operation)

    def _drain_events(self) -> None:
        while True:
            try:
                name, result, error = self.events.get_nowait()
            except queue.Empty:
                return
            if name == "guided_sync_progress":
                progress, message = result
                self.guided_sync_progress.setValue(max(3, min(96, round(float(progress) * 96))))
                self.guided_sync_subtitle.setText(str(message))
                continue
            if name == "guided_batch_progress":
                index, total, stage, progress, message = result
                day_percent = self._guided_progress_value(stage, progress)
                overall = round(((index + day_percent / 100.0) / max(1, total)) * 100)
                self.guided_distill_progress.setValue(max(1, min(99, overall)))
                self._update_guided_eta(overall)
                self.guided_live_progress.appendPlainText(
                    f"第 {index + 1}/{total} 天 · {stage.value} · {message}"
                )
                continue
            if name == "guided_day_start":
                index, total, day_label = result
                self.guided_live_progress.appendPlainText(
                    f"开始处理 {day_label}（{index + 1}/{total}）"
                )
                continue
            if name == "guided_day_complete":
                completed, total, day_label = result
                self.guided_completed_days = completed
                self.guided_live_progress.appendPlainText(
                    f"{day_label} 已完成并发送（{completed}/{total}）"
                )
                continue
            if name == "progress":
                stage, progress, message = result
                if self.guided_phase == "syncing" and stage == JobStage.IMPORTING:
                    self.guided_sync_progress.setValue(max(5, min(94, round(progress * 94))))
                    self.guided_sync_subtitle.setText(message)
                elif self.guided_phase == "distilling":
                    value = self._guided_progress_value(stage, progress)
                    self.guided_distill_progress.setValue(value)
                    self._update_guided_eta(value)
                    self.guided_live_progress.appendPlainText(f"{stage.value}  ·  {message}")
                self.stage_label.setText(f"{stage.value} · {message}")
                self.progress_bar.setValue(round(progress * 1000))
                self.distill_log.appendPlainText(f"[{stage.value}] {message}")
                continue
            if name == "guided_message":
                if self.guided_phase == "syncing":
                    self.guided_sync_subtitle.setText(str(result))
                elif self.guided_phase == "distilling":
                    self.guided_live_progress.appendPlainText(str(result))
                continue
            if name == "progress_message":
                self.distill_log.appendPlainText(str(result))
                continue
            if error:
                if name == "guided_discover":
                    self.guided_discovery_inflight = False
                    if self.guided_phase == "discovering":
                        self.guided_connection_status.setText("正在寻找设备")
                elif name == "guided_sync":
                    self.guided_phase = "sync_error"
                    self.guided_operation_active = False
                    self.hero_start_button.setDisabled(False)
                    if self.guided_backgrounded:
                        self.guided_home_status.setText(f"后台同步失败：{error}")
                    else:
                        self.guided_sync_subtitle.setText(f"同步失败：{error}")
                        self.guided_sync_actions.show()
                        self.guided_select_all_button.hide()
                        self.guided_start_distill_button.hide()
                        self.guided_resync_button.setText("重新同步")
                elif name == "guided_distill":
                    self.guided_phase = "distill_error"
                    self.guided_operation_active = False
                    self.hero_start_button.setDisabled(False)
                    partial = (
                        f"已完成 {self.guided_completed_days}/{self.guided_total_days} 天。"
                        if self.guided_total_days > 1
                        else ""
                    )
                    if self.guided_backgrounded:
                        self.guided_home_status.setText(f"后台蒸馏失败：{partial}{error}")
                    else:
                        self.guided_live_progress.appendPlainText(f"蒸馏失败：{partial}{error}")
                        self.guided_eta_label.setText("任务未完成，可返回首页")
                    self._set_busy(False)
                    self.refresh_history()
                elif name in {"distill", "cleanup_retry", "manual_regenerate", "manual_resend"}:
                    self.stage_label.setText(f"失败 · {error}")
                    self.distill_log.appendPlainText(f"{name} 失败：{error}")
                    self._set_busy(False)
                    self.refresh_history()
                elif name == "style_test":
                    self.style_preview_spinner.stop()
                    self.style_preview_spinner_layer.hide()
                    self.style_test_status.setText(f"生成失败：{error}")
                    self._set_busy(False)
                else:
                    self._device_log(f"{name} 失败：{error}")
                continue
            self._handle_result(name, result)

    def _handle_result(self, name: str, result: Any) -> None:
        if name == "guided_discover":
            from PySide6.QtCore import QTimer

            self.guided_discovery_inflight = False
            if self.guided_phase != "discovering":
                return
            if not result:
                if time.monotonic() >= self.guided_discovery_deadline:
                    self._guided_discovery_timeout()
                return
            port, status = result
            self.discovery_timer.stop()
            self.refresh_ports()
            self._select_port(port.device)
            self._apply_status(status, port)
            self.connect_spinner.stop()
            self.connect_success_icon.show()
            self.guided_connection_status.setText("设备已连接")
            QTimer.singleShot(700, self._guided_start_sync)
        elif name == "guided_sync":
            from PySide6.QtCore import QSignalBlocker, Qt
            from PySide6.QtWidgets import QListWidgetItem

            workflow, inventory = result
            self.guided_operation_active = False
            self.guided_sync_workflow = workflow
            self.guided_sync_inventory = inventory
            self.guided_phase = "synced"
            self.guided_sync_progress.setValue(100)
            blocker = QSignalBlocker(self.guided_records_list)
            self.guided_records_list.clear()
            single_day = len(inventory.days) == 1
            for synced_date in inventory.days:
                changed = len(synced_date.changed_record_names)
                detail = (
                    f"新增或更新 {changed} 条"
                    if changed
                    else "本地缓存已是最新"
                )
                item = QListWidgetItem(
                    f"{synced_date.target_date.isoformat()}   ·   "
                    f"{len(synced_date.record_names)} 条记录   ·   {detail}"
                )
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                )
                item.setData(Qt.ItemDataRole.UserRole, synced_date.target_date.isoformat())
                item.setCheckState(
                    Qt.CheckState.Checked if single_day else Qt.CheckState.Unchecked
                )
                self.guided_records_list.addItem(item)
            del blocker
            self.guided_records_list.show()
            if single_day:
                self.guided_sync_subtitle.setText(
                    f"已同步 {inventory.total_records} 条记录，将在 3 秒后自动开始蒸馏。"
                )
            else:
                self.guided_sync_subtitle.setText(
                    f"发现 {len(inventory.days)} 天、共 {inventory.total_records} 条记录。"
                    "请选择一个或多个日期，也可以全选。"
                )
            self.guided_start_distill_button.show()
            self.guided_select_all_button.setVisible(not single_day)
            self.guided_resync_button.setText("重新同步")
            self.guided_sync_actions.show()
            self._guided_date_selection_changed()
            if single_day and not self.guided_backgrounded:
                self.guided_countdown = 3
                self.guided_start_distill_button.setText("开始蒸馏（3s）")
                self.sync_countdown_timer.start()
            if self.guided_backgrounded:
                self.hero_start_button.setDisabled(False)
                self.guided_home_status.setText("后台同步已完成。点击开始可重新读取并选择蒸馏日期。")
        elif name == "guided_distill":
            self.guided_phase = "completed"
            self.guided_operation_active = False
            self.guided_distill_progress.setValue(100)
            self.guided_eta_label.setText("已完成")
            result_count = len(result) if isinstance(result, list) else 1
            self.guided_live_progress.appendPlainText(
                f"{result_count} 天的每日蒸馏已完成，邮件服务器均已接受。"
            )
            recipient = str(
                self.database.get_setting("desktop_v2", {}).get("resend", {}).get("recipient", "")
            ).strip()
            self.guided_complete_email.setText(
                (
                    f"已将 {result_count} 份日报发送至：{recipient}"
                    if result_count > 1 and recipient
                    else f"已发送至指定邮箱：{recipient}"
                    if recipient
                    else "邮件已发送至指定收件地址"
                )
            )
            self.hero_start_button.setDisabled(False)
            if self.guided_backgrounded:
                self.guided_home_status.setText(
                    f"后台蒸馏已完成，{result_count} 份日报已发送。"
                )
            else:
                self.home_stack.setCurrentIndex(4)
            self._set_busy(False)
            self.refresh_history()
        elif name == "auto_find":
            if not result:
                self._device_log("没有找到 Day Distiller 协议串口。")
                return
            port, status = result
            self.refresh_ports()
            self._select_port(port.device)
            self._close_device()
            self.device = UsbLinkDevice(port.device)
            self._apply_status(status, port)
            self._device_log(f"已连接 {port.device}。")
        elif name in {"connect", "status", "exit_msc"}:
            self._apply_status(result, self._selected_port_candidate())
            self._device_log(f"{name} 成功。")
            if name == "exit_msc":
                self._close_device()
        elif name in {"v2_dates", "v2_exports", "v2_end_session"}:
            self._device_log(
                f"{name} 成功：{json.dumps(result, ensure_ascii=False, separators=(',', ':'))}"
            )
            if name == "v2_end_session":
                self._close_device()
        elif name == "enter_msc":
            port = result.get("port")
            self._apply_status(result.get("status", {}), port)
            drive = result.get("drive")
            if drive:
                self.last_drive_letter = drive.letter
                self.drive_label.setText(f"{drive.root} {drive.label}".strip())
                self.source_edit.setText(drive.root)
                self._device_log(f"检测到 MSC 卷 {drive.root}")
            if port:
                self.refresh_ports()
                self._select_port(port.device)
        elif name == "eject":
            self._device_log(f"已弹出 {result}:，正在退出 MSC。")
            self.last_drive_letter = None
            self.drive_label.setText("-")
            self._start("exit_msc", lambda: self._manual_exit_msc(force=False))
        elif name == "distill":
            self.stage_label.setText(f"完成 · 清理状态 {result.cleanup_state}")
            self.progress_bar.setValue(1000)
            self.distill_log.appendPlainText(f"日报：{result.rendered.html_path}")
            self._set_busy(False)
            self.refresh_history()
        elif name == "cleanup_retry":
            self.distill_log.appendPlainText(f"设备清理完成：{len(result)} 个目录")
            self._set_busy(False)
            self.refresh_history()
        elif name == "manual_regenerate":
            _report, rendered = result
            self.distill_log.appendPlainText(f"已重新生成：{rendered.html_path}")
            self._set_busy(False)
            self.refresh_history()
        elif name == "manual_resend":
            self.distill_log.appendPlainText(f"邮件服务器已接受手动发送：{result.html_path}")
            self._set_busy(False)
            self.refresh_history()
        elif name == "style_test":
            path = Path(result)
            self._update_style_preview(path)
            self.style_test_status.setText(f"试片已生成 · {path.name}（未覆盖原日报，也未发送邮件）")
            self._set_busy(False)

    def _require_device(self) -> UsbLinkDevice:
        if self.device is None:
            port = self.port_combo.currentData()
            if not port:
                raise RuntimeError("没有选择协议 CDC 串口")
            self.device = UsbLinkDevice(port)
            self.device.open()
        return self.device

    def _manual_exit_msc(self, force: bool) -> dict[str, Any]:
        next_mode = (
            "maintenance"
            if self.connected_device_profile and self.connected_device_profile.is_firmware_v2
            else None
        )
        return self._require_device().exit_msc(force=force, next_mode=next_mode)

    def _close_device(self) -> None:
        if self.device:
            self.device.close()
            self.device = None

    def _select_port(self, port_name: str) -> None:
        index = self.port_combo.findData(port_name)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)

    def _selected_port_candidate(self) -> PortCandidate | None:
        selected = str(self.port_combo.currentData() or "")
        return next((port for port in self.ports if port.device == selected), None)

    def _apply_status(
        self,
        status: dict[str, Any],
        port: PortCandidate | None = None,
    ) -> None:
        storage = status.get("storage") if isinstance(status.get("storage"), dict) else {}
        profile = device_profile(status, port or self._selected_port_candidate())
        self.connected_device_profile = profile
        self.connection_label.setText("已连接")
        self.firmware_version_label.setText(profile.display_firmware)
        self.device_serial_label.setText(profile.serial_number)
        self.protocol_adapter_label.setText(profile.adapter_name)
        self.mode_label.setText(str(status.get("mode", "-")))
        battery = status.get("battery") if isinstance(status.get("battery"), dict) else None
        rtc = status.get("rtc") if isinstance(status.get("rtc"), dict) else None
        self.battery_status_label.setText(
            (
                f"{battery.get('soc_percent', battery.get('soc', '-'))}% · "
                f"{battery.get('voltage_v', battery.get('voltage', '-'))} V"
            )
            if battery
            else "当前固件未通过 USB 提供"
        )
        self.rtc_status_label.setText(
            (
                f"{'有效' if rtc.get('valid') else '无效'} · "
                f"{rtc.get('iso8601', rtc.get('iso', '-'))}"
            )
            if rtc
            else "当前固件未通过 USB 提供"
        )
        self.storage_label.setText(
            ", ".join(
                f"{key}={storage.get(key)}" for key in ("ready", "mounted", "usb_exposed", "read_only", "ejected")
                if key in storage
            )
            or "-"
        )
        self._set_device_feature_availability(profile)
        info = (
            f"固件 {profile.display_firmware}  ·  序列号 {profile.serial_number}  ·  "
            f"{profile.adapter_name}"
        )
        self.guided_device_info.setText(info)
        self.guided_sync_device_info.setText(info)
        self.guided_device_info.show()
        self.guided_sync_device_info.show()

    def _set_device_feature_availability(self, profile: DeviceProfile) -> None:
        enabled = profile.is_firmware_v2
        self.v2_dates_button.setEnabled(enabled and "list_record_dates" in profile.capabilities)
        self.v2_exports_button.setEnabled(enabled and "get_export_status" in profile.capabilities)
        self.v2_end_session_button.setEnabled(enabled and "end_session" in profile.capabilities)
        rw_index = self.access_combo.findData("rw")
        rw_item = self.access_combo.model().item(rw_index) if rw_index >= 0 else None
        rw_supported = not enabled or "msc_rw" in profile.capabilities
        if rw_item is not None:
            rw_item.setEnabled(rw_supported)
        if enabled and rw_supported:
            self.access_combo.setToolTip(
                "手动调试可选择只读或读写；一键同步和事务化导出仍始终使用只读 MSC。"
            )
        elif enabled:
            self.access_combo.setCurrentIndex(self.access_combo.findData("ro"))
            self.access_combo.setToolTip("当前固件未声明 msc_rw 能力，仅支持只读 MSC。")
        else:
            self.access_combo.setToolTip("可选择只读或读写 MSC；修改设备文件前请确认已有备份。")

    def _set_busy(self, busy: bool) -> None:
        self.start_folder_button.setDisabled(busy)
        self.start_device_button.setDisabled(busy)
        self.retry_job_button.setDisabled(busy)
        self.retry_cleanup_button.setDisabled(busy)
        self.edit_report_button.setDisabled(busy)
        self.regenerate_button.setDisabled(busy)
        self.resend_button.setDisabled(busy)
        self.resend_other_button.setDisabled(busy)
        self.dev_resend_button.setDisabled(busy)
        if hasattr(self, "style_test_button"):
            self.style_test_button.setDisabled(busy)

    def _device_log(self, message: str) -> None:
        self.device_log.appendPlainText(message)


if __name__ == "__main__":
    raise SystemExit(main())
