# -*- coding: utf-8 -*-
"""离屏渲染效果图，用于验收"看起来像不像桌面"。"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ["DM3_DATA_DIR"] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".testdata")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pygame                     # noqa: E402
from winicons import enable_dpi_awareness  # noqa: E402

enable_dpi_awareness()
from app import Game, wallpaper_crop, window_rect   # noqa: E402
from winicons import load_pieces                     # noqa: E402

WIN_W, WIN_H = window_rect()[2:]


def new_game(w=None, h=None, seed=11):
    """默认就按正式运行时的窗口尺寸出图（屏幕左侧那一条）。"""
    w, h = w or WIN_W, h or WIN_H
    pygame.init()
    pygame.display.set_mode((w, h))
    screen = pygame.display.get_surface()
    icons = load_pieces(10, min_types=6)
    print(f"{w}x{h} 棋子 {len(icons)} 种:", [n for n, _ in icons])
    game = Game(screen, icons, seed=seed, bg=wallpaper_crop(0, 0, w, h))
    print(f"  图标区 x = {int(game.bx)} · {game.cols}x{game.rows} 格 · "
          f"右侧余量 {game.W - int(game.bx + game.bw)}px")
    return screen, game


def advance(game, seconds, step=1 / 60.0):
    end = game.t + seconds
    while game.t < end:
        game.update(step)


DEMO_RECORDS = [
    {"s": 23480, "m": 68, "d": "11-09 21:14"},
    {"s": 18240, "m": 55, "d": "11-10 09:42"},
    {"s": 15120, "m": 47, "d": "11-08 15:22"},
    {"s": 12760, "m": 43, "d": "11-11 19:00"},
    {"s": 11050, "m": 39, "d": "11-07 10:08"},
]


def shot_static(path, w=None, h=None):
    screen, game = new_game(w, h)
    advance(game, 1.4)
    game.records = list(DEMO_RECORDS)
    game.stats["games"] = 6
    game.score, game.moves = 2480, 15
    game.sel = (2, 3)
    game.draw()
    pygame.image.save(screen, path)
    print("saved", path, f"{game.cols}x{game.rows} 格子 {game.cell_w}x{game.cell_h}")


def shot_action(path, w=None, h=None):
    screen, game = new_game(w, h, seed=23)
    advance(game, 1.4)
    game.score, game.moves, game.combo = 3960, 21, 3
    cells = {(3, 3), (3, 4), (3, 5)}
    for cell in cells:
        game.tiles[cell[0]][cell[1]].type = 0
    game._start_clear(cells)
    advance(game, 0.12)
    game.draw()
    pygame.image.save(screen, path)
    print("saved", path)


def shot_over(path, w=None, h=None):
    screen, game = new_game(w, h, seed=31)
    advance(game, 1.4)
    game.records = [
        {"s": 23480, "m": 68, "d": "11-09 21:14"},
        {"s": 18240, "m": 55, "d": "11-10 09:42"},
        {"s": 15120, "m": 47, "d": "11-08 15:22"},
        {"s": 12760, "m": 43, "d": "11-11 19:00"},
        {"s": 11050, "m": 39, "d": "11-07 10:08"},
        {"s": 9740, "m": 35, "d": "11-06 22:31"},
        {"s": 8210, "m": 31, "d": "11-06 19:05"},
        {"s": 6630, "m": 27, "d": "11-05 14:50"},
        {"s": 4980, "m": 22, "d": "11-04 11:26"},
        {"s": 3120, "m": 16, "d": "11-04 09:12"},
    ]
    game.stats["games"] = 11
    game.score, game.moves = 12760, 43
    game.rank = game._commit_score()
    game.state = "over"
    game.draw()
    pygame.image.save(screen, path)
    print("saved", path, "记录", len(game.records))


def shot_rec(path, w=None, h=None):
    screen, game = new_game(w, h, seed=41)
    advance(game, 1.4)
    game.records = [
        {"s": 23480, "m": 68, "d": "11-09 21:14"},
        {"s": 18240, "m": 55, "d": "11-10 09:42"},
        {"s": 15120, "m": 47, "d": "11-08 15:22"},
        {"s": 12760, "m": 43, "d": "11-11 19:00"},
        {"s": 11050, "m": 39, "d": "11-07 10:08"},
        {"s": 9740, "m": 35, "d": "11-06 22:31"},
        {"s": 8210, "m": 31, "d": "11-06 19:05"},
        {"s": 6630, "m": 27, "d": "11-05 14:50"},
        {"s": 4980, "m": 22, "d": "11-04 11:26"},
        {"s": 3120, "m": 16, "d": "11-04 09:12"},
    ]
    game.stats["games"] = 11
    game.score = 3920
    game.rec_until = game.t + 5
    game.draw()
    pygame.image.save(screen, path)
    print("saved", path)


if __name__ == "__main__":
    out = os.path.join(ROOT, "preview")
    os.makedirs(out, exist_ok=True)
    shot_static(os.path.join(out, "board.png"))
    shot_over(os.path.join(out, "over.png"))
    shot_action(os.path.join(out, "action.png"))
    shot_rec(os.path.join(out, "records.png"))
    pygame.quit()
