# -*- coding: utf-8 -*-
"""启动真实窗口，抓一张全屏截图，检查左边游戏和右边真桌面是不是接得上。"""
import os
import subprocess
import sys
import time

from PIL import ImageGrab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(ROOT, "preview", "live.png")

def show_desktop():
    """先把所有窗口收起来（Win+D），这样右边露出来的才是真桌面。"""
    import ctypes
    u = ctypes.windll.user32
    VK_LWIN, VK_D = 0x5B, 0x44
    KEYUP = 0x0002
    u.keybd_event(VK_LWIN, 0, 0, 0)
    u.keybd_event(VK_D, 0, 0, 0)
    u.keybd_event(VK_D, 0, KEYUP, 0)
    u.keybd_event(VK_LWIN, 0, KEYUP, 0)


show_desktop()
time.sleep(0.8)

proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py")],
                        cwd=ROOT, env={**os.environ, "DM3_DATA_DIR": os.path.join(ROOT, ".testdata")})
time.sleep(4.0)
try:
    img = ImageGrab.grab()
    img.save(out)
    print("saved", out, img.size)
    # 顺带确认窗口的位置和大小
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, "桌面")
    r = wintypes.RECT()
    u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
    print("窗口 rect", r.left, r.top, r.right - r.left, r.bottom - r.top)
finally:
    proc.terminate()
    proc.wait(timeout=10)
