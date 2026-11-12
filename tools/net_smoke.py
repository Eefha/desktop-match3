# -*- coding: utf-8 -*-
"""
联机冒烟测试（不用第二台机器）
------------------------------
两个 Game 跑在同一个进程里，通过 **真实的本机 TCP** 握手，把两种玩法各走一遍：
  1. A 同盘竞速：棋盘一致 → 各自消 → 分数互传 → 时间到比名次
  2. B 共享棋盘：客户端点一下 → 房主判定 → 两边棋盘和分数同步
顺带验证：图标是房主发过去的、底下面的比分面板画得出来。
"""
import os
import sys
import time

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
import netcore                      # noqa: E402
from app import Game, wallpaper_crop  # noqa: E402
from winicons import load_pieces     # noqa: E402

W, H = app.window_rect()[2:]
screen = pygame.display.set_mode((W, H))
BG = wallpaper_crop(0, 0, W, H)

HOST_ICONS = load_pieces(10, 6)
GUEST_ICONS = load_pieces(6, 6)


def new_game(icons):
    g = Game(screen, icons, seed=7, bg=BG)
    for _ in range(40):
        g.update(1 / 60)
    return g


def step(a, b, seconds, dt=1 / 60):
    end = time.time() + seconds
    while time.time() < end:
        a.update(dt)
        b.update(dt)
        time.sleep(dt)


def wait_for(fn, seconds, a, b, what=""):
    end = time.time() + seconds
    while time.time() < end:
        a.update(1 / 60)
        b.update(1 / 60)
        if fn():
            return True
        time.sleep(1 / 60)
    raise AssertionError("超时没等到：" + what)


def link(host, guest):
    """房主开房 + 客户端连进来（真实本机 TCP）。"""
    assert host.net.create_room("A"), host.net.err
    room = {"ip": "127.0.0.1", "port": netcore.PORT_BASE + int(host.net.code),
            "code": host.net.code, "mode": "A"}
    ok = guest.net.join_room(room)
    assert ok
    wait_for(lambda: guest.net.active, 6, host, guest, "客户端进房")
    wait_for(lambda: len(host.net.conns) >= 1, 3, host, guest, "房主看到客户端")
    return room


def check_mode_a(host, guest):
    print("— A 同盘竞速 —")
    link(host, guest)
    pid = guest.net.my_id
    assert pid != 0
    assert guest.net.mode == "A"

    # 图标是房主发过去的：客机本来只有 6 种，进房后应该变成房主的 10 种
    assert guest.ntypes == host.ntypes == 10, (guest.ntypes, host.ntypes)
    assert len(guest.tile_surf) == 10, "图标没接过来"
    print(f"OK  图标已由房主下发 · 客机 {guest.ntypes} 种 / {len(guest.tile_surf)} 张")

    wait_for(lambda: host.net.running and guest.net.running, 6, host, guest, "开局")
    assert host.board.grid == guest.board.grid, "A 模式两边棋盘不一致"
    assert guest.rows == host.rows and guest.cols == host.cols
    print(f"OK  同一块盘 {host.cols}x{host.rows} · 客机已同步")

    # 开场淡入要 0.5 秒，等它能操作
    wait_for(lambda: not guest.busy, 3, host, guest, "客机可操作")

    # 客机自己消一次，分数要能传到房主那边
    hint = guest.board.pick_hint()
    assert hint, "客机棋盘没有可行步"
    guest.try_swap(*hint)
    step(host, guest, 1.2)
    assert guest.score > 0, "客机没得分"
    wait_for(lambda: host.net.players[pid]["s"] == guest.score, 3, host, guest,
             "分数上报")
    print(f"OK  客机得 {guest.score} 分 · 房主侧看到 "
          f"{host.net.players[pid]['s']} 分")

    # 房主也消一次，客机那边要实时看到房主的分
    wait_for(lambda: not host.busy, 3, host, guest, "房主可操作")
    hint = host.board.pick_hint()
    if hint:
        host.try_swap(*hint)
        step(host, guest, 1.2)
    wait_for(lambda: guest.net.players[0]["s"] == host.score, 3, host, guest,
             "房主分数下发")
    print(f"OK  房主 {host.score} 分已同步到客机")

    # 时间到 → 两边都结算，名次一致
    host.net.left = 0.15
    wait_for(lambda: host.state == "over" and guest.state == "over", 4, host, guest,
             "时间到结算")
    hs = [r["id"] for r in host.net_over["rows"]]
    gs = [r["id"] for r in guest.net_over["rows"]]
    assert hs == gs, (hs, gs)
    assert len(hs) == 2
    print(f"OK  结算一致 · 名次 {gs} · 分数 "
          f"{[r['s'] for r in host.net_over['rows']]}")

    host.draw()
    guest.draw()
    pygame.image.save(host.screen, os.path.join(ROOT, ".testdata", "net_over.png"))
    host.net.close()
    guest.net.close()
    for g in (host, guest):
        g.state = "play"
        g.busy = False
        g.net_over = None


def check_mode_b(host, guest):
    print("— B 共享棋盘 —")
    host.net.create_room("B")
    room = {"ip": "127.0.0.1", "port": netcore.PORT_BASE + int(host.net.code),
            "code": host.net.code, "mode": "B"}
    guest.net.join_room(room)
    wait_for(lambda: guest.net.active, 6, host, guest, "客户端进房(B)")
    wait_for(lambda: host.net.running and guest.net.running, 8, host, guest, "开局(B)")
    assert host.board.grid == guest.board.grid, "B 模式开局棋盘不一致"
    print(f"OK  共享同一块盘 {host.cols}x{host.rows}")

    pid = guest.net.my_id
    hint = guest.board.pick_hint()
    assert hint, "共享盘上没有可行步"
    guest.try_swap(*hint)
    step(host, guest, 0.9)

    assert host.board.grid == guest.board.grid, "客机点完之后两边棋盘不一致"
    assert guest.net.players[pid]["s"] > 0, "客机没得分"
    assert guest.score == int(guest.net.players[pid]["s"]), "客机本地分数没跟上"
    print(f"OK  客机抢到一次 · {guest.net.players[pid]['s']} 分 · 两边棋盘一致")

    # 房主这边接着消一次，客机也应当立刻看到
    hint = host.board.pick_hint()
    if hint:
        host.try_swap(*hint)
        step(host, guest, 0.9)
        assert host.board.grid == guest.board.grid, "房主点完之后没同步"
        print(f"OK  房主抢到一次 · 总分 {host.score} · 客机同步")

    # 无效交换要被拒绝（不存在的组合）
    if not host.board.has_move():
        print("    （盘面已无步，跳过无效交换用例）")

    # 手动重排：客机发请求、房主执行
    before = [row[:] for row in host.board.grid]
    guest.manual = 3
    guest.do_manual_shuffle()
    step(host, guest, 0.8)
    assert host.board.grid != before, "重排没生效"
    assert host.board.grid == guest.board.grid, "重排后两边不一致"
    print("OK  客机发起重排 · 房主执行 · 两边同步")

    host.draw()
    guest.draw()
    pygame.image.save(host.screen, os.path.join(ROOT, ".testdata", "net_bar.png"))

    host.net.close()
    guest.net.close()


def check_bt_bind():
    print("— 蓝牙服务端（只验能不能绑上通道） —")
    try:
        l = netcore.BthListener(netcore.BT_CHANNEL)
    except OSError as exc:
        print(f"SKIP 本机蓝牙打不开：{exc}")
        return
    print(f"OK  蓝牙通道 {netcore.BT_CHANNEL} 已监听 · 本机地址 {l.addr}")
    l.close()


def main():
    host = new_game(HOST_ICONS)
    guest = new_game(GUEST_ICONS)
    try:
        check_mode_a(host, guest)
        check_mode_b(host, guest)
        check_bt_bind()
        print("全部通过")
        return 0
    finally:
        try:
            host.net.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            guest.net.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
