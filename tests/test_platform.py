import hashlib
from types import SimpleNamespace

import pytest
import requests

from prtsbox.config import ConfigStore
from prtsbox.models import TranslatorConfig
from prtsbox.translate import TranslationError, create_translator
from prtsbox.translate import platform as module
from test_gui_session import qapp, patched_pipeline, make_window


def translator(provider, **extra):
    config = TranslatorConfig(engine="platform", credentials={
        "translation_platform": provider, "azure_api_key": "azure-test-key",
        "deepl_api_key": "deepl-test-key", "deepl_plan": "free",
        "baidu_app_id": "test-app", "baidu_secret_key": "baidu-test-secret", **extra,
    })
    return create_translator(config)


@pytest.fixture
def api(monkeypatch):
    calls = []
    result = SimpleNamespace(status_code=200, payload=None)

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=result.status_code, json=lambda: result.payload)

    monkeypatch.setattr(module.requests, "post", post)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    result.calls = calls
    return result


def test_azure_auto_region_and_order(api):
    api.payload = [{"translations": [{"text": t}]} for t in ["甲", "乙"]]
    engine = translator("azure", azure_region="eastasia")
    assert engine.translate_batch(["Alpha", "Beta"]) == ["甲", "乙"]
    url, request = api.calls[0]
    assert url == module.AZURE_ENDPOINT + "/translate"
    assert request["params"] == {"api-version": "3.0", "to": "zh-Hans"}
    assert request["headers"]["Ocp-Apim-Subscription-Region"] == "eastasia"
    assert request["headers"]["Ocp-Apim-Subscription-Key"] == "azure-test-key"
    assert request["json"] == [{"Text": "Alpha"}, {"Text": "Beta"}]


def test_azure_custom_endpoint_and_explicit_source(api):
    api.payload = [{"translations": [{"text": "譯文"}]}]
    engine = translator("azure", azure_endpoint="https://example.cognitiveservices.azure.com/")
    engine.config.source_language = "ja"
    engine.config.target_language = "zh-TW"
    engine.translate_batch(["Hello"])
    assert api.calls[0][0].endswith("/translator/text/v3.0/translate")
    assert api.calls[0][1]["params"]["to"] == "zh-Hant"
    assert api.calls[0][1]["params"]["from"] == "ja"


@pytest.mark.parametrize("plan,host", [("free", "api-free.deepl.com"), ("pro", "api.deepl.com")])
def test_deepl_plan_auth_and_language(api, plan, host):
    api.payload = {"translations": [{"text": "譯文"}]}
    engine = translator("deepl", deepl_plan=plan)
    engine.config.target_language = "zh-TW"
    assert engine.translate_batch(["Hello"]) == ["譯文"]
    url, request = api.calls[0]
    assert url == f"https://{host}/v2/translate"
    assert request["headers"]["Authorization"] == "DeepL-Auth-Key deepl-test-key"
    assert request["json"] == {"text": ["Hello"], "target_lang": "ZH-HANT"}


def test_baidu_signature_unicode_and_language(api, monkeypatch):
    monkeypatch.setattr(module.secrets, "token_hex", lambda _: "salt")
    api.payload = {"trans_result": [{"dst": "one"}, {"dst": "two"}]}
    engine = translator("baidu")
    engine.config.source_language = "ko"
    engine.config.target_language = "fr"
    assert engine.translate_batch(["中文 & +", "Second\nline"]) == ["one", "two"]
    url, request = api.calls[0]
    data = request["data"]
    assert url == "https://fanyi-api.baidu.com/api/trans/vip/translate"
    assert data["q"] == "中文 & +\nSecond line"
    assert data["from"] == "kor" and data["to"] == "fra"
    assert data["sign"] == hashlib.md5(("test-app" + data["q"] + "saltbaidu-test-secret").encode()).hexdigest()
    assert "baidu-test-secret" not in repr(request)


@pytest.mark.parametrize("provider,credential", [("azure", "azure_api_key"), ("deepl", "deepl_api_key"), ("baidu", "baidu_app_id"), ("baidu", "baidu_secret_key")])
def test_missing_credentials_fail_before_network(api, provider, credential):
    with pytest.raises(TranslationError, match="凭据"):
        translator(provider, **{credential: ""}).translate_batch(["Hello"])
    assert not api.calls


@pytest.mark.parametrize("provider,payload", [("azure", []), ("deepl", {"translations": []}), ("baidu", {"trans_result": []}), ("azure", {"unexpected": 1}), ("deepl", {"translations": [{"text": None}]})])
def test_malformed_or_missing_translations_are_rejected(api, provider, payload):
    api.payload = payload
    with pytest.raises(TranslationError):
        translator(provider).translate_batch(["Hello"])


@pytest.mark.parametrize("status", [401, 403, 429, 456, 500])
def test_http_errors_do_not_expose_keys(api, status):
    api.status_code = status
    with pytest.raises(TranslationError) as error:
        translator("deepl").translate_batch(["Hello"])
    assert str(status) in str(error.value)
    assert "test-key" not in str(error.value)


def test_network_and_invalid_json_errors(api, monkeypatch):
    def fail(*a, **kw):
        raise requests.Timeout("contains-secret")
    monkeypatch.setattr(module.requests, "post", fail)
    with pytest.raises(TranslationError, match="超时") as error:
        translator("azure").translate_batch(["Hello"])
    assert "contains-secret" not in str(error.value)


def test_baidu_service_error(api):
    api.payload = {"error_code": "54001", "error_msg": "Invalid Sign"}
    with pytest.raises(TranslationError, match="签名错误"):
        translator("baidu").translate_batch(["Hello"])


def test_invalid_json_is_reported_without_response_body(monkeypatch):
    def bad_json():
        raise ValueError("sensitive-response")
    monkeypatch.setattr(module.requests, "post", lambda *a, **kw:
                        SimpleNamespace(status_code=200, json=bad_json))
    with pytest.raises(TranslationError, match="JSON") as error:
        translator("azure").translate_batch(["Hello"])
    assert "sensitive-response" not in str(error.value)


@pytest.mark.parametrize("url", ["http://example.com", "https://example.com?key=secret", "https://user:pass@example.com", "https://[invalid"])
def test_azure_rejects_invalid_endpoint_before_request(api, url):
    with pytest.raises(TranslationError):
        translator("azure", azure_endpoint=url).translate_batch(["Hello"])
    assert not api.calls


def test_baidu_batches_by_utf8_bytes_and_throttles(monkeypatch):
    calls, sleeps = [], []
    def post(url, **kw):
        query = kw["data"]["q"]
        calls.append(query)
        return SimpleNamespace(status_code=200, json=lambda: {
            "trans_result": [{"dst": text + "!"} for text in query.split("\n")]})
    monkeypatch.setattr(module.requests, "post", post)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(module, "_baidu_last_request", 100.0)
    texts = ["甲" * 200, "乙" * 200]
    assert translator("baidu").translate_batch(texts) == [t + "!" for t in texts]
    assert len(calls) == 2 and all(len(q.encode("utf-8")) <= 900 for q in calls)
    assert sleeps == [1.05, 1.05]


def test_empty_strings_keep_positions_and_skip_network(api):
    api.payload = {"translations": [{"text": "你好"}]}
    assert translator("deepl").translate_batch(["", "Hello", "  "]) == ["", "你好", "  "]
    assert len(api.calls) == 1
    api.calls.clear()
    assert translator("azure").translate_batch([]) == []
    assert not api.calls


def test_batch_size_limit_preserves_order(api):
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["json"]["text"])
        return SimpleNamespace(status_code=200, json=lambda: {"translations": [
            {"text": t + "!"} for t in kwargs["json"]["text"]]})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module.requests, "post", post)
        texts = [str(i) for i in range(51)]
        assert translator("deepl").translate_batch(texts) == [t + "!" for t in texts]
    assert [len(c) for c in calls] == [50, 1]


def test_platform_config_and_encrypted_keys_round_trip(qapp, tmp_path):
    from prtsbox.llama import LlamaManager
    from prtsbox.ui.settings_dialog import SettingsDialog
    store = ConfigStore(tmp_path / "config.json")
    dialog = SettingsDialog(store, LlamaManager())
    try:
        dialog._platform_fields["azure_api_key"].setText("my-azure-secret")
        dialog._platform_fields["deepl_api_key"].setText("my-deepl-secret")
        dialog._platform_fields["baidu_secret_key"].setText("my-baidu-secret")
        dialog._platform_fields["baidu_app_id"].setText("123456")
        dialog._platform_combo.setCurrentIndex(dialog._platform_combo.findData("baidu"))
        dialog._save_platform()
        store.set("engine", "platform")
        store.save()
        disk = store.path.read_text(encoding="utf-8")
        assert "my-azure-secret" not in disk and "my-baidu-secret" not in disk and "my-deepl-secret" not in disk
        loaded = ConfigStore(store.path)
        assert loaded.load()["engine"] == "platform"
        assert loaded.get("translation_platform") == "baidu"
        assert loaded.get_secret("azure_api_key") == "my-azure-secret"
        assert loaded.get_secret("deepl_api_key") == "my-deepl-secret"
        assert loaded.get_secret("baidu_secret_key") == "my-baidu-secret"
    finally:
        dialog.reject()


def test_main_window_has_three_engine_types(qapp, patched_pipeline):
    window = make_window(patched_pipeline)
    try:
        assert {window._engine_combo.itemData(i) for i in range(window._engine_combo.count())} == {"local", "openai", "platform"}
        window._engine_combo.setCurrentIndex(window._engine_combo.findData("platform"))
        window._platform_combo.setCurrentIndex(window._platform_combo.findData("deepl"))
        config = window.translator_config()
        assert config.engine == "platform"
        assert config.credentials["translation_platform"] == "deepl"
        assert window._model_combo.isHidden()
        assert not window._platform_combo.isHidden()
        window._config.update(engine="platform", translation_platform="baidu")
        window.load_from_config()
        assert window._platform_combo.currentData() == "baidu"
        assert window._model_combo.isHidden()
        assert not window._platform_combo.isHidden()
    finally:
        window.shutdown()
