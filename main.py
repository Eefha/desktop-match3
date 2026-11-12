# -*- coding: utf-8 -*-
"""桌面消消乐 · 启动入口

用法：
    直接双击          正常开始游戏
    加 --selftest 参数  无窗口自检，结果写到同目录 selftest.log（用于打包后验证）
"""
import os
import sys
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")   # 别在控制台里打广告

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

from app import run                 # noqa: E402
from winicons import load_pieces   # noqa: E402


def log_path():
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ROOT
    return os.path.join(base, "selftest.log")


def selftest():
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["DM3_DATA_DIR"] = os.path.join(ROOT, ".testdata")
    # 每次自检都从空存档开始，否则攒了一堆记录后本局进不了前 10、名次断言会挂
    fresh = os.path.join(os.environ["DM3_DATA_DIR"], "score.json")
    if os.path.exists(fresh):
        os.remove(fresh)
    lines = []
    try:
        import pygame
        from app import Game

        pygame.init()
        pygame.display.set_mode((1280, 720))
        icons = load_pieces(want=10, min_types=6)
        lines.append(f"棋子 {len(icons)} 种")   # 不写图标名字：那是使用者桌面上的文件
        assert len(icons) >= 6, "可用图标不足 6 种"
        game = Game(pygame.display.get_surface(), icons, seed=7)
        step = 1 / 60.0
        played = 0
        for _ in range(3000):
            if played >= 30:
                break
            if not game.busy and game.state == "play":
                mv = game.board.pick_hint()
                if mv:
                    game.try_swap(*mv)
                    played += 1
            game.update(step)
            if played % 10 == 0:
                for r in range(game.rows):
                    for c in range(game.cols):
                        t = game.tiles[r][c]
                        assert t is not None and t.type == game.board.grid[r][c]
        lines.append(f"对局 OK 走了 {played} 步，得分 {game.score}")

        # 死局 → 结算 → 记录
        game.reshuffles = 0
        game.busy = False
        game._no_moves()
        for _ in range(120):
            game.update(step)
        assert game.state == "over", "死局没有进入结算"
        assert game.rank >= 1 and len(game.records) >= 1
        game.draw()
        lines.append(f"结算 OK 排名 {game.rank}，历史记录 {len(game.records)} 条")

        game.new_game()
        for _ in range(90):
            game.update(step)
        assert game.state == "play" and game.score == 0
        lines.append("重开 OK")

        # 记录浮层：能开能收
        game.rec_until = 0.0
        game.toggle_records()
        assert game.rec_until > game.t
        game.toggle_records()
        assert game.rec_until == 0.0
        lines.append("记录浮层可开可收")

        # ESC 退出记分：不用打完一局
        game.new_game()
        for _ in range(60):
            game.update(step)
        for _ in range(4):
            if not game.busy and game.state == "play":
                mv = game.board.pick_hint()
                if mv:
                    game.try_swap(*mv)
            for _ in range(30):
                game.update(step)
        n0 = len(game.records)
        game.exit_save()
        assert game.committed, "退出没有标记已记录"
        assert any(r["s"] == game.score for r in game.records), "退出分数没进榜"
        assert len(game.records) == min(10, n0 + 1)
        lines.append(f"ESC 退出记分 OK（{game.score} 分，记录 {len(game.records)} 条）")

        # 左侧窗口：只占屏幕左边一条，右边留给真桌面照常操作
        from app import screen_size, window_rect

        wx, wy, ww, wh = window_rect()
        sw, sh = screen_size()
        assert 0 < ww < sw, "窗口宽度异常"
        assert wx + ww < sw, "窗口右边缘出屏"
        pygame.display.set_mode((ww, wh))
        screen = pygame.display.get_surface()
        part = Game(screen, icons, seed=13)
        for _ in range(60):
            part.update(step)
        part.draw()
        assert part.bx + part.bw <= part.W, "棋盘出窗口"
        rest = sw - (wx + ww)
        lines.append(f"左侧窗口 OK {ww}x{wh} · 右边留 {rest}px 真桌面 · "
                     f"{part.cols}x{part.rows} 格")

        # 常驻前五
        part.records = [{"s": 900 - i * 100, "m": i, "d": f"11-0{i + 1} 09:00"} for i in range(6)]
        part.draw()
        box = part._top5_rect()
        assert box.right <= part.W and box.bottom <= part.desk_h
        lines.append(f"常驻前五 OK {box.w}x{box.h} @({box.x},{box.y})")

        # 手动重排按钮：位置、次数、洗完棋盘真的变了
        part.new_game()
        for _ in range(90):
            part.update(step)
        btn = part.manual_rect()
        assert box.contains(btn), "重排按钮跑出底条"
        assert not btn.colliderect(
            pygame.Rect(int(part.bx), int(part.by), part.bw, part.rows * part.cell_h))

        # 「设置」按钮：在底条里、不压棋盘、不和另外两个按钮 / 得分块打架
        sbtn = part.settings_rect()
        assert box.contains(sbtn), "设置按钮跑出底条"
        assert not sbtn.colliderect(btn), "设置按钮压住重排按钮"
        assert not sbtn.colliderect(pygame.Rect(box.right - 104, box.y, 104, box.h))
        assert not sbtn.colliderect(
            pygame.Rect(int(part.bx), int(part.by), part.bw, part.rows * part.cell_h))
        assert part.settings_hit(sbtn.center) and not part.settings_hit(btn.center)
        lines.append(f"设置按钮 OK {sbtn.w}x{sbtn.h} @({sbtn.x},{sbtn.y})")
        assert part.manual == part.MANUAL == 3
        before = [row[:] for row in part.board.grid]
        assert part.do_manual_shuffle()
        for _ in range(72):
            part.update(step)
        assert part.manual == 2 and part.board.grid != before
        lines.append(f"手动重排 OK 按钮 {btn.w}x{btn.h} @({btn.x},{btn.y}) · "
                     f"剩 {part.manual}/{part.MANUAL} 次 · 棋盘已换")

        # 联机：自己连自己（真实 TCP 回环），A / B 两种玩法各走一遍
        import netcore

        guest = Game(screen, load_pieces(want=6, min_types=6), seed=19)

        def wait(cond, secs, what):
            end = time.time() + secs
            while time.time() < end:
                part.update(step)
                guest.update(step)
                time.sleep(step)
                if cond():
                    return True
            raise AssertionError("联机超时：" + what)

        for mode in ("A", "B"):
            assert part.net.create_room(mode), part.net.err
            port = netcore.PORT_BASE + int(part.net.code)
            guest.net.join_room({"ip": "127.0.0.1", "port": port,
                                 "code": part.net.code, "mode": mode})
            wait(lambda: guest.net.active, 8, "客户端进房")
            assert guest.ntypes == part.ntypes, "图标没从房主那边接过来"
            wait(lambda: part.net.running and guest.net.running, 9, "开局")
            assert guest.board.grid == part.board.grid, "两边棋盘不一致"
            wait(lambda: not guest.busy, 4, "客机可操作")
            mv = guest.board.pick_hint()
            if mv:
                guest.try_swap(*mv)
            pid = guest.net.my_id
            if mode == "A":
                # 竞速：客机自己算分，只要求分能上报到房主
                wait(lambda: part.net.players.get(pid, {}).get("s", 0) > 0, 5,
                     "客机分数上报")
                assert part.net.players[pid]["s"] == guest.score, "分数对不上"
            else:
                # 共享棋盘：等一会儿，等客机收下房主那份结算结果
                wait(lambda: not guest.net_locked
                     and part.board.grid == guest.board.grid, 6, "共享棋盘同步")
                assert part.net.players.get(pid, {}).get("s", 0) > 0, "客机没得分"
            lines.append(f"联机 {mode} OK 房间 {part.net.code} · "
                         f"{len(part.net.players)} 人 · 客机 {guest.score} 分已同步")
            part.net.close()
            guest.net.close()
            part.state = "play"
            part.busy = False
            part.net_over = None

        # 设置子窗口：选择 → 结果 → 真正开局（窗口本身要真机才能开，这里只走逻辑）
        from settings_win import SettingsWin

        sw = SettingsWin()
        sw.mode, sw.role, sw.transport = "B", "join", "bt"
        sw.btaddr = "93378c718800"
        sw._submit()
        cfg = sw.take_result()
        assert cfg and cfg["mode"] == "B" and cfg["bt"] == "93:37:8C:71:88:00", cfg
        sw.mode, sw.role, sw.transport, sw.addr = "A", "join", "lan", "10.0.2.15"
        sw._submit()
        assert sw.take_result() is None and sw.err, "半截地址没拦住"
        sw.addr = "10.0.2.15:48213"
        sw._submit()
        assert sw.take_result()["room"]["port"] == 48213
        lines.append("设置窗口 OK 单机/A/B · 蓝牙地址 · 地址校验")

        part.apply_setup({"mode": "A", "role": "host", "transport": "lan",
                          "nick": "测试员"})
        assert part.nick == "测试员" and part.net.active and part.net.panel == "wait"
        assert part.net.code.isdigit() and part.net.players[0]["n"] == "测试员"
        lines.append(f"设置生效 OK 建房 {part.net.code} · 昵称「{part.nick}」")
        part.net.close()
        part.apply_setup({"mode": "solo", "role": "host", "transport": "lan",
                          "nick": "测试员"})
        assert not part.net.active and part.state == "play"
        lines.append("设置生效 OK 切回单机")
        pygame.quit()
    except Exception as exc:  # noqa: BLE001
        import traceback
        lines.append("FAIL " + repr(exc))
        lines.append(traceback.format_exc())

    with open(log_path(), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def arg_value(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    icons = load_pieces(want=10, min_types=6)
    if not icons:
        sys.exit("没有找到任何可用图标")
    host = arg_value("--host")          # --host A / --host B：直接开房
    join = arg_value("--join")          # --join 10.0.2.15:48213：直接进房
    if host and host.upper() not in ("A", "B"):
        host = "A"
    run(icons, autohost=(host.upper() if host else None),
        autojoin=join, quiet=("--quiet" in sys.argv))


if __name__ == "__main__":
    main()
