# -*- coding: utf-8 -*-
"""核对打包后的 exe：窗口位置尺寸、右边留了多少真桌面、并抓一张只有游戏窗口的图。"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

from PIL import ImageGrab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "桌面消消乐.exe")
OUT = os.path.join(ROOT, "preview", "exe_window.png")

u = ctypes.windll.user32
u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
u.FindWindowW.restype = ctypes.c_void_p
u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]


def main():
    proc = subprocess.Popen([EXE])
    try:
        hwnd = 0
        for _ in range(60):
            time.sleep(0.3)
            hwnd = u.FindWindowW(None, "桌面")
            if hwnd:
                break
        if not hwnd:
            print("FAIL 没找到窗口")
            return 1
        time.sleep(2.2)
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        print(f"窗口 ({r.left},{r.top}) {w}x{h} · 屏幕 {sw}x{sh} · 右侧留 {sw - r.right}px")
        assert r.left == 0 and r.top == 0, "窗口没贴在左上角"
        assert r.right < sw, "窗口占满整屏，右边没有真桌面"
        assert r.bottom <= sh, "窗口超出屏幕"

        u.SetForegroundWindow(ctypes.c_void_p(hwnd))
        time.sleep(0.6)
        img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
        img.save(OUT)
        print("saved", OUT, img.size)
        return 0
    finally:
        h = u.FindWindowW(None, "桌面")
        if h:
            u.PostMessageW(h, 0x0010, 0, 0)
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
