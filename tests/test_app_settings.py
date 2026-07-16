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
        self.assertEqual(_validate_image_size("1328x1776"), "1328x1776")
        with self.assertRaisesRegex(ValueError, "236万"):
            _validate_image_size("1536x2048")
        with self.assertRaisesRegex(ValueError, "宽x高"):
            _validate_image_size("2k")
        with self.assertRaisesRegex(ValueError, "3:4"):
            _validate_image_size("1000x1600")


if __name__ == "__main__":
    unittest.main()
