import unittest

from day_distiller_client.art_styles import (
    ART_STYLES,
    CUSTOM_ART_STYLE_ID,
    MAX_CUSTOM_STYLE_CHARS,
    resolve_art_style_prompt,
    validate_custom_style_prompt,
)


class ArtStyleTests(unittest.TestCase):
    def test_five_presets_have_distinct_complete_prompts(self) -> None:
        self.assertEqual(len(ART_STYLES), 5)
        self.assertEqual(len({style.id for style in ART_STYLES}), 5)
        self.assertTrue(all(len(style.prompt) > 120 for style in ART_STYLES))
        self.assertTrue(all(style.name and style.description and style.accent for style in ART_STYLES))

    def test_custom_prompt_is_required_and_capped_at_200_characters(self) -> None:
        accepted = "柔和水彩与铅笔线条" * 10
        self.assertEqual(validate_custom_style_prompt(accepted), accepted)
        name, prompt = resolve_art_style_prompt(CUSTOM_ART_STYLE_ID, accepted)
        self.assertEqual(name, "自定义风格")
        self.assertEqual(prompt, accepted)
        with self.assertRaisesRegex(ValueError, "请填写"):
            resolve_art_style_prompt(CUSTOM_ART_STYLE_ID, "")
        with self.assertRaisesRegex(ValueError, str(MAX_CUSTOM_STYLE_CHARS)):
            validate_custom_style_prompt("风" * (MAX_CUSTOM_STYLE_CHARS + 1))


if __name__ == "__main__":
    unittest.main()
