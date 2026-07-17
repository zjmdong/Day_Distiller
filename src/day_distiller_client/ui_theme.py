from __future__ import annotations


APP_STYLESHEET = """
QMainWindow, QWidget#appRoot, QWidget#pageStack, QScrollArea, QScrollArea > QWidget > QWidget {
    background: #000000;
    color: #ffffff;
}
QStackedWidget#homeWorkflow, QStackedWidget#settingsStack {
    background: #000000;
    border: 0;
}
QWidget {
    color: #ffffff;
    font-family: "Inter", "Segoe UI Variable", "Microsoft YaHei UI", sans-serif;
    font-size: 14px;
}
QToolTip {
    background: #111111;
    color: #ffffff;
    border: 1px solid #2b2b2b;
    border-radius: 6px;
    padding: 6px;
}
QLabel { background: transparent; color: #ffffff; }
QLabel#brandTitle {
    color: #ffffff;
    font-size: 21px;
    font-weight: 600;
}
QLabel#brandSubtitle, QLabel[muted="true"] {
    color: #a6a6a6;
}
QLabel#pageTitle {
    color: #ffffff;
    font-size: 31px;
    font-weight: 600;
}
QLabel#pageSubtitle {
    color: #a6a6a6;
    font-size: 14px;
}
QLabel#heroTitle {
    color: #ffffff;
    font-size: 42px;
    font-weight: 650;
}
QLabel#heroSubtitle, QLabel#workflowSubtitle {
    color: #8f8f8f;
    font-size: 15px;
}
QLabel#workflowTitle {
    color: #ffffff;
    font-size: 34px;
    font-weight: 650;
}
QLabel#privacyFootnote {
    color: #5f5f5f;
    font-size: 11px;
}
QLabel#homeStatus {
    color: #737373;
    font-size: 12px;
    min-height: 20px;
}
QLabel#deviceMetaLabel {
    color: #666666;
    font-size: 11px;
    padding: 8px 4px 2px 4px;
}
QLabel[stepBadge="true"] {
    background: #0b1820;
    color: #4db7ff;
    border: 1px solid #153a52;
    border-radius: 13px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 600;
}
QLabel#workflowStatusText {
    color: #ffffff;
    font-size: 18px;
    font-weight: 600;
}
QLabel#successIconSmall, QLabel#successIconLarge {
    background: #0b2a1b;
    color: #4bd481;
    border: 1px solid #205f3b;
    border-radius: 19px;
    font-size: 23px;
    font-weight: 700;
}
QLabel#successIconLarge {
    border-radius: 43px;
    font-size: 46px;
}
QLabel#etaLabel {
    color: #8f8f8f;
    min-width: 145px;
    padding-left: 10px;
}
QLabel[chip="true"] {
    background: #0b0b0b;
    color: #a6a6a6;
    border: 1px solid #242424;
    border-radius: 17px;
    padding: 7px 13px;
    font-weight: 500;
}
QFrame#appHeader {
    background: #000000;
    border: 0;
    border-bottom: 1px solid #171717;
}
QFrame#sideBar {
    background: #000000;
    border: 0;
    border-right: 1px solid #171717;
}
QFrame#workflowStatusCard {
    background: #080808;
    border: 1px solid #242424;
    border-radius: 16px;
}
QFrame#modelCard {
    background: #080808;
    border: 1px solid #222222;
    border-radius: 13px;
    padding: 13px;
}
QLabel#modelName, QLabel#recipientValue {
    color: #ffffff;
    font-size: 15px;
    font-weight: 600;
}
QFrame#stylePreviewHolder {
    background: #050505;
    border: 1px solid #242424;
    border-radius: 12px;
}
QListWidget#sideNav {
    background: #000000;
    border: 0;
    outline: 0;
    padding: 8px;
}
QListWidget#sideNav::item {
    background: transparent;
    color: #a6a6a6;
    border: 0;
    border-radius: 20px;
    min-height: 42px;
    margin: 3px 0;
    padding: 0 15px;
    font-size: 14px;
    font-weight: 500;
}
QListWidget#sideNav::item:selected {
    background: #101010;
    color: #ffffff;
    border: 1px solid #252525;
}
QListWidget#sideNav::item:hover:!selected {
    background: #090909;
    color: #ffffff;
}
QGroupBox, QFrame[card="true"] {
    background: #090909;
    color: #ffffff;
    border: 1px solid #242424;
    border-radius: 13px;
    margin-top: 14px;
    padding: 16px;
    font-size: 15px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 15px;
    padding: 0 7px;
    color: #ffffff;
    background: #090909;
}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDateEdit, QTableWidget {
    background: #090909;
    color: #ffffff;
    border: 1px solid #2b2b2b;
    border-radius: 10px;
    padding: 8px 11px;
    selection-background-color: #0099ff;
    selection-color: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {
    border: 1px solid #0099ff;
}
QLineEdit:read-only {
    background: #060606;
    color: #8d8d8d;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    background: #060606;
    color: #5f5f5f;
    border-color: #171717;
}
QComboBox { min-height: 24px; }
QComboBox::drop-down {
    border: 0;
    width: 30px;
}
QComboBox QAbstractItemView {
    background: #0b0b0b;
    color: #ffffff;
    border: 1px solid #2b2b2b;
    selection-background-color: #14364c;
    selection-color: #ffffff;
    outline: 0;
}
QSpinBox::up-button, QSpinBox::down-button, QDateEdit::drop-down {
    background: #151515;
    border: 0;
    width: 24px;
}
QPushButton {
    min-height: 40px;
    background: rgba(255, 255, 255, 0.10);
    color: #ffffff;
    border: 1px solid #292929;
    border-radius: 20px;
    padding: 0 18px;
    font-weight: 500;
}
QPushButton:hover {
    background: rgba(255, 255, 255, 0.16);
    border-color: #3b3b3b;
}
QPushButton:pressed { background: #252525; }
QPushButton:disabled {
    background: #090909;
    color: #555555;
    border-color: #171717;
}
QPushButton[role="primary"], QPushButton[role="accent"], QPushButton[role="mint"] {
    background: #0099ff;
    color: #ffffff;
    border: 1px solid #0099ff;
}
QPushButton[role="primary"]:hover, QPushButton[role="accent"]:hover, QPushButton[role="mint"]:hover {
    background: #1aa3ff;
    border-color: #1aa3ff;
}
QPushButton#heroStartButton {
    min-height: 60px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(25, 166, 255, 235), stop:1 rgba(0, 122, 255, 235));
    border: 1px solid rgba(102, 201, 255, 210);
    border-radius: 18px;
    font-size: 20px;
    font-weight: 650;
}
QPushButton#heroStartButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(52, 178, 255, 245), stop:1 rgba(15, 135, 255, 245));
    border-color: #8bd6ff;
}
QPushButton#workflowBackButton {
    min-height: 34px;
    background: transparent;
    color: #8f8f8f;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 0 10px;
    text-align: left;
}
QPushButton#workflowBackButton:hover {
    background: #0d0d0d;
    color: #ffffff;
    border-color: #242424;
}
QPushButton[role="link"] {
    min-height: 28px;
    background: transparent;
    color: #737373;
    border: 0;
    border-radius: 4px;
    padding: 2px 7px;
    text-decoration: underline;
}
QPushButton[role="link"]:hover {
    background: transparent;
    color: #b5b5b5;
    border: 0;
}
QCheckBox { color: #a6a6a6; spacing: 8px; }
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    background: #090909;
    border: 1px solid #343434;
    border-radius: 5px;
}
QCheckBox::indicator:checked {
    background: #0099ff;
    border-color: #0099ff;
}
QProgressBar {
    min-height: 22px;
    max-height: 22px;
    background: #151515;
    border: 1px solid #242424;
    border-radius: 11px;
    text-align: center;
    color: #d8d8d8;
    font-size: 11px;
    font-weight: 600;
}
QProgressBar::chunk {
    border-radius: 10px;
    background: #0099ff;
}
QListWidget#contentList, QListWidget#historyList, QListWidget#guidedRecordsList,
QListWidget#developerHistoryList {
    background: #050505;
    color: #ffffff;
    border: 1px solid #202020;
    border-radius: 12px;
    padding: 8px;
    outline: 0;
}
QListWidget#contentList::item, QListWidget#historyList::item,
QListWidget#guidedRecordsList::item, QListWidget#developerHistoryList::item {
    background: #090909;
    color: #d7d7d7;
    border: 1px solid #202020;
    border-radius: 10px;
    min-height: 38px;
    margin: 4px;
    padding: 4px 12px;
}
QListWidget#contentList::item:selected, QListWidget#historyList::item:selected,
QListWidget#guidedRecordsList::item:selected, QListWidget#developerHistoryList::item:selected {
    background: #0d2230;
    color: #ffffff;
    border-color: #0099ff;
}
QListWidget#guidedRecordsList::indicator {
    width: 18px;
    height: 18px;
    background: #090909;
    border: 1px solid #3a3a3a;
    border-radius: 5px;
}
QListWidget#guidedRecordsList::indicator:checked {
    background: #0099ff;
    border-color: #0099ff;
}
QPlainTextEdit#guidedLiveProgress {
    color: #858585;
    background: #050505;
    border: 1px solid #202020;
    padding: 14px;
}
QTabWidget#manualDebugTabs::pane {
    background: #000000;
    border: 1px solid #202020;
    border-radius: 10px;
}
QTabWidget#manualDebugTabs QTabBar::tab {
    background: #090909;
    color: #8f8f8f;
    border: 1px solid #202020;
    padding: 9px 22px;
    margin-right: 4px;
}
QTabWidget#manualDebugTabs QTabBar::tab:selected {
    background: #102333;
    color: #ffffff;
    border-color: #1b5579;
}
QHeaderView::section {
    background: #101010;
    color: #a6a6a6;
    border: 0;
    border-right: 1px solid #242424;
    border-bottom: 1px solid #242424;
    padding: 8px;
}
QTableWidget {
    gridline-color: #242424;
    alternate-background-color: #0c0c0c;
}
QScrollArea { border: 0; }
QScrollBar:vertical {
    background: transparent;
    width: 9px;
    margin: 4px 1px;
}
QScrollBar::handle:vertical {
    background: #333333;
    min-height: 30px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #4a4a4a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: transparent;
    height: 9px;
}
QScrollBar::handle:horizontal {
    background: #333333;
    min-width: 30px;
    border-radius: 4px;
}
QMessageBox, QDialog { background: #050505; color: #ffffff; }
QDialogButtonBox QPushButton { min-width: 90px; }
"""
