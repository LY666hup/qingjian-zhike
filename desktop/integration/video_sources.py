"""Independent local/raw/RTSP video source adapters.

Only the newest decoded frame is rendered. No code in this module opens an RK
device or initiates a connection until the user explicitly selects a source.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink


@dataclass(frozen=True)
class SourceSpec:
    kind: str = "none"  # none | mp4 | yuyv | snapshot | rtsp
    location: str = ""
    width: int = 1920
    height: int = 1080
    fps: float = 25.0
    loop: bool = False
    status_url: str = ""
    provenance: str = "external"
    expected_processing_mode: str = ""

    def validate(self) -> None:
        if self.kind not in {"none", "mp4", "yuyv", "snapshot", "rtsp", "http"}:
            raise ValueError("不支持的视频源类型")
        if self.kind in {"mp4", "yuyv", "snapshot"}:
            path = Path(self.location).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"文件不存在：{path}")
            if self.kind == "mp4" and path.suffix.lower() not in {".mp4", ".mov", ".mkv"}:
                raise ValueError("请选择 MP4、MOV 或 MKV 视频")
            if self.kind == "yuyv" and path.suffix.lower() not in {".yuyv", ".raw"}:
                raise ValueError("请选择 YUYV 原始视频")
            if self.kind == "snapshot" and path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                raise ValueError("请选择 JPEG 或 PNG 诊断快照")
        if self.kind in {"rtsp", "http"}:
            url = QUrl(self.location)
            if url.scheme().lower() not in {"rtsp", "rtsps", "http", "https"} or not url.host():
                raise ValueError("RTSP 地址必须包含 rtsp:// 和主机名")
        if self.kind == "yuyv" and (
            self.width < 2 or self.width % 2 or self.height < 1
            or not (0.1 <= self.fps <= 120)
        ):
            raise ValueError("YUYV 需要正偶数宽度、正高度和 0.1–120 FPS")


def yuyv_preview(raw: bytes, width: int, height: int, max_width: int = 960) -> QImage:
    """BT.601 limited-range YUYV422 preview; preserve source dimensions in metadata."""
    if len(raw) != width * height * 2 or width % 2:
        raise ValueError("YUYV 帧长度或宽度不匹配")
    pairs = np.frombuffer(raw, dtype=np.uint8).reshape(height, width // 2, 4)
    yy = pairs[:, :, (0, 2)].reshape(height, width).astype(np.int32)
    uu = np.repeat(pairs[:, :, 1], 2, axis=1).astype(np.int32) - 128
    vv = np.repeat(pairs[:, :, 3], 2, axis=1).astype(np.int32) - 128
    c = np.maximum(yy - 16, 0)
    r = np.clip((298 * c + 409 * vv + 128) >> 8, 0, 255).astype(np.uint8)
    g = np.clip((298 * c - 100 * uu - 208 * vv + 128) >> 8, 0, 255).astype(np.uint8)
    b = np.clip((298 * c + 516 * uu + 128) >> 8, 0, 255).astype(np.uint8)
    rgb = np.ascontiguousarray(np.stack((r, g, b), axis=2))
    image=QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
    return image.scaledToWidth(max_width,Qt.TransformationMode.FastTransformation) if width>max_width else image


class YuyvReader(QThread):
    status = Signal(str, str)

    def __init__(self, spec: SourceSpec):
        super().__init__()
        self.spec = spec
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()
        self._image: QImage | None = None
        self._seq = 0
        self.image_at=0.0

    def request_stop(self) -> None:
        self._stop.set()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause.set()
        else:
            self._pause.clear()

    def latest(self) -> tuple[int, QImage | None]:
        with self._lock:
            return self._seq, self._image

    def run(self) -> None:
        spec = self.spec
        size = spec.width * spec.height * 2
        try:
            path = Path(spec.location)
            total = path.stat().st_size
            if total == 0 or total % size:
                raise ValueError(f"YUYV 文件 {total} 字节，不是 {size} 字节/帧的整数倍")
            with path.open("rb") as stream:
                due = time.monotonic()
                while not self._stop.is_set():
                    if self._pause.is_set():
                        self._stop.wait(0.04)
                        due = time.monotonic()
                        continue
                    raw = stream.read(size)
                    if len(raw) != size:
                        if spec.loop:
                            stream.seek(0)
                            continue
                        self.status.emit("ended", "文件播放完毕")
                        return
                    image = yuyv_preview(raw, spec.width, spec.height)
                    with self._lock:
                        self._image = image
                        self._seq += 1
                        self.image_at=time.monotonic()
                    due += 1.0 / spec.fps
                    delay = due - time.monotonic()
                    if delay > 0:
                        self._stop.wait(delay)
                    else:
                        # Drop old schedule debt rather than accumulating display delay.
                        due = time.monotonic()
        except Exception as exc:
            self.status.emit("error", str(exc))


class VideoLane(QObject):
    frame_ready = Signal(QImage)
    state_changed = Signal(str, str)
    stats_changed = Signal(str, float, int)  # source WxH, rendered FPS, count
    source_changed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.spec = SourceSpec()
        self.state = "disconnected"
        self.details = "未连接"
        self.player: QMediaPlayer | None = None
        self.sink: QVideoSink | None = None
        self.raw: YuyvReader | None = None
        self._latest_video: QVideoFrame | None = None
        self._video_seq = 0
        self._last_video_seq = 0
        self._last_raw_seq = 0
        self._epoch = 0
        self._paused = False
        self._source_size = "--"
        self.rendered_count = 0
        self.skipped_count = 0
        self._render_times: deque[float] = deque()
        self._last_frame_at = 0.0
        self._has_frame = False
        self._reconnect = True
        self._closing = False
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(16)
        self._render_timer.timeout.connect(self._render_latest)
        self._render_timer.start()
        self._connect_timeout = QTimer(self)
        self._connect_timeout.setSingleShot(True)
        self._connect_timeout.timeout.connect(self._on_connect_timeout)

    def _set_state(self, state: str, details: str) -> None:
        if self.state != state or self.details != details:
            self.state, self.details = state, details
            self.state_changed.emit(state, details)

    def start(self, spec: SourceSpec, *, reconnect: bool = True) -> None:
        spec.validate()
        self.disconnect(manual=False)
        self.spec = spec
        self._reconnect = reconnect
        self._epoch += 1
        epoch = self._epoch
        self._paused = False
        self._has_frame = False
        self._source_size = "--"
        self._last_frame_at = time.monotonic()
        self.source_changed.emit(spec.location or "无视频源")
        if spec.kind == "none":
            self._set_state("disconnected", "未选择视频源")
            return
        self._set_state("connecting", "正在打开视频源")
        if spec.kind == "yuyv":
            self._source_size = f"{spec.width}×{spec.height}"
            worker = YuyvReader(spec)
            self.raw = worker
            worker.status.connect(lambda state, detail, e=epoch: self._on_raw_status(e, state, detail))
            worker.start()
            return
        if spec.kind == "snapshot":
            image = QImage(str(Path(spec.location).expanduser()))
            if image.isNull():
                self._fail("诊断快照无法解码")
                return
            self._source_size = f"{image.width()}×{image.height()}"
            self._publish_snapshot(epoch, image)
            return
        player = QMediaPlayer(self)
        sink = QVideoSink(self)
        self.player, self.sink = player, sink
        player.setVideoOutput(sink)
        sink.videoFrameChanged.connect(lambda frame, e=epoch: self._on_media_frame(e, frame))
        player.mediaStatusChanged.connect(lambda status, e=epoch: self._on_media_status(e, status))
        player.errorOccurred.connect(lambda error, message, e=epoch: self._on_media_error(e, error, message))
        url = QUrl.fromLocalFile(str(Path(spec.location).resolve())) if spec.kind == "mp4" else QUrl(spec.location)
        player.setSource(url)
        player.play()
        if spec.kind in {"rtsp", "http"}:
            self._connect_timeout.start(6000)

    def _publish_snapshot(self, epoch: int, image: QImage) -> None:
        if epoch != self._epoch or self._closing:
            return
        self._has_frame = True
        self._last_frame_at = time.monotonic()
        self._set_state("connected", "板端诊断快照（非实时）")
        self.frame_ready.emit(image)
        self.rendered_count += 1
        self._render_times.append(time.monotonic())
        self.stats_changed.emit(self._source_size, 0.0, self.rendered_count)
        self._set_state("ended", "板端诊断快照已显示；非实时")

    def disconnect(self, *, manual: bool = True) -> None:
        self._epoch += 1
        self._connect_timeout.stop()
        self._latest_video = None
        self._video_seq = self._last_video_seq = 0
        self._last_raw_seq = 0
        self._has_frame = False
        self._paused = False
        if self.player is not None:
            self.player.stop()
            self.player.setVideoOutput(None)
            self.player.deleteLater()
            self.player = None
        if self.sink is not None:
            self.sink.deleteLater()
            self.sink = None
        if self.raw is not None:
            self.raw.request_stop()
            self.raw.wait(1500)
            self.raw.deleteLater()
            self.raw = None
        if manual:
            self._reconnect = False
            self._set_state("disconnected", "手动断开")

    def set_paused(self, paused: bool) -> None:
        if self.state not in {"connected", "buffering", "paused"}:
            return
        self._paused = paused
        if self.player:
            self.player.pause() if paused else self.player.play()
        if self.raw:
            self.raw.set_paused(paused)
        self._set_state("paused" if paused else "connected", "已暂停" if paused else "播放中")

    def _on_media_frame(self, epoch: int, frame: QVideoFrame) -> None:
        if epoch != self._epoch or not frame.isValid():
            return
        self._latest_video = frame
        self._video_seq += 1
        size = frame.size()
        if size.isValid():
            self._source_size = f"{size.width()}×{size.height()}"
        self._last_frame_at = time.monotonic()
        if not self._has_frame:
            self._has_frame = True
            self._connect_timeout.stop()
        if self.state!='connected' and not self._paused:
            self._set_state("connected", "播放中")

    def _on_media_status(self, epoch: int, status: QMediaPlayer.MediaStatus) -> None:
        if epoch != self._epoch:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.spec.loop and self.player is not None:
                self.player.setPosition(0)
                self.player.play()
            else:
                self._set_state("ended", "文件播放完毕")
        elif status in {QMediaPlayer.MediaStatus.BufferingMedia, QMediaPlayer.MediaStatus.StalledMedia}:
            if not self._paused:
                self._set_state("buffering", "视频缓冲中")
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._fail("视频格式不可用或地址无响应")

    def _on_media_error(self, epoch: int, error: QMediaPlayer.Error, message: str) -> None:
        if epoch == self._epoch and error != QMediaPlayer.Error.NoError:
            self._fail(message or "视频解码失败")

    def _on_raw_status(self, epoch: int, state: str, detail: str) -> None:
        if epoch != self._epoch:
            return
        if state == "ended":
            self._set_state("ended", detail)
        else:
            self._fail(detail)

    def _on_connect_timeout(self) -> None:
        if self.spec.kind in {"rtsp", "http"} and not self._has_frame and not self._closing:
            self._fail("6 秒内未收到视频帧")

    def _fail(self, message: str) -> None:
        if self.state == "interrupted" or self._closing:
            return
        self._set_state("interrupted", message[:160])
        if self.spec.kind in {"rtsp", "http"} and self._reconnect:
            epoch = self._epoch
            QTimer.singleShot(1800, lambda e=epoch: self._retry(e))

    def _retry(self, epoch: int) -> None:
        if epoch != self._epoch or self._closing or not self._reconnect:
            return
        spec = self.spec
        self._set_state("reconnecting", "准备自动重连")
        self.start(spec, reconnect=True)

    def _render_latest(self) -> None:
        if self._closing or self._paused:
            return
        image: QImage | None = None
        if self.raw is not None:
            seq, image = self.raw.latest()
            if seq == self._last_raw_seq:
                image = None
            else:
                self.skipped_count+=max(0,seq-self._last_raw_seq-1)
                self._last_raw_seq = seq
                self._last_frame_at = self.raw.image_at
                if not self._has_frame:
                    self._has_frame = True
                    self._set_state("connected", "播放中")
        elif self._video_seq != self._last_video_seq and self._latest_video is not None:
            self.skipped_count+=max(0,self._video_seq-self._last_video_seq-1)
            self._last_video_seq = self._video_seq
            image = self._latest_video.toImage()
            self._latest_video = None
        if image is None or image.isNull():
            return
        self.frame_ready.emit(image)
        self.rendered_count += 1
        now = time.monotonic()
        self._render_times.append(now)
        while self._render_times and self._render_times[0] < now - 1.0:
            self._render_times.popleft()
        self.stats_changed.emit(self._source_size, float(len(self._render_times)), self.rendered_count)

    def close(self) -> None:
        self._closing = True
        self._render_timer.stop()
        self.disconnect(manual=True)

