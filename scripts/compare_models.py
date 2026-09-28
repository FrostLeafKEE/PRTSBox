"""Side-by-side quality comparison between the two bundled Hy-MT2 models.

Runs one model per invocation and appends its output to a JSON file, so the
two models can be started one after another and compared afterwards:

    python scripts/compare_models.py --port 8123 --label 1.8B --out out.json
    python scripts/compare_models.py --port 8124 --label 7B   --out out.json
    python scripts/compare_models.py --diff out.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

import requests

TARGET_NAMES = {"zh": "简体中文", "en": "英语"}
PROMPT = "将以下文本翻译为 {target}，注意只需要输出翻译后的结果，不要额外解释：\n\n{source}"

# Representative of what a screen translator actually meets: UI labels, game
# HUD, technical prose, placeholders that must survive, and both directions.
CASES: list[tuple[str, str, str]] = [
    ("短标签", "zh", "Loading..."),
    ("游戏 HUD", "zh", "Damage: 1250  Critical Hit!"),
    ("占位符", "zh", "Welcome back, {username}! You have {count} new messages."),
    ("技术术语", "zh", "Enable hardware acceleration to reduce CPU usage during video decoding."),
    (
        "长段落",
        "zh",
        (
            "The application failed to initialize the graphics subsystem. This usually "
            "happens when the driver does not support the required feature level, or when "
            "another program holds an exclusive lock on the display adapter."
        ),
    ),
    ("歧义单词", "zh", "Save"),
    ("中译英", "en", "请在设置中填写 API Key 后重试。"),
    ("中译英长句", "en", "目标窗口不在前台时会暂停截图，避免占用资源。"),
]


def translate(base_url: str, text: str, target: str) -> tuple[str, float]:
    started = time.perf_counter()
    response = requests.post(
        f"{base_url}/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": PROMPT.format(
                target=TARGET_NAMES[target], source=text)}],
            "temperature": 0,
            "max_tokens": 512,
        },
        timeout=180,
    )
    response.raise_for_status()
    elapsed = (time.perf_counter() - started) * 1000.0
    return str(response.json()["choices"][0]["message"]["content"]).strip(), elapsed


def run(port: int, label: str, out_path: pathlib.Path) -> None:
    base_url = f"http://127.0.0.1:{port}"
    results = {}
    for name, target, text in CASES:
        translated, elapsed = translate(base_url, text, target)
        results[name] = {"source": text, "target": target, "output": translated, "ms": round(elapsed)}
        print(f"[{name}] {elapsed:.0f} ms\n  {translated}\n")

    payload = {}
    if out_path.exists():
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    payload[label] = results
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {out_path}")


def diff(out_path: pathlib.Path) -> None:
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    labels = list(payload)
    if len(labels) < 2:
        print(f"只有 {labels}，需要两个模型的结果才能对比")
        return
    for name, _target, text in CASES:
        print(f"\n=== {name} ===\n原文: {text}")
        for label in labels:
            entry = payload[label].get(name)
            if entry:
                print(f"  [{label}] ({entry['ms']} ms) {entry['output']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--label", default="")
    parser.add_argument("--out", default="models_compare.json")
    parser.add_argument("--diff", action="store_true")
    args = parser.parse_args()
    out_path = pathlib.Path(args.out)
    if args.diff:
        diff(out_path)
    else:
        run(args.port, args.label or f"port{args.port}", out_path)


if __name__ == "__main__":
    main()
