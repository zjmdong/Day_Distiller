from __future__ import annotations

import base64
import json
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from ..domain import MotionAssessment
from .base import DailySynthesis, SceneAnalysis


T = TypeVar("T")


@dataclass(frozen=True)
class ModelSettings:
    scene_model: str = "gpt-5.6-terra"
    daily_model: str = "gpt-5.6-sol"
    transcription_model: str = "gpt-4o-transcribe"
    image_model: str = "gpt-image-2"
    image_quality: str = "high"
    image_size: str = "1536x1024"


class OpenAIProvider:
    def __init__(
        self,
        api_key: str | None = None,
        settings: ModelSettings | None = None,
        client: Any | None = None,
        max_attempts: int = 3,
    ) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
        self.client = client
        self.settings = settings or ModelSettings()
        self.max_attempts = max(1, max_attempts)

    def transcribe(self, audio_path: Path) -> str:
        def request() -> Any:
            with Path(audio_path).open("rb") as stream:
                return self.client.audio.transcriptions.create(
                    file=stream,
                    model=self.settings.transcription_model,
                    language="zh",
                    response_format="text",
                )

        result = self._retry(request)
        if isinstance(result, str):
            return result.strip()
        return str(getattr(result, "text", "")).strip()

    def analyze_scene(
        self,
        record_id: str,
        captured_at: datetime,
        frame_paths: list[Path],
        transcript: str,
        motion: MotionAssessment | None,
    ) -> SceneAnalysis:
        motion_json = json.dumps(asdict(motion), ensure_ascii=False) if motion else "null"
        prompt = (
            f"记录ID：{record_id}\n记录时间：{captured_at.isoformat()}\n"
            f"音频转写：{transcript or '无可靠语音'}\n本地IMU分析：{motion_json}\n"
            "只分析这一个约5秒的采样片段。区分直接观察、传感器结论和推测；"
            "不得推断采样间隔内持续发生的事情，不得识别具体人物，不得凭空给出地点名。"
        )
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for frame_path in frame_paths:
            content.append(
                {
                    "type": "input_image",
                    "image_url": _image_data_url(frame_path),
                    "detail": "high",
                }
            )

        def request() -> Any:
            return self.client.responses.parse(
                model=self.settings.scene_model,
                store=False,
                reasoning={"effort": "medium"},
                instructions=(
                    "你是可穿戴生活记录产品的事实核验分析器。输出必须符合给定结构，"
                    "置信度代表证据强度，不要为了故事性补全未知信息。"
                ),
                input=[{"role": "user", "content": content}],
                text_format=SceneAnalysis,
                max_output_tokens=4000,
            )

        response = self._retry(request)
        if response.output_parsed is None:
            raise RuntimeError("OpenAI scene response did not contain parsed structured output")
        return response.output_parsed

    def synthesize_day(self, report_date: date, scenes: list[dict[str, object]]) -> DailySynthesis:
        prompt = json.dumps(
            {
                "date": report_date.isoformat(),
                "rules": {
                    "output": "只规划一张3:4竖版海报，筛选2-3个最有意义的场景融合",
                    "no_gap_inference": True,
                    "no_face_identification": True,
                    "assert_threshold": 0.8,
                    "cautious_threshold": 0.6,
                    "image_text": "图片中不要出现任何文字",
                },
                "scenes": scenes,
            },
            ensure_ascii=False,
        )

        def request() -> Any:
            return self.client.responses.parse(
                model=self.settings.daily_model,
                store=False,
                reasoning={"effort": "high"},
                instructions=(
                    "你是每日生活报告编辑。只能使用输入证据，生成主题标题、一句暖心话，"
                    "并规划一张融合2-3个精选瞬间的无文字竖版海报。"
                    "低置信度信息使用可能、看起来等措辞，低于0.6的推断省略。"
                ),
                input=prompt,
                text_format=DailySynthesis,
                max_output_tokens=12000,
            )

        response = self._retry(request)
        if response.output_parsed is None:
            raise RuntimeError("OpenAI daily response did not contain parsed structured output")
        return response.output_parsed

    def generate_panel(
        self,
        prompt: str,
        destination: Path,
        reference_images: list[Path] | None = None,
    ) -> Path:
        final_prompt = (
            "温暖电影感的现代日记漫画，统一角色与色彩，生活化构图。"
            "画面内不得出现文字、字幕、水印、对话框或标牌文字。" + prompt
        )

        def request() -> Any:
            references = [Path(path).open("rb") for path in (reference_images or [])]
            try:
                if references:
                    return self.client.images.edit(
                        model=self.settings.image_model,
                        image=references,
                        prompt=final_prompt,
                        quality=self.settings.image_quality,
                        size=self.settings.image_size,
                        output_format="jpeg",
                        response_format="b64_json",
                    )
                return self.client.images.generate(
                    model=self.settings.image_model,
                    prompt=final_prompt,
                    quality=self.settings.image_quality,
                    size=self.settings.image_size,
                    output_format="jpeg",
                    response_format="b64_json",
                )
            finally:
                for stream in references:
                    stream.close()

        result = self._retry(request)
        if not result.data or not result.data[0].b64_json:
            raise RuntimeError("OpenAI image response did not contain image data")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(base64.b64decode(result.data[0].b64_json))
        return destination

    def _retry(self, operation: Callable[[], T]) -> T:
        error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                return operation()
            except Exception as exc:
                error = exc
                if attempt + 1 < self.max_attempts:
                    time.sleep(2**attempt)
        assert error is not None
        raise error


def _image_data_url(path: Path) -> str:
    suffix = Path(path).suffix.lower()
    mime = {".png": "image/png", ".webp": "image/webp"}.get(suffix, "image/jpeg")
    encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"
