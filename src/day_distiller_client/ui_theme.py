from __future__ import annotations


APP_STYLESHEET = """
QMainWindow, QWidget#appRoot {
    background: #F5F6FA;
    color: #172033;
}
QWidget {
    font-family: "Segoe UI", "Microsoft YaHei UI";
    font-size: 14px;
}
QLabel#brandTitle {
    font-size: 23px;
    font-weight: 700;
    color: #111827;
}
QLabel#brandSubtitle, QLabel[muted="true"] {
    color: #687086;
}
QLabel#pageTitle {
    font-size: 28px;
    font-weight: 720;
    color: #111827;
}
QLabel#pageSubtitle {
    color: #687086;
    font-size: 14px;
}
QLabel[chip="true"] {
    background: #ECE9FF;
    color: #6047D7;
    border-radius: 12px;
    padding: 5px 11px;
    font-weight: 600;
}
QTabWidget::pane {
    border: 0;
    background: #F5F6FA;
}
QTabBar {
    background: #FFFFFF;
}
QTabBar::tab {
    min-width: 138px;
    min-height: 46px;
    margin: 4px 10px;
    padding: 3px 14px;
    border-radius: 13px;
    color: #626B80;
    font-size: 14px;
    font-weight: 600;
    text-align: left;
}
QTabBar::tab:selected {
    background: #EEEAFE;
    color: #6047D7;
}
QTabBar::tab:hover:!selected {
    background: #F5F4FB;
    color: #2C3447;
}
QGroupBox, QFrame[card="true"] {
    background: #FFFFFF;
    border: 1px solid #E8EAF1;
    border-radius: 18px;
    margin-top: 14px;
    padding: 16px;
    font-size: 16px;
    font-weight: 650;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 18px;
    padding: 0 7px;
    color: #1C2538;
    background: #FFFFFF;
}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDateEdit, QListWidget, QTableWidget {
    background: #FFFFFF;
    border: 1px solid #DDE1EB;
    border-radius: 11px;
    padding: 8px 10px;
    selection-background-color: #7867E8;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {
    border: 2px solid #7867E8;
    padding: 7px 9px;
}
QComboBox::drop-down {
    border: 0;
    width: 28px;
}
QPushButton {
    background: #FFFFFF;
    color: #30394D;
    border: 1px solid #DDE1EB;
    border-radius: 11px;
    padding: 9px 15px;
    font-weight: 600;
}
QPushButton:hover {
    background: #F7F5FF;
    border-color: #A99CF3;
}
QPushButton:pressed {
    background: #ECE8FF;
}
QPushButton:disabled {
    background: #F0F1F5;
    color: #A5AABA;
    border-color: #E7E8ED;
}
QPushButton[role="primary"] {
    background: #6C5CE7;
    color: #FFFFFF;
    border: 0;
    padding: 11px 20px;
}
QPushButton[role="primary"]:hover { background: #7C6DEB; }
QPushButton[role="accent"] {
    background: #FF6B8A;
    color: #FFFFFF;
    border: 0;
}
QPushButton[role="mint"] {
    background: #00BFA6;
    color: #FFFFFF;
    border: 0;
}
QProgressBar {
    background: #E9EAF0;
    border: 0;
    border-radius: 8px;
    height: 16px;
    text-align: center;
    color: #FFFFFF;
    font-weight: 600;
}
QProgressBar::chunk {
    border-radius: 8px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6C5CE7, stop:.5 #FF6B8A, stop:1 #FFB84D);
}
QListWidget::item {
    background: #FFFFFF;
    border: 1px solid #EAEBF1;
    border-radius: 12px;
    margin: 4px;
    padding: 12px;
}
QListWidget::item:selected {
    background: #EEEAFE;
    color: #4D3AC8;
    border-color: #A99CF3;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 4px;
}
QScrollBar::handle:vertical {
    background: #CBD0DD;
    min-height: 30px;
    border-radius: 5px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
