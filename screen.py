"""
Screen capture.

WindowScreen (Windows 10 1903+): Windows Graphics Capture of the game's own window, the
API OBS uses. Fast, works on any monitor, and only sees the game: our strip and other
windows on top of it never get in. Used whenever the game window is found.

GameScreen: a plain grab of the game monitor (mss). The fallback, and what runs on
other systems or before the game starts.

Picks the monitor that is 2560x1440 (or the largest one) as the game screen,
and reports the other monitor so the dashboard can open there.
"""

from __future__ import annotations

import sys
from typing import Optional

import numpy as np


def make_dpi_aware():
    """Without this, Windows scaling (125%, 150%) shifts every pixel position."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def list_monitors():
    import mss
    with mss.mss() as sct:
        return [dict(m) for m in sct.monitors[1:]]   # [0] is all screens stitched together


def pick_monitors(game_size=(2560, 1440), game_index: Optional[int] = None):
    """Returns (game_monitor, dashboard_monitor or None)."""
    mons = list_monitors()
    if not mons:
        raise RuntimeError("No monitors found")
    if game_index is not None and 1 <= game_index <= len(mons):
        game = mons[game_index - 1]
    else:
        exact = [m for m in mons if (m["width"], m["height"]) == tuple(game_size)]
        game = exact[0] if exact else max(mons, key=lambda m: m["width"] * m["height"])
    others = [m for m in mons if m is not game]
    return game, (others[0] if others else None)


class GameScreen:
    def __init__(self, monitor: dict):
        import mss
        self.monitor = {k: monitor[k] for k in ("left", "top", "width", "height")}
        self._sct = mss.mss()

    def grab(self) -> np.ndarray:
        shot = self._sct.grab(self.monitor)
        # mss gives BGRA; HUD reader wants RGB as int16
        arr = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return arr[:, :, [2, 1, 0]].astype(np.int16)

    def save(self, path: str):
        from PIL import Image
        Image.fromarray(self.grab().astype(np.uint8)).save(path)


def _import_windows_capture():
    """windows_capture imports OpenCV only to save images, which we never do. The installer
    leaves OpenCV out (it's large), so stand in a tiny placeholder when it's missing."""
    import importlib
    import types
    try:
        import cv2  # noqa: F401
    except ImportError:
        sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    return importlib.import_module("windows_capture")


class WindowScreen:
    """Frames from the game's window via Windows Graphics Capture, cropped to the game picture."""

    def __init__(self, game_window: dict, max_fps: int = 10):
        import threading
        wc = _import_windows_capture()
        self.window = game_window
        self._lock = threading.Lock()
        self._frame = None            # latest BGRA frame (a copy)
        self._new = threading.Event()
        self.closed = False
        cap = wc.WindowsCapture(cursor_capture=False, draw_border=False,
                                minimum_update_interval=int(1000 / max_fps),
                                window_hwnd=game_window["hwnd"])

        @cap.event
        def on_frame_arrived(frame, control):
            if self.closed:
                control.stop()
                return
            buf = frame.frame_buffer
            with self._lock:
                self._frame = buf.copy()          # the mapped buffer is only valid inside this call
            self._new.set()

        @cap.event
        def on_closed():
            self.closed = True                    # the game window went away
            self._new.set()

        self._control = cap.start_free_threaded()

    def grab(self, timeout: float = 1.5) -> np.ndarray:
        if self._frame is None:
            self._new.wait(timeout)
        with self._lock:
            f = self._frame
        if f is None:
            raise RuntimeError("no frame from the game window yet")
        # Windowed mode: the capture includes the title bar and borders; keep the game picture.
        c = (self.window or {}).get("client") or {}
        cw, ch = c.get("width") or f.shape[1], c.get("height") or f.shape[0]
        if 0 < cw < f.shape[1] or 0 < ch < f.shape[0]:
            side = max(0, (f.shape[1] - cw) // 2)
            top = max(0, f.shape[0] - ch - side)
            f = f[top:top + ch, side:side + cw]
        return f[:, :, [2, 1, 0]].astype(np.int16)

    def save(self, path: str):
        from PIL import Image
        Image.fromarray(self.grab().astype(np.uint8)).save(path)

    def stop(self):
        self.closed = True
        try:
            self._control.stop()
        except Exception:
            pass


def window_capture_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        _import_windows_capture()
        return True
    except Exception:
        return False
