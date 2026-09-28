"""Measure llama-server memory against context size and slot count.

The translation workload is a handful of short strings per frame, so the default
``-c 8192 -np 4`` (2048 tokens per slot) is far more context than it needs.  This
prices the alternatives so the setting can be chosen from data rather than
guessed: what does each configuration actually cost, and does a smaller one
still answer correctly?

    .venv\\Scripts\\python.exe scripts\\bench_context.py
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prtsbox import llama
from prtsbox.llama import LlamaServer
from prtsbox.memory import private_mb

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")

# Configurations to compare: (context, parallel slots).
CONFIGS = ((8192, 4), (4096, 4), (2048, 4), (2048, 2), (1024, 1))

# A realistic frame: this is what one refresh produces on a busy screen.
FRAME = [
    "Loading...",
    "Are you sure you want to delete this file?",
    "The connection timed out. Retrying in 5 seconds.",
    "Enable hardware acceleration to reduce CPU usage.",
    "Damage: 1250  Critical Hit!",
    "Target window is not in the foreground; capture is paused.",
]


def llama_private_mb() -> float:
    """Private bytes of the running llama-server, in MiB."""
    script = (
        "(Get-Process llama-server -ErrorAction SilentlyContinue | "
        "Measure-Object -Property PrivateMemorySize64 -Sum).Sum"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    text = completed.stdout.strip()
    if not text:
        return 0.0
    try:
        return float(text) / (1024 * 1024)
    except ValueError:
        return 0.0


def translate(server: LlamaServer, texts: list[str]) -> tuple[list[str], float]:
    """Translate every string, four at a time, and report the worst latency."""
    from concurrent.futures import ThreadPoolExecutor

    def one(text: str) -> tuple[str, float]:
        prompt = (
            f"将以下文本翻译为 简体中文，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"
        )
        started = time.perf_counter()
        response = requests.post(
            f"{server.base_url}/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 256,
            },
            timeout=120,
        )
        response.raise_for_status()
        elapsed = (time.perf_counter() - started) * 1000.0
        return str(response.json()["choices"][0]["message"]["content"]).strip(), elapsed

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, texts))
    wall = (time.perf_counter() - started) * 1000.0
    return [text for text, _ in results], wall


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=llama.DEFAULT_MODEL_ID)
    args = parser.parse_args()

    model = llama.find_model(args.model) or llama.default_model()
    variant = llama.resolve_variant("auto")
    if not variant.is_installed():
        print("运行时未安装")
        return 2

    base_mb = private_mb()
    print(f"模型：{model.filename}")
    print(f"运行时：{variant.id}")
    print(f"主进程基线：{base_mb:.0f} MiB\n")
    print(f"{'上下文':>8}{'并发':>6}{'/槽':>7}{'llama 内存':>13}{'加载':>8}{'一帧耗时':>11}  译文抽样")

    for context, parallel in CONFIGS:
        server = LlamaServer(variant, model.path, context_size=context, parallel=parallel)
        try:
            started = time.perf_counter()
            server.start()
            load_s = time.perf_counter() - started
            server.warmup()
            time.sleep(1.5)
            memory = llama_private_mb()
            translations, wall = translate(server, FRAME)
            sample = translations[0][:14] if translations else "(空)"
            print(
                f"{context:>8}{parallel:>6}{context // parallel:>7}"
                f"{memory:>10.0f} MiB{load_s:>7.1f}s{wall:>10.0f}ms  {sample}"
            )
        except Exception as exc:  # noqa: BLE001 - one bad config must not end the sweep
            print(f"{context:>8}{parallel:>6}{context // parallel:>7}   失败：{exc}")
        finally:
            server.stop()
            time.sleep(1.0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
