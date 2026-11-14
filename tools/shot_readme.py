# -*- coding: utf-8 -*-
"""生成 README 用的效果图：**不读真实桌面**。

把 USERPROFILE / PUBLIC 临时指到一个假桌面目录（里面放一批通用文件名），
壁纸用合成渐变，这样截出来的图和本机真实文件、真实壁纸完全无关。

    python tools/shot_readme.py
"""
import os
import random
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 假桌面：装一批通用文件，靠扩展名拿到不同图标
FAKE_FILES = [
    "项目计划.docx",
    "预算表.xlsx",
    "汇报稿.pptx",
    "合同扫描.pdf",
    "待办清单.txt",
    "素材包.zip",
    "会议录像.mp4",
    "截图.png",
    "通讯录.csv",
    "草稿.psd",
]

_fake = tempfile.mkdtemp(prefix="dm3_shot_")
_desk = os.path.join(_fake, "Desktop")
_pub = os.path.join(_fake, "PublicDesk")
os.makedirs(_desk, exist_ok=True)
os.makedirs(_pub, exist_ok=True)
for _n in FAKE_FILES:
    with open(os.path.join(_desk, _n), "wb") as _f:
        _f.write(b"\0" * 64)

os.environ["USERPROFILE"] = _fake
os.environ["PUBLIC"] = os.path.join(_fake, "Public")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

sys.path.insert(0, os.path.join(ROOT, "src"))

import pygame                                        # noqa: E402
from winicons import enable_dpi_awareness, load_pieces, desktop_paths  # noqa: E402

enable_dpi_awareness()
from app import Game, window_rect                    # noqa: E402


def fake_wallpaper(w, h):
    """合成一张柔和的斜向渐变当壁纸，避免泄露真实壁纸。"""
    top, bot = (36, 42, 66), (92, 118, 158)
    surf = pygame.Surface((w, h))
    for y in range(h):
        k = y / max(1, h - 1)
        # 加一点横向偏移，做成斜向渐变
        r = int(top[0] + (bot[0] - top[0]) * k)
        g = int(top[1] + (bot[1] - top[1]) * k)
        b = int(top[2] + (bot[2] - top[2]) * k)
        pygame.draw.line(surf, (r, g, b), (0, y), (w, y))
    glow = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.ellipse(glow, (255, 214, 160, 46),
                        pygame.Rect(int(w * 0.42), int(-h * 0.35), int(w * 0.95), int(h * 1.1)))
    surf.blit(glow, (0, 0))
    return surf


def main():
    w, h = window_rect()[2:]
    pygame.init()
    pygame.display.set_mode((w, h))
    screen = pygame.display.get_surface()

    print("假桌面条目:", [os.path.basename(p) for p in desktop_paths()])
    icons = load_pieces(10, min_types=6, rng=random.Random(5))
    print(f"{w}x{h} 棋子 {len(icons)} 种:", [n for n, _ in icons])

    game = Game(screen, icons, seed=11, bg=fake_wallpaper(w, h))
    end = game.t + 1.4
    while game.t < end:
        game.update(1 / 60.0)
    game.records = [
        {"s": 23480, "m": 68, "d": "11-09 21:14"},
        {"s": 18240, "m": 55, "d": "11-10 09:42"},
        {"s": 15120, "m": 47, "d": "11-08 15:22"},
        {"s": 12760, "m": 43, "d": "11-11 19:00"},
        {"s": 11050, "m": 39, "d": "11-07 10:08"},
    ]
    game.stats["games"] = 6
    game.score, game.moves = 2480, 15
    game.sel = (2, 3)
    game.draw()

    out = os.path.join(ROOT, "docs")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "screenshot.png")
    pygame.image.save(screen, path)
    print("saved", path, os.path.getsize(path) // 1024, "KB")


if __name__ == "__main__":
    try:
        main()
    finally:
        pygame.quit()
        shutil.rmtree(_fake, ignore_errors=True)
