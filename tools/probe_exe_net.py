# -*- coding: utf-8 -*-
"""
打包后的 exe 联机端到端探测
----------------------------
第一段：exe 当房主（--host A --quiet），Python 当客户端
        验证 广播能被搜到 → welcome 里有图标表 → start 有棋盘 → 交分数能收到回执
第二段：Python 当房主（B 模式），exe 当客户端（--join）
        验证 exe 真的进了房，并用真实鼠标点两下，房主要收到 swap
每段都顺手截一张 exe 窗口的图。
"""
import ctypes
import ctypes.wintypes as wt
import io
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("DM3_DATA_DIR", os.path.join(ROOT, ".testdata"))

from PIL import Image, ImageGrab  # noqa: E402

import netcore                     # noqa: E402

EXE = os.path.join(ROOT, "dist", "桌面消消乐.exe")
# 子进程要看到真窗口：把它继承来的 SDL_VIDEODRIVER=dummy 去掉
CHILD_ENV = {k: v for k, v in os.environ.items() if not k.startswith("SDL_")}
PREVIEW = os.path.join(ROOT, "preview")

u = ctypes.windll.user32
k = ctypes.windll.kernel32
u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
u.FindWindowW.restype = ctypes.c_void_p
u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]


def _image_of(pid):
    """进程的 exe 全路径。PyInstaller 单文件会多一层 bootloader 子进程，
    所以只能按「镜像名」认窗口，不能按 pid 认。"""
    h = k.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        k.CloseHandle(h)


def _windows_of_image(basename):
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        owner = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(owner))
        img = _image_of(owner.value)
        if img and os.path.basename(img).lower() == basename.lower() \
                and u.IsWindowVisible(h):
            out.append(h)
        return True

    u.EnumWindows(cb, None)
    return out


def _pid_of(hwnd):
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def find_window(timeout=30, exclude=()):
    base = os.path.basename(EXE)
    end = time.time() + timeout
    while time.time() < end:
        for h in _windows_of_image(base):
            if _pid_of(h) in exclude:
                continue
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if b.value == "桌面":
                    return h
        time.sleep(0.4)
    return 0


def kill_leftovers():
    for h in _windows_of_image(os.path.basename(EXE)):
        u.PostMessageW(h, 0x0010, 0, 0)
    time.sleep(1.0)
    subprocess.run(["taskkill", "/IM", os.path.basename(EXE), "/F"],
                   capture_output=True)


def grab_window(hwnd, name):
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
    path = os.path.join(PREVIEW, name)
    img.save(path)
    return path, (r.left, r.top, r.right - r.left, r.bottom - r.top)


def close_app(hwnds):
    for h in hwnds:
        if h:
            u.PostMessageW(h, 0x0010, 0, 0)
    time.sleep(1.2)


def wait_msg(conn, kinds, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        for m in conn.poll():
            if m.get("t") in kinds:
                return m
        time.sleep(0.05)
    return None


# ------------------------------------------------------------------ 第一段
def exe_as_host():
    print("— exe 当房主（A 同盘竞速） —")
    proc = subprocess.Popen([EXE, "--host", "A", "--quiet"], cwd=ROOT, env=CHILD_ENV)
    try:
        hwnd = find_window()
        assert hwnd, "没等到游戏窗口"
        time.sleep(1.5)

        scan = netcore.Scanner(4.0)
        while not scan.done:
            time.sleep(0.2)
        assert scan.rooms, "没搜到 exe 广播出来的房间"
        room = list(scan.rooms.values())[0]
        print(f"OK  搜到房间 {room['code']} · 模式 {room['mode']} · "
              f"{room['ip']}:{room['port']}")

        conn = netcore.Conn(netcore.tcp_connect("127.0.0.1", int(room["port"])), "exe")
        conn.send({"t": "hello", "ver": netcore.PROTO_VER, "name": "测试机"})
        hello = wait_msg(conn, {"welcome"})
        assert hello, "没收到 welcome"

        sheet = Image.open(io.BytesIO(__import__("base64").b64decode(hello["sheet"])))
        side = sheet.size[1]
        n_icons = sheet.size[0] // side
        assert n_icons >= 6, f"图标表只有 {n_icons} 个"
        print(f"OK  welcome：{n_icons} 个图标（{sheet.size[0]}x{side}）· "
              f"棋盘 {hello['cols']}x{hello['rows']} · 棋子 {hello['ntypes']} 种")

        start = wait_msg(conn, {"start"}, timeout=8)
        assert start, "没等到开局"
        grid = start["grid"]
        assert len(grid) == hello["rows"] and len(grid[0]) == hello["cols"]
        print(f"OK  start：同一块盘 {len(grid[0])}x{len(grid)} 已下发")

        pid = hello["pid"]
        conn.send({"t": "me", "s": 777, "m": 12})
        got = None
        end = time.time() + 6
        while time.time() < end and not got:
            for m in conn.poll():
                if m.get("t") == "scores":
                    for row in m.get("p") or []:
                        if row[0] == pid and row[2] == 777:
                            got = m
            time.sleep(0.05)
        assert got, "分数没被房主收下"
        print(f"OK  交分数 777 → 房主已广播回来（共 {len(got['p'])} 人）")

        path, size = grab_window(hwnd, "exe_host_net.png")
        print(f"OK  截图 {path} · 窗口 {size[2]}x{size[3]}")
        conn.close()
        return 0
    finally:
        close_app(_windows_of_image(os.path.basename(EXE)))
        try:
            proc.wait(timeout=6)
        except Exception:  # noqa: BLE001
            proc.kill()


# ------------------------------------------------------------------ 第二段
def exe_as_client():
    print("— exe 当客户端（B 共享棋盘） —")
    kill_leftovers()
    stale = {_pid_of(h) for h in _windows_of_image(os.path.basename(EXE))}
    import pygame

    pygame.init()
    import app
    from app import Game, wallpaper_crop
    from winicons import load_pieces

    W, H = app.window_rect()[2:]
    pygame.display.set_mode((W, H))
    host = Game(pygame.display.get_surface(), load_pieces(10, 6), seed=5,
                bg=wallpaper_crop(0, 0, W, H))
    for _ in range(40):
        host.update(1 / 60)
    assert host.net.create_room("B"), host.net.err
    port = netcore.PORT_BASE + int(host.net.code)
    print(f"    房主（Python）开房 {host.net.code} · 端口 {port}")

    proc = subprocess.Popen([EXE, "--join", f"127.0.0.1:{port}", "--quiet"],
                        cwd=ROOT, env=CHILD_ENV)
    try:
        end = time.time() + 25
        while time.time() < end and not host.net.conns:
            host.update(1 / 60)
            time.sleep(1 / 60)
        assert host.net.conns, "exe 没连上"
        print("OK  exe 已连上房主")

        end = time.time() + 10
        while time.time() < end and not host.net.running:
            host.update(1 / 60)
            time.sleep(1 / 60)
        assert host.net.running, "房主没能开局"
        print("OK  开局（共享棋盘）")

        hwnd = find_window(exclude=stale)
        assert hwnd, "没等到 exe 窗口"
        u.SetForegroundWindow(hwnd)
        u.BringWindowToTop(hwnd)
        time.sleep(2.0)

        # 房主挑一组能消的，然后真实鼠标去点 exe 的那两格
        mv = host.board.pick_hint()
        assert mv, "盘上没有可行步"
        a, b = mv
        ox, oy = 0, 0     # exe 窗口贴在 (0,0)
        for cell in (a, b):
            x, y = host.cell_center(*cell)
            u.SetCursorPos(int(ox + x), int(oy + y))
            time.sleep(0.25)
            u.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.08)
            u.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.4)

        pid_guest = list(host.net.conns)[0]
        # 注意：房主的 net.tick 会先一步把消息吃掉并结算，所以这里不能去抢消息队列，
        # 直接看「这个人的分数有没有涨」——分数涨了就说明点击 → 协议 → 结算整条链通了
        end = time.time() + 8
        while time.time() < end and host.net.players[pid_guest]["s"] == 0:
            host.update(1 / 60)
            time.sleep(1 / 60)
        assert host.net.players[pid_guest]["s"] > 0, "exe 点了两下，房主没算到分"
        s = host.net.players[pid_guest]["s"]
        print(f"OK  这次算 {s} 分，结果已广播回 exe")

        path, size = grab_window(hwnd, "exe_client_net.png")
        print(f"OK  截图 {path} · 窗口 {size[2]}x{size[3]}")
        return 0
    finally:
        close_app(_windows_of_image(os.path.basename(EXE)))
        host.net.close()
        try:
            proc.wait(timeout=6)
        except Exception:  # noqa: BLE001
            proc.kill()


def main():
    assert os.path.exists(EXE), "先打包 exe"
    kill_leftovers()
    rc = exe_as_host()
    rc |= exe_as_client()
    print("全部通过" if rc == 0 else "有问题")
    return rc


if __name__ == "__main__":
    sys.exit(main())
