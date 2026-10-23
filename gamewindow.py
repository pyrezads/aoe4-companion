"""
Finds Age of Empires IV's window (Windows only), so the app can pin its strip to it.

Looks for the game's program, RelicCardinal.exe, and returns that window's exact
title and position. The title is what electron-overlay-window attaches by.
On other systems, or when the game isn't running, returns None.
"""

from __future__ import annotations

import sys
from typing import Optional

GAME_EXES = ("reliccardinal.exe", "reliccardinal_es.exe")   # Steam / Microsoft Store builds
TITLE_PREFIX = "Age of Empires IV"


def find_game_window() -> Optional[dict]:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    found = []

    def exe_of(hwnd) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1].lower()
            return ""
        finally:
            kernel32.CloseHandle(h)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        exe = exe_of(hwnd)
        if exe in GAME_EXES or title.startswith(TITLE_PREFIX):
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            found.append({"title": title, "exe": exe, "left": r.left, "top": r.top,
                          "width": r.right - r.left, "height": r.bottom - r.top,
                          "by_exe": exe in GAME_EXES})
        return True

    try:
        user32.EnumWindows(visit, 0)
    except Exception:
        return None
    if not found:
        return None
    # the game's own window first, then the biggest
    found.sort(key=lambda w: (not w["by_exe"], -(w["width"] * w["height"])))
    return found[0]


if __name__ == "__main__":
    print(find_game_window())
