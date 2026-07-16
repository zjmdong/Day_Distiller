import os
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from day_distiller_client.app import NavigationStack
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


if __name__ == "__main__":
    unittest.main()
