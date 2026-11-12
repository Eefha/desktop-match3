# -*- coding: utf-8 -*-
"""
Windows 底层能力层
--------------------
1. 枚举用户桌面 / 公共桌面上的真实图标文件
2. 用 Shell 的 JUMBO(256px) 图像列表把 .lnk / .exe / 文件夹 的图标取出来
3. 读取当前壁纸、工作区尺寸
所有操作只读，不修改用户桌面的任何状态。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import os
import random
import uuid
from ctypes import (POINTER, Structure, byref, c_int, c_long, c_ubyte, c_uint,
                    c_void_p, c_wchar, c_wchar_p, sizeof)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# ---------------------------------------------------------------- 常量
SHGFI_ICON = 0x00000100
SHGFI_LARGEICON = 0x00000000
SHGFI_SYSICONINDEX = 0x00004000

SHIL_LARGE = 0x0
SHIL_EXTRALARGE = 0x2
SHIL_JUMBO = 0x4
ILD_TRANSPARENT = 0x1

SPI_GETWORKAREA = 0x0030
SPI_GETDESKWALLPAPER = 0x0073


# ---------------------------------------------------------------- 结构体
class GUID(Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                ("Data3", wt.WORD), ("Data4", c_ubyte * 8)]

    @staticmethod
    def from_string(s: str) -> "GUID":
        u = uuid.UUID(s)
        g = GUID()
        g.Data1 = u.time_low
        g.Data2 = u.time_mid
        g.Data3 = u.time_hi_version
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        return g


IID_IImageList = GUID.from_string("46EB5926-582E-4017-9FDF-E8998DAA0950")


class SHFILEINFOW(Structure):
    _fields_ = [("hIcon", c_void_p), ("iIcon", c_int), ("dwAttributes", wt.DWORD),
                ("szDisplayName", c_wchar * 260), ("szTypeName", c_wchar * 80)]


class ICONINFO(Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", c_void_p), ("hbmColor", c_void_p)]


class BITMAP(Structure):
    _fields_ = [("bmType", c_long), ("bmWidth", c_long), ("bmHeight", c_long),
                ("bmWidthBytes", c_long), ("bmPlanes", wt.WORD),
                ("bmBitsPixel", wt.WORD), ("bmBits", c_void_p)]


class BITMAPINFOHEADER(Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", c_long), ("biHeight", c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", c_long), ("biYPelsPerMeter", c_long),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class RECT(Structure):
    _fields_ = [("left", c_long), ("top", c_long),
                ("right", c_long), ("bottom", c_long)]


# ---------------------------------------------------------------- 原型
shell32.SHGetFileInfoW.argtypes = [c_wchar_p, wt.DWORD, POINTER(SHFILEINFOW),
                                   c_uint, c_uint]
shell32.SHGetFileInfoW.restype = c_void_p

shell32.SHGetImageList.argtypes = [c_int, POINTER(GUID), POINTER(c_void_p)]
shell32.SHGetImageList.restype = c_long

user32.GetIconInfo.argtypes = [c_void_p, POINTER(ICONINFO)]
user32.GetIconInfo.restype = wt.BOOL
user32.DestroyIcon.argtypes = [c_void_p]
user32.GetDC.argtypes = [c_void_p]
user32.GetDC.restype = c_void_p
user32.ReleaseDC.argtypes = [c_void_p, c_void_p]

gdi32.GetObjectW.argtypes = [c_void_p, c_int, c_void_p]
gdi32.GetObjectW.restype = c_int
gdi32.CreateCompatibleDC.argtypes = [c_void_p]
gdi32.CreateCompatibleDC.restype = c_void_p
gdi32.SelectObject.argtypes = [c_void_p, c_void_p]
gdi32.SelectObject.restype = c_void_p
gdi32.DeleteDC.argtypes = [c_void_p]
gdi32.DeleteObject.argtypes = [c_void_p]
gdi32.GetDIBits.argtypes = [c_void_p, c_void_p, c_uint, c_uint, c_void_p,
                            POINTER(BITMAPINFO), c_uint]
gdi32.GetDIBits.restype = c_int

_GetIconProto = ctypes.WINFUNCTYPE(c_long, c_void_p, c_int, c_uint,
                                   POINTER(c_void_p))

user32.FindWindowW.argtypes = [c_wchar_p, c_wchar_p]
user32.FindWindowW.restype = c_void_p
user32.FindWindowExW.argtypes = [c_void_p, c_void_p, c_wchar_p, c_wchar_p]
user32.FindWindowExW.restype = c_void_p
user32.SendMessageW.argtypes = [c_void_p, c_uint, c_void_p, c_void_p]
user32.SendMessageW.restype = c_void_p

_image_lists: dict[int, c_void_p] = {}


def _imagelist(which: int):
    """取 Shell 的系统图像列表（同一个图标索引在各尺寸列表里是一致的）。"""
    if which in _image_lists:
        return _image_lists[which] or None
    ptr = c_void_p()
    hr = shell32.SHGetImageList(which, byref(IID_IImageList), byref(ptr))
    _image_lists[which] = ptr if hr == 0 else c_void_p()
    return _image_lists[which] or None


def _vtable(ptr):
    return ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]


def _hicon_to_image(hicon):
    """HICON -> PIL.Image(RGBA)，保留 32 位 alpha。"""
    from PIL import Image

    if not hicon:
        return None
    ii = ICONINFO()
    if not user32.GetIconInfo(c_void_p(hicon), byref(ii)):
        return None
    if not ii.hbmColor:
        return None

    bmp = BITMAP()
    if not gdi32.GetObjectW(c_void_p(ii.hbmColor), sizeof(BITMAP), byref(bmp)):
        return None
    w, h = bmp.bmWidth, bmp.bmHeight
    if w <= 0 or h <= 0:
        return None

    hdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(hdc)
    old = gdi32.SelectObject(mdc, c_void_p(ii.hbmColor))

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h          # 负数 = 自上而下，省一次翻转
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0      # BI_RGB

    buf = (c_ubyte * (w * h * 4))()
    got = gdi32.GetDIBits(mdc, c_void_p(ii.hbmColor), 0, h, buf, byref(bi), 0)

    gdi32.SelectObject(mdc, old)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(None, hdc)

    if not got:
        return None

    im = Image.frombuffer("RGBA", (w, h), bytes(buf), "raw", "BGRA", 0, 1).copy()
    # 老式图标没有 alpha 通道，通体透明 —— 直接补成不透明
    if im.getextrema()[3][1] == 0:
        im.putalpha(255)
    return im


def _jumbo_icon(index: int):
    pil = _imagelist(SHIL_JUMBO)
    if not pil:
        return None
    get_icon = _GetIconProto(_vtable(pil)[10])   # IImageList::GetIcon 在 vtable[10]
    hicon = c_void_p()
    hr = get_icon(pil, index, ILD_TRANSPARENT, byref(hicon))
    if hr != 0 or not hicon:
        return None
    try:
        return _hicon_to_image(hicon)
    finally:
        user32.DestroyIcon(hicon)


def _sys_icon_index(path: str) -> int:
    sfi = SHFILEINFOW()
    flags = SHGFI_SYSICONINDEX | SHGFI_LARGEICON
    ok = shell32.SHGetFileInfoW(path, 0, byref(sfi), sizeof(sfi), flags)
    return sfi.iIcon if ok else -1


def icon_image(path: str):
    """取出某个路径的图标，优先 256px，失败回退 32px。"""
    try:
        idx = _sys_icon_index(path)
        if idx >= 0:
            im = _jumbo_icon(idx)
            if im is not None and im.size[0] >= 32:
                return im
    except Exception:
        pass

    try:
        sfi = SHFILEINFOW()
        flags = SHGFI_ICON | SHGFI_LARGEICON
        ok = shell32.SHGetFileInfoW(path, 0, byref(sfi), sizeof(sfi), flags)
        if ok and sfi.hIcon:
            try:
                return _hicon_to_image(sfi.hIcon)
            finally:
                user32.DestroyIcon(sfi.hIcon)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- 桌面文件
def desktop_paths() -> list[str]:
    """列出桌面（用户 + 公共）上的可见条目路径，快捷方式优先。"""
    dirs = [os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")]
    pub = os.environ.get("PUBLIC")
    if pub:
        dirs.append(os.path.join(pub, "Desktop"))

    shortcuts, folders, files = [], [], []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if n.lower() == "desktop.ini" or n.startswith("~$"):
                continue
            p = os.path.join(d, n)
            low = n.lower()
            if low.endswith((".lnk", ".url")):
                shortcuts.append(p)
            elif os.path.isdir(p):
                folders.append(p)
            else:
                files.append(p)

    def key(p):
        return os.path.basename(p).lower()

    shortcuts.sort(key=key)
    folders.sort(key=key)
    files.sort(key=key)
    return shortcuts + folders + files


# ---------------------------------------------------------------- 图标特征
def _feature(im) -> list[float]:
    """8x8 缩略图的 RGB 直方图式特征，用于去重和挑选差异最大的图标。"""
    small = im.convert("RGBA").resize((8, 8))
    px = list(small.getdata())
    feat = []
    for r, g, b, a in px:
        k = a / 255.0
        feat.extend((r * k / 255.0, g * k / 255.0, b * k / 255.0, k))
    return feat


def _dist(a, b) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _hue_of(im):
    """估算图标主色调。纯白/纯灰/黑白的图标返回 None —— 这类图标最容易被认错。"""
    small = im.convert("RGB").resize((16, 16))
    rs = gs = bs = n = 0
    for r, g, b in small.getdata():
        mx, mn = max(r, g, b), min(r, g, b)
        if mx - mn < 30:          # 近中性色，没有有效色彩
            continue
        if mn > 215:              # 大片留白
            continue
        rs += r
        gs += g
        bs += b
        n += 1
    if n < 12:
        return None
    r, g, b = rs / n, gs / n, bs / n
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        return None
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0


def collect_icon_images(want: int = 10, scan: int = 90, rng=None):
    """
    从桌面上挑出 `want` 个最容易分辨的图标。
    策略：先按主色调分 6 个色相桶，每个色桶最多取 2 个、无彩色桶最多取 1 个，
    避免出现一堆"白纸文档"当棋子导致玩家看错。
    rng 不为空时，桶内成员和色桶顺序都会被打乱 —— 每次启动挑出来的图标都不一样。
    """
    rng = rng or random.Random()
    cands = []          # (name, image, feature, hue_or_None)
    seen_feats = []
    paths = desktop_paths()[:scan]
    if rng is not None:
        paths = list(paths)
        rng.shuffle(paths)
    for p in paths:
        im = icon_image(p)
        if im is None:
            continue
        im = im.convert("RGBA")
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
        if im.size[0] < 16 or im.size[1] < 16:
            continue
        f = _feature(im)
        if any(_dist(f, s) < 0.05 for s in seen_feats):
            continue
        seen_feats.append(f)
        name = os.path.splitext(os.path.basename(p))[0]
        cands.append((name, im, f, _hue_of(im)))

    if not cands:
        return []

    # 分桶：色相 6 段，无彩色单独一桶
    buckets: dict[int, list[int]] = {}
    for i, (_, _, _, hue) in enumerate(cands):
        key = -1 if hue is None else int(hue // 60) % 6
        buckets.setdefault(key, []).append(i)

    picked: list[int] = []
    # 桶内顺序随机：同一色相里换着挑，每次启动的组合都不同
    for k in buckets:
        rng.shuffle(buckets[k])
    color_keys = sorted([k for k in buckets if k >= 0],
                        key=lambda k: (-len(buckets[k]), rng.random()))

    # 第一轮：每个色桶取一个代表
    for k in color_keys:
        if len(picked) >= want:
            break
        picked.append(buckets[k][0])
    # 无彩色桶最多一个
    if -1 in buckets and len(picked) < want:
        picked.append(buckets[-1][0])
    # 第二轮：色桶补第二个
    for k in color_keys:
        if len(picked) >= want:
            break
        if len(buckets[k]) >= 2:
            picked.append(buckets[k][1])
    # 第三轮：还差就从剩下的按视觉差异最远补
    while len(picked) < min(want, len(cands)):
        best, best_d = -1, -1.0
        for i in range(len(cands)):
            if i in picked:
                continue
            d = min(_dist(cands[i][2], cands[j][2]) for j in picked)
            if d > best_d:
                best, best_d = i, d
        if best < 0:
            break
        picked.append(best)

    return [(cands[i][0], cands[i][1]) for i in picked]


def square_fit(im, size: int, fill: float = 0.94):
    """把图标裁成正方形并缩放到 size，保持视觉大小一致。"""
    from PIL import Image

    im = im.convert("RGBA")
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    w, h = im.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - w) // 2, (side - h) // 2), im)
    inner = max(8, int(size * fill))
    return canvas.resize((inner, inner), Image.LANCZOS)


# ---------------------------------------------------------------- 壁纸 / 工作区
def enable_dpi_awareness():
    """
    必须先调用：否则在 125%/150% 缩放的机器上，拿到的"工作区"是缩放后的虚拟像素，
    窗口会被系统拉伸，图标发虚、格子错位。必须在 pygame 初始化之前调用。
    """
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def work_area() -> tuple[int, int, int, int]:
    r = RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, byref(r), 0):
        w, h = r.right - r.left, r.bottom - r.top
        if w > 0 and h > 0:
            return r.left, r.top, w, h
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def _wallpaper_candidates() -> list[str]:
    out = []
    buf = ctypes.create_unicode_buffer(1024)
    try:
        if user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 1024, buf, 0):
            if buf.value:
                out.append(buf.value)
    except Exception:
        pass

    themes = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Themes")
    out.append(os.path.join(themes, "TranscodedWallpaper"))
    cached = os.path.join(themes, "CachedFiles")
    if os.path.isdir(cached):
        try:
            for n in sorted(os.listdir(cached)):
                if n.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
                    out.append(os.path.join(cached, n))
        except OSError:
            pass
    return out


def _wallpaper_style():
    """读注册表里壁纸的填充方式，保证画出来和你屏幕上的一模一样。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as k:
            def get(name, default):
                try:
                    return winreg.QueryValueEx(k, name)[0]
                except OSError:
                    return default

            tile = str(get("TileWallpaper", "0"))
            style = str(get("WallpaperStyle", "10"))
    except Exception:
        tile, style = "0", "10"

    if tile == "1":
        return "tile"
    return {
        "0": "center",
        "2": "stretch",
        "6": "fit",
        "10": "fill",
        "22": "span",
    }.get(style, "fill")


def wallpaper_image(w: int, h: int):
    """按当前壁纸的真实填充方式铺满 w×h。不模糊、不压暗 —— 要和屏幕上完全一致。"""
    from PIL import Image

    src = None
    for p in _wallpaper_candidates():
        if p and os.path.isfile(p):
            try:
                src = Image.open(p).convert("RGB")
                break
            except Exception:
                continue

    if src is None:
        src = Image.new("RGB", (16, 9))
        px = src.load()
        for y in range(9):
            for x in range(16):
                t = (x / 15.0 + y / 8.0) / 2
                px[x, y] = (int(26 + 18 * t), int(32 + 22 * t), int(52 + 30 * t))

    style = _wallpaper_style()
    sw, sh = src.size

    if style == "tile":
        canvas = Image.new("RGB", (w, h), (0, 0, 0))
        for y in range(0, h, sh):
            for x in range(0, w, sw):
                canvas.paste(src, (x, y))
        return canvas

    if style == "center":
        canvas = Image.new("RGB", (w, h), (0, 0, 0))
        canvas.paste(src, ((w - sw) // 2, (h - sh) // 2))
        return canvas

    if style == "stretch":
        return src.resize((w, h), Image.LANCZOS)

    if style == "fit":
        scale = min(w / sw, h / sh)
        nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
        canvas = Image.new("RGB", (w, h), (0, 0, 0))
        canvas.paste(src.resize((nw, nh), Image.LANCZOS), ((w - nw) // 2, (h - nh) // 2))
        return canvas

    # fill / span：等比铺满后居中裁切
    scale = max(w / sw, h / sh)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    img = src.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


# ---------------------------------------------------------------- 桌面图标网格
def icon_metrics() -> tuple[int, int]:
    """
    桌面图标的真实横向/纵向间距（已按 DPI 缩放）。
    横向用系统真值；纵向给图标和文字留出呼吸空间，否则名字会贴到下一行图标上。
    """
    cx = user32.GetSystemMetrics(38) or 75     # SM_CXICONSPACING
    cy = user32.GetSystemMetrics(39) or 96     # SM_CYICONSPACING
    if cx < 40:
        cx = 75
    if cy <= cx:
        cy = int(cx * 1.24)
    return int(cx), int(cy)


def _defview():
    progman = user32.FindWindowW("Progman", None)
    if progman:
        dv = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if dv:
            return dv
    w = None
    while True:
        w = user32.FindWindowExW(None, w, "WorkerW", None)
        if not w:
            return None
        dv = user32.FindWindowExW(w, None, "SHELLDLL_DefView", None)
        if dv:
            return dv


_icons_hidden = None


def toggle_desktop_icons() -> bool:
    """
    切换桌面图标的显示状态（相当于右键桌面 → 查看 → 显示桌面图标）。
    调用两次必然回到最初状态，所以只要保证进入和退出各调一次就不会改变用户设置。
    """
    global _icons_hidden
    dv = _defview()
    if not dv:
        return False
    user32.SendMessageW(dv, 0x0111, 0x7402, 0)   # WM_COMMAND / ID 0x7402
    _icons_hidden = not _icons_hidden
    return True


def icons_hidden() -> bool:
    return bool(_icons_hidden)


# ---------------------------------------------------------------- 兜底图标
def fallback_icons(n: int = 8):
    """桌面可辨识图标不足时，用内置矢量图形补齐，保证任何电脑都能玩。"""
    from PIL import Image, ImageDraw

    S = 256
    specs = [
        ((70, 136, 255), "circle"),
        ((46, 199, 137), "square"),
        ((255, 143, 82), "triangle"),
        ((166, 106, 255), "hex"),
        ((255, 92, 140), "star"),
        ((255, 190, 58), "diamond"),
        ((64, 196, 214), "ring"),
        ((198, 96, 240), "cross"),
    ]

    out = []
    for i, (col, shape) in enumerate(specs[:n]):
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([6, 6, S - 6, S - 6], radius=58, fill=col + (255,))
        white = (255, 255, 255, 235)
        cx = cy = S // 2
        if shape == "circle":
            d.ellipse([cx - 58, cy - 58, cx + 58, cy + 58], fill=white)
            d.ellipse([cx - 26, cy - 26, cx + 26, cy + 26], fill=col + (255,))
        elif shape == "square":
            d.rectangle([cx - 52, cy - 52, cx + 52, cy + 52], fill=white)
            d.rectangle([cx - 22, cy - 22, cx + 22, cy + 22], fill=col + (255,))
        elif shape == "triangle":
            d.polygon([(cx, cy - 62), (cx + 62, cy + 46), (cx - 62, cy + 46)], fill=white)
            d.polygon([(cx, cy - 16), (cx + 30, cy + 24), (cx - 30, cy + 24)], fill=col + (255,))
        elif shape == "hex":
            pts = []
            for k in range(6):
                a = -1.5708 + k * 1.0472
                pts.append((cx + 62 * math.cos(a), cy + 62 * math.sin(a)))
            d.polygon(pts, fill=white)
            d.ellipse([cx - 24, cy - 24, cx + 24, cy + 24], fill=col + (255,))
        elif shape == "star":
            pts = []
            for k in range(10):
                a = -1.5708 + k * 0.6283
                rr = 66 if k % 2 == 0 else 28
                pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
            d.polygon(pts, fill=white)
        elif shape == "ring":
            d.ellipse([cx - 64, cy - 64, cx + 64, cy + 64], fill=white)
            d.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=col + (255,))
            d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=white)
        elif shape == "cross":
            d.rounded_rectangle([cx - 20, cy - 62, cx + 20, cy + 62], radius=10, fill=white)
            d.rounded_rectangle([cx - 62, cy - 20, cx + 62, cy + 20], radius=10, fill=white)
        else:
            d.polygon([(cx, cy - 66), (cx + 58, cy), (cx, cy + 66), (cx - 58, cy)], fill=white)
            d.ellipse([cx - 20, cy - 20, cx + 20, cy + 20], fill=col + (255,))
        out.append((f"内置图案{i + 1}", im))
    return out


def load_pieces(want: int = 10, min_types: int = 6, rng=None):
    """
    对外入口：优先用桌面真实图标，不足就补内置图案。
    每次调用都会重新洗牌挑选顺序，所以两次启动拿到的棋子组合大概率不同。
    """
    icons = collect_icon_images(want, rng=rng)
    if len(icons) < min_types:
        have = len(icons)
        icons += fallback_icons(want)[have:]
    return icons[:want]
