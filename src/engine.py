# -*- coding: utf-8 -*-
"""
消消乐核心逻辑（纯数据，不碰渲染）
------------------------------------
grid 用 list[list[int]] 表示，值 = 图标种类 id，-1 表示空。
"""
from __future__ import annotations

import random

EMPTY = -1


class Board:
    def __init__(self, rows: int, cols: int, ntypes: int, seed: int | None = None):
        self.rows = rows
        self.cols = cols
        self.ntypes = ntypes
        self.rng = random.Random(seed)
        self.grid = self._generate()

    # ---------------------------------------------------------- 生成
    def _generate(self) -> list[list[int]]:
        """生成一个开局无三连、且必有可行步的棋盘。"""
        for _ in range(200):
            grid = [[self.rng.randrange(self.ntypes) for _ in range(self.cols)]
                    for _ in range(self.rows)]
            for r in range(self.rows):
                for c in range(self.cols):
                    # 避免横竖直接凑成三连
                    while True:
                        bad_h = (c >= 2 and grid[r][c] == grid[r][c - 1] == grid[r][c - 2])
                        bad_v = (r >= 2 and grid[r][c] == grid[r - 1][c] == grid[r - 2][c])
                        if not bad_h and not bad_v:
                            break
                        grid[r][c] = self.rng.randrange(self.ntypes)
            if not find_matches(grid) and has_move(grid):
                return grid
        # 极端情况兜底
        return grid

    def reset(self):
        self.grid = self._generate()

    # ---------------------------------------------------------- 联机用
    @classmethod
    def from_grid(cls, grid, ntypes=None, seed=None):
        """用主发过来的棋盘直接造一个，不再随机生成。"""
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        b = cls.__new__(cls)
        b.rows, b.cols = rows, cols
        b.ntypes = ntypes if ntypes else (max((max(r) for r in grid), default=0) + 1)
        b.rng = random.Random(seed)
        b.grid = [list(r) for r in grid]
        return b

    def dump(self):
        """棋盘 -> 纯数据，140 个整数，直接塞进 JSON。"""
        return [list(r) for r in self.grid]

    def same_as(self, grid) -> bool:
        return self.grid == grid

    # ---------------------------------------------------------- 查询
    def matches(self) -> set[tuple[int, int]]:
        return find_matches(self.grid)

    def has_move(self) -> bool:
        return has_move(self.grid)

    def pick_hint(self):
        """找一组可行的交换，供提示功能使用。"""
        g = self.grid
        for r in range(self.rows):
            for c in range(self.cols):
                for dr, dc in ((0, 1), (1, 0)):
                    r2, c2 = r + dr, c + dc
                    if r2 >= self.rows or c2 >= self.cols:
                        continue
                    g[r][c], g[r2][c2] = g[r2][c2], g[r][c]
                    ok = bool(find_matches(g))
                    g[r][c], g[r2][c2] = g[r2][c2], g[r][c]
                    if ok:
                        return (r, c), (r2, c2)
        return None

    # ---------------------------------------------------------- 变更
    def swap(self, a, b):
        (r1, c1), (r2, c2) = a, b
        self.grid[r1][c1], self.grid[r2][c2] = self.grid[r2][c2], self.grid[r1][c1]

    def clear(self, cells):
        for r, c in cells:
            self.grid[r][c] = EMPTY

    def collapse(self):
        """
        重力下落 + 顶部补充。
        返回 (moves, spawns)：
          moves  = [(to_r, to_c, from_r, from_c), ...]  已有棋子下落
          spawns = [(to_r, to_c, type, from_r), ...]    新棋子从棋盘上方入场
        """
        rows, cols = self.rows, self.cols
        new = [[EMPTY] * cols for _ in range(rows)]
        moves, spawns = [], []

        for c in range(cols):
            write = rows - 1
            for r in range(rows - 1, -1, -1):
                t = self.grid[r][c]
                if t == EMPTY:
                    continue
                new[write][c] = t
                if write != r:
                    moves.append((write, c, r, c))
                write -= 1

            k = 0
            for r in range(write, -1, -1):
                t = self.rng.randrange(self.ntypes)
                new[r][c] = t
                spawns.append((r, c, t, -1 - k))
                k += 1

        self.grid = new
        return moves, spawns

    def shuffle(self, tries: int = 300):
        """洗牌：打乱现有种类，保证无三连且还有步可走。"""
        flat = [t for row in self.grid for t in row if t != EMPTY]
        for _ in range(tries):
            self.rng.shuffle(flat)
            g, k = [], 0
            for r in range(self.rows):
                row = []
                for c in range(self.cols):
                    row.append(flat[k] if k < len(flat) else self.rng.randrange(self.ntypes))
                    k += 1
                g.append(row)
            if not find_matches(g) and has_move(g):
                self.grid = g
                return True
        self.grid = self._generate()
        return False


# ================================================================ 工具函数
def adjacent(a, b) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def score_of(n: int, combo: int) -> int:
    """和单机版 _start_clear 完全同一套算法，联机时两边分数才对得上。"""
    return int((10 * n + max(0, n - 3) * 12) * (1.0 + (combo - 1) * 0.5))


def resolve_swap(board, a, b):
    """
    一次交换的**同步**结算（不带动画）：交换 → 消 → 落 → 补 → 连锁，直到不再有可消。

    返回 None 表示这步换不出三连（棋盘已还原）；
    否则返回 {"gain", "cells", "combo"}，board.grid 已是结算后的最终局面。
    联机用这个：主机算完立刻把结果广播出去，不用等动画播完。
    """
    if board is None or not adjacent(a, b):
        return None
    if board.grid[a[0]][a[1]] == EMPTY or board.grid[b[0]][b[1]] == EMPTY:
        return None

    board.swap(a, b)
    cur = find_matches(board.grid)
    if not cur:
        board.swap(a, b)
        return None

    gain, cells, combo = 0, set(), 0
    while cur:
        combo += 1
        gain += score_of(len(cur), combo)
        cells |= cur
        board.clear(cur)
        board.collapse()
        cur = find_matches(board.grid)

    return {"gain": gain, "cells": sorted(cells), "combo": combo}


def find_matches(grid) -> set[tuple[int, int]]:
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    out: set[tuple[int, int]] = set()

    for r in range(rows):
        run = 1
        for c in range(1, cols + 1):
            same = (c < cols and grid[r][c] != EMPTY and grid[r][c] == grid[r][c - 1])
            if same:
                run += 1
            else:
                if run >= 3:
                    for k in range(c - run, c):
                        out.add((r, k))
                run = 1

    for c in range(cols):
        run = 1
        for r in range(1, rows + 1):
            same = (r < rows and grid[r][c] != EMPTY and grid[r][c] == grid[r - 1][c])
            if same:
                run += 1
            else:
                if run >= 3:
                    for k in range(r - run, r):
                        out.add((k, c))
                run = 1

    return out


def has_move(grid) -> bool:
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    for r in range(rows):
        for c in range(cols):
            for dr, dc in ((0, 1), (1, 0)):
                r2, c2 = r + dr, c + dc
                if r2 >= rows or c2 >= cols:
                    continue
                grid[r][c], grid[r2][c2] = grid[r2][c2], grid[r][c]
                ok = bool(find_matches(grid))
                grid[r][c], grid[r2][c2] = grid[r2][c2], grid[r][c]
                if ok:
                    return True
    return False
