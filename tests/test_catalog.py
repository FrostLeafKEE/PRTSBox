"""Model catalogue, runtime catalogue and translation cache."""

from __future__ import annotations

import pytest

from prtsbox import llama
from prtsbox.llama import catalog
from prtsbox.models import TranslatorConfig, language_name
from prtsbox.pipeline import TranslationCache
from prtsbox.translate.base import build_prompt


class TestCatalogue:
    def test_pinned_sizes_and_digests(self) -> None:
        # These are the values the downloader verifies against; a typo here
        # would reject a perfectly good download.
        for model in llama.MODELS:
            assert model.size_bytes > 0
            assert len(model.sha256) == 64
            assert model.filename.endswith(".gguf")

    def test_both_models_are_independent(self) -> None:
        ids = [model.id for model in llama.MODELS]
        assert len(ids) == len(set(ids))
        assert llama.DEFAULT_MODEL_ID in ids

    def test_model_paths_are_distinct(self) -> None:
        paths = {model.path for model in llama.MODELS}
        assert len(paths) == len(llama.MODELS)

    def test_sources_lead_with_the_domestic_host(self) -> None:
        """ModelScope is tried first: it is reachable where HuggingFace is not,
        and all sources are verified to serve identical bytes."""
        sources = llama.MODELS[0].sources()
        assert "modelscope.cn" in sources[0]
        assert any("hf-mirror.com" in url for url in sources)
        assert any(url.startswith("https://huggingface.co/") for url in sources)

    def test_every_source_is_tried_before_giving_up(self) -> None:
        # A source list that silently dropped one would turn a recoverable
        # network problem into a failed download.
        for model in llama.MODELS:
            sources = model.sources()
            assert len(sources) >= 3
            assert len(set(sources)) == len(sources), "下载源有重复"
            assert all(model.filename in url for url in sources)

    def test_modelscope_url_points_at_the_right_repo(self) -> None:
        model = llama.find_model("hy-mt2-7b")
        assert model is not None
        first = model.sources()[0]
        assert "Tencent-Hunyuan/Hy-MT2-7B-GGUF" in first
        assert first.endswith(model.filename)

    def test_find_model(self) -> None:
        assert llama.find_model("hy-mt2-1.8b") is not None
        assert llama.find_model("does-not-exist") is None

    def test_find_variant(self) -> None:
        assert llama.find_variant("vulkan") is not None
        assert llama.find_variant("nope") is None

    def test_vram_labels(self) -> None:
        small = llama.find_model("hy-mt2-1.8b")
        large = llama.find_model("hy-mt2-7b")
        assert small is not None and large is not None
        assert "2.0 GB" in small.vram_label
        assert "6.0 GB" in large.vram_label

    def test_runtime_variants_have_expected_ids(self) -> None:
        ids = {variant.id for variant in llama.RUNTIME_VARIANTS}
        assert {"vulkan", "cpu"} <= ids

    def test_runtime_sources_lead_with_a_mirror(self) -> None:
        """The runtime is the first download a new user hits, and GitHub
        Releases is often unreachable without a proxy."""
        variant = llama.find_variant("vulkan")
        assert variant is not None
        sources = variant.sources()
        assert not sources[0].startswith("https://github.com/"), "应优先使用加速源"
        assert sources[-1].startswith("https://github.com/"), "官方源应作为兜底保留"
        assert all(variant.asset in url for url in sources)
        assert len(set(sources)) == len(sources)

    def test_runtime_source_labels_match_urls(self) -> None:
        variant = llama.find_variant("cpu")
        assert variant is not None
        labelled = variant.sources_with_labels()
        assert len(labelled) == len(variant.sources())
        assert [url for _label, url in labelled] == variant.sources()


class TestSourcePreference:
    """Which hosts a download may use is a user choice, not a fixed order.

    Reachability varies enormously between networks, and a wrong guess only
    shows up at the end of a multi-gigabyte download.
    """

    def test_official_excludes_domestic_hosts(self) -> None:
        for model in llama.MODELS:
            urls = model.sources(llama.SOURCE_OFFICIAL)
            assert all("huggingface.co" in url for url in urls)
            assert not any("modelscope" in url or "hf-mirror" in url for url in urls)

    def test_domestic_excludes_the_official_host(self) -> None:
        for model in llama.MODELS:
            urls = model.sources(llama.SOURCE_DOMESTIC)
            assert urls, "国内源不能为空"
            assert not any(url.startswith("https://huggingface.co/") for url in urls)
            assert any("modelscope.cn" in url for url in urls)

    def test_auto_covers_both_and_puts_domestic_first(self) -> None:
        urls = llama.MODELS[0].sources(llama.SOURCE_AUTO)
        assert "modelscope.cn" in urls[0]
        assert any(url.startswith("https://huggingface.co/") for url in urls)

    def test_runtime_official_is_github_only(self) -> None:
        variant = llama.find_variant("vulkan")
        assert variant is not None
        urls = variant.sources(llama.SOURCE_OFFICIAL)
        assert len(urls) == 1
        assert urls[0].startswith("https://github.com/")

    def test_runtime_domestic_has_no_github(self) -> None:
        variant = llama.find_variant("vulkan")
        assert variant is not None
        urls = variant.sources(llama.SOURCE_DOMESTIC)
        assert urls
        assert not any(url.startswith("https://github.com/") for url in urls)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("auto", llama.SOURCE_AUTO),
            ("DOMESTIC", llama.SOURCE_DOMESTIC),
            (" official ", llama.SOURCE_OFFICIAL),
            ("nonsense", llama.SOURCE_AUTO),
            (None, llama.SOURCE_AUTO),
            ("", llama.SOURCE_AUTO),
        ],
    )
    def test_normalise_source(self, raw, expected: str) -> None:
        assert llama.normalise_source(raw) == expected

    def test_labels_cover_every_source(self) -> None:
        for preference in llama.DOWNLOAD_SOURCES:
            labelled = llama.MODELS[0].sources_with_labels(preference)
            assert len(labelled) == len(llama.MODELS[0].sources(preference))
            assert all(label for label, _url in labelled)


class TestVariantResolution:
    def test_explicit_choice_wins(self) -> None:
        assert llama.resolve_variant("cpu").id == "cpu"

    def test_unknown_falls_back_without_raising(self) -> None:
        assert llama.resolve_variant("nonsense") is not None

    def test_auto_resolves(self) -> None:
        assert llama.resolve_variant("auto") is not None


class TestDefaultModel:
    def test_prefers_the_installed_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        small = llama.find_model("hy-mt2-1.8b")
        assert small is not None
        monkeypatch.setattr(type(small), "is_downloaded", lambda self: True)
        assert llama.default_model().id == "hy-mt2-1.8b"

    def test_falls_back_to_any_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            catalog.LocalModel, "is_downloaded", lambda self: self.id == "hy-mt2-7b"
        )
        assert llama.default_model().id == "hy-mt2-7b"

    def test_falls_back_to_catalogue_head_when_nothing_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(catalog.LocalModel, "is_downloaded", lambda self: False)
        assert llama.default_model().id == llama.DEFAULT_MODEL_ID


class TestTranslationCache:
    def test_hit_and_miss(self) -> None:
        cache = TranslationCache(capacity=4)
        assert cache.get(("a",)) is None
        cache.put(("a",), "A")
        assert cache.get(("a",)) == "A"

    def test_evicts_least_recently_used(self) -> None:
        cache = TranslationCache(capacity=2)
        cache.put(("a",), "A")
        cache.put(("b",), "B")
        cache.get(("a",))  # "a" becomes the most recent
        cache.put(("c",), "C")
        assert cache.get(("b",)) is None
        assert cache.get(("a",)) == "A"
        assert cache.get(("c",)) == "C"

    def test_never_exceeds_capacity(self) -> None:
        cache = TranslationCache(capacity=8)
        for index in range(100):
            cache.put((str(index),), str(index))
        assert len(cache) == 8

    def test_clear(self) -> None:
        cache = TranslationCache(capacity=4)
        cache.put(("a",), "A")
        cache.clear()
        assert len(cache) == 0


class TestPrompt:
    def test_uses_full_language_name(self) -> None:
        # The model card requires "简体中文" rather than "zh-CN".
        prompt = build_prompt("Hello", "zh-CN")
        assert "简体中文" in prompt
        assert "zh-CN" not in prompt

    def test_matches_documented_instruction(self) -> None:
        prompt = build_prompt("Hello", "en")
        assert prompt.startswith("将以下文本翻译为 英语")
        assert "不要额外解释" in prompt
        assert prompt.endswith("Hello")

    def test_unknown_language_falls_back_to_code(self) -> None:
        assert language_name("xx-YY") == "xx-YY"


class TestTranslatorConfig:
    def test_round_trip_through_dict(self) -> None:
        original = TranslatorConfig(
            engine="openai",
            source_language="en",
            target_language="ja",
            model_id="hy-mt2-7b",
            credentials={"openai_model": "gpt-4o-mini"},
        )
        restored = TranslatorConfig.from_dict(original.to_dict())
        assert restored == original

    def test_to_dict_is_detached(self) -> None:
        credentials = {"openai_api_key": "sk-1"}
        config = TranslatorConfig(credentials=credentials)
        payload = config.to_dict()
        credentials["openai_api_key"] = "changed"
        assert payload["credentials"]["openai_api_key"] == "sk-1"

    def test_from_dict_tolerates_missing_fields(self) -> None:
        config = TranslatorConfig.from_dict({})
        assert config.engine == "local"
        assert config.credentials == {}
