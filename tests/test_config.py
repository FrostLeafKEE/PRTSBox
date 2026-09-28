"""Configuration store: defaults, persistence and secret handling."""

from __future__ import annotations

import json

import pytest

from prtsbox.config import DEFAULTS, ConfigStore


@pytest.fixture()
def store(tmp_path):
    return ConfigStore(tmp_path / "config.json")


class TestDefaults:
    def test_fresh_store_uses_defaults(self, store: ConfigStore) -> None:
        store.load()
        assert store.get("engine") == "local"
        assert store.get("target_language") == "zh-CN"

    def test_missing_file_is_not_an_error(self, store: ConfigStore) -> None:
        assert store.load() == DEFAULTS


class TestPersistence:
    def test_round_trip(self, store: ConfigStore) -> None:
        store.load()
        store.set("overlay_font_size", 20)
        store.save()

        reopened = ConfigStore(store.path)
        reopened.load()
        assert reopened.get("overlay_font_size") == 20

    def test_corrupt_file_falls_back_to_defaults(self, store: ConfigStore) -> None:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{ this is not json", encoding="utf-8")
        loaded = store.load()
        assert loaded["engine"] == DEFAULTS["engine"]

    def test_non_dict_payload_falls_back(self, store: ConfigStore) -> None:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("[1, 2, 3]", encoding="utf-8")
        assert store.load()["engine"] == DEFAULTS["engine"]

    def test_save_is_atomic_leaving_no_temp_file(self, store: ConfigStore) -> None:
        store.load()
        store.save()
        assert not store.path.with_suffix(".json.tmp").exists()
        assert json.loads(store.path.read_text(encoding="utf-8"))


class TestEngineMigration:
    @pytest.mark.parametrize("retired", ["baidu", "baidu_llm", "tencent"])
    def test_retired_engines_fall_back_to_local(self, store: ConfigStore, retired: str) -> None:
        # A config from an older build must not leave the app pointing at a
        # backend this build cannot construct.
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps({"engine": retired}), encoding="utf-8")
        assert store.load()["engine"] == "local"

    @pytest.mark.parametrize(
        ("alias", "expected"),
        [("local_model", "local"), ("llama", "local"), ("openai_compatible", "openai")],
    )
    def test_engine_aliases_resolve(self, store: ConfigStore, alias: str, expected: str) -> None:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps({"engine": alias}), encoding="utf-8")
        assert store.load()["engine"] == expected

    def test_openai_is_preserved(self, store: ConfigStore) -> None:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps({"engine": "openai"}), encoding="utf-8")
        assert store.load()["engine"] == "openai"


class TestValidation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("99", 28), ("1", 9), ("nonsense", 14), (None, 14), (16, 16)],
    )
    def test_font_size_is_clamped(self, store: ConfigStore, raw, expected: int) -> None:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps({"overlay_font_size": raw}), encoding="utf-8")
        assert store.load()["overlay_font_size"] == expected


class TestSecrets:
    def test_secret_round_trip(self, store: ConfigStore) -> None:
        store.load()
        store.set_secret("openai_api_key", "sk-test-12345")
        assert store.get_secret("openai_api_key") == "sk-test-12345"

    def test_secret_is_not_stored_in_clear(self, store: ConfigStore) -> None:
        store.load()
        store.set_secret("openai_api_key", "sk-test-12345")
        store.save()
        raw = store.path.read_text(encoding="utf-8")
        assert "sk-test-12345" not in raw

    def test_missing_secret_is_empty(self, store: ConfigStore) -> None:
        store.load()
        assert store.get_secret("openai_api_key") == ""
        assert not store.has_secret("openai_api_key")

    def test_clearing_a_secret_removes_it(self, store: ConfigStore) -> None:
        store.load()
        store.set_secret("openai_api_key", "sk-test")
        store.set_secret("openai_api_key", "")
        assert not store.has_secret("openai_api_key")

    def test_undecryptable_secret_degrades_to_empty(self, store: ConfigStore) -> None:
        # A config copied from another machine must not crash the app.
        store.load()
        store.update(openai_api_key_dpapi="bm90LXZhbGlkLWNpcGhlcnRleHQ=")
        assert store.get_secret("openai_api_key") == ""
