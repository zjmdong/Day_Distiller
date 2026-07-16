from __future__ import annotations

from dataclasses import dataclass


MAX_CUSTOM_STYLE_CHARS = 200
DEFAULT_ART_STYLE_ID = "flat_pop"
CUSTOM_ART_STYLE_ID = "custom"


@dataclass(frozen=True)
class ArtStyle:
    id: str
    name: str
    tagline: str
    description: str
    prompt: str
    accent: str


ART_STYLES: tuple[ArtStyle, ...] = (
    ArtStyle(
        id="flat_pop",
        name="扁平色块波普",
        tagline="醒目、有活力，适合人物与城市日常",
        description="大面积纯色色块、圆润轮廓与极简五官，把真实瞬间提炼成有记忆点的当代海报。",
        prompt=(
            "Minimal flat-color pop illustration. Summarize people and environments with large, "
            "confident blocks of saturated color; use clean rounded contours, simplified facial "
            "features, very little material detail, and subtle controlled gradients only where "
            "needed for depth. Use a lively cobalt-blue, coral-orange, sun-yellow and deep-navy "
            "palette. The result should feel warm, contemporary, premium and instantly readable."
        ),
        accent="#FF6B6B",
    ),
    ArtStyle(
        id="cinematic_realism",
        name="电影感写实",
        tagline="真实、克制，保留一天的光线与质感",
        description="像高品质生活方式杂志的电影剧照，强调自然光、真实环境和温柔的情绪色调。",
        prompt=(
            "Cinematic editorial realism with authentic natural light, believable skin and fabric "
            "textures, a documentary sense of place, restrained depth of field, and a refined film "
            "color grade. Keep people natural rather than glamorous, preserve small imperfections, "
            "and blend the moments with elegant lighting transitions. Avoid uncanny faces, plastic "
            "skin, excessive HDR, or fantasy embellishment."
        ),
        accent="#4D96FF",
    ),
    ArtStyle(
        id="graphic_novel",
        name="当代漫画",
        tagline="叙事感强，让多个瞬间自然连成一幕",
        description="现代图像小说风格，以有力度的线条、层次分明的色彩和电影化构图讲述当天故事。",
        prompt=(
            "Premium contemporary graphic-novel illustration with expressive ink contours, "
            "cinematic perspective, controlled halftone texture, layered cel shading and rich but "
            "harmonious colors. Fuse the selected moments into one continuous visual narrative, "
            "without comic boxes, gutters, speech balloons, captions, or printed sound effects."
        ),
        accent="#845EF7",
    ),
    ArtStyle(
        id="abstract_minimal",
        name="抽象极简",
        tagline="安静、留白，用形状提炼当天的情绪",
        description="用几何、负空间和少量关键物件表达一天的主题，适合氛围与情绪型记忆。",
        prompt=(
            "Abstract minimalist editorial art built from geometric forms, generous negative space, "
            "a disciplined limited palette and a few recognizable objects or silhouettes from the "
            "source moments. Express the emotional rhythm of the day through scale, balance and "
            "color relationships. Keep the composition calm, intentional and museum-poster quality, "
            "while retaining enough factual visual anchors to recognize the memories."
        ),
        accent="#00B894",
    ),
    ArtStyle(
        id="poetic_collage",
        name="诗意拼贴",
        tagline="梦境般的层次，适合旅行、聚会与特别一天",
        description="摄影碎片、纸张肌理和手绘笔触彼此穿插，在真实记录与想象空间之间形成纪念册质感。",
        prompt=(
            "Poetic mixed-media editorial collage combining carefully cut photographic forms, "
            "soft paper grain, translucent color fields and delicate hand-painted marks. Use surreal "
            "but graceful transitions to connect the real moments, with strong visual hierarchy and "
            "tactile depth. Keep the people and key places recognizable, elegant and emotionally warm; "
            "avoid scrapbook clutter, stickers, frames, or decorative typography."
        ),
        accent="#FF9F43",
    ),
)


def get_art_style(style_id: str) -> ArtStyle:
    return next((style for style in ART_STYLES if style.id == style_id), ART_STYLES[0])


def validate_custom_style_prompt(value: str) -> str:
    prompt = value.strip()
    if len(prompt) > MAX_CUSTOM_STYLE_CHARS:
        raise ValueError(f"自定义艺术风格不能超过 {MAX_CUSTOM_STYLE_CHARS} 字")
    return prompt


def resolve_art_style_prompt(style_id: str, custom_prompt: str = "") -> tuple[str, str]:
    if style_id == CUSTOM_ART_STYLE_ID:
        prompt = validate_custom_style_prompt(custom_prompt)
        if not prompt:
            raise ValueError("选择自定义风格后，请填写一段艺术风格提示词")
        return "自定义风格", prompt
    style = get_art_style(style_id)
    return style.name, style.prompt
