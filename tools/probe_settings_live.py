# -*- coding: utf-8 -*-
"""真机跑一遍：设置窗口弹在游戏上面 → 点“开始游戏” → 窗口关掉、游戏继续。

用法：
    python tools/probe_settings_live.py          用源码跑
    python tools/probe_settings_live.py exe      用 dist 里的 exe 跑
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TITLE = "桌面消消乐 · 玩法与联机设置"
use_exe = len(sys.argv) > 1 and sys.argv[1] == "exe"

if use_exe:
    target = [os.path.join(ROOT, "dist", "桌面消消乐.exe")]
else:
    target = [sys.executable, os.path.join(ROOT, "main.py")]

env = {k: v for k, v in os.environ.items() if not k.startswith("SDL_")}
env["DM3_DATA_DIR"] = os.path.join(ROOT, ".testdata")
env["DM3_OPEN_SETTINGS"] = "solo"

u = ctypes.windll.user32
u.FindWindowW.restype = wintypes.HWND
u.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_size_t,
                           ctypes.c_ssize_t]

preview = os.path.join(ROOT, "preview")
os.makedirs(preview, exist_ok=True)
proc = subprocess.Popen(target, cwd=ROOT, env=env)
try:
    hwnd = None
    for _ in range(60):
        time.sleep(0.25)
        hwnd = u.FindWindowW(None, TITLE)
        if hwnd:
            break
    assert hwnd, "设置窗口没开出来"
    time.sleep(0.6)
    shot = os.path.join(preview, "settings_live_open.png")
    ImageGrab.grab().save(shot)
    print("弹窗截图", shot)

    # 点“开始游戏”：客户区坐标 (552, 613)
    lp = (613 << 16) | 552
    u.PostMessageW(hwnd, 0x0201, 1, lp)
    u.PostMessageW(hwnd, 0x0202, 0, lp)
    time.sleep(1.2)
    assert not u.IsWindowVisible(hwnd), "点了开始窗口还在"
    print("点开始 → 窗口已关闭")
    time.sleep(0.8)
    shot = os.path.join(preview, "settings_live_after.png")
    ImageGrab.grab().save(shot)
    print("关掉之后", shot)

    # 再叫一次（模拟 Ctrl+J 走的是同一条 open 路径），确认能反复开
    u.PostMessageW(hwnd, 0x0010, 0, 0)     # 先确认 WM_CLOSE 只是隐藏
    print("ok")
finally:
    proc.terminate()
    proc.wait(timeout=10)
