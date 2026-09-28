"""Translation backends."""

from __future__ import annotations

from .base import TranslationError, Translator, create_translator
from .local import LocalModelTranslator
from .openai import OpenAICompatibleTranslator
from .platform import PlatformTranslator

__all__ = [
    "LocalModelTranslator",
    "OpenAICompatibleTranslator",
    "PlatformTranslator",
    "TranslationError",
    "Translator",
    "create_translator",
]
