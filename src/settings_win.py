# -*- coding: utf-8 -*-
"""
玩法 / 联机设置 —— 独立子窗口
--------------------------------
为什么要单独开一个窗口：
- 游戏窗口只占屏幕左边一条，硬把六七个选项塞进去会把桌面挤得没法看
- 原生窗口可以输入中文（昵称）、可以粘贴地址（IP:端口 / 蓝牙地址）

实现：Win32 原生窗口（带系统标题栏，可拖动、可关）+ pygame 离屏渲染画面，
文字输入用系统 EDIT 控件（自带光标、选区、剪贴板、输入法），配色和游戏里是一套。
窗口跑在主线程，由主循环每帧调 pump() 抽消息，不另开线程，避免和 pygame 抢 DPI / 焦点。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

import pygame

import netcore

# ------------------------------------------------------------------ Win32 常量
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CLIPCHILDREN = 0x02000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_TABSTOP = 0x00010000
ES_LEFT = 0x0000
ES_AUTOHSCROLL = 0x0080

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_SETCURSOR = 0x0020
WM_KEYDOWN = 0x0100
WM_CHAR = 0x0102
WM_COMMAND = 0x0111
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_CTLCOLOREDIT = 0x0133
WM_CTLCOLORSTATIC = 0x0138
WM_SETICON = 0x0080

SW_HIDE, SW_SHOW, SW_SHOWNORMAL = 0, 5, 1
PM_REMOVE = 1
GWLP_WNDPROC = -4
VK_RETURN, VK_ESCAPE = 13, 27
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
FW_NORMAL, DEFAULT_CHARSET, CLEARTYPE_QUALITY = 400, 1, 5
EM_SETLIMITTEXT = 0x00C5
WM_SETFONT = 0x0030

# 配色：和游戏里的浮层同一套
BG = (16, 20, 28)
CARD = (25, 31, 41)
CARD_ON = (37, 45, 58)
LINE = (44, 54, 70)
TXT = (232, 238, 248)
DIM = (134, 145, 165)
FAINT = (96, 104, 120)
GOLD = (232, 196, 104)
RED = (238, 132, 132)
BTN = (34, 41, 53)

FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msyh.ttc"


# ------------------------------------------------------------------ 结构体
class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT),
                ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# 64 位下这些必须声明类型，否则句柄会被截断
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT,
                                   wt.WPARAM, wt.LPARAM]
user32.CallWindowProcW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.CreateWindowExW.restype = wt.HWND
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wt.HDC
user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
gdi32.SetBkColor.argtypes = [wt.HDC, wt.COLORREF]
gdi32.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 13 + [wt.LPCWSTR]
gdi32.CreateFontW.restype = wt.HFONT
gdi32.StretchDIBits.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                ctypes.c_void_p, wt.UINT, wt.DWORD]
gdi32.StretchDIBits.restype = ctypes.c_int

_DEBUG = bool(os.environ.get("DM3_DEBUG"))
_INST = None          # 当前实例（WndProc 是 C 回调，拿不到 self）
_OLD_PROCS = {}       # EDIT 子类化前的原始过程


def _rgb(c):
    return c[0] | (c[1] << 8) | (c[2] << 16)


def _edit_proc(hwnd, msg, wp, lp):
    """EDIT 控件：回车 = 开始、Esc = 关闭，其他照旧。"""
    if msg == WM_KEYDOWN and wp in (VK_RETURN, VK_ESCAPE):
        if _INST is not None:
            _INST._key(wp)
        return 0
    if msg == WM_CHAR and wp in (13, 27):
        return 0
    old = _OLD_PROCS.get(hwnd)
    if old:
        return user32.CallWindowProcW(old, hwnd, msg, wp, lp)
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


_EDITPROC = WNDPROC(_edit_proc)


def _wnd_proc(hwnd, msg, wp, lp):
    if _INST is not None:
        try:
            return _INST._on_msg(hwnd, msg, wp, lp)
        except Exception:  # noqa: BLE001  绝不让异常穿过 C 回调
            if _DEBUG:
                import traceback
                traceback.print_exc()
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


_WNDPROC = WNDPROC(_wnd_proc)


def _xy(lp):
    return (ctypes.c_short(lp & 0xFFFF).value,
            ctypes.c_short((lp >> 16) & 0xFFFF).value)


def _valid_ip(txt):
    ip, _, port = txt.replace(" ", "").rpartition(":")
    ok_ip = (ip.count(".") == 3 and all(
        p.isdigit() and 0 <= int(p) <= 255 for p in ip.split(".")))
    try:
        ok_port = 0 < int(port) < 65536
    except ValueError:
        ok_port = False
    return (ip, int(port or 0)) if (ok_ip and ok_port) else ("", 0)


class SettingsWin:
    """一个设置子窗口。open_ 为真时主循环每帧 pump() 一次。"""

    W, H = 660, 670
    TITLE = "桌面消消乐 · 玩法与联机设置"

    def __init__(self, icon=None):
        global _INST
        self.hwnd = None
        self.open_ = False
        self.result = None
        self.err = ""
        self.hover = ""
        self.icon = icon
        self._dirty = True
        self._t_paint = 0.0
        self._surf = None
        self._hits = []
        self._fonts = {}
        self._brushes = {}
        self._hfont = None
        self._setting = False
        self.edits = {}

        # 状态（open() 时由外部传入初值）
        self.mode = "solo"          # solo / A / B
        self.transport = "lan"      # lan / bt
        self.role = "host"          # host / join
        self.nick = ""
        self.addr = ""
        self.btaddr = ""
        self.pick = ""
        self.rooms = []
        self.scan = None

    # ------------------------------------------------------------- 窗口创建
    def _ensure(self):
        global _INST
        if self.hwnd:
            return True
        _INST = self
        hinst = kernel32.GetModuleHandleW(None)
        cls = "DM3SettingsWnd"
        wc = WNDCLASSW()
        wc.style = 0x0020 | 0x0002      # CS_OWNDC | CS_DBLCLKS
        wc.lpfnWndProc = ctypes.cast(_WNDPROC, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, 32512)   # IDC_ARROW
        wc.hbrBackground = None
        wc.lpszClassName = cls
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom and kernel32.GetLastError() != 1410:   # 1410 = 类已注册
            return False
        style = WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN
        rc = wt.RECT(0, 0, self.W, self.H)
        user32.AdjustWindowRectEx(ctypes.byref(rc), style, False, 0)
        ww, wh = rc.right - rc.left, rc.bottom - rc.top
        x, y, sw, sh = self._work_area()
        px = x + max(0, (sw - ww) // 2)
        py = y + max(0, (sh - wh) // 2)
        self.hwnd = user32.CreateWindowExW(
            0, cls, self.TITLE, style, px, py, ww, wh, None, None, hinst, None)
        if not self.hwnd:
            return False
        try:    # Win10 1903+ / Win11：标题栏跟着变深色
            on = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wt.HWND(self.hwnd), 20, ctypes.byref(on), 4)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wt.HWND(self.hwnd), 19, ctypes.byref(on), 4)
        except Exception:  # noqa: BLE001
            pass
        if self.icon is not None:
            try:
                user32.SendMessageW(self.hwnd, WM_SETICON, 1, self.icon)
                user32.SendMessageW(self.hwnd, WM_SETICON, 0, self.icon)
            except Exception:  # noqa: BLE001
                pass
        self._mk_edits()
        self._surf = pygame.Surface((self.W, self.H)).convert()
        return True

    @staticmethod
    def _work_area():
        from winicons import work_area
        return work_area()

    def _mk_edits(self):
        if self._hfont is None:
            self._hfont = gdi32.CreateFontW(
                -15, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                0, 0, CLEARTYPE_QUALITY, 0, "Microsoft YaHei UI")
        for name, (x, y, w, h) in self._field_pos().items():
            hw = user32.CreateWindowExW(
                0, "EDIT", "", WS_CHILD | ES_LEFT | ES_AUTOHSCROLL | WS_TABSTOP,
                x, y, w, h, self.hwnd, 100 + len(self.edits), None, None)
            if not hw:
                continue
            user32.SendMessageW(hw, WM_SETFONT, self._hfont, 1)
            user32.SendMessageW(hw, EM_SETLIMITTEXT, 24, 0)
            try:
                old = user32.SetWindowLongPtrW(
                    hw, GWLP_WNDPROC, ctypes.cast(_EDITPROC, ctypes.c_void_p))
                if old:
                    _OLD_PROCS[hw] = old
            except Exception:  # noqa: BLE001
                pass
            self.edits[name] = hw

    @staticmethod
    def _field_pos():
        """三个输入框在客户区里的落点（用不到的那个会被藏起来）。"""
        return {
            "nick": (40, 306, 336, 24),
            "addr": (40, 528, 336, 24),
            "bt": (40, 420, 336, 24),
        }

    @staticmethod
    def _field_rect(name):
        x, y, w, h = SettingsWin._field_pos()[name]
        return pygame.Rect(x - 14, y - 8, w + 28, h + 16)

    # ------------------------------------------------------------- 开关
    def open(self, mode="solo", transport="lan", role="host",
             nick="", addr="", btaddr=""):
        if not self._ensure():
            return False
        self.mode = mode if mode in ("solo", "A", "B") else "solo"
        self.transport = transport if transport in ("lan", "bt") else "lan"
        self.role = role if role in ("host", "join") else "host"
        self.nick = nick
        self.addr = addr
        self.btaddr = btaddr
        self.pick = ""
        self.rooms = []
        self.scan = None
        self.err = ""
        self.result = None
        self._set_text("nick", nick)
        self._set_text("addr", addr)
        self._set_text("bt", btaddr)
        self._relayout()
        self.open_ = True
        user32.ShowWindow(self.hwnd, SW_SHOWNORMAL)
        user32.SetForegroundWindow(self.hwnd)
        self._dirty = True
        self.pump()
        return True

    def hide(self):
        self.open_ = False
        if self.hwnd:
            user32.ShowWindow(self.hwnd, SW_HIDE)

    def destroy(self):
        global _INST
        if self.hwnd:
            try:
                user32.DestroyWindow(self.hwnd)
            except Exception:  # noqa: BLE001
                pass
        self.hwnd = None
        self.open_ = False
        _INST = None

    def take_result(self):
        """主循环每帧取一次：有结果就消费掉。"""
        r, self.result = self.result, None
        return r

    # ------------------------------------------------------------- 消息泵
    def pump(self):
        if not self.hwnd:
            return
        msg = wt.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            if msg.message == 0x0012:          # WM_QUIT：别吞，交回游戏循环
                user32.PostQuitMessage(msg.wParam)
                break
            if not user32.IsDialogMessageW(self.hwnd, ctypes.byref(msg)):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        if not self.open_:
            return
        # 搜索结果 / 光标闪烁：隔一会儿重画一次就够
        now = pygame.time.get_ticks() / 1000.0
        if self.scan is not None:
            rows = sorted(self.scan.rooms.values(), key=lambda r: str(r.get("code", "")))
            if rows != self.rooms:
                self.rooms = rows
                self._dirty = True
        if self._dirty or now - self._t_paint > 0.4:
            self._t_paint = now
            self._dirty = False
            user32.InvalidateRect(self.hwnd, None, False)

    # ------------------------------------------------------------- 消息处理
    def _on_msg(self, hwnd, msg, wp, lp):
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            self._paint(hdc)
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg in (WM_CLOSE, WM_DESTROY):
            self.hide()
            return 0
        if msg == WM_MOUSEMOVE:
            x, y = _xy(lp)
            key = self._hit(x, y)
            if key != self.hover:
                self.hover = key
                self._dirty = True
                self._cursor(key)
            return 0
        if msg == WM_SETCURSOR:
            return 0
        if msg == WM_LBUTTONDOWN:
            x, y = _xy(lp)
            key = self._hit(x, y)
            if key:
                self._act(key)
            return 0
        if msg == WM_LBUTTONUP:
            return 0
        if msg == WM_KEYDOWN:
            self._key(wp)
            return 0
        if msg == WM_COMMAND:
            cid = int(wp) & 0xFFFF
            code = (int(wp) >> 16) & 0xFFFF
            if code == 0x0300:              # EN_CHANGE
                self._read_fields()
            elif cid in (1, 2):             # IDOK / IDCANCEL：双保险
                self._key(VK_RETURN if cid == 1 else VK_ESCAPE)
            return 0
        if msg in (WM_CTLCOLOREDIT, WM_CTLCOLORSTATIC):
            hdc = wt.HDC(wp)
            gdi32.SetBkColor(hdc, _rgb(CARD))
            gdi32.SetTextColor(hdc, _rgb(TXT))
            return self._brush(CARD)
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _brush(self, color):
        h = self._brushes.get(color)
        if not h:
            h = gdi32.CreateSolidBrush(_rgb(color))
            self._brushes[color] = h
        return h

    def _cursor(self, key):
        try:
            user32.SetCursor(user32.LoadCursorW(None, 32649 if key else 32512))
        except Exception:  # noqa: BLE001
            pass

    def _key(self, vk):
        if vk == VK_RETURN:
            self._act("ok")
        elif vk == VK_ESCAPE:
            self._act("cancel")

    def _hit(self, x, y):
        for rect, key in self._hits:
            if rect.collidepoint(x, y):
                return key
        return ""

    # ------------------------------------------------------------- 输入框
    def _set_text(self, name, txt):
        hw = self.edits.get(name)
        if not hw:
            return
        self._setting = True
        user32.SetWindowTextW(hw, txt or "")
        self._setting = False

    def _get_text(self, name):
        hw = self.edits.get(name)
        if not hw:
            return ""
        buf = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hw, buf, 64)
        return buf.value.strip()

    def _read_fields(self):
        if self._setting:
            return
        if "nick" in self.edits:          # 没有真实控件时保留原值（自检环境）
            self.nick = self._get_text("nick")[:8]
        if "addr" in self.edits:
            self.addr = self._get_text("addr")
        if "bt" in self.edits:
            self.btaddr = self._get_text("bt")

    def _show_edit(self, name, on):
        hw = self.edits.get(name)
        if hw:
            user32.ShowWindow(hw, SW_SHOWNORMAL if on else SW_HIDE)

    # ------------------------------------------------------------- 状态机
    def _relayout(self):
        solo = self.mode == "solo"
        join = (not solo) and self.role == "join"
        self._show_edit("nick", True)
        self._show_edit("addr", join and self.transport == "lan")
        self._show_edit("bt", join and self.transport == "bt")
        if join and self.transport == "lan":
            if self.scan is None:
                self._rescan()
        else:
            self.scan = None
            self.rooms = []
        self.err = ""
        self._dirty = True
        self._render()

    def _rescan(self):
        self.scan = netcore.Scanner(2.4)
        self.rooms = []
        self.pick = ""

    def _act(self, key):
        if key == "ok":
            self._submit()
            return
        if key in ("cancel", "close"):
            self.hide()
            return
        if key.startswith("mode:"):
            self.mode = key.split(":", 1)[1]
        elif key.startswith("tr:"):
            self.transport = key.split(":", 1)[1]
        elif key.startswith("role:"):
            self.role = key.split(":", 1)[1]
        elif key == "refresh":
            self._rescan()
            self._dirty = True
            return
        elif key.startswith("room:"):
            self.pick = key.split(":", 1)[1]
            self.err = ""
            self._dirty = True
            return
        self._relayout()

    def _submit(self):
        self._read_fields()
        nick = self.nick[:8]
        if self.mode == "solo":
            self.result = {"mode": "solo", "transport": "lan", "role": "host",
                           "nick": nick}
            self.hide()
            return
        if self.role == "host":
            self.result = {"mode": self.mode, "transport": self.transport,
                           "role": "host", "nick": nick}
            self.hide()
            return
        if self.transport == "lan":
            room = next((r for r in self.rooms if str(r.get("code")) == self.pick), None)
            if room is None:
                ip, port = _valid_ip(self.addr)
                if not ip:
                    self.err = "点上面的房间，或者填房主的 IP:端口"
                    self._dirty = True
                    return
                room = {"ip": ip, "port": port, "code": "", "mode": self.mode}
            self.result = {"mode": self.mode, "transport": "lan", "role": "join",
                           "nick": nick, "room": room}
            self.hide()
            return
        hexs = "".join(ch for ch in self.btaddr
                       if ch in "0123456789abcdefABCDEF")
        if len(hexs) != 12:
            self.err = "蓝牙地址要 12 位十六进制，比如 93378C718800"
            self._dirty = True
            return
        mac = ":".join(hexs[i:i + 2] for i in range(0, 12, 2)).upper()
        self.result = {"mode": self.mode, "transport": "bt", "role": "join",
                       "nick": nick, "bt": mac}
        self.hide()

    # ------------------------------------------------------------- 字体/绘制
    def _font(self, size, bold=True):
        key = (size, bold)
        f = self._fonts.get(key)
        if f is None:
            try:
                f = pygame.font.Font(FONT_BOLD if bold else FONT_REG, size)
            except Exception:  # noqa: BLE001
                f = pygame.font.Font(None, size + 4)
            self._fonts[key] = f
        return f

    def _t(self, s, txt, size, col, pos, bold=True, anchor="topleft"):
        surf = self._font(size, bold).render(txt, True, col)
        r = surf.get_rect(**{anchor: pos})
        s.blit(surf, r)
        return r

    @staticmethod
    def _card(s, rect, fill, border=None, radius=9):
        pygame.draw.rect(s, fill, rect, border_radius=radius)
        if border:
            pygame.draw.rect(s, border, rect, width=1, border_radius=radius)

    def _option(self, s, rect, key, title, desc, on):
        hov = self.hover == key
        fill = CARD_ON if on else (CARD if not hov else (30, 37, 48))
        border = GOLD if on else (LINE if not hov else (68, 82, 104))
        self._card(s, rect, fill, border)
        if on:
            pygame.draw.circle(s, GOLD, (rect.x + 15, rect.y + 20), 4)
        self._t(s, title, 13, TXT if on else (218, 226, 240),
                (rect.x + 28 if on else rect.x + 15, rect.y + 12))
        for i, ln in enumerate(desc):
            self._t(s, ln, 10, DIM if on else FAINT,
                    (rect.x + 15, rect.y + 38 + i * 17), bold=False)
        self._hits.append((rect, key))

    def _chip(self, s, rect, key, label, on, dead=False, size=12):
        hov = self.hover == key and not dead
        fill = CARD_ON if on else (BTN if hov else CARD)
        border = GOLD if on else (LINE if not hov else (68, 82, 104))
        if dead:
            fill, border = (21, 26, 34), (36, 44, 58)
        self._card(s, rect, fill, border, radius=8)
        col = (TXT if on else DIM) if not dead else (72, 80, 96)
        self._t(s, label, size, col, rect.center, bold=on, anchor="center")
        if not dead:
            self._hits.append((rect, key))

    def _button(self, s, rect, key, label, primary=False):
        hov = self.hover == key
        if primary:
            fill = (250, 214, 120) if hov else GOLD
            col = (26, 22, 12)
        else:
            fill = (46, 55, 70) if hov else BTN
            col = TXT
        self._card(s, rect, fill, None, radius=10)
        self._t(s, label, 14, col, rect.center, anchor="center")
        self._hits.append((rect, key))

    def _render(self):
        s = self._surf
        if s is None:
            return
        self._hits = []
        s.fill(BG)

        # ---- 顶栏
        pygame.draw.rect(s, GOLD, pygame.Rect(26, 20, 4, 34), border_radius=2)
        self._t(s, "玩法与联机", 19, TXT, (40, 18))
        self._t(s, "选好玩法再开；单机直接玩，联机让对方在自己机器上选“加入房间”",
                11, DIM, (40, 45), bold=False)
        self._t(s, "Esc 关闭", 10, FAINT, (self.W - 26, 28), bold=False,
                anchor="topright")
        pygame.draw.line(s, LINE, (0, 74), (self.W, 74))

        # ---- ① 玩法
        self._t(s, "玩法", 11, DIM, (26, 88), bold=False)
        cw, gap, cy, ch = 196, 10, 106, 92
        self._option(s, pygame.Rect(26, cy, cw, ch), "mode:solo", "单机",
                     ["一个人玩", "桌面图标当棋子"], self.mode == "solo")
        self._option(s, pygame.Rect(26 + cw + gap, cy, cw, ch), "mode:A", "A 同盘竞速",
                     ["同一块板，各玩各的", "限时 3 分钟比分数"], self.mode == "A")
        self._option(s, pygame.Rect(26 + (cw + gap) * 2, cy, cw, ch), "mode:B",
                     "B 共享棋盘", ["只有一块板", "谁先换成功谁得分"],
                     self.mode == "B")

        solo = self.mode == "solo"
        # ---- ② 联机方式
        self._t(s, "联机方式", 11, FAINT if solo else DIM, (26, 212), bold=False)
        self._chip(s, pygame.Rect(26, 230, 112, 38), "tr:lan", "局域网",
                   self.transport == "lan", solo)
        self._chip(s, pygame.Rect(148, 230, 112, 38), "tr:bt", "蓝牙",
                   self.transport == "bt", solo)
        self._t(s, "局域网：同一个 WiFi / 网线，自动搜房间", 10, FAINT,
                (272, 232), bold=False)
        self._t(s, f"蓝牙：两台机器先在系统里配对，通道 {netcore.BT_CHANNEL}",
                10, FAINT, (272, 250), bold=False)

        # ---- ③ 昵称
        self._t(s, "我的昵称", 11, DIM, (26, 284), bold=False)
        nr = self._field_rect("nick")
        self._card(s, nr, CARD, LINE)
        self._t(s, "别人在房间里看到的名字，留空显示“玩家1”", 10, FAINT,
                (nr.right + 14, nr.centery - 7), bold=False)

        # ---- ④ 房间 / 地址
        self._draw_join(s)

        # ---- 底部
        pygame.draw.line(s, LINE, (0, 576), (self.W, 576))
        self._button(s, pygame.Rect(340, 590, 118, 46), "cancel", "取消")
        self._button(s, pygame.Rect(470, 590, 164, 46), "ok",
                     "开始游戏" if solo else "开始联机", primary=True)
        if self.err:
            self._t(s, self.err, 10, RED, (26, 604), bold=False)
        else:
            self._t(s, "回车开始　·　Ctrl+J 随时叫出来", 10, FAINT,
                    (26, 604), bold=False)

    def _draw_join(self, s):
        if self.mode == "solo":
            self._card(s, pygame.Rect(26, 366, 608, 146), (21, 26, 34), (36, 44, 58))
            self._t(s, "单机怎么玩", 11, DIM, (40, 382), bold=False)
            tips = ["拖两个相邻图标交换位置，三个以上相同的连成一线就消掉",
                    "S 键或右下角「重新排列」洗一次牌，每局 3 次",
                    "Tab 看历史前五，Esc 直接退出（分数照样进榜）",
                    "Ctrl+M 把窗口收起来，点任务栏再叫回来"]
            for i, ln in enumerate(tips):
                self._t(s, "·", 10, GOLD, (40, 408 + i * 22), bold=False)
                self._t(s, ln, 10, DIM, (56, 408 + i * 22), bold=False)
            return
        # 角色
        self._chip(s, pygame.Rect(26, 352, 112, 34), "role:host", "我建房",
                   self.role == "host")
        self._chip(s, pygame.Rect(148, 352, 112, 34), "role:join", "加入房间",
                   self.role == "join")
        self._t(s, "房主就是服务器，棋子用他的桌面图标，不用云、不用登录",
                10, FAINT, (272, 364), bold=False)

        if self.role == "host":
            self._card(s, pygame.Rect(26, 406, 608, 104), (21, 26, 34), (36, 44, 58))
            if self.transport == "lan":
                msg = ["开局后窗口里会显示房间号和 IP:端口；同一局域网的同事能自动搜到，",
                       "搜不到就把那串地址报给他，让他选“加入房间”后手填。"]
            else:
                msg = ["开局后窗口里会显示本机蓝牙地址，报给对方；",
                       "两台机器要先在系统设置里配对成功（一次就够）。"]
            for i, ln in enumerate(msg):
                self._t(s, ln, 10, DIM, (40, 424 + i * 20), bold=False)
            self._t(s, "最多 5 人，对方一进来就自动开局；开局后实时比分在底部条上",
                    10, FAINT, (40, 480), bold=False)
            return

        if self.transport == "lan":
            self._t(s, "附近的房间", 11, DIM, (26, 400), bold=False)
            self._chip(s, pygame.Rect(552, 396, 82, 22), "refresh", "重新搜索",
                       False, size=10)
            y = 426
            rows = self.rooms[:2]
            if not rows:
                self._card(s, pygame.Rect(26, y, 608, 48), (21, 26, 34), (36, 44, 58))
                txt = ("正在找附近的房间…" if (self.scan and not self.scan.done)
                       else "没找到房间，填下面的地址")
                self._t(s, txt, 11, FAINT, (40, y + 16), bold=False)
            for i, r in enumerate(rows):
                box = pygame.Rect(26, y + i * 50, 608, 44)
                on = self.pick == str(r.get("code"))
                self._card(s, box, CARD_ON if on else CARD, GOLD if on else LINE)
                mode = "同盘竞速" if r.get("mode") == "A" else "共享棋盘"
                self._t(s, f"房间 {r.get('code')}", 12, TXT, (box.x + 14, box.y + 6))
                self._t(s, f"{mode}　{r.get('n', 1)}/{r.get('cap', 5)} 人",
                        10, DIM, (box.x + 14, box.y + 24), bold=False)
                self._t(s, str(r.get("ip", "")), 10, FAINT,
                        (box.right - 14, box.y + 24), bold=False, anchor="topright")
                self._hits.append((box, "room:" + str(r.get("code"))))
            ar = self._field_rect("addr")
            self._card(s, ar, CARD, LINE)
            self._t(s, "或填房主地址", 10, FAINT, (ar.right + 14, ar.y + 3), bold=False)
            self._t(s, "例 192.168.1.7:48213", 10, FAINT,
                    (ar.right + 14, ar.y + 19), bold=False)
            return

        br = self._field_rect("bt")
        self._card(s, br, CARD, LINE)
        self._t(s, "房主的蓝牙地址", 10, DIM, (26, br.y - 22), bold=False)
        self._t(s, "12 位十六进制，房主窗口里写着", 10, FAINT,
                (br.right + 14, br.y + 3), bold=False)
        for i, ln in enumerate(["两台电脑先配对：系统设置 → 蓝牙 → 添加设备。",
                                "配对成功后，房主点“开始联机”，把地址报给你。"]):
            self._t(s, ln, 10, FAINT, (26, 480 + i * 20), bold=False)

    def _paint(self, hdc):
        self._render()
        buf = pygame.image.tobytes(self._surf, "BGRA")
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.W
        bmi.bmiHeader.biHeight = -self.H
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        gdi32.StretchDIBits(hdc, 0, 0, self.W, self.H, 0, 0, self.W, self.H,
                            buf, ctypes.byref(bmi), DIB_RGB_COLORS, SRCCOPY)
