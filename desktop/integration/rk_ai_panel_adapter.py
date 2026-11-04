"""Normalize RK YOLO preview results for an independent Windows result panel.

This adapter does not run inference, infer source-frame identity, or create pose data.
It deliberately keeps the panel separate from qjzc.analysis.v1 skeleton messages.
"""
from __future__ import annotations

import json
import math
import base64
import hashlib
from typing import Any


MODEL_SHA256 = "c9f43d33ca07413bee582baf3bfd76a91b87c240482ff9e7a91e47f3a54b495b"


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def _detections(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("detections must be a list of at most 32 items")
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("detection must be an object")
        class_id = item.get("class_id")
        score = item.get("score")
        box = item.get("xyxy")
        if type(class_id) is not int or class_id < 0:
            raise ValueError("class_id is invalid")
        if not _finite_number(score) or not 0 <= score <= 1:
            raise ValueError("score is invalid")
        if not isinstance(box, list) or len(box) != 4 or not all(_finite_number(v) for v in box):
            raise ValueError("xyxy is invalid")
        x1, y1, x2, y2 = map(float, box)
        if x2 < x1 or y2 < y1 or x1 < 0 or y1 < 0 or x2 > 960 or y2 > 540:
            raise ValueError("xyxy is outside the preview image")
        out.append({"class_id": class_id, "score": float(score), "xyxy": [x1, y1, x2, y2]})
    return out


def normalize(result: dict[str, Any]) -> dict[str, Any]:
    """Return a safe, display-only detection panel record."""
    if not isinstance(result, dict):
        raise ValueError("analysis result must be an object")
    if result.get("device") != "rk3576-npu" or result.get("backend") != "rknnlite":
        raise ValueError("unexpected inference backend")
    if result.get("model_sha256") != MODEL_SHA256:
        raise ValueError("unexpected model sha256")
    if result.get("camera_id") not in (None, "A"):
        raise ValueError("camera_id is invalid")
    if result.get("processing_mode") not in ("original", "gray", "gaussian", "histogram"):
        raise ValueError("processing_mode is invalid")
    if result.get("source_frame_id") is not None:
        raise ValueError("source_frame_id must remain null")
    state = result.get("state")
    if state not in ("WAITING_FOR_CAPTURE", "RUNNING", "ENDED", "ERROR"):
        raise ValueError("state is invalid")
    sequence = result.get("preview_sequence")
    if sequence is not None and (type(sequence) is not int or sequence < 0):
        raise ValueError("preview_sequence is invalid")
    processed = result.get("processed_frames", 0)
    if type(processed) is not int or processed < 0:
        raise ValueError("processed_frames is invalid")
    npu_ms = result.get("npu_ms")
    if npu_ms is not None and (not _finite_number(npu_ms) or npu_ms < 0):
        raise ValueError("npu_ms is invalid")
    detections = _detections(result.get("detections", []))
    persons = result.get("persons", sum(d["class_id"] == 0 for d in detections))
    if type(persons) is not int or persons < 0 or persons > len(detections):
        raise ValueError("persons is invalid")
    model_status = result.get("model_accuracy_status")
    if not isinstance(model_status, str) or len(model_status) > 256:
        raise ValueError("model_accuracy_status is invalid")
    source_session = result.get("source_session")
    if source_session is not None and (not isinstance(source_session, str) or not 0 < len(source_session) <= 128):
        raise ValueError("source_session is invalid")
    jpeg_sha256 = result.get("jpeg_sha256")
    image_base64 = result.get("image_base64")
    exact_preview_jpeg = False
    if image_base64 is not None:
        if not isinstance(image_base64, str) or len(image_base64) > 24 * 1024 * 1024:
            raise ValueError("image_base64 is invalid or oversized")
        if not _sha256(jpeg_sha256) or sequence is None or source_session is None:
            raise ValueError("embedded AI image is missing session, sequence, or SHA")
        try:
            jpeg_bytes = base64.b64decode(image_base64, validate=True)
        except Exception as exc:
            raise ValueError("image_base64 is malformed") from exc
        if not jpeg_bytes.startswith(b"\xff\xd8") or not jpeg_bytes.endswith(b"\xff\xd9"):
            raise ValueError("embedded AI input is not a complete JPEG")
        if hashlib.sha256(jpeg_bytes).hexdigest() != jpeg_sha256.lower():
            raise ValueError("embedded AI input SHA does not match jpeg_sha256")
        exact_preview_jpeg = True
    # Detection boxes can be overlaid only on the exact JPEG submitted to RK NPU.
    # This remains a preview identity, not a sensor source-frame identity.
    panel = {
        "schema": "qjzc.ai.panel.v1",
        "state": state,
        "camera_id": result.get("camera_id"),
        "processing_mode": result["processing_mode"],
        "device": result["device"],
        "backend": result["backend"],
        "model_sha256": result["model_sha256"],
        "model_accuracy_status": model_status,
        "source_frame_id": None,
        "preview_sequence": sequence,
        "processed_frames": processed,
        "persons": persons,
        "detections": detections,
        "npu_ms": float(npu_ms) if npu_ms is not None else None,
        "source_session": source_session,
        "jpeg_sha256": jpeg_sha256,
        "frame_match": "exact_preview_jpeg" if exact_preview_jpeg else "unknown",
        "overlay_allowed": exact_preview_jpeg,
        "pose_available": False,
        "student_identity": "unbound",
    }
    if exact_preview_jpeg:
        panel["image_base64"] = image_base64
    return panel


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Normalize one RK analysis.json record")
    parser.add_argument("input", help="JSON file or - for stdin")
    parser.add_argument("--output", default="-", help="JSON output file or - for stdout")
    args = parser.parse_args()
    raw = __import__("sys").stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    panel = normalize(json.loads(raw))
    encoded = json.dumps(panel, ensure_ascii=False, indent=2) + "\n"
    if args.output == "-":
        print(encoded, end="")
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded)


if __name__ == "__main__":
    main()
