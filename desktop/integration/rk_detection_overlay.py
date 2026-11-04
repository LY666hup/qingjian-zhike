"""RK YOLO detection boxes for the AI stage; independent from skeletons."""
from __future__ import annotations
from typing import Any
from PySide6.QtGui import QColor, QFont, QPen, QPainter

def draw_boxes(painter: Any, rect: Any, panel: dict[str, Any] | None, highlight: int | None = None) -> None:
    if not panel or not panel.get("detections"):
        return
    width = rect.width(); height = rect.height()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    drawn = 0
    for index, det in enumerate(panel["detections"]):
        if det.get("class_id") != 0:
            continue
        drawn += 1
        x1, y1, x2, y2 = det["xyxy"]
        qx = rect.x() + x1 / 960 * width
        qy = rect.y() + y1 / 540 * height
        qw = (x2 - x1) / 960 * width
        qh = (y2 - y1) / 540 * height
        active = highlight is None or highlight == index
        pen = QPen(QColor(90, 216, 205, 255 if active else 60), 3 if highlight == index else 2)
        painter.setPen(pen)
        painter.drawRect(qx, qy, qw, qh)
        font = QFont("Microsoft YaHei"); font.setPixelSize(12); painter.setFont(font)
        painter.drawText(qx + 2, min(qy + 14, rect.y() + height - 2), "人 " + str(int(det["score"] * 100)) + "%")
    if drawn:
        painter.fillRect(0, 0, 210, 26, QColor(4, 20, 30, 210))
        painter.setPen(QColor("#f0ca7d"))
        font = QFont("Microsoft YaHei"); font.setPixelSize(12); painter.setFont(font)
        # Exact NPU-input identity still is not sensor-frame identity.
        exact = panel.get("frame_match") == "exact_preview_jpeg"
        label = ("RK检测框 · NPU输入JPEG精确一致 · 非传感器源帧/骨架"
                 if exact else "RK检测框 · 非同帧保证 · 非骨架")
        painter.fillRect(0, 0, 360 if exact else 210, 26, QColor(4, 20, 30, 210))
        painter.drawText(10, 18, label)
    painter.restore()
