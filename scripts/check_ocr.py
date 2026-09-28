"""OCR smoke test: render known text, recognise it, report timing.

Run with the project virtual environment:

    .venv\\Scripts\\python.exe scripts\\check_ocr.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prtsbox.ocr import OcrService

LINES = [
    "Loading...",
    "Damage: 1250  Critical Hit!",
    "Are you sure you want to delete this file?",
    "Enable hardware acceleration to reduce CPU usage.",
]


def render(lines: list[str], width: int = 900, height: int = 260) -> np.ndarray:
    """Draw the sample lines onto a light background, as a screenshot would look."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), (246, 246, 248))
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((24, 20 + index * 58), line, fill=(20, 20, 26))
    # PIL is RGB; the pipeline and OpenCV expect BGR.
    return np.array(image)[:, :, ::-1].copy()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    logger = logging.getLogger("check_ocr")
    frame = render(LINES)

    results = {}
    for backend in ("onnxruntime", "openvino"):
        label = "OpenVINO" if backend == "openvino" else "ONNX Runtime"
        started = time.perf_counter()
        service = OcrService(backend=backend)
        load_ms = (time.perf_counter() - started) * 1000.0
        logger.info("%s 初始化耗时 %.0f ms，实际后端 OpenVINO=%s", label, load_ms, service.using_openvino)

        service.recognize(frame)  # warm up
        timings = []
        items = []
        for _ in range(5):
            items, timing = service.recognize_timed(frame)
            timings.append(timing["ocr_ms"])
        results[label] = (load_ms, min(timings), items)

        print(f"\n=== {label} ===")
        print(f"  初始化 {load_ms:.0f} ms  推理最快 {min(timings):.0f} ms  平均 {sum(timings)/len(timings):.0f} ms")
        for item in items:
            left, top, _right, _bottom = item.bounds
            print(f"  ({left:6.0f},{top:6.0f}) conf={item.confidence:.2f}  {item.text!r}")

    print("\n=== 召回对比 ===")
    for label, (_load, _best, items) in results.items():
        recognised = " ".join(item.text for item in items).casefold()
        hits = sum(1 for line in LINES if line.casefold()[:14] in recognised)
        print(f"  {label}: {len(items)} 行，命中原文 {hits}/{len(LINES)}")


if __name__ == "__main__":
    main()
