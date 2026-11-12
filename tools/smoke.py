# -*- coding: utf-8 -*-
"""无界面冒烟测试：走若干步检查数据一致性，并验证死局 → 结算 → 记录落盘。"""
import os
import random
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ["DM3_DATA_DIR"] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".testdata")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import zipfile                    # noqa: E402

import pygame                     # noqa: E402
from winicons import enable_dpi_awareness  # noqa: E402

enable_dpi_awareness()
import app                        # noqa: E402
from app import Game              # noqa: E402
from winicons import load_pieces  # noqa: E402

STEP = 1 / 60.0


def advance(game, seconds):
    end = game.t + seconds
    while game.t < end:
        game.update(STEP)


def check(game, tag):
    for r in range(game.rows):
        for c in range(game.cols):
            t = game.tiles[r][c]
            assert t is not None, f"{tag}: 格子 ({r},{c}) 为空"
            assert t.type == game.board.grid[r][c], f"{tag}: ({r},{c}) 显示与数据不一致"
            assert t.r == r and t.c == c, f"{tag}: ({r},{c}) 索引错位"


def check_records_ui(game):
    """记录浮层：Tab / 点右下角分数都能开，再点一次能收。"""
    game.rec_until = 0.0
    game.toggle_records()
    assert game.rec_until > game.t, "Tab 没唤出记录浮层"
    game.draw()
    game.toggle_records()
    assert game.rec_until == 0.0, "再按一次没有收起"
    assert game.score_hit((game.W - 30, game.H - 20)), "右下角分数没命中"
    assert not game.score_hit((game.W // 2, game.H // 2)), "分数热区太大"
    print("OK  记录浮层可开可收 · 右下角分数可点")


def check_doc_dblclick(game):
    """办公区双击 → 弹系统对话框。这里把 open_file 换成桩，避免真弹窗卡住测试。"""
    fired = []
    orig = game.open_file
    game.open_file = lambda hwnd=None: (fired.append(1), True)[1]
    try:
        y = game.H // 2
        p = ((game.doc_zone // 2, y) if game.doc_side == "left"
             else (game.W - game.doc_zone // 2, y))
        icon_p = (game.bx + 4, game.by + 4)
        assert game.doc_zone_hit(p), "办公区命中检测失败"
        assert not game.doc_zone_hit(icon_p), "图标区被误判成办公区"
        assert game.handle_doc_click(icon_p) is False, "图标区点击被办公区吃掉了"

        assert game.handle_doc_click(p) is True
        assert not fired, "单击不该弹对话框"
        game.t += 0.2
        assert game.handle_doc_click(p) is True
        assert len(fired) == 1, "双击没有触发打开对话框"

        game.t += 1.0
        game.handle_doc_click(p)
        game.t += 1.0
        game.handle_doc_click(p)
        assert len(fired) == 1, "两次慢点击被误判成双击"
        print("OK  办公区双击调起文件对话框（单击只给提示，慢点两下不算）")
    finally:
        game.open_file = orig


def check_exit_save(game):
    """ESC 退出时也要把分数记下来，不用等死局。"""
    game.new_game()
    advance(game, 0.9)
    for _ in range(4):
        if not game.busy:
            mv = game.board.pick_hint()
            if mv:
                game.try_swap(*mv)
        advance(game, 0.5)
    assert game.score > 0, "走了几步应该有分"
    n0 = len(game.records)
    game.exit_save()
    assert game.committed, "退出没把本局标记为已记录"
    assert any(r["s"] == game.score for r in game.records), "ESC 退出的分数没进榜"
    assert len(game.records) == min(10, n0 + 1)
    game.exit_save()                       # 再关一次不该重复记
    assert len(game.records) == min(10, n0 + 1), "退出记分重复写入"

    game.new_game()
    advance(game, 0.9)
    n1 = len(game.records)
    game.score = 0
    game.exit_save()
    assert len(game.records) == n1, "0 分不该进榜"
    print(f"OK  ESC 退出记分（{n0 + 1} 条记录；0 分不记）")


def check_window_top5():
    """左侧窗口布局 + 常驻前五。"""
    wx, wy, ww, wh = app.window_rect()
    sw, sh = app.screen_size()
    print(f"  窗口 {ww}x{wh} @({wx},{wy}) · 屏幕 {sw}x{sh}")
    assert ww < sw, "窗口不能占满整屏，右边得留给真桌面"
    assert wx + ww < sw, "窗口右边缘必须在屏幕以内"

    pygame.display.set_mode((ww, wh))
    scr = pygame.display.get_surface()
    game = Game(scr, load_pieces(want=10, min_types=6), seed=77)
    advance(game, 0.8)
    assert game.bx + game.bw <= game.W, "棋盘出窗口"
    assert game.by + game.rows * game.cell_h <= game.desk_h, "棋盘超出窗口高度"
    print(f"OK  窗口内 {game.cols}x{game.rows} 格 · 右侧余量 {game.W - int(game.bx + game.bw)}px")

    # 图标必须铺到右边去（不再给竖条让位）
    assert game.W - (game.bx + game.bw) < game.cell_w, "右边还留着一条没用的空白"

    # 常驻前五：横在底部，不能压图标、不能出窗口
    game.records = [{"s": 900 - i * 100, "m": i, "d": f"11-0{i + 1} 09:00"} for i in range(6)]
    game.score = 480
    game.draw()
    box = game._top5_rect()
    assert box.right <= game.W and box.bottom <= game.desk_h, "常驻榜出窗口"
    grid = pygame.Rect(int(game.bx), int(game.by), game.bw, game.rows * game.cell_h)
    assert not box.colliderect(grid), "常驻榜压到图标区"
    assert box.top >= grid.bottom, "常驻榜没落在图标下面"
    assert box.w >= 600, "底部这条太窄，5 条记录排不开"
    print(f"OK  底部常驻榜 {box.w}x{box.h} @({box.x},{box.y}) · 图标区底边 {grid.bottom}")


def check_manual_shuffle(game):
    """玩家自己点「重新排列」：棋盘变了、次数减一、不会马上出现三连。"""
    from engine import find_matches, has_move

    game.new_game()
    advance(game, 1.4)
    assert game.MANUAL == 3 and game.manual == 3, "开局重排次数不对"

    btn = game.manual_rect()
    box = game._top5_rect()
    assert box.contains(btn), "重排按钮跑出底条"
    assert btn.right <= box.right - 104, "重排按钮压到「本局得分」"
    grid = pygame.Rect(int(game.bx), int(game.by), game.bw, game.rows * game.cell_h)
    assert not btn.colliderect(grid), "重排按钮压到图标"
    assert game.manual_hit(btn.center), "按钮点不到"

    before = [row[:] for row in game.board.grid]
    moves_before = game.moves
    assert game.do_manual_shuffle(), "重排没执行"
    advance(game, 1.2)
    assert game.manual == game.MANUAL - 1, "次数没扣"
    assert game.board.grid != before, "重排后棋盘没变"
    assert not find_matches(game.board.grid), "重排后立刻出现三连"
    assert has_move(game.board.grid), "重排后没有可走的步"
    assert game.moves == moves_before, "重排不该算步数"
    check(game, "重排后")
    print(f"OK  手动重排：{game.MANUAL} → {game.manual} 次，棋盘已换、无三连、仍有步可走")

    # 次数用完：点得下去但会被拒绝（会弹一句「已用完」），棋盘不再变
    game.manual = 0
    snap = [row[:] for row in game.board.grid]
    assert game.manual_hit(btn.center), "次数用完按钮应该还能点（给提示）"
    assert not game.do_manual_shuffle()
    assert game.board.grid == snap, "次数用完还能重排"
    assert "已用完" in (game.toast or {}).get("text", ""), "没提示次数用完"
    print("OK  次数用完时点得动但不会重排，并给出提示")

    game.new_game()
    assert game.manual == game.MANUAL, "重开后次数没复位"
    print("OK  重开后重排次数复位")


def main():
    # 每次从干净的存档开始，否则上一轮的记录会让本局进不了前 10
    score = os.path.join(os.environ["DM3_DATA_DIR"], "score.json")
    if os.path.exists(score):
        os.remove(score)
    pygame.init()
    pygame.display.set_mode(app.window_rect()[2:])
    icons = load_pieces(want=10, min_types=6)
    print("棋子:", [n for n, _ in icons])
    assert len(icons) >= 6, "棋子种类不足 6 种"
    game = Game(pygame.display.get_surface(), icons, seed=5)
    assert game.ntypes >= 6
    advance(game, 1.5)
    check(game, "开局")

    game.reshuffles = 999     # 这轮只验数据一致性，别让死局提前结束
    played = 0
    max_combo = 0
    for _ in range(4000):
        if played >= 60:
            break
        max_combo = max(max_combo, game.combo)
        if not game.busy and game.state == "play":
            mv = game.board.pick_hint()
            if mv:
                game.try_swap(*mv)
                played += 1
        advance(game, STEP)
        if played % 10 == 0:
            check(game, f"第{played}步")

    check(game, "走子后")
    print(f"OK  走了 {played} 步  得分 {game.score}  最高连击 {max_combo}  棋子数 {game.ntypes}")

    # ---- 死局流程：先消耗重排次数，再死一次就该结束 ----
    before = len(game.records)
    game.reshuffles = 1
    game.busy = False
    game._no_moves()
    assert game.state == "play", "还有重排次数时不该结束"
    assert game.reshuffles == 0
    advance(game, 1.0)

    game.busy = False
    game._no_moves()
    advance(game, 1.0)
    assert game.state == "over", "没有重排次数且无可消除组合时应结束"
    assert len(game.records) == min(10, before + 1), "成绩没有写入记录"
    assert game.records[0]["s"] >= game.score or game.score in [r["s"] for r in game.records]
    assert game.rank >= 1, "结算名次异常"
    print(f"OK  结算：得分 {game.score}  排名 {game.rank}  记录 {len(game.records)} 条")

    # 弹窗能画出来、按钮有点击区域
    game.draw()
    assert {"again", "quit"} <= set(game.btn), "弹窗按钮缺失"

    # ---- 再来一局 ----
    game.new_game()
    assert game.state == "play" and game.score == 0 and game.moves == 0
    assert game.reshuffles == game.RESHUFFLES
    advance(game, 1.5)
    check(game, "重开")
    print("OK  重开正常")

    check_records_ui(game)
    check_manual_shuffle(game)
    check_exit_save(game)
    check_window_top5()

    # 没有文档区以后：整块窗口只由壁纸 + 图标 + 面板组成
    game.new_game()
    advance(game, 0.9)
    assert not hasattr(game, "doc_layer"), "文档层还在"
    print("OK  窗口里只有图标和记录面板")

    pygame.quit()


if __name__ == "__main__":
    main()
