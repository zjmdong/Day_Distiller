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
)


class _ChatCompletions:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(next(self.payloads))))]
        )


class MainlandProviderTests(unittest.TestCase):
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
        self.assertTrue(any(item["type"] == "input_audio" for item in chat.calls[0]["messages"][0]["content"]))

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
        images = SimpleNamespace(
            generate=lambda **kwargs: SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(image_bytes).decode(), url=None)]
            )
        )
        image_provider = SeedreamImageProvider(client=SimpleNamespace(images=images), max_attempts=1)
        with tempfile.TemporaryDirectory() as temporary:
            output = image_provider.generate_panel("street", Path(temporary) / "panel.jpg")
            result = story.synthesize_day(date(2026, 7, 16), [{"record_id": "r1"}])
            self.assertEqual(output.read_bytes(), image_bytes)
        self.assertEqual(result.title, "一天")
        self.assertEqual(chat.calls[0]["model"], "deepseek-v4-pro")


if __name__ == "__main__":
    unittest.main()
