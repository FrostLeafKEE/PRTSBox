"""Shared data structures and the language table."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class LayoutMode(str, Enum):
    """Where a translation is drawn relative to its source text."""

    BELOW = "below"
    RIGHT = "right"

    @classmethod
    def parse(cls, value: object) -> LayoutMode:
        try:
            return cls(str(value))
        except ValueError:
            return cls.BELOW


# Hy-MT2 prompts take language names spelled out in full, not BCP-47 tags:
# the model card is explicit that "中文使用中文全称，英文使用英文全称".  The
# values here are therefore used verbatim as ``{target_lang}``.
LANGUAGES: dict[str, str] = {
    "auto": "自动检测",
    "zh-CN": "简体中文",
    "zh-TW": "繁体中文",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "pt": "葡萄牙语",
    "ru": "俄语",
    "it": "意大利语",
    "ar": "阿拉伯语",
    "th": "泰语",
    "vi": "越南语",
}

# Languages a user may pick as the translation target.  ``auto`` only makes
# sense as a source.
TARGET_LANGUAGES: list[str] = [code for code in LANGUAGES if code != "auto"]


def language_name(code: str) -> str:
    """Full name of a language code, falling back to the code itself."""
    return LANGUAGES.get(code, code)


def is_chinese_language(code: str) -> bool:
    """Whether a language code is a Chinese variant.

    Used to decide when "skip text that is already Chinese" applies: with an
    English target, Chinese source text is exactly what needs translating.
    """
    return str(code).casefold().startswith("zh")


@dataclass(frozen=True)
class WindowInfo:
    """A capturable top-level window, in virtual-screen pixel coordinates."""

    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height


@dataclass(frozen=True)
class OcrItem:
    """One recognised line of text and, once translated, its translation."""

    box: tuple[tuple[float, float], ...]
    text: str
    confidence: float
    translation: str = ""

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = [point[0] for point in self.box]
        ys = [point[1] for point in self.box]
        return min(xs), min(ys), max(xs), max(ys)

    def with_translation(self, translation: str) -> OcrItem:
        return replace(self, translation=translation)


@dataclass
class TranslatorConfig:
    """Everything a translation backend needs for one batch."""

    engine: str = "local"
    source_language: str = "auto"
    target_language: str = "zh-CN"
    # Which bundled GGUF to serve when ``engine`` is ``local``.  Not a
    # credential, so it stays a first-class field.
    model_id: str = ""
    # Drop lines that are already written in the target language's script.
    skip_chinese: bool = True
    credentials: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict) -> TranslatorConfig:
        credentials = raw.get("credentials")
        return cls(
            engine=str(raw.get("engine", "local")),
            source_language=str(raw.get("source_language", "auto")),
            target_language=str(raw.get("target_language", "zh-CN")),
            model_id=str(raw.get("model_id", "")),
            skip_chinese=bool(raw.get("skip_chinese", True)),
            credentials=dict(credentials) if isinstance(credentials, dict) else {},
        )

    def to_dict(self) -> dict:
        """Plain-data form, safe to hand to a worker thread.

        The worker runs on another thread, so it must never hold a reference to
        the config object the UI mutates.
        """
        return {
            "engine": self.engine,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "model_id": self.model_id,
            "skip_chinese": self.skip_chinese,
            "credentials": dict(self.credentials),
        }


@dataclass
class CaptureTiming:
    """Per-frame measurements, surfaced in the overlay and the log."""

    capture_ms: float = 0.0
    ocr_ms: float = 0.0
    translate_ms: float = 0.0
    render_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return self.capture_ms + self.ocr_ms + self.translate_ms + self.render_ms
