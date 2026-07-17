import os
import tempfile
import unittest
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from day_distiller_client.app import MainWindow, NavigationStack
from day_distiller_client.ui_theme import APP_STYLESHEET


class NavigationStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setStyleSheet(APP_STYLESHEET)

    def test_navigation_uses_horizontal_readable_items_and_switches_pages(self) -> None:
        navigation = NavigationStack()
        for label in ("开始", "设备", "记录", "生成", "回忆", "形象与风格", "设置"):
            navigation.addTab(QLabel(label), label)
        navigation.widget.resize(900, 640)
        navigation.widget.show()
        self.application.processEvents()

        self.assertEqual(navigation.count(), 7)
        self.assertEqual(navigation.tabText(5), "形象与风格")
        first_rect = navigation.navigation.visualItemRect(navigation.navigation.item(0))
        self.assertGreater(first_rect.width(), first_rect.height() * 2)
        navigation.setCurrentIndex(5)
        self.application.processEvents()
        self.assertEqual(navigation.currentIndex(), 5)

        navigation.widget.close()

    def test_theme_has_single_blue_accent_on_pure_black(self) -> None:
        self.assertIn("background: #000000", APP_STYLESHEET)
        self.assertIn("#0099ff", APP_STYLESHEET.lower())
        self.assertNotIn("#6c5ce7", APP_STYLESHEET.lower())
        self.assertNotIn("#ff6b8a", APP_STYLESHEET.lower())

    def test_production_navigation_and_nested_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"DAY_DISTILLER_DATA_DIR": root}
        ), patch("day_distiller_client.app.CredentialStore") as credentials:
            credentials.return_value.get.return_value = None
            window = MainWindow()
            labels = [window.tabs.tabText(index) for index in range(window.tabs.count())]
            self.assertEqual(labels, ["开始", "回忆", "形象与风格", "设置"])
            self.assertNotIn("设备", labels)
            self.assertNotIn("记录", labels)
            self.assertNotIn("生成", labels)
            self.assertEqual(window.home_stack.count(), 5)
            self.assertEqual(window.settings_stack.count(), 5)
            self.assertEqual(window.settings_stack.widget(4).findChildren(QLabel)[0].text(), "设备设置")
            self.assertEqual(window.art_style_combo.itemText(0), "扁平色块波普")
            self.assertNotIn("醒目", window.art_style_combo.itemText(0))
            window.window.close()


if __name__ == "__main__":
    unittest.main()
