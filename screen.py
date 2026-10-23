"""
Screen capture for the game monitor only.

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
