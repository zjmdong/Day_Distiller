import base64
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from day_distiller_client.providers.mainland import (
    DeepSeekStoryProvider,
    QwenEvidenceProvider,
    SeedreamImageProvider,
    _normalize_batch_payload,
    _normalize_daily_payload,
    _parse_json_payload,
)


class _ChatCompletions:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.dumps(next(self.payloads))
        if kwargs.get("stream"):
            return iter(
                [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content=payload))]
                    )
                ]
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
        )


class MainlandProviderTests(unittest.TestCase):
    def test_json_parser_accepts_markdown_fences_and_trailing_fence(self) -> None:
        self.assertEqual(_parse_json_payload('{"ok":true}\n```', "qwen"), {"ok": True})
        self.assertEqual(
            _parse_json_payload('```json\n{"ok":true}\n```', "deepseek"), {"ok": True}
        )

    def test_batch_normalizer_repairs_common_omni_type_drift(self) -> None:
        payload = _normalize_batch_payload(
            {
                "transcript": None,
                "ambient_sounds": "道路声",
                "visual_observations": "室内人物挥手",
                "media_quality": "clear enough",
                "frame_scores": ["0.8"],
            },
            2,
        )
        self.assertEqual(payload["ambient_sounds"], ["道路声"])
        self.assertEqual(payload["visual_observations"], ["室内人物挥手"])
        self.assertEqual(payload["frame_scores"], [0.8, 0.5])
        self.assertEqual(payload["media_quality"], 0.5)

    def test_daily_normalizer_maps_common_deepseek_field_names(self) -> None:
        payload = _normalize_daily_payload(
            {
                "date": "2026-07-16",
                "narrative": "晚间记录。",
                "panels": [
                    {
                        "time": "19:31",
                        "title": "挥手",
                        "description": "A person waving indoors",
                    }
                ],
            },
            [{"record_id": "r1", "time_label": "19:31", "summary": "室内挥手"}],
            date(2026, 7, 16),
        )
        self.assertEqual(payload["panels"][0]["record_ids"], ["r1"])
        self.assertEqual(payload["panels"][0]["caption"], "挥手")
        self.assertEqual(payload["one_sentence_summary"], "晚间记录。")

    def test_qwen_omni_then_keyframe_review(self) -> None:
        batch = {
            "transcript": "你好",
            "ambient_sounds": ["道路交通声"],
            "ocr_text": ["便利店"],
            "visual_observations": ["路边"],
            "frame_scores": [0.2, 0.9],
            "media_quality": 0.8,
            "privacy_flags": [],
        }
        scene = {
            "summary": "画面显示用户可能位于道路旁",
            "location_candidate": None,
            "location_confidence": 0,
            "visual_activity": "walking",
            "visual_activity_confidence": 0.8,
            "semantic_significance": 0.5,
            "novelty": 0.4,
            "audio_value": 0.7,
            "memory_relevance": 0,
            "media_quality": 0.8,
            "claims": [],
            "privacy_flags": [],
            "ocr_text": ["便利店"],
            "ambient_sounds": ["道路交通声"],
            "selected_frame_indices": [0],
        }
        chat = _ChatCompletions([batch, scene])
        client = SimpleNamespace(chat=SimpleNamespace(completions=chat))
        provider = QwenEvidenceProvider(client=client, max_attempts=1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = [root / "a.jpg", root / "b.jpg"]
            for frame in frames:
                Image.new("RGB", (8, 8), "red").save(frame)
            audio = root / "audio.wav"
            audio.write_bytes(b"RIFF-test")
            batch_result = provider.analyze_batch(frames, audio)
            scene_result = provider.analyze_scene("r1", datetime.now(), frames, batch_result, None)

        self.assertEqual(batch_result.ambient_sounds, ["道路交通声"])
        self.assertEqual(scene_result.ocr_text, ["便利店"])
        self.assertEqual(chat.calls[0]["model"], "qwen3.5-omni-plus")
        self.assertEqual(chat.calls[1]["model"], "qwen3.7-plus")
        self.assertTrue(chat.calls[0]["stream"])
        self.assertEqual(chat.calls[0]["modalities"], ["text"])
        self.assertFalse(chat.calls[0]["extra_body"]["enable_thinking"])
        audio_item = next(
            item for item in chat.calls[0]["messages"][0]["content"] if item["type"] == "input_audio"
        )
        self.assertTrue(audio_item["input_audio"]["data"].startswith("data:;base64,"))

    def test_deepseek_and_seedream_defaults(self) -> None:
        daily = {
            "title": "一天",
            "one_sentence_summary": "记录了回家路上的片段。",
            "narrative": "傍晚，你走在回家的路上。",
            "panels": [{"record_ids": ["r1"], "time_label": "18:00", "caption": "回家", "image_prompt": "walking home"}],
        }
        chat = _ChatCompletions([daily])
        story = DeepSeekStoryProvider(client=SimpleNamespace(chat=SimpleNamespace(completions=chat)), max_attempts=1)
        image_bytes = b"jpeg-data"
        image_calls = []
        def generate_image(**kwargs):
            image_calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(image_bytes).decode(), url=None)]
            )
        images = SimpleNamespace(
            generate=generate_image
        )
        image_provider = SeedreamImageProvider(client=SimpleNamespace(images=images), max_attempts=1)
        with tempfile.TemporaryDirectory() as temporary:
            output = image_provider.generate_panel("street", Path(temporary) / "panel.jpg")
            result = story.synthesize_day(date(2026, 7, 16), [{"record_id": "r1"}])
            self.assertEqual(output.read_bytes(), image_bytes)
        self.assertEqual(result.title, "一天")
        self.assertEqual(chat.calls[0]["model"], "deepseek-v4-pro")
        self.assertEqual(chat.calls[0]["reasoning_effort"], "high")
        self.assertNotIn("sequential_image_generation", image_calls[0]["extra_body"])
        self.assertNotIn("stream", image_calls[0]["extra_body"])


if __name__ == "__main__":
    unittest.main()
