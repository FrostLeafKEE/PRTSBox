"""Built-in catalogue of downloadable models and llama.cpp runtimes.

Sizes and digests are pinned to exact upstream values so a download can be
verified rather than trusted.  ``SHA256`` for the runtimes is filled in after
the first successful download when unknown - the models always carry theirs.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .. import paths

# Pinned so a given PRTSBox build always fetches a runtime that was tested
# against it, rather than whatever upstream published most recently.
LLAMA_TAG = "b10227"
LLAMA_RELEASE_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}"
LLAMA_ASSET_PREFIX = f"llama-{LLAMA_TAG}-bin-win"

# Public proxies in front of GitHub Releases, used for the runtime download.
# These are widely used domestic accelerators rather than official endpoints, so
# they are tried before GitHub instead of replacing it: if one is down or starts
# serving something unexpected, the canonical URL still backs it up.
GITHUB_MIRRORS = (
    "https://gh-proxy.com",
    "https://ghfast.top",
)
GITHUB_MIRROR_LABELS = ("gh-proxy 加速", "ghfast 加速")

HUGGINGFACE_BASE = "https://huggingface.co"
# HuggingFace is frequently unreachable from mainland China; the mirror serves
# byte-identical files and is tried automatically when the primary fails.
HUGGINGFACE_MIRROR = "https://hf-mirror.com"
# Tencent publishes these models on ModelScope as well, under their own
# organisation.  Verified byte-identical to the HuggingFace copies by downloading
# each in full and comparing SHA-256 (scripts/check_mirrors.py --hashes), and
# measured at ~20 MB/s here.
MODELSCOPE_BASE = "https://modelscope.cn/models"
MODELSCOPE_ORG = "Tencent-Hunyuan"
MODELSCOPE_REVISION = "master"

# Which hosts a download may use.  Reachability varies enormously by network -
# a domestic mirror that is fast for one user is unreachable for another, and a
# third-party proxy is a liability for someone who can reach the official host
# directly - so this is a user choice rather than a fixed order.
SOURCE_AUTO = "auto"
SOURCE_DOMESTIC = "domestic"
SOURCE_OFFICIAL = "official"
DOWNLOAD_SOURCES = (SOURCE_AUTO, SOURCE_DOMESTIC, SOURCE_OFFICIAL)

SOURCE_LABELS = {
    SOURCE_AUTO: "自动（国内优先，失败后转官方）",
    SOURCE_DOMESTIC: "仅国内源（ModelScope / GitHub 加速）",
    SOURCE_OFFICIAL: "仅官方源（HuggingFace / GitHub）",
}


def normalise_source(value: object) -> str:
    """Coerce a config value into a known source preference."""
    text = str(value or "").strip().casefold()
    return text if text in DOWNLOAD_SOURCES else SOURCE_AUTO


def probe_source(url: str, timeout: float = 8.0) -> tuple[bool, str]:
    """Check whether a download URL answers, without fetching the file.

    A one-byte ranged request rather than HEAD: several of these hosts answer
    HEAD without a Content-Length, and some redirect to object storage that only
    reports the total in Content-Range, so HEAD would report a working source as
    broken.

    Returns ``(reachable, detail)`` where detail is a short human-readable note.
    """
    import requests

    try:
        response = requests.get(
            url, headers={"Range": "bytes=0-0"}, stream=True, timeout=timeout, allow_redirects=True
        )
        with response:
            if response.status_code not in (200, 206):
                return False, f"HTTP {response.status_code}"
            content_range = response.headers.get("Content-Range", "")
            total = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
            if not total.isdigit():
                total = response.headers.get("Content-Length", "")
            if total.isdigit():
                return True, f"{int(total) / (1024**3):.2f} GB"
            # Answered, but did not advertise a size; confirm it serves data.
            next(response.iter_content(1), None)
            return True, "可访问"
    except requests.RequestException as exc:
        return False, str(exc)[:60]


@dataclass(frozen=True)
class LocalModel:
    """A GGUF translation model offered for download."""

    id: str
    name: str
    tagline: str
    filename: str
    size_bytes: int
    vram_mb: int
    repo: str
    sha256: str

    @property
    def path(self) -> Path:
        return paths.model_path(self.filename)

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024**3)

    @property
    def vram_label(self) -> str:
        """Roughly what the model needs, rounded up to a friendly number."""
        return f"约 {self.vram_mb / 1024:.1f} GB 显存"

    def sources(self, preference: str = SOURCE_AUTO) -> list[str]:
        """Download URLs to try, in order, for the given preference.

        All three hosts are verified to serve identical bytes (see
        ``scripts/check_mirrors.py --hashes``), so the choice is purely about
        reachability and speed.  ``SOURCE_DOMESTIC`` deliberately excludes the
        official hosts so a user behind a firewall fails fast instead of waiting
        for a timeout, and ``SOURCE_OFFICIAL`` excludes the third-party mirror
        for a user who would rather not route a download through one.
        """
        name = self.repo.partition("/")[2] or self.repo
        domestic = [
            f"{MODELSCOPE_BASE}/{MODELSCOPE_ORG}/{name}/resolve/{MODELSCOPE_REVISION}/{self.filename}",
            f"{HUGGINGFACE_MIRROR}/{self.repo}/resolve/main/{self.filename}",
        ]
        official = [f"{HUGGINGFACE_BASE}/{self.repo}/resolve/main/{self.filename}"]

        if preference == SOURCE_DOMESTIC:
            return domestic
        if preference == SOURCE_OFFICIAL:
            return official
        return domestic + official

    def sources_with_labels(self, preference: str = SOURCE_AUTO) -> list[tuple[str, str]]:
        """``(label, url)`` pairs, for progress messages and diagnostics."""
        labels: list[str] = []
        for url in self.sources(preference):
            if "modelscope.cn" in url:
                labels.append("ModelScope")
            elif "hf-mirror.com" in url:
                labels.append("HF 镜像")
            else:
                labels.append("HuggingFace 官方")
        return list(zip(labels, self.sources(preference), strict=True))

    def is_downloaded(self) -> bool:
        try:
            return self.path.stat().st_size == self.size_bytes
        except OSError:
            return False


@dataclass(frozen=True)
class RuntimeVariant:
    """A llama.cpp Windows build.  They differ only in the acceleration backend."""

    id: str
    name: str
    asset: str
    size_bytes: int
    note: str

    def sources(self, preference: str = SOURCE_AUTO) -> list[str]:
        """Download URLs to try, in order, for the given preference.

        GitHub Releases is frequently unreachable from mainland China and this
        is the first thing a new user downloads, so the public GitHub proxies
        are offered first under the default preference.  They are byte-for-byte
        proxies of the release assets rather than rebuilds, which is what makes
        them usable: the extracted ``llama-server.exe`` has to actually run, and
        a recompiled binary would not be the one this was tested against.
        """
        mirrors = [f"{mirror}/{LLAMA_RELEASE_BASE}/{self.asset}" for mirror in GITHUB_MIRRORS]
        official = [f"{LLAMA_RELEASE_BASE}/{self.asset}"]

        if preference == SOURCE_DOMESTIC:
            return mirrors
        if preference == SOURCE_OFFICIAL:
            return official
        return mirrors + official

    @property
    def url(self) -> str:
        """Canonical GitHub URL, kept for display and diagnostics."""
        return f"{LLAMA_RELEASE_BASE}/{self.asset}"

    def sources_with_labels(self, preference: str = SOURCE_AUTO) -> list[tuple[str, str]]:
        """``(label, url)`` pairs, for progress messages and diagnostics."""
        labels: list[str] = []
        for url in self.sources(preference):
            label = "GitHub 官方"
            for mirror, mirror_label in zip(GITHUB_MIRRORS, GITHUB_MIRROR_LABELS, strict=True):
                if url.startswith(mirror):
                    label = mirror_label
                    break
            labels.append(label)
        return list(zip(labels, self.sources(preference), strict=True))

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024**2)

    @property
    def root(self) -> Path:
        return paths.runtime_root(self.id)

    @property
    def server_path(self) -> Path:
        return self.root / "llama-server.exe"

    def is_installed(self) -> bool:
        return self.server_path.is_file()


# The 1.8B model is the default on purpose.  Measured against the 7B on screen
# text it was equal or better (the 7B over-translates short UI strings such as
# "Damage: 1250  Critical Hit!"), while being ~1.7x faster, using a third of
# the VRAM and loading in 1.2 s instead of 4.2 s.  The 7B only pulls ahead on
# long paragraphs, which is what its label says.
MODELS: tuple[LocalModel, ...] = (
    LocalModel(
        id="hy-mt2-1.8b",
        name="标准（推荐）",
        tagline="响应快、占用低，屏幕短文本表现与增强档相当",
        filename="Hy-MT2-1.8B-Q4_K_M.gguf",
        size_bytes=1_133_080_448,
        vram_mb=2048,
        repo="tencent/Hy-MT2-1.8B-GGUF",
        sha256="dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699",
    ),
    LocalModel(
        id="hy-mt2-7b",
        name="增强（长文本更佳）",
        tagline="长段落措辞更自然，速度较慢、显存占用较高",
        filename="Hy-MT2-7B-Q4_K_M.gguf",
        size_bytes=4_624_648_896,
        vram_mb=6144,
        repo="tencent/Hy-MT2-7B-GGUF",
        sha256="9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
    ),
)

DEFAULT_MODEL_ID = "hy-mt2-1.8b"

RUNTIME_VARIANTS: tuple[RuntimeVariant, ...] = (
    RuntimeVariant(
        id="vulkan",
        name="Vulkan（推荐）",
        asset=f"{LLAMA_ASSET_PREFIX}-vulkan-x64.zip",
        size_bytes=34_102_057,
        note="NVIDIA / AMD / Intel 通用，自动使用独立显卡",
    ),
    RuntimeVariant(
        id="cpu",
        name="CPU（兜底）",
        asset=f"{LLAMA_ASSET_PREFIX}-cpu-x64.zip",
        size_bytes=18_363_328,
        note="无独立显卡或 Vulkan 不可用时使用，速度明显更慢",
    ),
    RuntimeVariant(
        id="hip",
        name="ROCm / HIP（AMD 专用）",
        asset=f"{LLAMA_ASSET_PREFIX}-hip-radeon-x64.zip",
        size_bytes=324_609_557,
        note="AMD 显卡专用，需较新的驱动；体积较大",
    ),
)

DEFAULT_VARIANT_ID = "vulkan"


def find_model(model_id: str) -> LocalModel | None:
    for model in MODELS:
        if model.id == model_id:
            return model
    return None


def find_variant(variant_id: str) -> RuntimeVariant | None:
    for variant in RUNTIME_VARIANTS:
        if variant.id == variant_id:
            return variant
    return None


def downloaded_models() -> list[LocalModel]:
    return [model for model in MODELS if model.is_downloaded()]


def default_model() -> LocalModel:
    """The preferred model: the pinned default when present, else any ready one."""
    preferred = find_model(DEFAULT_MODEL_ID)
    if preferred is not None and preferred.is_downloaded():
        return preferred
    ready = downloaded_models()
    return ready[0] if ready else (preferred or MODELS[0])


def installed_variants() -> list[RuntimeVariant]:
    return [variant for variant in RUNTIME_VARIANTS if variant.is_installed()]


def has_discrete_gpu() -> bool:
    """Whether a real GPU is present, used to pick a default runtime."""
    if sys.platform != "win32":
        return False
    try:
        import subprocess

        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController).Name",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False

    ignored = ("microsoft basic display", "virtual", "remote", "meta", "parsec")
    for line in completed.stdout.splitlines():
        name = line.strip().lower()
        if not name or any(token in name for token in ignored):
            continue
        # Integrated adapters are still worth using through Vulkan, so any
        # non-virtual adapter counts.
        return True
    return False


def recommend_variant() -> RuntimeVariant:
    """Pick a runtime for this machine, honouring an explicit override."""
    override = os.environ.get("PRTSBOX_RUNTIME_VARIANT", "").strip()
    if override:
        chosen = find_variant(override)
        if chosen is not None:
            return chosen

    if has_discrete_gpu():
        vulkan = find_variant("vulkan")
        if vulkan is not None:
            return vulkan
    cpu = find_variant("cpu")
    return cpu if cpu is not None else RUNTIME_VARIANTS[0]


def resolve_variant(variant_id: str) -> RuntimeVariant:
    """Turn a config value (including ``auto``) into a concrete variant."""
    if variant_id and variant_id != "auto":
        chosen = find_variant(variant_id)
        if chosen is not None:
            return chosen
    installed = installed_variants()
    if installed:
        return installed[0]
    return recommend_variant()
