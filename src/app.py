# -*- coding: utf-8 -*-
"""
渲染 + 游戏循环（隐蔽版）
--------------------------
目标：看起来就是你的桌面，只是图标多了一些。
- 只占屏幕左侧一条，右边是**真的**桌面：照常点文件、照常开软件
- 背景从整屏壁纸里裁出窗口那一块，和右边真桌面严丝合缝
- 棋盘格子直接套用桌面图标的真实间距，从窗口左上角开始排
- 每个棋子下面带文件名，和真桌面图标一样
- 没有标题、没有说明文字、没有玻璃底板，只有角落一个小分数
"""
from __future__ import annotations

import json
import math
import os
import random
import time

import pygame

from engine import Board, find_matches, score_of
from netplay import MODE_A, MODE_B, Netplay
from settings_win import SettingsWin
from winicons import icon_metrics, square_fit, wallpaper_image

# 窗口只占屏幕左边这一条；剩下的右侧留给真实桌面，鼠标点过去就是真图标
WIN_RATIO = float(os.environ.get("DM3_WIN_RATIO") or 0.58)

LABEL_COLOR = (245, 248, 252)
SCORE_COLOR = (226, 232, 242)
SEL_COLOR = (126, 186, 255)

FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msyh.ttc"


def ease_out_cubic(p):
    return 1 - (1 - p) ** 3


def ease_out_back(p, s=1.6):
    p -= 1.0
    return p * p * ((s + 1) * p + s) + 1


def ease_in_cubic(p):
    return p ** 3


class Tile:
    __slots__ = ("type", "r", "c", "vr", "vc", "scale", "alpha", "tw", "dead")

    def __init__(self, t, r, c, vr=None, vc=None):
        self.type = t
        self.r = r
        self.c = c
        self.vr = float(r if vr is None else vr)
        self.vc = float(c if vc is None else vc)
        self.scale = 1.0
        self.alpha = 1.0
        self.dead = False
        self.tw = {}

    def tween(self, field, target, dur, now, ease=ease_out_cubic):
        if dur <= 0:
            setattr(self, field, float(target))
            self.tw.pop(field, None)
            return
        self.tw[field] = (now, dur, float(getattr(self, field)), float(target), ease)

    def update(self, now):
        for field in list(self.tw):
            t0, dur, v0, v1, ease = self.tw[field]
            p = (now - t0) / dur
            if p >= 1.0:
                setattr(self, field, v1)
                del self.tw[field]
            else:
                setattr(self, field, v0 + (v1 - v0) * ease(max(0.0, p)))


class Game:
    MAX_COLS = 14
    MAX_ROWS = 10
    BAR_H = 52             # 窗口底部给常驻榜留的一条，横排 5 条记录
    RESHUFFLES = 3         # 每局可用的自动重排次数，用完再死局就结束
    MANUAL = 3             # 每局玩家自己按「重新排列」的次数

    def __init__(self, screen, icons, seed=None, hwnd=None, bg=None):
        self.screen = screen
        self.W, self.H = screen.get_size()
        self.icons = icons
        self.ntypes = len(icons)
        self.seed = seed
        self.rng = random.Random(seed)
        self.hwnd = hwnd
        self._bg_img = bg           # 整屏壁纸裁出来的那一块（由 run() 传进来）

        # 窗口只占左边一条，任务栏在窗口外面露着，是系统那条真的，
        # 所以这里不画假任务栏，也不需要预留桌面高度
        self.desk_h = self.H

        self.t = 0.0
        self.fonts = {}
        self._text_cache = {}
        self.busy = True
        self.timers = []
        self.particles = []
        self.popups = []
        self.combo = 0
        self.score = 0
        self.moves = 0
        self.sel = None
        self.hint = None
        self.hint_t = 0.0
        self.idle = 0.0
        self.dragging = False
        self.drag_used = False

        self.state = "play"        # play / over
        self.reshuffles = self.RESHUFFLES
        self.manual = self.MANUAL
        self.toast = None
        self.rank = 0
        self.btn = {}
        self.rec_until = 0.0
        self.committed = False     # 本局成绩是否已经写进记录（ESC 退出也算一次）

        self.stats = self._load_stats()
        self.high = self.stats.get("high", 0)
        self.records = self.stats.get("records", [])
        # 联机：房主即服务器；A 同盘竞速 / B 共享棋盘
        self.net_locked = False
        self.net_over = None
        self.net_started = False
        self.net = Netplay(self)
        # 玩法 / 联机方式都在子窗口里选（Ctrl+J 打开），不再挤在棋盘上
        self.settings = SettingsWin()
        self.nick = str(self.stats.get("nick") or "")[:8]
        self.setup = self.stats.get("setup") if isinstance(
            self.stats.get("setup"), dict) else {}
        self.net.nick = self.nick
        # 前几次启动给一行很淡的提示，之后不再出现（免得长期露馅）
        self.hints = int(self.stats.get("hints", 0))
        self.hint_until = 0.0
        self._layout()
        self._build_art()
        self.new_game()
        if self.hints < 3:
            self.hint_until = 8.5
            self.stats["hints"] = self.hints + 1

    # -------------------------------------------------------------- 布局
    def _layout(self):
        self.cell_w, self.cell_h = icon_metrics()
        self.margin = max(4, int(self.W * 0.006))
        # 底部留一条放常驻榜，上面整块都归图标 —— 右边不再留白条
        self.bar_gap = 8
        self.grid_h = self.desk_h - 6 - self.BAR_H - self.bar_gap
        self.rows = max(6, min(self.MAX_ROWS, int(self.grid_h // self.cell_h)))
        avail = self.W - self.margin * 2
        self.cols = max(5, min(self.MAX_COLS, int(avail // self.cell_w)))
        self.bw = self.cell_w * self.cols
        self.bh = self.cell_h * self.rows
        self.bx = float(self.margin)
        self.by = 6.0
        self.icon_px = int(self.cell_w * 0.62)
        self.label_size = max(10, int(self.cell_w * 0.155))

    # -------------------------------------------------------------- 联机
    def reset_net_state(self):
        self.net_locked = False
        self.net_over = None
        self.net_started = False
        self.busy = False

    def _fit_cells(self, rows, cols):
        """对方发来的棋盘尺寸可能和本机不一样，格子按窗口重新算一遍塞进去。"""
        mw, mh = icon_metrics()
        self.rows, self.cols = int(rows), int(cols)
        avail_w = self.W - self.margin * 2
        avail_h = self.desk_h - 6 - self.BAR_H - self.bar_gap
        self.cell_w = int(min(mw, max(30, avail_w // max(1, cols))))
        self.cell_h = int(min(mh, max(26, avail_h // max(1, rows))))
        self.bw = self.cell_w * self.cols
        self.bh = self.cell_h * self.rows
        self.icon_px = int(self.cell_w * 0.62)
        self.label_size = max(9, int(self.cell_w * 0.155))

    def adopt_net(self, rows, cols, ntypes, names, sheet_b64):
        """
        进房第一件事：把房主的桌面图标接过来。
        图标是他那边拼好的一整张 PNG（base64），切成一个个就是了 —— 两边看到的一模一样。
        """
        imgs = []
        if sheet_b64:
            try:
                import base64
                import io

                from PIL import Image

                sheet = Image.open(io.BytesIO(base64.b64decode(sheet_b64))).convert("RGBA")
                side = sheet.size[1]
                n = max(1, sheet.size[0] // side)
                for i in range(n):
                    imgs.append(sheet.crop((i * side, 0, (i + 1) * side, side)))
            except Exception:  # noqa: BLE001
                imgs = []
        if not imgs:
            return
        pairs = []
        for i, im in enumerate(imgs):
            nm = names[i] if i < len(names) else f"图标{i + 1}"
            pairs.append((nm, im))
        self.icons = pairs
        self.ntypes = len(pairs)
        if rows > 0 and cols > 0:
            self._fit_cells(rows, cols)
        self.board.ntypes = self.ntypes
        self._build_art()
        self._net_rebuild_tiles(instant=True)

    def start_net_race(self, grid, left):
        """A 模式：拿到房主的棋盘，各玩各的，计时由房主统一报。"""
        self.board = Board.from_grid(grid, ntypes=self.ntypes,
                                     seed=random.randrange(1 << 30))
        self.board.ntypes = self.ntypes
        self._fit_cells(self.board.rows, self.board.cols)
        self._build_art()

        self.score = 0
        self.moves = 0
        self.combo = 0
        self.sel = None
        self.hint = None
        self.particles.clear()
        self.popups.clear()
        self.timers.clear()
        self.toast = None
        self.state = "play"
        self.busy = True
        self.committed = True        # 联机分数不进个人榜，免得把历史记录冲花
        self.reshuffles = self.RESHUFFLES
        self.manual = self.MANUAL
        self.net_locked = False
        self.net_over = None
        self.net_started = True
        self._net_rebuild_tiles(instant=False)
        self.schedule(0.5, self._end_busy)

    def net_sync(self, grid, cleared):
        """B 模式：房主算完发过来，直接照着落子。"""
        if not grid:
            return
        self.board.grid = [list(r) for r in grid]
        self.board.ntypes = self.ntypes
        self.net_apply(cleared)

    def net_apply(self, cleared, gain=0, by=None):
        """棋盘已经是最终局面了，这里只负责让它看起来像刚消了一把。"""
        cells = []
        for c in (cleared or []):
            try:
                r, cc = int(c[0]), int(c[1])
            except Exception:  # noqa: BLE001
                continue
            if 0 <= r < self.rows and 0 <= cc < self.cols:
                cells.append((r, cc))
        sx = sy = 0.0
        for (r, c) in cells:
            t = self.tiles[r][c]
            if t is not None:
                t.dead = True
                t.tween("scale", 0.18, 0.16, self.t, ease_in_cubic)
                t.tween("alpha", 0.0, 0.16, self.t, ease_in_cubic)
                px, py = self.cell_center(r, c)
                self._burst(px, py, t.type)
                sx += px
                sy += py
        if gain and cells:
            self.popups.append({"text": f"+{gain}", "x": sx / len(cells),
                                "y": sy / len(cells), "t": 0.0, "dur": 0.8,
                                "color": (236, 242, 250)})
        self.schedule(0.13, self._net_rebuild_tiles)

    def net_rebuild_now(self):
        self._net_rebuild_tiles(instant=True)

    def _net_rebuild_tiles(self, instant=False):
        """按 board.grid 把画面拨正；变了的格子淡入，没变的保持不动。"""
        for r in range(self.rows):
            for c in range(self.cols):
                if r >= len(self.board.grid) or c >= len(self.board.grid[r]):
                    continue
                ty = self.board.grid[r][c]
                t = self.tiles[r][c]
                if t is None or t.dead or t.type != ty:
                    nt = Tile(ty, r, c)
                    if instant:
                        self.tiles[r][c] = nt
                    else:
                        nt.alpha = 0.0
                        nt.tween("alpha", 1.0, 0.16, self.t + (r + c) * 0.004)
                        self.tiles[r][c] = nt
                else:
                    t.r, t.c = r, c
                    t.vr, t.vc = float(r), float(c)

    def score_hit(self, pos) -> bool:
        """底部常驻榜最右边那块「本局」，点它等于按 Tab。"""
        box = self._top5_rect()
        return pygame.Rect(box.right - 104, box.y, 104, box.h).collidepoint(pos)

    def manual_rect(self):
        """底部条上的「重新排列」按钮：卡住了自己按一下。"""
        box = self._top5_rect()
        w, h = 108, 30
        return pygame.Rect(box.right - 104 - 10 - w, box.centery - h // 2, w, h)

    def manual_hit(self, pos) -> bool:
        return self.state == "play" and self.manual_rect().collidepoint(pos)

    def do_manual_shuffle(self) -> bool:
        """玩家自己点重排：不扣分，只消耗每局的次数。"""
        if self.state != "play" or self.busy:
            return False
        if self.net and self.net.active and self.net.mode == MODE_B:
            # 共享棋盘只有房主说了算，其他人发个请求过去
            if self.manual <= 0:
                self._toast("重新排列已用完（每局 3 次）", 2.2)
                return False
            return self.net.request_shuffle()
        if self.manual <= 0:
            self._toast("重新排列已用完（每局 3 次）", 2.2)
            return False
        self.manual -= 1
        left = f"剩 {self.manual} 次" if self.manual else "这是最后一次"
        self._toast(f"已重新排列 · {left}", 2.2)
        self.sel = None
        self.hint = None
        self._reshuffle()
        return True

    def minimize(self):
        """把窗口最小化，露出真正的桌面（任务栏上点一下就能回来）。"""
        if not self.hwnd:
            return False
        try:
            import ctypes

            ctypes.windll.user32.ShowWindow(ctypes.c_void_p(self.hwnd), 6)   # SW_MINIMIZE
            return True
        except Exception:  # noqa: BLE001
            return False

    def exit_save(self):
        """
        退出（ESC / 关窗口）时把成绩记下来 —— 不用打完，玩多少算多少。
        0 分不记，免得榜里塞一堆 0。
        """
        try:
            if self.net and self.net.active:
                pass                      # 联机这局不算进个人榜，退出时顺手关房间
            elif not self.committed and self.state == "play" and self.score > 0:
                self.rank = self._commit_score()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.net and self.net.active:
                self.net.leave_game()     # 房主：解散并通知；成员：说一声 bye 再走
        except Exception:  # noqa: BLE001
            pass
        self._save_stats()

    def toggle_records(self):
        """点一次出现，再点一次收起 —— 和按 Tab 是同一个行为。"""
        if self.t < self.rec_until:
            self.rec_until = 0.0
        else:
            self.rec_until = self.t + 8.0

    # -------------------------------------------------------------- 素材
    def _build_art(self):
        if self._bg_img is not None:
            # run() 用整屏壁纸裁出来的那块：和右边真桌面的壁纸是完全同一张图
            self.bg = self._bg_img.convert()
        else:
            bg = wallpaper_image(self.W, self.H)
            self.bg = pygame.image.frombytes(bg.tobytes(), bg.size, "RGB").convert()

        self.tile_surf = []
        self.avg_color = []
        self.label_surf = []

        for name, im in self.icons:
            fitted = square_fit(im, self.icon_px, fill=0.98)
            self.tile_surf.append(
                pygame.image.frombytes(fitted.tobytes(), fitted.size, "RGBA").convert_alpha())

            small = im.convert("RGB").resize((8, 8))
            data = list(small.getdata())
            n = max(1, len(data))
            self.avg_color.append(tuple(sum(p[k] for p in data) // n for k in range(3)))

            self.label_surf.append(self._make_label(name))

    def _label_font(self, name):
        """英文名用 Segoe UI（和真桌面一致，也更窄），中文名用雅黑。"""
        path = "C:/Windows/Fonts/segoeui.ttf" if name.isascii() else FONT_REG
        key = (path, self.label_size)
        if key not in self.fonts:
            try:
                self.fonts[key] = pygame.font.Font(path, self.label_size)
            except Exception:
                self.fonts[key] = self.font(self.label_size, False)
        return self.fonts[key]

    def _make_label(self, name):
        """和真桌面一样折成最多两行；放不下才加省略号。"""
        name = name.replace(" - 快捷方式", "").replace(".lnk", "").strip()
        limit = self.cell_w - 8
        f = self._label_font(name)
        lines = self._wrap_label(name, f, limit)

        lh = f.get_height() + 1
        w = max((f.size(s)[0] for s in lines), default=1) + 4
        surf = pygame.Surface((max(1, w), max(1, lh * len(lines) + 2)), pygame.SRCALPHA)
        for k, line in enumerate(lines):
            body = f.render(line, True, LABEL_COLOR)
            sh = f.render(line, True, (0, 0, 0))
            sh.set_alpha(200)
            ly = k * lh + 1
            lx = (surf.get_width() - body.get_width()) // 2
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                           (-1, -1), (1, -1), (-1, 1), (1, 1)):
                surf.blit(sh, (lx + dx, ly + dy))
            surf.blit(body, (lx, ly))
        return surf

    @staticmethod
    def _wrap_label(name, f, limit):
        """最多两行，优先在空格处断开，避免把英文单词劈成两半。"""
        lines, rest = [], name
        while rest and len(lines) < 2:
            if f.size(rest)[0] <= limit:
                lines.append(rest)
                rest = ""
                break
            cut, i = 1, 1
            while i < len(rest) and f.size(rest[:i + 1])[0] <= limit:
                i += 1
                cut = i
            # 优先在空格 / 下划线 / 连字符处断开，别把单词劈两半
            best = -1
            for sep in (" ", "_", "-"):
                k = rest.rfind(sep, max(1, int(cut * 0.45)), cut)
                best = max(best, k)
            if best > 0:
                cut = best + 1
            lines.append(rest[:cut].rstrip(" _-"))
            rest = rest[cut:].lstrip()
        if rest and lines:
            tail = lines[-1]
            while tail and f.size(tail + "…")[0] > limit:
                tail = tail[:-1]
            lines[-1] = tail + "…"
        return lines

    # -------------------------------------------------------------- 字体
    def font(self, size, bold=True):
        key = (size, bold)
        if key not in self.fonts:
            path = FONT_BOLD if bold else FONT_REG
            try:
                f = pygame.font.Font(path, size)
            except Exception:
                f = pygame.font.SysFont("microsoftyahei", size, bold=bold)
            self.fonts[key] = f
        return self.fonts[key]

    def text(self, s, size, color, bold=True):
        key = (s, size, color, bold)
        surf = self._text_cache.get(key)
        if surf is None:
            surf = self.font(size, bold).render(s, True, color)
            if len(self._text_cache) > 400:
                self._text_cache.clear()
            self._text_cache[key] = surf
        return surf

    def blit_text(self, s, size, color, pos, anchor="topleft", bold=True, shadow=140):
        surf = self.text(s, size, color, bold)
        r = surf.get_rect(**{anchor: pos})
        if shadow:
            sh = self.text(s, size, (0, 0, 0), bold)
            sh.set_alpha(shadow)
            self.screen.blit(sh, r.move(0, 1))
        self.screen.blit(surf, r)
        return r

    # -------------------------------------------------------------- 分数存档
    def _score_path(self):
        base = os.environ.get("DM3_DATA_DIR") or os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), "DesktopMatch3")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "score.json")

    def _load_stats(self):
        try:
            with open(self._score_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            data["records"] = [r for r in data.get("records", [])
                               if isinstance(r, dict) and "s" in r]
            return data
        except Exception:
            return {}

    def _save_stats(self):
        try:
            with open(self._score_path(), "w", encoding="utf-8") as f:
                json.dump({"high": self.high, "records": self.records[:10],
                           "games": self.stats.get("games", 0),
                           "hints": int(self.stats.get("hints", 0)),
                           "nick": self.nick,
                           "setup": self.setup}, f, ensure_ascii=False)
        except Exception:
            pass

    def _commit_score(self):
        """把本局成绩写进历史记录，返回名次（1 起，未进前 10 返回 0）。"""
        rec = {"s": int(self.score), "m": int(self.moves),
               "d": time.strftime("%m-%d %H:%M")}
        self.records.append(rec)
        self.records.sort(key=lambda r: (-r["s"], r["m"]))
        self.records = self.records[:10]
        self.stats["games"] = int(self.stats.get("games", 0)) + 1
        if self.score > self.high:
            self.high = int(self.score)
        self.last_rec = rec
        self.committed = True
        self._save_stats()
        for i, r in enumerate(self.records):
            if r is rec:
                return i + 1
        return 0

    # -------------------------------------------------------------- 开局
    def new_game(self):
        seed, self.seed = self.seed, None
        self.board = Board(self.rows, self.cols, self.ntypes, seed=seed)
        self.tiles = [[None] * self.cols for _ in range(self.rows)]
        for r in range(self.rows):
            for c in range(self.cols):
                self.tiles[r][c] = Tile(self.board.grid[r][c], r, c)

        self.score = 0
        self.combo = 0
        self.moves = 0
        self.particles.clear()
        self.popups.clear()
        self.sel = None
        self.hint = None
        self.timers.clear()
        self.dragging = False
        self.busy = True
        self.state = "play"
        self.committed = False
        self.reshuffles = self.RESHUFFLES
        self.manual = self.MANUAL
        self.rank = 0
        self.toast = None
        self.btn = {}
        self.rec_until = 0.0

        # 整体淡入，不做掉落动画，免得一眼看出是游戏
        for r in range(self.rows):
            for c in range(self.cols):
                t = self.tiles[r][c]
                t.alpha = 0.0
                t.tween("alpha", 1.0, 0.30, self.t + (r + c) * 0.012)
        self.schedule(0.55 + (self.rows + self.cols) * 0.012, self._intro_done)

    def _intro_done(self):
        self.busy = False
        self.schedule(0.35, self._maybe_initial_match)

    def _maybe_initial_match(self):
        m = find_matches(self.board.grid)
        if m:
            self.combo = 1
            self._start_clear(m)

    # -------------------------------------------------------------- 计时器
    def schedule(self, delay, cb):
        self.timers.append([self.t + delay, cb])

    def _run_timers(self):
        due = [x for x in self.timers if x[0] <= self.t]
        if not due:
            return
        for item in due:
            self.timers.remove(item)
        for _, cb in due:
            cb()

    # -------------------------------------------------------------- 几何
    def cell_at(self, pos):
        x, y = pos
        if x < self.bx or y < self.by:
            return None
        c = int((x - self.bx) // self.cell_w)
        r = int((y - self.by) // self.cell_h)
        if 0 <= r < self.rows and 0 <= c < self.cols:
            return r, c
        return None

    def cell_center(self, r, c):
        return (self.bx + (c + 0.5) * self.cell_w,
                self.by + (r + 0.5) * self.cell_h)

    # -------------------------------------------------------------- 交互
    def click(self, r, c):
        if self.busy or self.state != "play":
            return
        self.idle = 0.0
        self.hint = None
        if self.sel is None:
            self.sel = (r, c)
            return
        if self.sel == (r, c):
            self.sel = None
            return
        r0, c0 = self.sel
        if abs(r0 - r) + abs(c0 - c) == 1:
            self.sel = None
            self.try_swap((r0, c0), (r, c))
        else:
            self.sel = (r, c)

    def try_swap(self, a, b):
        if self.busy or self.state != "play":
            return
        if self.net and self.net.on_local_swap(a, b):
            self.sel = None              # 共享棋盘：交给房主判定，本地不动手
            return
        self.busy = True
        self.sel = None
        ta = self.tiles[a[0]][a[1]]
        tb = self.tiles[b[0]][b[1]]
        if ta is None or tb is None:
            self.busy = False
            return

        ta.tween("vr", b[0], 0.13, self.t)
        ta.tween("vc", b[1], 0.13, self.t)
        tb.tween("vr", a[0], 0.13, self.t)
        tb.tween("vc", a[1], 0.13, self.t)
        self.schedule(0.13, lambda: self._after_swap(a, b))

    def _after_swap(self, a, b):
        (r1, c1), (r2, c2) = a, b
        self.tiles[r1][c1], self.tiles[r2][c2] = self.tiles[r2][c2], self.tiles[r1][c1]
        self.tiles[r1][c1].r, self.tiles[r1][c1].c = r1, c1
        self.tiles[r2][c2].r, self.tiles[r2][c2].c = r2, c2
        self.board.swap(a, b)

        m = find_matches(self.board.grid)
        if not m:
            # 无效交换：数据和画面一起退回原位
            self.tiles[r1][c1], self.tiles[r2][c2] = self.tiles[r2][c2], self.tiles[r1][c1]
            self.board.swap(a, b)
            ta = self.tiles[r1][c1]
            tb = self.tiles[r2][c2]
            ta.r, ta.c = r1, c1
            tb.r, tb.c = r2, c2
            ta.tween("vr", r1, 0.15, self.t)
            ta.tween("vc", c1, 0.15, self.t)
            tb.tween("vr", r2, 0.15, self.t)
            tb.tween("vc", c2, 0.15, self.t)
            self.schedule(0.16, self._end_busy)
            return

        self.moves += 1
        self.combo = 1
        self._start_clear(m)

    def _end_busy(self):
        self.busy = False

    def _start_clear(self, cells):
        n = len(cells)
        gain = score_of(n, self.combo)      # 和联机版共用同一套算分，分数才对得上
        self.score += gain
        if self.score > self.high:
            self.high = self.score

        cx = cy = 0.0
        for (r, c) in cells:
            t = self.tiles[r][c]
            if t is None:
                continue
            t.dead = True
            t.tween("scale", 0.15, 0.22, self.t, ease_in_cubic)
            t.tween("alpha", 0.0, 0.22, self.t, ease_in_cubic)
            px, py = self.cell_center(r, c)
            cx += px
            cy += py
            self._burst(px, py, t.type)

        cx /= max(1, n)
        cy /= max(1, n)
        self.popups.append({"text": f"+{gain}", "x": cx, "y": cy, "t": 0.0,
                            "dur": 0.8, "color": (236, 242, 250)})

        self.schedule(0.22, lambda: self._after_clear(cells))

    def _after_clear(self, cells):
        for (r, c) in cells:
            self.tiles[r][c] = None
        self.board.clear(cells)
        moves, spawns = self.board.collapse()

        for (tr, tc, fr, fc) in moves:
            t = self.tiles[fr][fc]
            self.tiles[fr][fc] = None
            self.tiles[tr][tc] = t
            t.r, t.c = tr, tc
            t.vr, t.vc = float(fr), float(fc)
            t.tween("vr", tr, 0.24, self.t, ease_out_back)

        for (tr, tc, ttype, from_r) in spawns:
            t = Tile(ttype, tr, tc, from_r, tc)
            self.tiles[tr][tc] = t
            t.tween("vr", tr, 0.26, self.t + 0.02, ease_out_back)

        self.schedule(0.30, self._after_fall)

    def _after_fall(self):
        m = find_matches(self.board.grid)
        if m:
            self.combo += 1
            self._start_clear(m)
            return
        self.combo = 0
        self.busy = False
        self.idle = 0.0
        if not self.board.has_move():
            self._no_moves()

    # -------------------------------------------------------------- 死局 / 结束
    def _no_moves(self):
        """棋盘上再没有任何能凑成三连的交换。"""
        if self.state != "play":
            return
        if self.net and self.net.active and self.net.mode == MODE_A:
            # 竞速模式：不结束，直接洗一次，等房主那边时间到
            self.reshuffles = 1
            self._toast("没有可消除的组合 · 已重新排列", 2.4)
            self._reshuffle()
            return
        if self.reshuffles > 0:
            self.reshuffles -= 1
            self._toast(f"没有可消除的组合 · 已重新排列（剩 {self.reshuffles} 次）", 2.8)
            self._reshuffle()
            return
        self._toast("没有可消除的组合了", 1.0)
        self.schedule(0.65, self.game_over)

    def _toast(self, text, dur=2.4):
        self.toast = {"text": text, "t": 0.0, "dur": dur}

    def game_over(self):
        if self.state != "play":
            return
        self.state = "over"
        self.busy = True
        self.sel = None
        self.hint = None
        self.rank = self._commit_score()

    def hit_button(self, pos):
        for key, rect in self.btn.items():
            if rect.collidepoint(pos):
                return key
        return None

    def _reshuffle(self):
        self.busy = True
        for r in range(self.rows):
            for c in range(self.cols):
                t = self.tiles[r][c]
                if t:
                    t.tween("alpha", 0.0, 0.16, self.t)
        self.schedule(0.18, self._do_reshuffle)

    def _do_reshuffle(self):
        self.board.shuffle()
        for r in range(self.rows):
            for c in range(self.cols):
                t = self.tiles[r][c]
                if t is None:
                    self.tiles[r][c] = t = Tile(self.board.grid[r][c], r, c)
                t.type = self.board.grid[r][c]
                t.alpha = 0.0
                t.tween("alpha", 1.0, 0.22, self.t + (r + c) * 0.005)
        self.schedule(0.45, self._end_busy)

    # -------------------------------------------------------------- 特效
    def _burst(self, px, py, ttype):
        """很克制的碎屑：量少颗粒小，远看几乎注意不到。"""
        base = self.avg_color[ttype] if ttype < len(self.avg_color) else (255, 255, 255)
        col = tuple(min(255, int(c * 0.7 + 90)) for c in base)
        for _ in range(7):
            ang = self.rng.uniform(0, math.tau)
            spd = self.rng.uniform(40, 190)
            self.particles.append({
                "x": px + self.rng.uniform(-8, 8),
                "y": py + self.rng.uniform(-8, 8),
                "vx": math.cos(ang) * spd,
                "vy": math.sin(ang) * spd - 60,
                "life": 0.0,
                "max": self.rng.uniform(0.30, 0.55),
                "color": col,
                "size": self.rng.uniform(1.5, 3.0),
            })

    # -------------------------------------------------------------- 更新
    def update(self, dt):
        self.t += dt
        self._run_timers()

        for row in self.tiles:
            for t in row:
                if t:
                    t.update(self.t)

        alive = []
        for p in self.particles:
            p["life"] += dt
            if p["life"] >= p["max"]:
                continue
            p["vy"] += 640 * dt
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            alive.append(p)
        self.particles = alive

        for p in self.popups:
            p["t"] += dt
        self.popups = [p for p in self.popups if p["t"] < p["dur"]]

        if self.toast:
            self.toast["t"] += dt
            if self.toast["t"] >= self.toast["dur"]:
                self.toast = None

        if not self.busy and self.state == "play":
            self.idle += dt
            if self.idle > 10.0 and not self.hint:
                self.hint = self.board.pick_hint()
                self.hint_t = self.t
        if self.hint and self.t - self.hint_t > 2.4:
            self.hint = None

        # 联机：收消息、发分数、倒计时
        if self.net:
            self.net.tick(dt)

    # -------------------------------------------------------------- 绘制
    def draw(self):
        scr = self.screen
        scr.blit(self.bg, (0, 0))

        hover = None
        if not self.busy and self.state == "play":
            hover = self.cell_at(pygame.mouse.get_pos())
        if hover and hover != self.sel:
            self._draw_sel(hover, 0.32, fill_only=True)

        for r in range(self.rows):
            for c in range(self.cols):
                t = self.tiles[r][c]
                if t is None or t.alpha <= 0.01:
                    continue
                self._draw_tile(t)

        if self.sel and self.state == "play":
            self._draw_sel(self.sel, 1.0)

        if self.hint and self.state == "play":
            pulse = 0.35 + 0.35 * math.sin((self.t - self.hint_t) * 6)
            for cell in self.hint:
                self._draw_sel(cell, pulse, fill_only=True)

        for p in self.particles:
            k = 1.0 - p["life"] / p["max"]
            a = int(190 * k)
            rad = max(1, int(p["size"] * (0.5 + k * 0.5)))
            s = pygame.Surface((rad * 2 + 2, rad * 2 + 2), pygame.SRCALPHA)
            pygame.draw.circle(s, (*p["color"], a), (rad + 1, rad + 1), rad)
            scr.blit(s, (p["x"] - rad, p["y"] - rad))

        for p in self.popups:
            k = p["t"] / p["dur"]
            a = int(210 * (1 - k ** 2))
            surf = self.font(13, True).render(p["text"], True, p["color"])
            surf.set_alpha(a)
            r = surf.get_rect(center=(p["x"], p["y"] - 22 * k))
            scr.blit(surf, r)

        # 常驻的历史前五（底部一条，不用点，一直看得到）
        self._draw_top5()

        if self.toast:
            k = min(1.0, self.toast["t"] / 0.18)
            a = int(235 * min(1.0, (self.toast["dur"] - self.toast["t"]) / 0.5))
            surf = self.font(12, False).render(self.toast["text"], True, (236, 240, 248))
            surf.set_alpha(a)
            bg = pygame.Surface((surf.get_width() + 22, surf.get_height() + 12),
                                pygame.SRCALPHA)
            pygame.draw.rect(bg, (28, 32, 40, int(150 * k)), bg.get_rect(), border_radius=6)
            x = self.W // 2
            y = self._top5_rect().top - 22
            scr.blit(bg, (x - bg.get_width() // 2, y - bg.get_height() // 2))
            scr.blit(surf, (x - surf.get_width() // 2, y - surf.get_height() // 2))

        # 前几次启动的一行提示：淡入 → 停一会 → 淡出，之后永不出现
        if 0 < self.t < self.hint_until + 1.4 and not self.toast:
            fade = min(1.0, self.t / 0.6)
            left = self.hint_until + 1.4 - self.t
            if left < 1.4:
                fade = min(fade, max(0.0, left / 1.4))
            tip = "Ctrl+J 玩法/联机　·　Tab 看记录　·　拖动交换　·　S 重排　·　Ctrl+M 收起"
            surf = self.font(12, False).render(tip, True, (238, 242, 250))
            surf.set_alpha(int(214 * fade))
            sh = self.font(12, False).render(tip, True, (0, 0, 0))
            sh.set_alpha(int(120 * fade))
            cx = self.W / 2.0
            cy = self._top5_rect().top - 34
            scr.blit(sh, (cx - sh.get_width() / 2 + 1, cy + 1))
            scr.blit(surf, (cx - surf.get_width() / 2, cy))

        if self.state == "over":
            if self.net_over:
                self._draw_net_over()
            else:
                self._draw_over()
        elif self.t < self.rec_until:
            self._draw_records()

        # 联机面板最后画，压在所有东西上面
        if self.net:
            self.net.draw_panel()

    def _draw_tile(self, t):
        surf = self.tile_surf[t.type]
        if abs(t.scale - 1.0) > 0.01:
            s = max(0.06, t.scale)
            surf = pygame.transform.smoothscale(
                surf, (max(1, int(surf.get_width() * s)),
                       max(1, int(surf.get_height() * s))))
        if t.alpha < 0.99:
            surf = surf.copy()
            surf.fill((255, 255, 255, int(255 * t.alpha)),
                      special_flags=pygame.BLEND_RGBA_MULT)

        cx = self.bx + t.vc * self.cell_w + self.cell_w / 2.0
        top = self.by + t.vr * self.cell_h + max(3, (self.cell_h - self.icon_px) * 0.14)
        if top < -self.cell_h * 1.4 or top > self.H:
            return
        self.screen.blit(surf, (cx - surf.get_width() / 2.0, top))

        if t.alpha > 0.35:
            lab = self.label_surf[t.type]
            if t.alpha < 0.99:
                lab = lab.copy()
                lab.fill((255, 255, 255, int(255 * t.alpha)),
                         special_flags=pygame.BLEND_RGBA_MULT)
            ly = top + self.icon_px + 3
            self.screen.blit(lab, (cx - lab.get_width() / 2.0, ly))

    def _draw_sel(self, cell, k, fill_only=False):
        """Windows 桌面选中图标的那种半透明蓝框。"""
        r, c = cell
        x = int(self.bx + c * self.cell_w) + 1
        y = int(self.by + r * self.cell_h) + 1
        w = self.cell_w - 2
        h = self.cell_h - 2
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(s, (*SEL_COLOR, int(56 * k)), s.get_rect(), border_radius=4)
        if not fill_only:
            pygame.draw.rect(s, (*SEL_COLOR, int(170 * k)), s.get_rect(),
                             width=1, border_radius=4)
        self.screen.blit(s, (x, y))

    # -------------------------------------------------------------- 结算弹窗
    def _top5_rect(self):
        """常驻榜贴在窗口底部一条，横着排，把整个右边和上方都让给图标。"""
        h = self.BAR_H
        w = self.W - self.margin * 2
        return pygame.Rect(self.margin, self.desk_h - 6 - h, w, h)

    def _draw_top5(self):
        """一直显示的历史前五：不用按 Tab，扫一眼就看得到。"""
        if self.state != "play":
            return
        scr = self.screen
        box = self._top5_rect()
        rows = self.records[:5]

        label_w, score_w = 112, 104
        btn = self.manual_rect()
        x0 = box.x + label_w
        x1 = btn.x - 10
        ew = max(88.0, (x1 - x0) / 5.0)

        panel = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, (18, 22, 30, 172), panel.get_rect(), border_radius=7)
        pygame.draw.rect(panel, (255, 255, 255, 28), panel.get_rect(),
                         width=1, border_radius=7)
        # 竖分隔线画在带 alpha 的面上，才会真的半透明
        for i in range(1, 5):
            sx = int(x0 + ew * i - 8 - box.x)
            pygame.draw.line(panel, (255, 255, 255, 20), (sx, 11), (sx, box.h - 11))
        pygame.draw.line(panel, (255, 255, 255, 22),
                         (int(label_w - 10), 11), (int(label_w - 10), box.h - 11))
        pygame.draw.line(panel, (255, 255, 255, 22),
                         (int(box.w - score_w), 11), (int(box.w - score_w), box.h - 11))

        # 「重新排列」按钮（卡住了自己点，不扣分）
        on = self.manual > 0 and not self.busy
        bhov = self.manual_hit(pygame.mouse.get_pos())
        br = pygame.Rect(btn.x - box.x, btn.y - box.y, btn.w, btn.h)
        if on and bhov:
            pygame.draw.rect(panel, (255, 255, 255, 46), br, border_radius=6)
            pygame.draw.rect(panel, (255, 255, 255, 96), br, width=1, border_radius=6)
        else:
            pygame.draw.rect(panel, (255, 255, 255, 18), br, border_radius=6)
            pygame.draw.rect(panel, (255, 255, 255, 40), br, width=1, border_radius=6)
        scr.blit(panel, box.topleft)

        # 左边标题块：单人 = 个人记录；联机 = 房间和玩法
        net = self.net if (self.net and self.net.active) else None
        if net:
            t1, t2 = net.bar_title()
        else:
            t1, t2 = "个人记录", f"前 5 · 共 {self.stats.get('games', 0)} 局"
        self.blit_text(t1, 11, (176, 184, 198), (box.x + 13, box.y + 9), shadow=0)
        self.blit_text(t2, 9, (126, 134, 148),
                       (box.x + 13, box.y + 29), bold=False, shadow=0)

        if net:
            net.draw_bar_slots(box, x0, ew)
        else:
            for i in range(5):
                ex = int(x0 + ew * i)
                if i < len(rows):
                    rec = rows[i]
                    top = i == 0
                    self.blit_text(f"{i + 1}", 9,
                                   (232, 196, 104) if top else (150, 158, 172),
                                   (ex + 6, box.y + 12), bold=False, shadow=0)
                    self.blit_text(f"{rec['s']:,}", 15,
                                   (244, 232, 198) if top else (226, 232, 244),
                                   (ex + 20, box.y + 8), shadow=0)
                    sub = f"{rec.get('m', 0)} 步 · {rec.get('d', '')}"
                    self.blit_text(sub, 9, (124, 132, 146), (ex + 6, box.y + 30),
                                   bold=False, shadow=0)
                else:
                    self.blit_text(f"{i + 1}", 9, (108, 114, 126), (ex + 6, box.y + 12),
                                   bold=False, shadow=0)
                    self.blit_text("—", 11, (96, 102, 114), (ex + 22, box.y + 12),
                                   bold=False, shadow=0)

        # 右边：单人 = 本局得分（点它展开前十）；联机 = 倒计时 / 名次
        hov = self.score_hit(pygame.mouse.get_pos())
        if net:
            lab, val = net.bar_right()
            self.blit_text(lab, 9, (124, 132, 146),
                           (box.right - 13, box.y + 10), anchor="topright",
                           bold=False, shadow=0)
            self.blit_text(val, 15, SCORE_COLOR,
                           (box.right - 13, box.y + 26), anchor="topright", shadow=0)
        else:
            self.blit_text("本局得分", 9, (150, 158, 172) if hov else (124, 132, 146),
                           (box.right - 13, box.y + 10), anchor="topright",
                           bold=False, shadow=0)
            self.blit_text(f"{self.score:,}", 15,
                           (255, 255, 255) if hov else SCORE_COLOR,
                           (box.right - 13, box.y + 26), anchor="topright", shadow=0)

        # 按钮上的字
        if self.manual > 0:
            col = (255, 255, 255) if (on and bhov) else (206, 214, 228)
        else:
            col = (112, 118, 130)
        self.blit_text(f"重新排列 {self.manual}", 10, col, btn.center,
                       anchor="center", bold=False, shadow=0)

    def _draw_records(self):
        """Tab 唤出的完整榜单：小、暗、贴边，像系统浮层。"""
        scr = self.screen
        rows = self.records[:10]
        w = 246
        h = 40 + max(1, len(rows)) * 19
        # 从底部那条常驻榜上方长出来，像系统的候选列表
        x = self.W - 16 - w
        y = max(10, self._top5_rect().top - 10 - h)
        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(panel, (20, 24, 32, 248), panel.get_rect(), border_radius=7)
        pygame.draw.rect(panel, (255, 255, 255, 40), panel.get_rect(),
                         width=1, border_radius=7)
        scr.blit(panel, (x, y))

        self.blit_text("个人记录 · 前 10", 10, (158, 166, 180), (x + 12, y + 8), bold=False, shadow=0)
        self.blit_text(f"共 {self.stats.get('games', 0)} 局", 10, (158, 166, 180),
                       (x + w - 12, y + 8), anchor="topright", bold=False, shadow=0)
        if not rows:
            self.blit_text("还没有记录", 10, (128, 134, 146), (x + 12, y + 28),
                           bold=False, shadow=0)
            return
        for i, rec in enumerate(rows):
            ry = y + 28 + i * 19
            self.blit_text(f"{i + 1}", 10, (150, 158, 172), (x + 12, ry), bold=False, shadow=0)
            self.blit_text(f"{rec['s']:,}", 11, (232, 238, 248), (x + 30, ry), shadow=0)
            self.blit_text(rec.get("d", ""), 10, (140, 148, 162),
                           (x + w - 12, ry), anchor="topright", bold=False, shadow=0)

    def _draw_over(self):
        """做成一个像系统提示的对话框，而不是游戏结算界面。"""
        scr = self.screen
        dim = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        dim.fill((12, 16, 24, 96))
        scr.blit(dim, (0, 0))

        cw = 520
        shown = min(10, max(1, len(self.records)))
        ch = 300 + shown * 21 + 24
        card = pygame.Rect((self.W - cw) // 2, (self.desk_h - ch) // 2, cw, ch)
        for i in range(18, 0, -2):
            s = pygame.Surface((card.w + i * 2, card.h + i * 2), pygame.SRCALPHA)
            pygame.draw.rect(s, (0, 0, 0, 10), s.get_rect(), border_radius=10 + i // 2)
            scr.blit(s, (card.x - i, card.y - i + 6))

        pygame.draw.rect(scr, (250, 250, 252), card, border_radius=10)
        pygame.draw.rect(scr, (208, 212, 220), card, width=1, border_radius=10)

        # 标题栏
        tb = pygame.Rect(card.x, card.y, cw, 44)
        pygame.draw.rect(scr, (246, 247, 250), tb,
                         border_top_left_radius=10, border_top_right_radius=10)
        pygame.draw.line(scr, (228, 231, 236), (tb.x, tb.bottom - 1), (tb.right, tb.bottom - 1))
        self.blit_text("提示", 13, (52, 56, 64), (tb.x + 18, tb.centery),
                       anchor="midleft", shadow=0)

        cx = card.x + cw // 2
        y = card.y + 66
        self.blit_text("已经没有可以消除的组合了", 17, (30, 32, 38), (cx, y),
                       anchor="midtop", shadow=0)
        sub = f"本局结束，成绩已记入个人记录（第 {self.stats.get('games', 1)} 局）"
        self.blit_text(sub, 11, (124, 130, 140), (cx, y + 30), anchor="midtop",
                       bold=False, shadow=0)

        # 三个数字
        y += 62
        cols = [("本局得分", f"{self.score:,}", (32, 34, 40)),
                ("步数", f"{self.moves}", (32, 34, 40)),
                ("历史最佳", f"{self.high:,}", (36, 92, 168))]
        for i, (label, value, color) in enumerate(cols):
            px = card.x + cw * (i + 0.5) / 3
            self.blit_text(label, 10, (140, 145, 155), (px, y), anchor="midtop",
                           bold=False, shadow=0)
            self.blit_text(value, 21, color, (px, y + 16), anchor="midtop", shadow=0)

        # 历史记录
        y += 62
        pygame.draw.line(scr, (230, 233, 238), (card.x + 40, y), (card.right - 40, y))
        y += 12
        head = "个人记录 TOP 10" + (f"     本局排名 第 {self.rank} 位" if self.rank else "")
        self.blit_text(head, 10, (140, 145, 155), (card.x + 40, y), bold=False, shadow=0)
        y += 20

        f = self.font(12, False)
        for i, rec in enumerate(self.records[:10]):
            ry = y + i * 21
            cur = rec is getattr(self, "last_rec", None)
            if cur:
                hi = pygame.Rect(card.x + 36, ry - 2, cw - 72, 21)
                pygame.draw.rect(scr, (234, 241, 251), hi, border_radius=4)
            color = (36, 92, 168) if cur else (70, 74, 82)
            self.blit_text(f"{i + 1}", 11, color, (card.x + 46, ry), bold=cur, shadow=0)
            self.blit_text(f"{rec['s']:,}", 12, color, (card.x + 74, ry), bold=True, shadow=0)
            self.blit_text(f"步数 {rec.get('m', 0)}", 11, (140, 145, 155),
                           (card.x + 190, ry), bold=False, shadow=0)
            self.blit_text(rec.get("d", ""), 11, (140, 145, 155),
                           (card.right - 46, ry), anchor="topright", bold=False, shadow=0)
        if not self.records:
            self.blit_text("还没有记录", 11, (150, 155, 165), (card.x + 46, y),
                           bold=False, shadow=0)

        # 按钮
        bw, bh = 116, 32
        gap = 12
        bx = card.right - 24 - bw * 2 - gap
        by = card.bottom - 24 - bh
        again = pygame.Rect(bx, by, bw, bh)
        quit_ = pygame.Rect(bx + bw + gap, by, bw, bh)
        self.btn = {"again": again, "quit": quit_}

        hover = pygame.mouse.get_pos()
        pygame.draw.rect(scr, (36, 100, 180) if again.collidepoint(hover) else (40, 108, 192),
                         again, border_radius=5)
        self.blit_text("再来一局", 12, (255, 255, 255), again.center, anchor="center", shadow=0)

        pygame.draw.rect(scr, (238, 240, 244) if not quit_.collidepoint(hover) else (230, 233, 239),
                         quit_, border_radius=5)
        pygame.draw.rect(scr, (206, 210, 218), quit_, width=1, border_radius=5)
        self.blit_text("退出", 12, (60, 64, 72), quit_.center, anchor="center", shadow=0)

        self.blit_text("R 再来一局    ESC 退出", 10, (168, 173, 182),
                       (card.x + 26, card.bottom - 38), bold=False, shadow=0)

    def _draw_net_over(self):
        """联机结算：一排名字和分数，谁第一一眼就看到。"""
        scr = self.screen
        info = self.net_over or {}
        rows = info.get("rows") or []
        reason = info.get("reason") or "time"
        mode = self.net.mode if self.net else MODE_A

        dim = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        dim.fill((12, 16, 24, 96))
        scr.blit(dim, (0, 0))

        cw = 480
        n = max(1, len(rows))
        ch = 196 + n * 28
        card = pygame.Rect((self.W - cw) // 2, (self.desk_h - ch) // 2, cw, ch)
        for i in range(18, 0, -2):
            s = pygame.Surface((card.w + i * 2, card.h + i * 2), pygame.SRCALPHA)
            pygame.draw.rect(s, (0, 0, 0, 10), s.get_rect(), border_radius=10 + i // 2)
            scr.blit(s, (card.x - i, card.y - i + 6))
        pygame.draw.rect(scr, (250, 250, 252), card, border_radius=10)
        pygame.draw.rect(scr, (208, 212, 220), card, width=1, border_radius=10)

        tb = pygame.Rect(card.x, card.y, cw, 44)
        pygame.draw.rect(scr, (246, 247, 250), tb,
                         border_top_left_radius=10, border_top_right_radius=10)
        pygame.draw.line(scr, (228, 231, 236), (tb.x, tb.bottom - 1), (tb.right, tb.bottom - 1))
        self.blit_text("提示", 13, (52, 56, 64), (tb.x + 18, tb.centery),
                       anchor="midleft", shadow=0)

        head = {"time": "时间到，本局结束",
                "nomoves": "没有可以消除的组合了",
                "host_left": "房主解散了房间"}.get(reason, "本局结束")
        cx = card.x + cw // 2
        y = card.y + 64
        self.blit_text(head, 17, (30, 32, 38), (cx, y), anchor="midtop", shadow=0)
        sub = ("同盘竞速 · 3 分钟" if mode == MODE_A else "共享棋盘 · 抢着消") + \
              f"　·　房间 {self.net.code if self.net else ''}"
        self.blit_text(sub, 11, (124, 130, 140), (cx, y + 30), anchor="midtop",
                       bold=False, shadow=0)

        y += 64
        me = self.net.my_id if self.net else -1
        for i, r in enumerate(rows):
            ry = y + i * 28
            mine = r.get("id") == me
            if mine:
                hi = pygame.Rect(card.x + 34, ry - 4, cw - 68, 26)
                pygame.draw.rect(scr, (234, 241, 251), hi, border_radius=5)
            color = (36, 92, 168) if mine else (70, 74, 82)
            medal = ("①", "②", "③")[i] if i < 3 else f"{i + 1}."
            nm = r.get("n") or "?"
            if mine:
                nm = f"{nm}（我）"
            self.blit_text(medal, 13, color, (card.x + 44, ry), shadow=0)
            self.blit_text(nm, 12, color, (card.x + 78, ry),
                           bold=mine, shadow=0)
            self.blit_text(f"{int(r.get('s', 0)):,}", 14, color,
                           (card.x + cw - 48, ry - 2), anchor="topright", shadow=0)
        if not rows:
            self.blit_text("没人得分", 11, (150, 155, 165), (card.x + 46, y),
                           bold=False, shadow=0)

        bw, bh = 116, 32
        gap = 12
        bx = card.right - 24 - bw * 2 - gap
        by = card.bottom - 24 - bh
        again = pygame.Rect(bx, by, bw, bh)
        quit_ = pygame.Rect(bx + bw + gap, by, bw, bh)
        self.btn = {"again": again, "quit": quit_}
        hover = pygame.mouse.get_pos()
        pygame.draw.rect(scr, (36, 100, 180) if again.collidepoint(hover) else (40, 108, 192),
                         again, border_radius=5)
        self.blit_text("再来一局", 12, (255, 255, 255), again.center,
                       anchor="center", shadow=0)
        pygame.draw.rect(scr, (238, 240, 244) if not quit_.collidepoint(hover) else (230, 233, 239),
                         quit_, border_radius=5)
        pygame.draw.rect(scr, (206, 210, 218), quit_, width=1, border_radius=5)
        self.blit_text("退出", 12, (60, 64, 72), quit_.center, anchor="center", shadow=0)
        self.blit_text("R 再来一局    ESC 退出", 10, (168, 173, 182),
                       (card.x + 26, card.bottom - 38), bold=False, shadow=0)

    # -------------------------------------------------------------- 设置窗口
    def open_settings(self):
        """叫出设置子窗口：玩法、联机方式、昵称都在里面选。"""
        if self.settings.open_:
            return
        s = self.setup if isinstance(self.setup, dict) else {}
        mode = s.get("mode") if s.get("mode") in ("solo", "A", "B") else "solo"
        self.settings.open(mode=mode,
                           transport=s.get("transport") or "lan",
                           role=s.get("role") or "host",
                           nick=self.nick,
                           addr=s.get("addr") or "",
                           btaddr=s.get("btaddr") or "")

    def apply_setup(self, cfg):
        """子窗口点“开始”之后，按选择进单机 / 开房 / 进房。"""
        self.nick = str(cfg.get("nick") or "")[:8]
        self.net.nick = self.nick
        mode = cfg.get("mode") or "solo"
        self.setup = {"mode": mode,
                      "transport": cfg.get("transport") or "lan",
                      "role": cfg.get("role") or "host",
                      "addr": self.settings.addr,
                      "btaddr": self.settings.btaddr}
        self._save_stats()

        if self.net.active or self.net.panel:
            self.net.close()          # 换玩法前先把原来的房间解散 / 断开
        self.net_over = None
        if mode == "solo":
            self.new_game()
            self._toast("单机模式", 1.2)
            return
        if cfg.get("role") == "host":
            if not self.net.create_room(mode, self.setup["transport"]):
                self._toast(self.net.err or "建房失败，换个时间再试", 3.0)
                self.new_game()
            return
        if self.setup["transport"] == "bt":
            self.net.join_bt(cfg.get("bt") or "")
        else:
            self.net.join_room(cfg.get("room") or {})

    def net_rematch(self):
        """再来一局：房主直接开，其他人喊一声房主。"""
        if not self.net or not self.net.active:
            self.new_game()
            return
        self.net_over = None
        if self.net.role == "host":
            self.net.start_match()
        else:
            self.net.conn and self.net.conn.send({"t": "rematch"})
            self.state = "play"
            self.busy = True
            self._toast("等房主开下一局…", 6.0)


# ------------------------------------------------------------------ 主循环
def screen_size():
    """整块屏幕的像素尺寸（不是工作区）。"""
    import ctypes
    u = ctypes.windll.user32
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)


def window_rect():
    """
    游戏窗口的位置和大小：贴屏幕左边一条，高度正好到工作区底边。
    右边剩下的那块是**真桌面**，点文件、开软件、拉窗口全都照常。
    任务栏可见时工作区会扣掉任务栏，所以窗口不会压到它。
    """
    from winicons import work_area

    sw, _ = screen_size()
    x, y, w, h = work_area()
    ww = int(max(560, min(w, sw * WIN_RATIO)))
    return x, y, ww, h


def wallpaper_crop(x, y, w, h):
    """把整屏壁纸裁出窗口所在的那一块 —— 和右边真桌面严丝合缝。"""
    sw, sh = screen_size()
    img = wallpaper_image(sw, sh).crop((x, y, x + w, y + h))
    return pygame.image.frombytes(img.tobytes(), img.size, "RGB")


def run(icons, seed=None, autohost=None, autojoin=None, quiet=False):
    from winicons import enable_dpi_awareness

    enable_dpi_awareness()
    x, y, w, h = window_rect()
    os.environ["SDL_VIDEO_WINDOW_POS"] = f"{x},{y}"

    pygame.init()
    pygame.display.set_caption("桌面")
    screen = pygame.display.set_mode((w, h), pygame.NOFRAME)
    hwnd = None
    try:
        import ctypes
        hwnd = pygame.display.get_wm_info()["window"]
        SWP_NOSIZE, SWP_NOZORDER, SWP_SHOWWINDOW = 0x1, 0x4, 0x40
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), None, x, y, 0, 0,
                                          SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW)
    except Exception:
        pass

    game = Game(screen, icons, seed=seed, hwnd=hwnd, bg=wallpaper_crop(x, y, w, h))

    # 命令行直接开房 / 进房：省得在面板里点，也方便脚本化验证
    if autohost:
        game.net.create_room(autohost)
    elif autojoin:
        host, _, port = autojoin.partition(":")
        try:
            game.net.join_room({"ip": host, "port": int(port or 0),
                                "code": "", "mode": MODE_A})
        except Exception:  # noqa: BLE001
            pass
    if quiet and game.net.panel:
        game.net.panel = ""

    # 自检用：DM3_OPEN_SETTINGS="A:join:lan" 直接开设置窗口（截图 / 联调）
    probe = os.environ.get("DM3_OPEN_SETTINGS")
    if probe and probe != "0":
        m, _, rest = probe.partition(":")
        r, _, t = rest.partition(":")
        m = (m or "solo").upper()
        game.settings.open(mode=("solo" if m in ("SOLO", "1", "") else
                                 ("A" if m == "A" else "B")),
                           role=r or "host", transport=t or "lan", nick=game.nick)

    clock = pygame.time.Clock()
    running = True
    while running:
        dt = min(clock.tick(60) / 1000.0, 0.05)

        # 设置子窗口开着的时候：棋盘冻住，消息都给它，游戏本身不响应任何操作
        if game.settings.open_:
            game.settings.pump()
            cfg = game.settings.take_result()
            if cfg:
                game.apply_setup(cfg)
            game.draw()
            pygame.display.flip()
            continue

        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                game.exit_save()          # 关窗口 = 一局结束，成绩也记下来
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_j and (e.mod & pygame.KMOD_CTRL):
                    if game.net.active:
                        game.net.toggle_panel()   # 已经在房间里：看实时比分
                    else:
                        game.open_settings()      # 没联机：叫出玩法/联机设置
                elif game.net.panel:
                    game.net.handle_event(e)  # 面板打开时按键都归它
                elif e.key == pygame.K_ESCAPE:
                    game.exit_save()      # ESC 直接退，不用打完；分数照样入榜
                    running = False
                elif e.key == pygame.K_m and (e.mod & pygame.KMOD_CTRL):
                    game.minimize()       # 有人过来：Ctrl+M 收起来，点任务栏再叫回来
                elif e.key == pygame.K_r:
                    if game.state == "over":
                        game.net_rematch()
                    elif not game.busy:
                        game.new_game()
                elif e.key == pygame.K_s and game.state == "play":
                    game.do_manual_shuffle()   # 找不到能消的：自己重排一次
                elif e.key == pygame.K_h and not game.busy and game.state == "play":
                    game.hint = game.board.pick_hint()
                    game.hint_t = game.t
                elif e.key == pygame.K_TAB:
                    game.toggle_records()
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                if game.net.panel:
                    continue                      # 面板开着就别碰棋盘
                if game.state == "over":
                    hit = game.hit_button(e.pos)
                    if hit == "again":
                        game.net_rematch()
                    elif hit == "quit":
                        running = False
                elif game.score_hit(e.pos):
                    game.toggle_records()          # 点底部「本局」= 按 Tab
                elif game.manual_hit(e.pos):
                    game.do_manual_shuffle()       # 点「重新排列」按钮
                else:
                    cell = game.cell_at(e.pos)
                    if cell:
                        game.dragging = True
                        game.drag_used = False
                        game.click(*cell)
            elif e.type == pygame.MOUSEMOTION and game.dragging and not game.busy:
                if game.sel and not game.drag_used:
                    cell = game.cell_at(e.pos)
                    if cell and cell != game.sel:
                        r0, c0 = game.sel
                        if abs(r0 - cell[0]) + abs(c0 - cell[1]) == 1:
                            game.drag_used = True
                            game.try_swap(game.sel, cell)
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                game.dragging = False

        game.update(dt)
        game.draw()
        pygame.display.flip()

    game.exit_save()
    pygame.quit()
