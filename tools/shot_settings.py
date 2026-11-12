# -*- coding: utf-8 -*-
"""开真实窗口，把设置子窗口截下来看看排版对不对。

用法：python tools/shot_settings.py [A:join:lan] [out.png]
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
state = sys.argv[1] if len(sys.argv) > 1 else "solo"
out = os.path.join(ROOT, "preview", (sys.argv[2] if len(sys.argv) > 2
                                     else f"settings_{state.replace(':', '_')}.png"))

env = {k: v for k, v in os.environ.items() if not k.startswith("SDL_")}
env["DM3_DATA_DIR"] = os.path.join(ROOT, ".testdata")
env["DM3_OPEN_SETTINGS"] = state

proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py")],
                        cwd=ROOT, env=env)
try:
    time.sleep(3.2)
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, "桌面消消乐 · 玩法与联机设置")
    if not hwnd:
        print("没找到设置窗口")
        sys.exit(1)
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    box = (r.left - 8, r.top - 34, r.right + 8, r.bottom + 8)
    img = ImageGrab.grab()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.crop(box).save(out)
    print("saved", out, os.path.getsize(out), "rect", r.left, r.top,
          r.right - r.left, r.bottom - r.top)
finally:
    proc.terminate()
    proc.wait(timeout=10)
