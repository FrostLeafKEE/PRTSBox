"""Translation through a local Hy-MT2 model served by llama.cpp.

Batching strategy is deliberately *not* the obvious one.  Sending the whole
frame as a single request (numbered lines, or a JSON array) looks cheaper but
measured badly on the 1.8B model: the JSON-array prompt dropped the first item
and returned it untranslated, twice in a row, and the numbered-line variant was
only 1.0-1.4x faster while degrading short UI strings.  Instead each string
gets its own request, issued four at a time to line up with the server's
``-np 4`` slots.

Concurrency was tuned rather than guessed.  The server runs ``-np 4``, and
raising it to ``-np 8`` halves each slot's share of the context; the measured
latency for six strings then got *worse* (1936 ms versus 854 ms at four).
Context size was priced separately and only mildly affects the footprint - see
``LlamaServer.context_size`` - so it is not a lever for the memory problems.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import requests

from ..models import TranslatorConfig
from .base import TranslationError, Translator, build_prompt

if TYPE_CHECKING:
    from ..llama import LlamaManager

# Matches the ``-np`` value the server is started with.
_CONCURRENCY = 4
_MAX_ATTEMPTS = 2
_DEFAULT_TIMEOUT = 60.0
# Kept under the per-slot context (see LlamaServer.context_size) minus the
# prompt, so a long answer is never truncated by the server.
_MAX_TOKENS = 512


def _clean_output(text: str) -> str:
    """Strip artefacts the model occasionally wraps translations in."""
    cleaned = text.strip()
    # A whole-answer quote pair is a wrapper, not part of the translation.
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'“”‘’":
        cleaned = cleaned[1:-1].strip()
    return cleaned


class LocalModelTranslator(Translator):
    def __init__(self, config: TranslatorConfig, manager: LlamaManager) -> None:
        super().__init__(config)
        self._manager = manager
        self._logger = logging.getLogger("prtsbox.translate.local")
        try:
            self._timeout = float(config.credentials.get("local_timeout", _DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            self._timeout = _DEFAULT_TIMEOUT

    def preflight(self) -> None:
        """Fail early, with an actionable message, before a session starts."""
        from ..llama import resolve_model

        if not self._manager.is_runtime_installed():
            raise TranslationError("尚未安装本地推理运行时，请在设置中下载 llama.cpp 运行时")
        model = resolve_model(self.config.model_id)
        if not model.is_downloaded():
            raise TranslationError("尚未下载本地模型，请在设置中下载「标准」或「增强」模型")

    def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []

        from ..llama import resolve_model

        model = resolve_model(self.config.model_id)
        try:
            # No-op when this model is already loaded; on a cold start this is
            # where the 1.2 s (1.8B) / 4.2 s (7B) load happens, kept out of the
            # per-frame budget.
            server = self._manager.ensure_server(model)
        except Exception as exc:
            raise TranslationError(f"启动本地模型失败：{exc}") from exc

        # Strings that need no translation are passed straight through so the
        # worker's cache stores a sensible value for them.
        results: list[str] = [""] * len(texts)
        pending = [(index, text) for index, text in enumerate(texts) if text.strip()]

        if pending:
            workers = min(_CONCURRENCY, len(pending))
            failures: list[str] = []
            # Every request is submitted before any result is awaited, so the
            # four server slots stay busy instead of draining one at a time.
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="prtsbox-mt") as pool:
                futures = {
                    pool.submit(self._translate_one, server.base_url, text): (index, text)
                    for index, text in pending
                }
                for future, (index, text) in futures.items():
                    try:
                        results[index] = future.result()
                    except TranslationError as exc:
                        failures.append(f"{text[:20]!r}: {exc}")
            if failures:
                raise TranslationError("本地翻译失败：" + "；".join(failures[:3]))

        for index, text in enumerate(texts):
            if not text.strip():
                results[index] = text
        return results

    def _translate_one(self, base_url: str, text: str) -> str:
        payload = {
            "messages": [{"role": "user", "content": build_prompt(text, self.config.target_language)}],
            "temperature": 0,
            # Must fit inside one server slot alongside the prompt.  The server
            # runs -c 4096 across 4 slots, so a request gets 1024 tokens total
            # and the prompt takes roughly 100 of them.  512 leaves ample room
            # for a translated line while guaranteeing the two never collide -
            # asking for more than the slot holds truncates the answer instead
            # of failing, which would silently cut translations short.
            "max_tokens": _MAX_TOKENS,
        }
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            if self._manager.closing:
                raise TranslationError("程序正在退出")
            try:
                response = requests.post(
                    f"{base_url}/v1/chat/completions",
                    json=payload,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return _clean_output(str(content))
            except requests.RequestException as exc:
                last_error = exc
                self._logger.warning("本地翻译请求失败（第 %d 次）：%s", attempt + 1, exc)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
                self._logger.warning("本地翻译返回异常：%s", exc)
        raise TranslationError(str(last_error) if last_error else "未知错误")
