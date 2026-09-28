"""Translator interface shared by every backend.

The contract is deliberately narrow: hand it the strings that came out of one
frame and get back a list of the same length and order.  Callers rely on the
index alignment to place each translation over the right box, so a backend that
cannot guarantee it must raise rather than return a short list.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..models import TranslatorConfig, language_name

if TYPE_CHECKING:
    from ..llama import LlamaManager


class TranslationError(RuntimeError):
    pass


def build_prompt(text: str, target_language: str) -> str:
    """The prompt Hy-MT2 documents for plain translation.

    The model card is explicit that language names must be spelled out in full
    ("简体中文" rather than "zh-CN") and that the instruction should demand the
    translation alone, otherwise the model tends to prepend commentary.
    """
    return (
        f"将以下文本翻译为 {language_name(target_language)}，"
        f"注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"
    )


class Translator(ABC):
    def __init__(self, config: TranslatorConfig) -> None:
        self.config = config

    @abstractmethod
    def translate_batch(self, texts: list[str]) -> list[str]:
        """Translate every string, preserving order and length."""

    def preflight(self) -> None:
        """Raise :class:`TranslationError` if the backend is not usable yet.

        Called before a session starts so that a missing API key or an absent
        model surfaces as a dialog rather than as a failed first frame.
        """


def create_translator(
    config: TranslatorConfig,
    *,
    llama_manager: LlamaManager | None = None,
) -> Translator:
    from .local import LocalModelTranslator
    from .openai import OpenAICompatibleTranslator

    if config.engine == "local":
        if llama_manager is None:
            raise TranslationError("本地翻译引擎需要 llama 运行时管理器")
        return LocalModelTranslator(config, llama_manager)
    if config.engine == "openai":
        return OpenAICompatibleTranslator(config)
    if config.engine == "platform":
        from .platform import PlatformTranslator
        return PlatformTranslator(config)
    raise TranslationError(f"不支持的翻译引擎：{config.engine}")
