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
from .device import PortCandidate, UsbLinkDevice, find_device, list_serial_ports
from .device_workflow import LegacyDeviceWorkflow
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
from .resources import bundled_ffmpeg_paths
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


def main() -> int:
    from PySide6.QtWidgets import QApplication

    application = QApplication([])
    application.setApplicationName("Day Distiller v2")
    application.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return application.exec()


class MainWindow:
    def __init__(self) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMainWindow

        self.window = QMainWindow()
        self.window.setWindowTitle("Day Distiller · 把今天变成一张值得收藏的海报")
        self.window.resize(1280, 820)
        self.window.setMinimumSize(1060, 700)
        self.window.setStyleSheet(APP_STYLESHEET)
        self.paths = AppPaths.default().ensure()
        self.database = JobDatabase(self.paths.database)
        self.credentials = CredentialStore()
        self.events: queue.Queue[tuple[str, Any, Exception | None]] = queue.Queue()
        self.worker = Worker(self.events.put)
        self.device: UsbLinkDevice | None = None
        self.ports: list[PortCandidate] = []
        self.last_drive_letter: str | None = None
        self.active_job_id: str | None = None
        self.scanned_source_root: Path | None = None
        self.scanned_target_date: date | None = None
        self.scanned_record_count = 0

        self._build_ui()
        self._load_cloud_settings()
        self._load_avatar_profile()
        self.refresh_ports()
        self.refresh_history()

        self.timer = QTimer()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._drain_events)
        self.timer.start()

    def show(self) -> None:
        self.window.show()

    def _build_ui(self) -> None:
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.West)
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._guide_page(), "开始")
        self.tabs.addTab(self._device_page(), "设备")
        self.tabs.addTab(self._records_page(), "记录")
        self.tabs.addTab(self._distillation_page(), "生成")
        self.tabs.addTab(self._history_page(), "回忆")
        self.tabs.addTab(self._avatar_page(), "形象与风格")
        self.tabs.addTab(self._settings_page_v2(), "设置")

        root = QWidget()
        root.setObjectName("appRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        header = QWidget()
        header.setStyleSheet("background:#FFFFFF; border-bottom:1px solid #E9EAF0;")
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
        cost_chip = QLabel("1K 海报 · 低于 100 万像素")
        cost_chip.setProperty("chip", True)
        header_layout.addWidget(cost_chip)
        root_layout.addWidget(header)
        root_layout.addWidget(self.tabs, 1)
        self.window.setCentralWidget(root)

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
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)
        layout.addWidget(
            self._page_header(
                "今天，值得被好好记住",
                "连接设备、选择当天记录，然后让 AI 从所有素材中找到真正重要的 2–3 个 Moments。",
            )
        )

        steps = QHBoxLayout()
        steps.setSpacing(14)
        for number, title, copy, color in (
            ("1", "连接设备", "回家后唤醒设备并连接电脑。", "#6C5CE7"),
            ("2", "确认记录", "扫描今天，快速确认素材数量。", "#FF6B8A"),
            ("3", "生成海报", "一键筛选、分析、创作并发送。", "#00BFA6"),
        ):
            card = QGroupBox()
            card_layout = QVBoxLayout(card)
            badge = QLabel(number)
            badge.setFixedSize(38, 38)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                f"background:{color}; color:white; border-radius:19px; font-size:18px; font-weight:700;"
            )
            heading = QLabel(title)
            heading.setStyleSheet("font-size:18px; font-weight:700;")
            description = QLabel(copy)
            description.setProperty("muted", True)
            description.setWordWrap(True)
            card_layout.addWidget(badge)
            card_layout.addWidget(heading)
            card_layout.addWidget(description)
            card_layout.addStretch(1)
            steps.addWidget(card, 1)
        layout.addLayout(steps)

        action_row = QHBoxLayout()
        begin = self._mark_button(QPushButton("开始整理今天"))
        begin.clicked.connect(lambda: self._select_tab("记录"))
        go_settings = QPushButton("首次使用？完成配置")
        go_settings.clicked.connect(lambda: self.tabs.setCurrentIndex(self.tabs.count() - 1))
        action_row.addWidget(begin)
        action_row.addWidget(go_settings)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        privacy = QLabel(
            "隐私与费用  ·  IMU 与地点记忆只在本机处理；只有关键帧、音频和必要证据会按流程发送给已配置模型。"
            "最终海报固定为 864×1152（约 99.5 万像素）。FFmpeg 与 IMU 模型已经内置。"
        )
        privacy.setWordWrap(True)
        privacy.setProperty("muted", True)
        layout.addWidget(privacy)
        layout.addStretch(1)
        return page

    def _avatar_page(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QComboBox,
            QFileDialog,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
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
            "border:1px dashed #C8CDDA; border-radius:16px; background:#F8F9FC; color:#8B92A5;"
        )
        self.avatar_edit = QLineEdit()
        self.avatar_edit.setReadOnly(True)
        self.avatar_edit.setPlaceholderText("可上传 AI 形象，也可上传自己的实拍参考图")
        self.avatar_description_edit = QPlainTextEdit()
        self.avatar_description_edit.setPlaceholderText("简短描述发型、衣着、配饰和希望保持的角色特征")
        self.avatar_description_edit.setMaximumHeight(92)
        choose = QPushButton("选择参考形象")
        choose.clicked.connect(lambda: self._choose_avatar(QFileDialog))
        save = self._mark_button(QPushButton("保存形象与风格"))
        save.clicked.connect(self.save_avatar_profile)
        avatar_layout.addWidget(self.avatar_preview, 1)
        avatar_layout.addWidget(self.avatar_edit)
        avatar_layout.addWidget(self.avatar_description_edit)
        avatar_actions = QHBoxLayout()
        avatar_actions.addWidget(choose)
        avatar_actions.addWidget(save)
        avatar_layout.addLayout(avatar_actions)
        columns.addWidget(avatar_card, 1)

        style_card = QGroupBox("海报艺术风格")
        style_layout = QVBoxLayout(style_card)
        self.art_style_combo = QComboBox()
        for style in ART_STYLES:
            self.art_style_combo.addItem(f"{style.name}  ·  {style.tagline}", style.id)
        self.art_style_combo.addItem("自定义风格  ·  用自己的 200 字提示词", CUSTOM_ART_STYLE_ID)
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

        history_label = QLabel("用一条成功记录测试当前风格")
        history_label.setStyleSheet("font-weight:650; margin-top:8px;")
        self.style_test_job_combo = QComboBox()
        self.style_test_job_combo.setPlaceholderText("选择历史日报")
        self.style_test_button = self._mark_button(
            QPushButton("只生成一张风格试片（调用 1 次 Seedream）"), "accent"
        )
        self.style_test_button.clicked.connect(self.test_current_art_style)
        self.style_test_status = QLabel("不会重新分析视频、音频或 IMU，也不会发送邮件。")
        self.style_test_status.setProperty("muted", True)
        self.style_test_status.setWordWrap(True)
        self.style_preview = QLabel("新风格试片会显示在这里")
        self.style_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.style_preview.setMinimumHeight(240)
        self.style_preview.setStyleSheet(
            "border:1px dashed #C8CDDA; border-radius:16px; background:#F8F9FC; color:#8B92A5;"
        )
        style_layout.addWidget(self.art_style_combo)
        style_layout.addWidget(self.art_style_description)
        style_layout.addWidget(self.custom_style_edit)
        style_layout.addWidget(self.custom_style_counter)
        style_layout.addWidget(history_label)
        style_layout.addWidget(self.style_test_job_combo)
        style_layout.addWidget(self.style_test_button)
        style_layout.addWidget(self.style_test_status)
        style_layout.addWidget(self.style_preview, 1)
        columns.addWidget(style_card, 1)
        layout.addLayout(columns, 1)
        self._on_art_style_changed()
        return page

    def _settings_page_v2(self):
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
                "服务设置",
                "只需首次填写。密钥保存在 Windows 凭据管理器，并按你的要求在此页明文显示。",
            )
        )
        notice = QLabel(
            "安全提醒 · 请勿截图或向他人展示此页面。Base URL、模型名和邮件参数保存在本地 SQLite。"
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(
            "background:#FFF2D9; color:#7A4A00; border-radius:12px; padding:11px; font-weight:600;"
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
        self.refresh_data_usage()
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
        for index, (label, value) in enumerate(
            (("连接", self.connection_label), ("模式", self.mode_label), ("TF 卡", self.storage_label), ("Windows 盘符", self.drive_label))
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
        self.device_log = QPlainTextEdit()
        self.device_log.setReadOnly(True)
        layout.addWidget(self.device_log, 1)

        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self.connect_selected)
        self.auto_button.clicked.connect(self.auto_find)
        self.status_button.clicked.connect(lambda: self._start("status", lambda: self._require_device().get_status()))
        self.enter_button.clicked.connect(self.enter_msc)
        self.eject_button.clicked.connect(self.eject_and_exit)
        self.force_exit_button.clicked.connect(lambda: self._start("exit_msc", lambda: self._require_device().exit_msc(force=True)))
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
        self.stage_label.setStyleSheet("font-size:18px; font-weight:700; color:#312A63;")
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
        layout.addWidget(
            self._page_header("我的回忆", "打开、微调或再次发送已经完成的日报；失败任务也可以从这里恢复。")
        )
        row = QHBoxLayout()
        refresh = QPushButton("刷新")
        self.open_report_button = self._mark_button(QPushButton("打开日报"))
        self.edit_report_button = QPushButton("修改地点与文字")
        self.regenerate_button = QPushButton("重新编排并生成")
        self.resend_button = QPushButton("手动再次发送")
        self.retry_job_button = QPushButton("重试失败任务")
        self.retry_cleanup_button = QPushButton("重试设备清理")
        for widget in (
            refresh,
            self.open_report_button,
            self.edit_report_button,
            self.regenerate_button,
            self.resend_button,
            self.retry_job_button,
            self.retry_cleanup_button,
        ):
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
        self.history_list = QListWidget()
        layout.addWidget(self.history_list, 1)
        refresh.clicked.connect(self.refresh_history)
        self.open_report_button.clicked.connect(self.open_selected_report)
        self.edit_report_button.clicked.connect(self.edit_selected_report)
        self.regenerate_button.clicked.connect(self.regenerate_selected_report)
        self.resend_button.clicked.connect(self.resend_selected_report)
        self.retry_job_button.clicked.connect(self.retry_selected_job)
        self.retry_cleanup_button.clicked.connect(self.retry_selected_cleanup)
        self.history_list.itemDoubleClicked.connect(lambda _item: self.open_selected_report())
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
            self.port_combo.addItem(
                f"{port.device} · {port.description}{' · 推荐' if port.likely else ''}", port.device
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
            if report_row is not None and hasattr(self, "style_test_job_combo"):
                self.style_test_job_combo.addItem(
                    f"{job.target_date.isoformat()}  ·  {report_title or '已完成日报'}", job.id
                )
        if hasattr(self, "data_usage_label"):
            self.refresh_data_usage()

    def open_selected_report(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        job_id = self._selected_job_id()
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

    def resend_selected_report(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        provider_mode = self.database.get_job(job_id).provider_mode
        self._set_busy(True)
        self._start("manual_resend", lambda: self._create_pipeline(provider_mode).resend(job_id))

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
            self._update_avatar_preview(Path(selected))

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
            accent = "#6C5CE7"
        else:
            style = get_art_style(style_id)
            description = f"{style.description}\n{style.tagline}"
            accent = style.accent
        self.art_style_description.setText(description)
        self.art_style_description.setStyleSheet(
            f"background:#F7F5FF; color:#30394D; border:2px solid {accent}; "
            "border-radius:13px; padding:12px; font-weight:600;"
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
        if pixmap.isNull():
            self.style_preview.setText("图片已经生成，但预览加载失败")
            return
        self.style_preview.setPixmap(
            pixmap.scaled(
                self.style_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

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
        self.avatar_description_edit.setPlainText(settings.get("avatar_description", ""))
        path = Path(self.avatar_edit.text()) if self.avatar_edit.text() else None
        if path and path.is_file():
            self._update_avatar_preview(path)
        style_id = settings.get("art_style_id", DEFAULT_ART_STYLE_ID)
        index = self.art_style_combo.findData(style_id)
        self.art_style_combo.setCurrentIndex(index if index >= 0 else 0)
        self.custom_style_edit.setPlainText(settings.get("custom_art_style_prompt", ""))
        self._on_art_style_changed()

    def _create_pipeline(self, provider_mode: str) -> DistillationPipeline:
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
                    recipient=resend.get("recipient", ""),
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
            progress=lambda stage, progress, message: self.events.put(
                ("progress", (stage, progress, message), None)
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

    def _selected_job_id(self) -> str | None:
        from PySide6.QtCore import Qt

        item = self.history_list.currentItem()
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
            if name == "progress":
                stage, progress, message = result
                self.stage_label.setText(f"{stage.value} · {message}")
                self.progress_bar.setValue(round(progress * 1000))
                self.distill_log.appendPlainText(f"[{stage.value}] {message}")
                continue
            if name == "progress_message":
                self.distill_log.appendPlainText(str(result))
                continue
            if error:
                if name in {"distill", "cleanup_retry", "manual_regenerate", "manual_resend"}:
                    self.stage_label.setText(f"失败 · {error}")
                    self.distill_log.appendPlainText(f"{name} 失败：{error}")
                    self._set_busy(False)
                    self.refresh_history()
                elif name == "style_test":
                    self.style_test_status.setText(f"生成失败：{error}")
                    self._set_busy(False)
                else:
                    self._device_log(f"{name} 失败：{error}")
                continue
            self._handle_result(name, result)

    def _handle_result(self, name: str, result: Any) -> None:
        if name == "auto_find":
            if not result:
                self._device_log("没有找到 Day Distiller 协议串口。")
                return
            port, status = result
            self.refresh_ports()
            self._select_port(port.device)
            self._close_device()
            self.device = UsbLinkDevice(port.device)
            self._apply_status(status)
            self._device_log(f"已连接 {port.device}。")
        elif name in {"connect", "status", "exit_msc"}:
            self._apply_status(result)
            self._device_log(f"{name} 成功。")
            if name == "exit_msc":
                self._close_device()
        elif name == "enter_msc":
            self._apply_status(result.get("status", {}))
            drive = result.get("drive")
            if drive:
                self.last_drive_letter = drive.letter
                self.drive_label.setText(f"{drive.root} {drive.label}".strip())
                self.source_edit.setText(drive.root)
                self._device_log(f"检测到 MSC 卷 {drive.root}")
            port = result.get("port")
            if port:
                self.refresh_ports()
                self._select_port(port.device)
        elif name == "eject":
            self._device_log(f"已弹出 {result}:，正在退出 MSC。")
            self.last_drive_letter = None
            self.drive_label.setText("-")
            self._start("exit_msc", lambda: self._require_device().exit_msc(force=False))
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

    def _close_device(self) -> None:
        if self.device:
            self.device.close()
            self.device = None

    def _select_port(self, port_name: str) -> None:
        index = self.port_combo.findData(port_name)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)

    def _apply_status(self, status: dict[str, Any]) -> None:
        storage = status.get("storage") if isinstance(status.get("storage"), dict) else {}
        self.connection_label.setText("已连接")
        self.mode_label.setText(str(status.get("mode", "-")))
        self.storage_label.setText(
            ", ".join(
                f"{key}={storage.get(key)}" for key in ("ready", "mounted", "usb_exposed", "read_only", "ejected")
                if key in storage
            )
            or "-"
        )

    def _set_busy(self, busy: bool) -> None:
        self.start_folder_button.setDisabled(busy)
        self.start_device_button.setDisabled(busy)
        self.retry_job_button.setDisabled(busy)
        self.retry_cleanup_button.setDisabled(busy)
        self.edit_report_button.setDisabled(busy)
        self.regenerate_button.setDisabled(busy)
        self.resend_button.setDisabled(busy)
        if hasattr(self, "style_test_button"):
            self.style_test_button.setDisabled(busy)

    def _device_log(self, message: str) -> None:
        self.device_log.appendPlainText(message)


if __name__ == "__main__":
    raise SystemExit(main())
