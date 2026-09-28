"""Any OpenAI-compatible chat-completions endpoint.

Unlike the local backend this one sends the whole batch as a single request and
asks for a JSON array back.  That is safe here because the models this path
targets (GPT, DeepSeek, Kimi, GLM, ...) follow the format reliably, and it keeps
the request count - and therefore the bill - proportional to frames rather than
to strings.
"""

from __future__ import annotations

import json
import re

import requests

from ..models import TranslatorConfig, language_name
from .base import TranslationError, Translator

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_TIMEOUT = 45.0

# Models that reason by default would spend seconds thinking about each frame
# before emitting anything, which is fatal for a live overlay.  Each family
# disables it differently.
_THINKING_OFF: tuple[tuple[str, dict], ...] = (
    ("deepseek", {"thinking": {"type": "disabled"}}),
    ("kimi-k2.6", {"thinking": {"type": "disabled"}}),
    ("minimax-m3", {"reasoning": {"effort": "none"}}),
)
_GLM_PATTERN = re.compile(r"glm-(?:[5-9](?:\.\d+)?|4\.[5-9])(?:$|[-.])")


def _thinking_switch(model: str) -> dict:
    name = model.casefold().replace("_", "-")
    for prefix, payload in _THINKING_OFF:
        if name.startswith(prefix):
            return payload
    if name.startswith("kimi-k3"):
        return {"reasoning_effort": "low"}
    if _GLM_PATTERN.match(name):
        return {"thinking": {"type": "disabled"}}
    return {}


class OpenAICompatibleTranslator(Translator):
    def __init__(self, config: TranslatorConfig) -> None:
        super().__init__(config)
        credentials = config.credentials
        self._api_key = credentials.get("openai_api_key", "").strip()
        self._base_url = (
            credentials.get("openai_base_url", "").strip() or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._model = credentials.get("openai_model", "").strip()
        try:
            self._timeout = float(credentials.get("openai_timeout", _DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            self._timeout = _DEFAULT_TIMEOUT

    def preflight(self) -> None:
        if not self._api_key:
            raise TranslationError("请在设置中填写接口 API Key")
        if not self._model:
            raise TranslationError("请在设置中填写模型名称")

    def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        self.preflight()

        target = language_name(self.config.target_language)
        prompt = (
            f"把下面的文本逐条翻译为{target}。"
            "只返回一个 JSON 字符串数组，元素数量和顺序必须与输入一致，不要解释。\n"
            + json.dumps(texts, ensure_ascii=False)
        )
        payload = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "你是实时屏幕翻译器，准确简洁地翻译文本。"},
                {"role": "user", "content": prompt},
            ],
        }
        payload.update(_thinking_switch(self._model))

        try:
            response = requests.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise TranslationError(f"翻译请求失败：{exc}") from exc
        except ValueError as exc:
            raise TranslationError(f"接口返回的不是 JSON：{exc}") from exc

        if "error" in data and isinstance(data["error"], dict):
            message = data["error"].get("message", data["error"])
            raise TranslationError(f"接口返回错误：{message}")

        try:
            content = str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError(f"接口返回结构异常：{data}") from exc

        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
        try:
            translated = json.loads(content)
        except ValueError as exc:
            raise TranslationError(f"模型没有返回有效的译文数组：{content[:200]}") from exc

        if not isinstance(translated, list) or len(translated) != len(texts):
            count = len(translated) if isinstance(translated, list) else "非数组"
            raise TranslationError(
                f"模型返回的译文数量（{count}）与原文数量（{len(texts)}）不一致"
            )
        return [str(item) for item in translated]
