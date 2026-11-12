# -*- coding: utf-8 -*-
"""生成 exe 图标：深色圆角底 + 三个棋子方块。"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(size=512):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = size / 512.0

    # 底板
    d.rounded_rectangle([8 * u, 8 * u, 504 * u, 504 * u], radius=112 * u,
                        fill=(22, 26, 38, 255))
    d.rounded_rectangle([8 * u, 8 * u, 504 * u, 504 * u], radius=112 * u,
                        outline=(70, 78, 96, 255), width=max(2, int(6 * u)))

    # 三个棋子：两个正常 + 一个正在消散
    tiles = [
        (96, 128, (255, 198, 96), 1.0),
        (206, 128, (116, 224, 255), 1.0),
        (316, 128, (120, 232, 168), 0.72),
    ]
    s = 100 * u
    for x, y, col, k in tiles:
        cx, cy = (x + 50) * u, (y + 50) * u
        half = s * k / 2
        box = [cx - half, cy - half, cx + half, cy + half]
        alpha = 255 if k >= 1 else 150
        d.rounded_rectangle(box, radius=26 * u * k, fill=col + (alpha,))
        if k >= 1:
            inner = half * 0.42
            d.rounded_rectangle([cx - inner, cy - inner, cx + inner, cy + inner],
                                radius=10 * u, fill=(22, 26, 38, 255))

    # 居中的三条进度线，暗示"消除"
    for i, w in enumerate((170, 120, 70)):
        y0 = (300 + i * 28) * u
        d.rounded_rectangle([(256 - w / 2) * u, y0, (256 + w / 2) * u, y0 + 12 * u],
                            radius=6 * u, fill=(96, 106, 126, 255))
    return im


def main():
    im = build()
    out = os.path.join(ROOT, "app.ico")
    im.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                        (128, 128), (256, 256)])
    im.save(os.path.join(ROOT, "preview", "icon.png"))
    print("icon ->", out)


if __name__ == "__main__":
    os.makedirs(os.path.join(ROOT, "preview"), exist_ok=True)
    main()
