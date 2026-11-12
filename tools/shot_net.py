# -*- coding: utf-8 -*-
"""联机界面出图：大厅、选玩法、等待、联机中、比分条。"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["DM3_DATA_DIR"] = os.path.join(ROOT, ".testdata")
os.chdir(ROOT)

import pygame                       # noqa: E402

from winicons import enable_dpi_awareness  # noqa: E402

enable_dpi_awareness()
pygame.init()

import app                          # noqa: E402
from app import Game, wallpaper_crop  # noqa: E402
from winicons import load_pieces     # noqa: E402

OUT = os.path.join(ROOT, "preview")
W, H = app.window_rect()[2:]


def make(seed=21):
    pygame.display.set_mode((W, H))
    g = Game(pygame.display.get_surface(), load_pieces(10, 6), seed=seed,
             bg=wallpaper_crop(0, 0, W, H))
    for _ in range(60):
        g.update(1 / 60)
    return g


def fake_players(g, n=3):
    names = ["房主", "老王", "小张", "阿伟", "小李"]
    g.net.players = {}
    for i in range(n):
        g.net.players[i] = {"id": i, "n": names[i], "s": 0, "m": 0, "lat": 8 + i * 5}
    g.net.players[0]["s"] = 1240
    g.net.players[0]["m"] = 47
    if n > 1:
        g.net.players[1]["s"] = 980
        g.net.players[1]["m"] = 41
    if n > 2:
        g.net.players[2]["s"] = 610
        g.net.players[2]["m"] = 33


def main():
    # 1 大厅：假装搜到两个房间
    g = make()
    g.net.panel = "menu"
    g.net.scan = types.SimpleNamespace(done=True, rooms={
        "4821": {"t": "room", "code": "4821", "mode": "A", "n": 2, "cap": 5,
                 "ip": "10.0.2.37"},
        "7106": {"t": "room", "code": "7106", "mode": "B", "n": 1, "cap": 5,
                 "ip": "10.0.2.51"},
    })
    g.draw()
    pygame.image.save(g.screen, os.path.join(OUT, "net_menu.png"))

    # 2 选玩法
    g.net.panel = "pick"
    g.draw()
    pygame.image.save(g.screen, os.path.join(OUT, "net_pick.png"))

    # 3 等待对方进来
    g.net.panel = "wait"
    g.net.active, g.net.role, g.net.mode = True, "host", "A"
    g.net.code, g.net.transport, g.net._ip = "4821", "lan", "10.0.2.15"
    fake_players(g, 2)
    g.draw()
    pygame.image.save(g.screen, os.path.join(OUT, "net_wait.png"))

    # 4 联机中（A 模式：右边显示倒计时）
    g.net.panel = "status"
    g.net.mode, g.net.running = "A", True
    g.net.left = 97.4
    g.net.my_id = 1
    fake_players(g, 3)
    g.score = g.net.players[1]["s"]
    g.moves = g.net.players[1]["m"]
    g.draw()
    pygame.image.save(g.screen, os.path.join(OUT, "net_status_a.png"))

    # 5 联机中（B 模式：比分条 + 名次）
    g.net.panel = ""
    g.net.mode = "B"
    g.net.my_id = 2
    g.score = g.net.players[2]["s"]
    g.draw()
    pygame.image.save(g.screen, os.path.join(OUT, "net_bar_b.png"))

    # 6 蓝牙面板
    g.net.panel = "wait"
    g.net.transport, g.net.role = "bt", "host"
    g.net.btl = types.SimpleNamespace(addr="93:37:8C:71:88:00")
    g.draw()
    pygame.image.save(g.screen, os.path.join(OUT, "net_wait_bt.png"))

    print("saved 6 shots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
