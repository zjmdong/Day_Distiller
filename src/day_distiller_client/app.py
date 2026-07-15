from __future__ import annotations

import queue
import threading
import time
import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .credentials import CredentialName, CredentialStore
from .database import JobDatabase
from .device import PortCandidate, UsbLinkDevice, find_device, list_serial_ports
from .device_workflow import LegacyDeviceWorkflow
from .domain import JobStage
from .legacy_import import available_record_dates, scan_record_directories
from .media import MediaPreprocessor
from .paths import AppPaths
from .pipeline import DistillationPipeline
from .providers import (
    MockAIProvider,
    MockMailProvider,
    ModelSettings,
    OpenAIProvider,
    SmtpMailProvider,
    SmtpSettings,
)
from .reporting import ReportRenderer, day_report_from_json
from .windows import drive_letters, list_removable_drives, safe_eject, wait_for_new_drive


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

        self._build_ui()
        self._load_settings()
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
        self.tabs.addTab(self._device_page(), "设备")
        self.tabs.addTab(self._records_page(), "今日记录")
        self.tabs.addTab(self._distillation_page(), "蒸馏进度")
        self.tabs.addTab(self._history_page(), "报告历史")
        self.tabs.addTab(self._settings_page(), "设置")
        self.window.setCentralWidget(self.tabs)

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
        self.provider_combo.addItem("OpenAI + SMTP", "openai")
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
        self.usage_label = QLabel("Mock 模式估算用量为 0；真实模式的图像与模型调用量取决于当天记录数和漫画格数。")
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
        self.regenerate_button = QPushButton("重新生成漫画")
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
        self.api_key_edit.setPlaceholderText("留空则保留已保存的 Key")
        self.scene_model_edit = QLineEdit("gpt-5.6-terra")
        self.daily_model_edit = QLineEdit("gpt-5.6-sol")
        self.transcription_model_edit = QLineEdit("gpt-4o-transcribe")
        self.image_model_edit = QLineEdit("gpt-image-2")
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
        form.addRow("OpenAI API Key", self.api_key_edit)
        form.addRow("逐片理解模型", self.scene_model_edit)
        form.addRow("全天综合模型", self.daily_model_edit)
        form.addRow("音频转写模型", self.transcription_model_edit)
        form.addRow("漫画生图模型", self.image_model_edit)
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
        root = Path(self.source_edit.text().strip())
        try:
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
        except Exception as exc:
            self.records_hint.setText(f"扫描失败：{exc}")

    def start_folder_distillation(self) -> None:
        source = Path(self.source_edit.text().strip())
        target = self._selected_date()
        provider_mode = str(self.provider_combo.currentData())
        delete_source = self.delete_virtual_source.isChecked()
        self._set_busy(True)
        self.tabs.setCurrentIndex(2)

        def work():
            pipeline = self._create_pipeline(provider_mode)
            job_id = pipeline.import_legacy(source, target, provider_mode=provider_mode)
            self.active_job_id = job_id
            cleanup = source if delete_source else None
            return pipeline.process(job_id, cleanup_source_root=cleanup, avatar_references=self._avatar_references())

        self._start("distill", work)

    def start_device_distillation(self) -> None:
        target = self._selected_date()
        provider_mode = str(self.provider_combo.currentData())
        self._set_busy(True)
        self.tabs.setCurrentIndex(2)

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
        self.tabs.setCurrentIndex(2)
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
        narrative = QPlainTextEdit(report.narrative)
        form.addRow("标题", title)
        form.addRow("一句话总结", summary)
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
                "daily": self.daily_model_edit.text().strip(),
                "transcription": self.transcription_model_edit.text().strip(),
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
            if self.api_key_edit.text():
                self.credentials.set(CredentialName.OPENAI_API_KEY, self.api_key_edit.text())
                self.api_key_edit.clear()
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
        self.daily_model_edit.setText(models.get("daily", self.daily_model_edit.text()))
        self.transcription_model_edit.setText(models.get("transcription", self.transcription_model_edit.text()))
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

    def _create_pipeline(self, provider_mode: str) -> DistillationPipeline:
        settings = self.database.get_setting("desktop_v2", {})
        models = settings.get("models", {})
        if provider_mode == "mock":
            ai = MockAIProvider()
            mail = MockMailProvider(self.paths.root / "mock_outbox")
        else:
            api_key = self.credentials.get(CredentialName.OPENAI_API_KEY)
            if not api_key:
                raise RuntimeError("请先在设置页保存 OpenAI API Key")
            model_settings = ModelSettings(
                scene_model=models.get("scene", "gpt-5.6-terra"),
                daily_model=models.get("daily", "gpt-5.6-sol"),
                transcription_model=models.get("transcription", "gpt-4o-transcribe"),
                image_model=models.get("image", "gpt-image-2"),
            )
            ai = OpenAIProvider(api_key=api_key, settings=model_settings)
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
            ai,
            ai,
            ai,
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
