"""End-to-end check of the local translation path.

Exercises the real code paths, not mocks: downloads the llama.cpp runtime and
the model through :class:`LlamaManager`, starts ``llama-server``, and translates
through the same translator the pipeline uses.

    .venv\\Scripts\\python.exe scripts\\e2e_local.py
    .venv\\Scripts\\python.exe scripts\\e2e_local.py --model hy-mt2-7b
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prtsbox import llama, paths
from prtsbox.llama import LlamaManager
from prtsbox.models import TranslatorConfig
from prtsbox.translate import create_translator

SAMPLES = [
    "Loading...",
    "Are you sure you want to delete this file?",
    "The connection timed out. Retrying in 5 seconds.",
    "Enable hardware acceleration to reduce CPU usage.",
]


def human(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def make_progress(label: str):
    state = {"last": 0.0}

    def report(progress: llama.DownloadProgress) -> None:
        now = time.monotonic()
        if now - state["last"] < 1.0 and progress.fraction < 1.0:
            return
        state["last"] = now
        eta = progress.eta_seconds
        suffix = f"  剩余 {eta:.0f}s" if eta > 1 else ""
        print(
            f"  {label}: {progress.fraction * 100:5.1f}%  "
            f"{human(progress.bytes_per_second)}/s{suffix}",
            flush=True,
        )

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=llama.DEFAULT_MODEL_ID)
    parser.add_argument("--variant", default="")
    parser.add_argument("--keep-server", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("prtsbox.ocr").setLevel(logging.WARNING)

    model = llama.find_model(args.model)
    if model is None:
        print(f"未知模型：{args.model}")
        return 2

    print(f"数据目录：{paths.data_dir()}")
    manager = LlamaManager()

    # 1. runtime
    variant = llama.find_variant(args.variant) if args.variant else None
    if variant is None or not variant.is_installed():
        installed = llama.installed_variants()
        variant = installed[0] if installed else llama.recommend_variant()
    if variant.is_installed():
        print(f"[1/3] 运行时已安装：{variant.name}")
    else:
        print(f"[1/3] 下载运行时 {variant.name}（{human(variant.size_bytes)}）")
        started = time.perf_counter()
        manager.install_runtime(variant, on_progress=make_progress("运行时"))
        print(f"      完成，用时 {time.perf_counter() - started:.0f}s -> {variant.root}")

    # 2. model
    if model.is_downloaded():
        print(f"[2/3] 模型已存在：{model.filename}")
    else:
        print(f"[2/3] 下载模型 {model.filename}（{human(model.size_bytes)}）")
        started = time.perf_counter()
        manager.download_model(model, on_progress=make_progress("模型"))
        print(f"      完成，用时 {time.perf_counter() - started:.0f}s -> {model.path}")

    # 3. translate
    print("[3/3] 启动 llama-server 并翻译")
    config = TranslatorConfig(engine="local", target_language="zh-CN", model_id=model.id)
    translator = create_translator(config, llama_manager=manager)
    translator.preflight()

    started = time.perf_counter()
    manager.ensure_server(model)
    print(f"      模型加载 + 预热：{time.perf_counter() - started:.1f}s")

    started = time.perf_counter()
    results = translator.translate_batch(SAMPLES)
    elapsed = (time.perf_counter() - started) * 1000.0
    print(f"      首轮翻译 {len(SAMPLES)} 条：{elapsed:.0f} ms")
    for source, translated in zip(SAMPLES, results, strict=True):
        print(f"        {source!r}\n          -> {translated!r}")

    # The steady state is what matters for a live overlay: everything cached.
    dupes = SAMPLES * 5
    started = time.perf_counter()
    translator.translate_batch(dupes)
    print(f"      重复 {len(dupes)} 条（模拟缓存命中）：{(time.perf_counter() - started) * 1000:.0f} ms")

    print("\n结果数量校验：", "OK" if len(results) == len(SAMPLES) else "不一致！")
    if not args.keep_server:
        manager.stop()
        print("已停止 llama-server")
    else:
        print(f"llama-server 保持运行于 {manager.base_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
