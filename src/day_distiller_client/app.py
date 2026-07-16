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


DEFAULT_POSTER_IMAGE_SIZE = "1328x1776"


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
        raise ValueError("海报尺寸应填写明确的宽x高像素值，例如 1328x1776")
    width, height = map(int, match.groups())
    if min(width, height) < 512 or width >= height:
        raise ValueError("海报必须使用竖版尺寸")
    if abs(width / height - 0.75) > 0.02:
        raise ValueError("海报必须接近 3:4 竖版比例")
    if width * height >= 2_360_000:
        raise ValueError("海报总像素数必须少于236万，以控制 Seedream 生成成本")
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
    window = MainWindow()
    window.show()
    return application.exec()


class MainWindow:
    def __init__(self) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMainWindow

        self.window = QMainWindow()
        self.window.setWindowTitle("Day Distiller · AI 每日蒸馏 v2")
        self.window.resize(1080, 760)
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
        from PySide6.QtWidgets import QTabWidget

        self.tabs = QTabWidget()
        self.tabs.addTab(self._guide_page(), "用户指引")
        self.tabs.addTab(self._device_page(), "设备")
        self.tabs.addTab(self._records_page(), "今日记录")
        self.tabs.addTab(self._distillation_page(), "蒸馏进度")
        self.tabs.addTab(self._history_page(), "报告历史")
        self.tabs.addTab(self._avatar_page(), "参考形象")
        self.tabs.addTab(self._settings_page_v2(), "设置")
        self.window.setCentralWidget(self.tabs)

    def _guide_page(self):
        from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("<h1>欢迎使用 AI 每日蒸馏</h1>")
        guide = QLabel(
            "<h3>首次配置</h3>"
            "<ol><li>打开“设置”，填写百炼、DeepSeek、火山方舟和 Resend SMTP 参数。</li>"
            "<li>打开“参考形象”，上传一张清晰的单人参考图并填写简短描述。</li>"
            "<li>在 Resend 控制台验证发件域名；发件地址必须属于该域名。</li></ol>"
            "<h3>每天使用</h3>"
            "<ol><li>出门前携带并启动设备，让设备按计划无感记录。</li>"
            "<li>回家后唤醒设备并连接电脑。</li>"
            "<li>在“设备”确认连接，在“今日记录”选择日期。</li>"
            "<li>进入“蒸馏进度”，点击从设备开始蒸馏。</li>"
            "<li>邮件被 Resend 接受后，可在“报告历史”查看、修改或重新发送。</li></ol>"
            "<p><b>隐私提示：</b>IMU和地点记忆留在本地；关键帧和音频发送到百炼，结构化证据发送给DeepSeek；最终筛选出的2–3张原始关键帧和无文字海报提示发送到火山方舟。</p>"
            "<p>FFmpeg和IMU分类器已内置，无需安装或配置。</p>"
        )
        guide.setWordWrap(True)
        go_settings = QPushButton("前往设置")
        go_settings.clicked.connect(lambda: self.tabs.setCurrentIndex(self.tabs.count() - 1))
        layout.addWidget(title)
        layout.addWidget(guide)
        layout.addWidget(go_settings)
        layout.addStretch(1)
        return page

    def _avatar_page(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QFileDialog,
            QLabel,
            QLineEdit,
            QPlainTextEdit,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )

        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel(
            "上传一张清晰、光线均匀、只有一名主体的正面或半身照片。"
            "描述建议控制在20–100字，例如：短黑发、圆框眼镜、常穿深蓝夹克，海报中保持温和自然的形象。"
        )
        intro.setWordWrap(True)
        self.avatar_preview = QLabel("尚未选择参考形象")
        self.avatar_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_preview.setMinimumHeight(320)
        self.avatar_preview.setStyleSheet("border: 1px solid #888; background: #202020;")
        self.avatar_edit = QLineEdit()
        self.avatar_edit.setReadOnly(True)
        self.avatar_description_edit = QPlainTextEdit()
        self.avatar_description_edit.setPlaceholderText("简短描述你的发型、衣着、配饰和希望保持的海报角色特征")
        self.avatar_description_edit.setMaximumHeight(120)
        choose = QPushButton("选择参考形象")
        choose.clicked.connect(lambda: self._choose_avatar(QFileDialog))
        save = QPushButton("保存参考形象")
        save.clicked.connect(self.save_avatar_profile)
        layout.addWidget(intro)
        layout.addWidget(self.avatar_preview, 1)
        layout.addWidget(self.avatar_edit)
        layout.addWidget(self.avatar_description_edit)
        layout.addWidget(choose)
        layout.addWidget(save)
        return page

    def _settings_page_v2(self):
        from PySide6.QtWidgets import (
            QComboBox,
            QFormLayout,
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
            "API Key仅保存到本机 Windows Credential Manager，并会在本设置页明文显示，便于检查和修改；"
            "请勿截图或向他人展示此页面。Base URL、模型名和邮件参数保存在本地SQLite。"
        )
        notice.setWordWrap(True)
        form = QFormLayout()
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
        self.resend_sender_edit = QLineEdit()
        self.resend_recipient_edit = QLineEdit()
        form.addRow("百炼 Base URL", self.qwen_base_url_edit)
        form.addRow("百炼 API Key", self.api_key_edit)
        form.addRow("关键帧模型", self.scene_model_edit)
        form.addRow("批量音视频/OCR模型", self.omni_model_edit)
        form.addRow("DeepSeek Base URL", self.deepseek_base_url_edit)
        form.addRow("DeepSeek API Key", self.deepseek_key_edit)
        form.addRow("日报模型", self.daily_model_edit)
        form.addRow("火山方舟 Base URL", self.volcengine_base_url_edit)
        form.addRow("火山方舟 API Key", self.volcengine_key_edit)
        form.addRow("Seedream模型/Endpoint ID", self.image_model_edit)
        form.addRow("单张海报尺寸（3:4，<236万像素）", self.image_size_edit)
        form.addRow("Resend SMTP主机", self.resend_host_edit)
        form.addRow("Resend SMTP端口", self.resend_port_edit)
        form.addRow("Resend SMTP安全方式", self.resend_security_combo)
        form.addRow("Resend API Key（SMTP密码）", self.resend_key_edit)
        form.addRow("Resend发件地址", self.resend_sender_edit)
        form.addRow("日报收件地址", self.resend_recipient_edit)
        save = QPushButton("保存设置")
        save.clicked.connect(self.save_cloud_settings)
        layout.addWidget(notice)
        layout.addLayout(form)
        layout.addWidget(save)
        self.data_usage_label = QLabel()
        layout.addWidget(self.data_usage_label)
        layout.addStretch(1)
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
        row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.refresh_button = QPushButton("刷新串口")
        self.connect_button = QPushButton("连接")
        self.auto_button = QPushButton("自动发现")
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
        scan_button = QPushButton("扫描所选日期")
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
        top = QHBoxLayout()
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("离线 Mock（零云端调用）", "mock")
        self.provider_combo.addItem("中国大陆模型工作流 + Resend", "mainland")
        self.delete_virtual_source = QCheckBox("邮件成功后删除所选虚拟卡记录")
        self.start_folder_button = QPushButton("从文件夹开始蒸馏")
        self.start_device_button = QPushButton("从当前设备一键蒸馏")
        top.addWidget(QLabel("产出模式"))
        top.addWidget(self.provider_combo)
        top.addWidget(self.delete_virtual_source)
        top.addStretch(1)
        top.addWidget(self.start_folder_button)
        top.addWidget(self.start_device_button)
        layout.addLayout(top)
        self.stage_label = QLabel("等待开始")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.usage_label = QLabel("Mock 模式估算用量为 0；真实模式只调用一次 Seedream，最多输入3张原始关键帧，输出单张3:4海报且少于236万像素。")
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
        row = QHBoxLayout()
        refresh = QPushButton("刷新")
        self.open_report_button = QPushButton("打开日报")
        self.edit_report_button = QPushButton("修改地点与文字")
        self.regenerate_button = QPushButton("重新生成海报")
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
            self._select_tab("今日记录")
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
                "请在“今日记录”页选择记录根目录和日期，并确认扫描到了至少一条记录。",
            )
            self._select_tab("今日记录")
            return
        provider_mode = str(self.provider_combo.currentData())
        delete_source = self.delete_virtual_source.isChecked()
        self._set_busy(True)
        self._select_tab("蒸馏进度")

        def work():
            pipeline = self._create_pipeline(provider_mode)
            job_id = pipeline.import_legacy(source, target, provider_mode=provider_mode)
            self.active_job_id = job_id
            cleanup = source if delete_source else None
            return pipeline.process(job_id, cleanup_source_root=cleanup, avatar_references=self._avatar_references())

        self._start("distill", work)

    def start_device_distillation(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if self.device is None and not self.port_combo.currentData():
            QMessageBox.information(self.window, "请先连接设备", "尚未选择设备串口，请先在“设备”页连接或自动发现设备。")
            self._select_tab("设备")
            return
        target = self._selected_date()
        provider_mode = str(self.provider_combo.currentData())
        self._set_busy(True)
        self._select_tab("蒸馏进度")

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
        self._select_tab("蒸馏进度")
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
        for job in self.database.list_jobs():
            item_text = f"{job.target_date.isoformat()}  ·  {job.stage.value}  ·  {job.provider_mode}"
            if job.error:
                item_text += f"  ·  {job.error}"
            self.history_list.addItem(item_text)
            self.history_list.item(self.history_list.count() - 1).setData(Qt.ItemDataRole.UserRole, job.id)
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
            "选择漫画参考形象",
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
        if source is None or not source.is_file():
            QMessageBox.warning(self.window, "参考形象", "请先选择一张有效图片。")
            return
        if not description:
            QMessageBox.warning(self.window, "参考形象", "请填写一段简短的形象描述。")
            return
        try:
            with Image.open(source) as image:
                image.verify()
            profile_dir = self.paths.root / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            destination = profile_dir / ("reference" + source.suffix.lower())
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            settings = self.database.get_setting("desktop_v2", {})
            settings["avatar"] = str(destination)
            settings["avatar_description"] = description
            self.database.set_setting("desktop_v2", settings)
            self.avatar_edit.setText(str(destination))
            self._update_avatar_preview(destination)
        except Exception as exc:
            QMessageBox.warning(self.window, "参考形象保存失败", str(exc))
            return
        QMessageBox.information(self.window, "参考形象", "参考形象和描述已保存在本机。")

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
        saved_image_size = models.get("image_size", DEFAULT_POSTER_IMAGE_SIZE)
        try:
            saved_image_size = _validate_image_size(saved_image_size)
        except ValueError:
            # Migrate the former 4:3 / 2K panel setting to the cost-capped
            # vertical poster default without blocking existing installations.
            saved_image_size = DEFAULT_POSTER_IMAGE_SIZE
        self.image_size_edit.setText(saved_image_size)
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
            try:
                poster_image_size = _validate_image_size(
                    models.get("image_size", DEFAULT_POSTER_IMAGE_SIZE)
                )
            except ValueError:
                poster_image_size = DEFAULT_POSTER_IMAGE_SIZE
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
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == title:
                self.tabs.setCurrentIndex(index)
                return

    def _start(self, name: str, operation: Callable[[], Any]) -> None:
        if name in {"distill", "cleanup_retry", "manual_regenerate", "manual_resend"}:
            self.distill_log.appendPlainText(f"{name}...")
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

    def _device_log(self, message: str) -> None:
        self.device_log.appendPlainText(message)


if __name__ == "__main__":
    raise SystemExit(main())
