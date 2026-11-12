# -*- coding: utf-8 -*-
"""验证「第一次打开就弹设置窗口」以及底部条的「设置」按钮。"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
u = ctypes.windll.user32
u.FindWindowW.restype = wintypes.HWND

data = os.path.join(ROOT, ".testdata")
score = os.path.join(data, "score.json")
if "again" not in sys.argv:                # 带 again 就不清档：验证“第二次不再弹”
    if os.path.exists(score):
        os.remove(score)

env = {k: v for k, v in os.environ.items() if not k.startswith("SDL_")}
env["DM3_DATA_DIR"] = data
exe = os.path.join(ROOT, "dist", "桌面消消乐.exe")
cmd = ([exe] if "exe" in sys.argv and os.path.exists(exe)
       else [sys.executable, os.path.join(ROOT, "main.py")])
p = subprocess.Popen(cmd, cwd=ROOT, env=env)
try:
    time.sleep(6.5 if "exe" in sys.argv else 4.5)
    raw = u.FindWindowW(None, "桌面消消乐 · 玩法与联机设置")
    dlg = raw if (raw and u.IsWindowVisible(raw)) else None   # 可能残留上一轮的隐藏窗口
    game = u.FindWindowW(None, "桌面")
    print("首次启动自动弹出设置窗口:", bool(dlg), "| 游戏窗口:", bool(game))
    if dlg:
        u.SetForegroundWindow(dlg)
        time.sleep(0.6)
    ImageGrab.grab().save(os.path.join(ROOT, "preview", "first_run.png"))

    # 点「取消」把它关掉：直接往窗口投鼠标消息，不怕被别的窗口挡着
    if dlg:
        lp = (613 << 16) | 399                 # 客户区里那个「取消」按钮的中心
        u.PostMessageW(dlg, 0x0201, 1, lp)
        u.PostMessageW(dlg, 0x0202, 0, lp)
    time.sleep(1.2)
    print("关掉后设置窗口还在:", bool(u.IsWindowVisible(dlg)) if dlg else None)

    # 底部条上的「设置」按钮：坐标由 selftest 打印过的布局推出来
    ImageGrab.grab().save(os.path.join(ROOT, "preview", "first_run_after.png"))
    print("截图已存 preview/first_run*.png")
finally:
    p.terminate()
    p.wait(timeout=10)
