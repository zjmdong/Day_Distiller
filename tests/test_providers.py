import base64
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from day_distiller_client.providers.base import DailySynthesis, PanelPlan, SceneAnalysis
from day_distiller_client.providers.openai_provider import OpenAIProvider


class _Responses:
    def __init__(self) -> None:
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["text_format"] is SceneAnalysis:
            value = SceneAnalysis(
                summary="直接观察到桌面",
                location_candidate=None,
                location_confidence=0,
                visual_activity="stationary",
                visual_activity_confidence=0.9,
                semantic_significance=0.5,
                novelty=0.4,
                audio_value=0,
                memory_relevance=0,
                media_quality=0.9,
                claims=[],
                privacy_flags=[],
            )
        else:
            value = DailySynthesis(
                title="测试日报",
                one_sentence_summary="一天",
                narrative="事实",
                panels=[PanelPlan(record_ids=["r1"], time_label="10:00", caption="桌面", image_prompt="桌面")],
            )
        return SimpleNamespace(output_parsed=value)


class _Client:
    def __init__(self, image_bytes: bytes) -> None:
        self.responses = _Responses()
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(create=lambda **kwargs: "转写")
        )
        image_result = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(image_bytes).decode())])
        self.images = SimpleNamespace(generate=lambda **kwargs: image_result, edit=lambda **kwargs: image_result)


class ProviderTests(unittest.TestCase):
    def test_openai_uses_ephemeral_structured_multi_image_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame = root / "frame.jpg"
            Image.new("RGB", (10, 10), "red").save(frame)
            client = _Client(frame.read_bytes())
            provider = OpenAIProvider(client=client, max_attempts=1)

            result = provider.analyze_scene("r1", datetime.now(), [frame], "", None)

            self.assertEqual(result.summary, "直接观察到桌面")
            call = client.responses.calls[0]
            self.assertFalse(call["store"])
            self.assertIs(call["text_format"], SceneAnalysis)
            image_input = call["input"][0]["content"][1]
            self.assertEqual(image_input["type"], "input_image")
            self.assertTrue(image_input["image_url"].startswith("data:image/jpeg;base64,"))

    def test_daily_and_image_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample = root / "sample.jpg"
            Image.new("RGB", (10, 10), "blue").save(sample)
            client = _Client(sample.read_bytes())
            provider = OpenAIProvider(client=client, max_attempts=1)

            synthesis = provider.synthesize_day(date(2026, 7, 15), [{"record_id": "r1"}])
            destination = provider.generate_panel("没有文字", root / "panel.jpg")

            self.assertEqual(synthesis.title, "测试日报")
            self.assertTrue(destination.is_file())
            self.assertFalse(client.responses.calls[0]["store"])


if __name__ == "__main__":
    unittest.main()
