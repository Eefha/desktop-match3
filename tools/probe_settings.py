# -*- coding: utf-8 -*-
"""在进程内真开一次设置窗口，用真鼠标消息点一遍，验证点击 → 状态 → 结果。"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import ctypes

import pygame

import settings_win as sw

pygame.init()
pygame.display.set_mode((320, 200))


def click(w, key):
    """按命中表里那个控件的中心，发一条真的鼠标按下 / 抬起。"""
    w._render()
    rect = next((r for r, k in w._hits if k == key), None)
    if rect is None:
        raise AssertionError("界面里没有 " + key)
    x, y = rect.centerx, rect.centery
    lp = (y << 16) | (x & 0xFFFF)
    sw.user32.PostMessageW(w.hwnd, sw.WM_LBUTTONDOWN, 1, lp)
    sw.user32.PostMessageW(w.hwnd, sw.WM_LBUTTONUP, 0, lp)
    for _ in range(6):
        w.pump()
        pygame.event.get()
        time.sleep(0.02)


w = sw.SettingsWin()
assert w.open(mode="solo"), "窗口打不开"
click(w, "mode:B")
assert w.mode == "B", w.mode
click(w, "role:join")
assert w.role == "join"
click(w, "tr:bt")
assert w.transport == "bt"
w._set_text("bt", "93378c718800")
w._read_fields()
click(w, "ok")
cfg = w.take_result()
assert cfg and cfg["bt"] == "93:37:8C:71:88:00", cfg
assert not w.open_, "点了开始窗口应该关掉"
print("点击 OK", cfg)

# 再开一次：地址写错要拦住
w.open(mode="A", role="join", transport="lan")
w._set_text("addr", "10.0.2")
w._read_fields()
w._submit()
assert w.take_result() is None and w.err, w.err
print("校验 OK", w.err)
w._set_text("addr", "10.0.2.15:48213")
w._read_fields()
w._submit()
cfg = w.take_result()
assert cfg["room"] == {"ip": "10.0.2.15", "port": 48213, "code": "", "mode": "A"}, cfg
print("地址 OK", cfg["room"])
w.destroy()
pygame.quit()
