import unittest

from day_distiller_client.app import _validate_base_url, _validate_image_size


class AppSettingsTests(unittest.TestCase):
    def test_validates_provider_base_urls(self) -> None:
        self.assertEqual(
            _validate_base_url("https://api.deepseek.com/", "DeepSeek"),
            "https://api.deepseek.com",
        )
        with self.assertRaisesRegex(ValueError, "Base URL"):
            _validate_base_url(
                "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                "百炼",
            )

    def test_validates_seedream_size(self) -> None:
        self.assertEqual(_validate_image_size("2048x1536"), "2048x1536")
        self.assertEqual(_validate_image_size("2k"), "2K")
        with self.assertRaisesRegex(ValueError, "漫画尺寸"):
            _validate_image_size("16:9")


if __name__ == "__main__":
    unittest.main()
