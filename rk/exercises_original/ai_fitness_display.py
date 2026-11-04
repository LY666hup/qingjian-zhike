from __future__ import annotations

import logging
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional


LOGGER = logging.getLogger(__name__)

DISPLAY_AUTO_VALUES = {"", "auto"}
DISPLAY_ON_VALUES = {"1", "true", "yes", "on"}
DISPLAY_OFF_VALUES = {"0", "false", "no", "off"}
NO_KEY = 255


def discover_desktop_environment(
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Add same-user local desktop hints that SSH normally does not inherit."""
    detected = dict(os.environ if environ is None else environ)
    if detected.get("DISPLAY") or detected.get("WAYLAND_DISPLAY"):
        return detected

    runtime_dir = detected.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    runtime_path = Path(runtime_dir)
    if runtime_path.is_dir():
        for socket_path in sorted(runtime_path.glob("wayland-*")):
            try:
                if stat.S_ISSOCK(socket_path.stat().st_mode):
                    detected["XDG_RUNTIME_DIR"] = runtime_dir
                    detected["WAYLAND_DISPLAY"] = socket_path.name
                    break
            except OSError:
                continue

    x11_path = Path("/tmp/.X11-unix/X0")
    try:
        if not detected.get("DISPLAY") and stat.S_ISSOCK(x11_path.stat().st_mode):
            detected["DISPLAY"] = ":0"
    except OSError:
        pass

    return detected


def probe_opencv_gui(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Probe HighGUI in a child process so a backend abort cannot kill recognition."""
    probe_code = """
import cv2
import numpy as np

name = "fitness-display-probe"
try:
    cv2.namedWindow(name, cv2.WINDOW_NORMAL)
    cv2.imshow(name, np.zeros((1, 1, 3), dtype=np.uint8))
    cv2.waitKey(1)
finally:
    try:
        cv2.destroyWindow(name)
    except Exception:
        pass
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe_code],
            env=dict(os.environ if environ is None else environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def resolve_display_enabled(
    environ: Optional[Mapping[str, str]] = None,
    probe: Callable[[Optional[Mapping[str, str]]], bool] = probe_opencv_gui,
) -> bool:
    """Resolve FITNESS_DISPLAY at action startup instead of import time."""
    source_environ = os.environ if environ is None else environ
    raw_mode = source_environ.get("FITNESS_DISPLAY", "auto").strip().lower()
    display_environ = (
        discover_desktop_environment() if environ is None else dict(environ)
    )

    if raw_mode in DISPLAY_OFF_VALUES:
        LOGGER.info("摄像头预览：已强制关闭")
        return False

    if raw_mode in DISPLAY_ON_VALUES:
        if probe(display_environ):
            LOGGER.info("摄像头预览：已强制开启")
            return True
        LOGGER.warning("摄像头预览不可用，自动切换为无头模式")
        return False

    if raw_mode not in DISPLAY_AUTO_VALUES:
        LOGGER.warning(
            "FITNESS_DISPLAY=%r 无效，将按 auto 自动判断", raw_mode
        )

    if not (display_environ.get("DISPLAY") or display_environ.get("WAYLAND_DISPLAY")):
        LOGGER.info("摄像头预览：未发现可用桌面环境，使用无头模式")
        return False

    if probe(display_environ):
        LOGGER.info("摄像头预览：自动检测到可用桌面")
        return True

    LOGGER.warning("检测到桌面环境但 OpenCV 无法显示，自动切换为无头模式")
    return False


class DisplaySession:
    """Own one optional OpenCV window and downgrade safely on HighGUI errors."""

    def __init__(
        self,
        cv2_module,
        window_name: str,
        width: int = 960,
        height: int = 720,
        environ: Optional[Mapping[str, str]] = None,
        probe: Callable[[Optional[Mapping[str, str]]], bool] = probe_opencv_gui,
    ):
        self.cv2 = cv2_module
        self.window_name = window_name
        self.width = width
        self.height = height
        self.environ = environ
        self.probe = probe
        self.enabled = False
        self.created = False
        self._warned = False

    def open(self) -> bool:
        display_environ = (
            discover_desktop_environment()
            if self.environ is None
            else dict(self.environ)
        )
        if not resolve_display_enabled(display_environ, self.probe):
            return False

        if self.environ is None:
            for name in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY"):
                value = display_environ.get(name)
                if value:
                    os.environ[name] = value

        try:
            self.cv2.namedWindow(self.window_name, self.cv2.WINDOW_NORMAL)
            self.created = True
            self.cv2.resizeWindow(self.window_name, self.width, self.height)
            self.enabled = True
            return True
        except Exception as exc:
            self._disable("创建摄像头预览窗口失败", exc)
            return False

    def set_mouse_callback(self, callback, userdata=None) -> bool:
        if not self.enabled:
            return False

        try:
            self.cv2.setMouseCallback(self.window_name, callback, userdata)
            return True
        except Exception as exc:
            self._disable("设置摄像头预览鼠标回调失败", exc)
            return False

    def show(self, frame) -> int:
        if not self.enabled:
            return NO_KEY

        try:
            self.cv2.imshow(self.window_name, frame)
            return self.cv2.waitKey(1) & 0xFF
        except Exception as exc:
            self._disable("摄像头预览运行失败", exc)
            return NO_KEY

    def close(self):
        if not self.created:
            self.enabled = False
            return

        try:
            self.cv2.destroyWindow(self.window_name)
        except Exception as exc:
            if not self._warned:
                LOGGER.warning("关闭摄像头预览窗口失败：%s", exc)
                self._warned = True
        finally:
            self.created = False
            self.enabled = False

    def _disable(self, message: str, exc: Exception):
        if not self._warned:
            LOGGER.warning("%s，自动切换为无头模式：%s", message, exc)
            self._warned = True
        self.close()
