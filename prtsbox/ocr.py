"""PP-OCRv5 text recognition through the bundled ``onnxocr`` package.

The wheel ships its own detection and recognition ONNX models, so OCR needs no
download of its own.

**OpenVINO is not the default, despite being ~2.6x faster here** (measured 61 ms
against 160 ms on a Ryzen 7 7700).  Its recognition model has a fully dynamic
input shape - width is derived per batch from the widest crop - and every new
shape costs roughly 4 MiB that is never returned.  A screen whose text changes
in width, which is any game HUD or video subtitle, therefore grows without
bound: measured 13 MiB per frame end to end, with the process reaching 2.3 GiB
after 60 frames.  ONNX Runtime handled the identical workload at 0.05 MiB per
frame.

The growth was bisected rather than guessed: the detector alone is clean
(+0.03 MiB/frame), the recogniser is clean when its input shape is constant
(+0.06), and only the combination of the recogniser with varying crop widths
grows (+4.40).  OpenVINO remains selectable for users who want the speed and
accept the footprint; the pipeline recycles the service if it grows anyway.

Frames are downscaled to a ~720p-equivalent before inference and the resulting
boxes are scaled back to source coordinates.  Detection cost grows with pixel
count while the text stays equally legible at the smaller size, so this buys
latency without losing lines.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from .models import OcrItem

# Detector input is normalised to roughly this long side.  Wider inputs slow
# detection down quadratically for no recognition benefit.
_ANALYSIS_LONG_SIDE = 1280.0
_MAX_UPSCALE = 2.0

BACKEND_AUTO = "auto"
BACKEND_OPENVINO = "openvino"
BACKEND_ONNXRUNTIME = "onnxruntime"

OCR_BACKENDS = (BACKEND_ONNXRUNTIME, BACKEND_OPENVINO)

# What ``auto`` resolves to.  See the module docstring: OpenVINO's recognition
# path leaks on changing input shapes.
AUTO_BACKEND = BACKEND_ONNXRUNTIME


class OcrService:
    def __init__(
        self,
        *,
        backend: str = BACKEND_AUTO,
        confidence_threshold: float = 0.50,
    ) -> None:
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        self._logger = logging.getLogger("prtsbox.ocr")
        self._confidence_threshold = confidence_threshold

        requested = backend if backend in OCR_BACKENDS else AUTO_BACKEND
        use_openvino = requested == BACKEND_OPENVINO
        self.backend = requested
        self._logger.info("创建 OCR 模型（后端=%s）", requested)

        self._engine = ONNXPaddleOcr(
            logger=self._logger,
            use_openvino=use_openvino,
            # The NPU probe can crash inside native code on some builds, and
            # the desktop workload gains nothing from it.
            use_npu=False,
            use_angle_cls=False,
            rec_batch_num=16,
            det_limit_side_len=1600,
            det_db_thresh=0.20,
            det_db_box_thresh=0.60,
            det_db_unclip_ratio=1.8,
            drop_score=0.35,
        )
        self.using_openvino = bool(use_openvino)
        detector_flag = getattr(getattr(self._engine, "text_detector", None), "is_openvino", None)
        recognizer_flag = getattr(getattr(self._engine, "text_recognizer", None), "is_openvino", None)
        if detector_flag is not None and recognizer_flag is not None:
            self.using_openvino = bool(detector_flag and recognizer_flag)
        self._logger.info("OCR 模型就绪（OpenVINO=%s）", self.using_openvino)

    def recognize(self, image_bgr: np.ndarray) -> list[OcrItem]:
        items, _timing = self.recognize_timed(image_bgr)
        return items

    def recognize_timed(self, image_bgr: np.ndarray) -> tuple[list[OcrItem], dict[str, float]]:
        if image_bgr is None or image_bgr.size == 0:
            return [], {"ocr_ms": 0.0}

        height, width = image_bgr.shape[:2]
        scale = min(_MAX_UPSCALE, _ANALYSIS_LONG_SIDE / max(height, width))
        analysis = image_bgr
        if abs(scale - 1.0) > 0.05:
            import cv2

            analysis = cv2.resize(
                image_bgr,
                None,
                fx=scale,
                fy=scale,
                # Shrinking wants area averaging; enlarging wants cubic.
                interpolation=cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA,
            )

        started = time.perf_counter()
        try:
            raw = self._engine.ocr(analysis, cls=False)
        except Exception as exc:
            self._logger.exception("OCR 推理失败")
            raise RuntimeError(f"OCR 推理失败：{exc}") from exc
        elapsed = (time.perf_counter() - started) * 1000.0

        return self._parse(raw, scale), {"ocr_ms": elapsed}

    def _parse(self, raw: object, scale: float) -> list[OcrItem]:
        if not raw or not isinstance(raw, list):
            return []
        first = raw[0]
        if not first:
            return []

        items: list[OcrItem] = []
        inverse = 1.0 / scale if scale else 1.0
        for entry in first:
            try:
                box, recognition = entry
                text, score = recognition
            except (TypeError, ValueError):
                continue
            cleaned = str(text).strip()
            confidence = float(score)
            if not cleaned or confidence < self._confidence_threshold:
                continue
            points = tuple(
                (float(point[0]) * inverse, float(point[1]) * inverse) for point in box
            )
            if len(points) < 4:
                continue
            items.append(OcrItem(box=points, text=cleaned, confidence=confidence))
        return items
