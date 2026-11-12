# -*- coding: utf-8 -*-
"""端到端点一下 exe 底部的「重新排列」按钮：棋盘应该真的换了。"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

from PIL import ImageChops, ImageGrab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "桌面消消乐.exe")

u = ctypes.windll.user32
u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
u.FindWindowW.restype = ctypes.c_void_p
u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
u.PostMessageW.restype = wt.BOOL

BTN = (939, 1048)          # 按钮中心（客户区坐标）


def click(hwnd, x, y, origin=(0, 0)):
    """用真实鼠标事件点（PostMessage 打不进 SDL 的输入链）。"""
    ox, oy = origin
    u.SetCursorPos(ox + x, oy + y)
    time.sleep(0.25)
    u.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
    time.sleep(0.09)
    u.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP


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
        u.SetForegroundWindow(hwnd)
        time.sleep(2.0)
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        box = (r.left, r.top, r.right, r.bottom)
        before = ImageGrab.grab(bbox=box)
        before.save(os.path.join(ROOT, "preview", "shuffle_before.png"))

        click(hwnd, *BTN, origin=(r.left, r.top))
        time.sleep(1.6)
        after = ImageGrab.grab(bbox=box)
        after.save(os.path.join(ROOT, "preview", "shuffle_after.png"))

        # 只看棋盘区（避开底部条），算像素差
        w, h = before.size
        grid = (0, 0, w, h - 150)
        diff = ImageChops.difference(before.crop(grid), after.crop(grid)).convert("L")
        hist = diff.histogram()
        changed = sum(hist[8:])                      # 忽略抗锯齿级别的微小差异
        total = w * (h - 150)
        ratio = changed / total
        print(f"棋盘区变化像素 {changed}/{total} = {ratio:.1%}")
        if ratio < 0.10:
            print("FAIL 棋盘几乎没变，重排没生效")
            return 1
        print("OK  点按钮后棋盘确实重排了")
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
