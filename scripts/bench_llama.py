"""Phase 0 benchmark for the local Hy-MT2 translation backend.

Measures, against a running ``llama-server``:

* per-request latency and generation speed for single strings,
* the two candidate batching strategies for ``translate_batch``:
  A) one request per text, B) one request holding every text as a numbered line.

The official Hy-MT2 prompt format is used verbatim, including the requirement
that language names are spelled out in full ("简体中文", not "zh-CN").

Usage:
    python scripts/bench_llama.py --port 8123 --label 1.8B
"""

from __future__ import annotations

import argparse
import json
import time

import requests

PROMPT_TEMPLATE = (
    "将以下文本翻译为 {target}，注意只需要输出翻译后的结果，不要额外解释：\n\n{source}"
)
BATCH_TEMPLATE = (
    "将以下文本逐条翻译为 {target}，注意只需要输出翻译后的结果，不要额外解释，"
    "必须保持编号与行数完全一致：\n\n{source}"
)

SAMPLES = [
    "Hello, world.",
    "Please open the settings menu to configure your translation engine.",
    "Loading...",
    "Are you sure you want to delete this file?",
    "The connection timed out. Retrying in 5 seconds.",
    "Damage: 1250  Critical Hit!",
]


def ask(base_url: str, prompt: str, timeout: float = 120.0) -> tuple[str, dict]:
    started = time.perf_counter()
    response = requests.post(
        f"{base_url}/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 512,
        },
        timeout=timeout,
    )
    elapsed = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    data = response.json()
    content = str(data["choices"][0]["message"]["content"]).strip()
    return content, {"wall_ms": elapsed, **data.get("timings", {})}


def report(label: str, timings: dict) -> None:
    print(
        f"    wall={timings['wall_ms']:.0f} ms  "
        f"prompt={timings.get('prompt_n', '?')} tok  "
        f"gen={timings.get('predicted_n', '?')} tok  "
        f"speed={timings.get('predicted_per_second', 0):.1f} tok/s"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--label", default="model")
    parser.add_argument("--target", default="简体中文")
    args = parser.parse_args()
    base_url = f"http://127.0.0.1:{args.port}"

    print(f"=== {args.label} | target={args.target} ===")

    print("\n[1] 单条翻译（逐条请求）")
    per_request = []
    for text in SAMPLES:
        prompt = PROMPT_TEMPLATE.format(target=args.target, source=text)
        translated, timings = ask(base_url, prompt)
        per_request.append(timings["wall_ms"])
        print(f"  {text!r}\n    -> {translated!r}")
        report(args.label, timings)
    total = sum(per_request)
    print(f"  合计 {total:.0f} ms，平均 {total / len(SAMPLES):.0f} ms/条")

    print("\n[2] 批量翻译（单请求多行编号）")
    numbered = "\n".join(f"{index}. {text}" for index, text in enumerate(SAMPLES, 1))
    prompt = BATCH_TEMPLATE.format(target=args.target, source=numbered)
    translated, timings = ask(base_url, prompt)
    print(f"  返回：\n{translated}")
    report(args.label, timings)
    lines = [line for line in translated.splitlines() if line.strip()]
    print(f"  行数校验：输入 {len(SAMPLES)} 行 -> 返回 {len(lines)} 行 "
          f"{'OK' if len(lines) == len(SAMPLES) else '不一致！'}")
    if total:
        print(f"  相对逐条请求加速：{total / max(timings['wall_ms'], 1):.1f}x")

    print("\n[3] 冷启动/预热开销")
    started = time.perf_counter()
    ask(base_url, PROMPT_TEMPLATE.format(target=args.target, source="warm up"))
    print(f"  首次请求（含缓存预热）{(time.perf_counter() - started) * 1000:.0f} ms")

    print("\n[4] 结构化输出（JSON 数组，供 translate_batch 参考）")
    payload = json.dumps(SAMPLES, ensure_ascii=False)
    prompt = (
        f"把下面的 JSON 字符串数组中的每一条翻译为{args.target}。"
        f"只返回一个 JSON 字符串数组，元素数量和顺序必须与输入一致，不要解释。\n{payload}"
    )
    translated, timings = ask(base_url, prompt)
    print(f"  返回：{translated}")
    report(args.label, timings)
    try:
        parsed = json.loads(translated)
        ok = isinstance(parsed, list) and len(parsed) == len(SAMPLES)
        print(f"  JSON 解析：{'OK' if ok else '数量不一致'}（{len(parsed)} 条）")
    except ValueError as exc:
        print(f"  JSON 解析失败：{exc}")

    print("\n[5] 并发请求（考验 llama-server 的 -np 并行槽）")
    from concurrent.futures import ThreadPoolExecutor

    def one(text: str) -> str:
        return ask(base_url, PROMPT_TEMPLATE.format(target=args.target, source=text))[0]

    for workers in (1, 2, 4, 8):
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, SAMPLES))
        elapsed = (time.perf_counter() - started) * 1000.0
        print(
            f"  并发 {workers}: {elapsed:.0f} ms  "
            f"相对串行加速 {total / max(elapsed, 1):.1f}x  "
            f"等效 {elapsed / len(SAMPLES):.0f} ms/条"
        )

    print("\n[6] 模拟真实一帧（20 条短文本，并发 4）")
    frame = (SAMPLES * 4)[:20]
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, frame))
    elapsed = (time.perf_counter() - started) * 1000.0
    print(f"  20 条并发 4：{elapsed:.0f} ms（刷新周期目标 900 ms）")


if __name__ == "__main__":
    main()
