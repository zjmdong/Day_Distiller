from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

from .device import PortCandidate, UsbLinkDevice, find_device, list_serial_ports
from .windows import drive_letters, list_removable_drives, safe_eject, wait_for_new_drive


def _load_qt():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    return {
        "QApplication": QApplication,
        "QComboBox": QComboBox,
        "QGridLayout": QGridLayout,
        "QGroupBox": QGroupBox,
        "QHBoxLayout": QHBoxLayout,
        "QLabel": QLabel,
        "QMainWindow": QMainWindow,
        "QMessageBox": QMessageBox,
        "QPlainTextEdit": QPlainTextEdit,
        "QPushButton": QPushButton,
        "QTimer": QTimer,
        "QVBoxLayout": QVBoxLayout,
        "QWidget": QWidget,
    }


class Worker:
    def __init__(self, done: Callable[[tuple[str, Any, Exception | None]], None]) -> None:
        self._done = done

    def run(self, name: str, fn: Callable[[], Any]) -> None:
        def target() -> None:
            try:
                result = fn()
                self._done((name, result, None))
            except Exception as exc:
                self._done((name, None, exc))

        threading.Thread(target=target, daemon=True).start()


def main() -> int:
    qt = _load_qt()
    app = qt["QApplication"]([])
    window = MainWindow(qt)
    window.show()
    return app.exec()


class MainWindow:
    def __init__(self, qt: dict[str, Any]) -> None:
        self.qt = qt
        self.window = qt["QMainWindow"]()
        self.window.setWindowTitle("Day Distiller USB Link")
        self.window.resize(760, 520)
        self.worker_events: queue.Queue[tuple[str, Any, Exception | None]] = queue.Queue()
        self.worker = Worker(self.worker_events.put)
        self.device: UsbLinkDevice | None = None
        self.current_status: dict[str, Any] = {}
        self.last_drive_letter: str | None = None
        self.ports: list[PortCandidate] = []

        self._build_ui()
        self._wire()
        self.refresh_ports()

        self.timer = qt["QTimer"]()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._drain_worker_events)
        self.timer.start()

    def show(self) -> None:
        self.window.show()

    def _build_ui(self) -> None:
        QComboBox = self.qt["QComboBox"]
        QGridLayout = self.qt["QGridLayout"]
        QGroupBox = self.qt["QGroupBox"]
        QHBoxLayout = self.qt["QHBoxLayout"]
        QLabel = self.qt["QLabel"]
        QPlainTextEdit = self.qt["QPlainTextEdit"]
        QPushButton = self.qt["QPushButton"]
        QVBoxLayout = self.qt["QVBoxLayout"]
        QWidget = self.qt["QWidget"]

        root = QWidget()
        layout = QVBoxLayout(root)

        top = QHBoxLayout()
        self.port_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        self.connect_button = QPushButton("Connect")
        self.auto_button = QPushButton("Auto Find")
        top.addWidget(QLabel("Protocol CDC"))
        top.addWidget(self.port_combo, 1)
        top.addWidget(self.refresh_button)
        top.addWidget(self.connect_button)
        top.addWidget(self.auto_button)
        layout.addLayout(top)

        status_box = QGroupBox("Device")
        grid = QGridLayout(status_box)
        self.connection_label = QLabel("Disconnected")
        self.mode_label = QLabel("-")
        self.storage_label = QLabel("-")
        self.drive_label = QLabel("-")
        grid.addWidget(QLabel("Connection"), 0, 0)
        grid.addWidget(self.connection_label, 0, 1)
        grid.addWidget(QLabel("Mode"), 1, 0)
        grid.addWidget(self.mode_label, 1, 1)
        grid.addWidget(QLabel("TF Card"), 2, 0)
        grid.addWidget(self.storage_label, 2, 1)
        grid.addWidget(QLabel("Windows Drive"), 3, 0)
        grid.addWidget(self.drive_label, 3, 1)
        layout.addWidget(status_box)

        controls = QHBoxLayout()
        self.access_combo = QComboBox()
        self.access_combo.addItem("Read / Write", "rw")
        self.access_combo.addItem("Read Only", "ro")
        self.status_button = QPushButton("Status")
        self.enter_button = QPushButton("Enter U Disk")
        self.eject_button = QPushButton("Eject + Exit")
        self.force_exit_button = QPushButton("Force Exit")
        controls.addWidget(QLabel("Access"))
        controls.addWidget(self.access_combo)
        controls.addWidget(self.status_button)
        controls.addWidget(self.enter_button)
        controls.addWidget(self.eject_button)
        controls.addWidget(self.force_exit_button)
        layout.addLayout(controls)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

        self.window.setCentralWidget(root)

    def _wire(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self.connect_selected)
        self.auto_button.clicked.connect(self.auto_find)
        self.status_button.clicked.connect(self.read_status)
        self.enter_button.clicked.connect(self.enter_msc)
        self.eject_button.clicked.connect(self.eject_and_exit)
        self.force_exit_button.clicked.connect(lambda: self.exit_msc(force=True))

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def refresh_ports(self) -> None:
        self.ports = list_serial_ports()
        self.port_combo.clear()
        for port in self.ports:
            hint = " *" if port.likely else ""
            self.port_combo.addItem(f"{port.device} - {port.description}{hint}", port.device)
        self._log(f"Found {len(self.ports)} serial port(s).")

    def selected_port(self) -> str | None:
        index = self.port_combo.currentIndex()
        if index < 0:
            return None
        return self.port_combo.itemData(index)

    def connect_selected(self) -> None:
        port = self.selected_port()
        if not port:
            self._log("No serial port selected.")
            return
        self._start("connect", lambda: self._connect_and_hello(port))

    def auto_find(self) -> None:
        self._start("auto_find", lambda: find_device())

    def read_status(self) -> None:
        self._start("status", self._status)

    def enter_msc(self) -> None:
        access = self.access_combo.currentData()
        before = drive_letters()

        def work() -> dict[str, Any]:
            status = self._require_device().enter_msc(access)
            self._close_device()
            drive = wait_for_new_drive(before, timeout=25)
            found = find_device(timeout_per_port=0.6)
            port = found[0] if found else None
            msc_status = found[1] if found else status
            return {"status": msc_status, "drive": drive, "port": port}

        self._start("enter_msc", work)

    def eject_and_exit(self) -> None:
        letter = self.last_drive_letter
        if not letter:
            drives = list_removable_drives()
            letter = drives[0].letter if drives else None
        if not letter:
            self._log("No removable drive found to eject.")
            return

        def work() -> str:
            safe_eject(letter)
            time.sleep(1.5)
            return letter

        self._start("eject", work)

    def exit_msc(self, force: bool = False) -> None:
        self._start("exit_msc", lambda: self._require_device().exit_msc(force=force))

    def _connect_and_hello(self, port: str) -> dict[str, Any]:
        self._close_device()
        self.device = UsbLinkDevice(port)
        status = self.device.hello()
        return status

    def _status(self) -> dict[str, Any]:
        return self._require_device().get_status()

    def _require_device(self) -> UsbLinkDevice:
        if self.device is None:
            port = self.selected_port()
            if not port:
                raise RuntimeError("no selected protocol CDC port")
            self.device = UsbLinkDevice(port)
            self.device.open()
        return self.device

    def _close_device(self) -> None:
        if self.device is not None:
            self.device.close()
            self.device = None

    def _start(self, name: str, fn: Callable[[], Any]) -> None:
        self._log(f"{name}...")
        self.worker.run(name, fn)

    def _drain_worker_events(self) -> None:
        while True:
            try:
                name, result, exc = self.worker_events.get_nowait()
            except queue.Empty:
                return
            if exc:
                self._log(f"{name} failed: {exc}")
                continue
            self._handle_result(name, result)

    def _handle_result(self, name: str, result: Any) -> None:
        if name == "auto_find":
            if not result:
                self._log("No Day Distiller protocol CDC found.")
                return
            port, status = result
            self.refresh_ports()
            for i in range(self.port_combo.count()):
                if self.port_combo.itemData(i) == port.device:
                    self.port_combo.setCurrentIndex(i)
                    break
            self._close_device()
            self.device = UsbLinkDevice(port.device)
            self.current_status = status
            self._apply_status(status)
            self._log(f"Connected to {port.device}.")
            return
        if name in {"connect", "status", "exit_msc"}:
            self.current_status = result
            self._apply_status(result)
            self._log(f"{name} OK.")
            if name == "exit_msc":
                self._close_device()
            return
        if name == "enter_msc":
            drive = result.get("drive")
            self.current_status = result.get("status", {})
            self._apply_status(self.current_status)
            if drive:
                self.last_drive_letter = drive.letter
                self.drive_label.setText(f"{drive.root} {drive.label}".strip())
                self._log(f"MSC drive detected: {drive.root}")
            else:
                self.drive_label.setText("not detected")
                self._log("MSC mode entered, but no new drive letter was detected.")
            port = result.get("port")
            if port:
                self.refresh_ports()
                for i in range(self.port_combo.count()):
                    if self.port_combo.itemData(i) == port.device:
                        self.port_combo.setCurrentIndex(i)
                        break
                self._close_device()
                self.device = UsbLinkDevice(port.device)
                self._log(f"MSC protocol CDC detected: {port.device}")
            return
        if name == "eject":
            self._log(f"Ejected {result}:")
            self.last_drive_letter = None
            self.drive_label.setText("-")
            self.exit_msc(force=False)

    def _apply_status(self, status: dict[str, Any]) -> None:
        mode = str(status.get("mode", "-"))
        storage = status.get("storage") if isinstance(status.get("storage"), dict) else {}
        ready = storage.get("ready")
        mounted = storage.get("mounted")
        exposed = storage.get("usb_exposed")
        read_only = storage.get("read_only")
        ejected = storage.get("ejected")

        self.connection_label.setText("Connected")
        self.mode_label.setText(mode)
        parts = [f"ready={ready}", f"mounted={mounted}", f"usb={exposed}"]
        if read_only is not None:
            parts.append(f"ro={read_only}")
        if ejected is not None:
            parts.append(f"ejected={ejected}")
        self.storage_label.setText(", ".join(parts))


if __name__ == "__main__":
    raise SystemExit(main())
