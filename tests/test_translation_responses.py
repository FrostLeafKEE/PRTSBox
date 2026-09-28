import json
from types import SimpleNamespace

import pytest

from prtsbox.models import TranslatorConfig
from prtsbox.translate import TranslationError
from prtsbox.translate import local, openai


@pytest.mark.parametrize("translation", [None, 123, {"text": "result"}, "", "   "])
def test_remote_invalid_items_are_not_accepted_as_translations(monkeypatch, translation):
    payload = {"choices": [{"message": {"content": json.dumps([translation])}}]}
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr(openai.requests, "post", lambda *args, **kwargs: response)
    translator = openai.OpenAICompatibleTranslator(TranslatorConfig(credentials={
        "openai_api_key": "test-key", "openai_model": "test-model"}))
    with pytest.raises(TranslationError):
        translator.translate_batch(["Hello"])


@pytest.mark.parametrize("content,reason", [(None, "stop"), ("", "stop"), ("unfinished", "length")])
def test_local_empty_or_truncated_output_is_not_cached(monkeypatch, content, reason):
    payload = {"choices": [{"message": {"content": content}, "finish_reason": reason}]}
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr(local.requests, "post", lambda *args, **kwargs: response)
    translator = local.LocalModelTranslator(TranslatorConfig(), SimpleNamespace(closing=False))
    with pytest.raises(TranslationError):
        translator._translate_one("http://127.0.0.1:1", "Hello")
