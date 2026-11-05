#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯标准库生成启动图标：深底 + 金色准星环。4x 超采样后降采样。"""
import math
import os
import struct
import zlib

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "android", "res")
SIZES = {"mipmap-mdpi": 48, "mipmap-hdpi": 72, "mipmap-xhdpi": 96,
         "mipmap-xxhdpi": 144, "mipmap-xxxhdpi": 192}
SS = 4

BG = (23, 26, 33)
GOLD = (200, 162, 74)
GOLD2 = (224, 187, 99)
DARK = (15, 17, 21)


def png(path, w, h, rgba_rows):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(r) for r in rgba_rows)
    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(raw, 9))
    out += chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(out)


def render(size):
    big = size * SS
    px = [[(0, 0, 0, 0)] * big for _ in range(big)]
    cx = cy = big / 2.0
    r_out = big * 0.355
    r_in = big * 0.245
    for y in range(big):
        for x in range(big):
            dx, dy = x + 0.5 - cx, y + 0.5 - cy
            d = math.hypot(dx, dy)
            a = math.degrees(math.atan2(dy, dx)) % 360
            col = None
            if d <= r_out:
                col = BG
            if r_in <= d <= r_out:
                t = (d - r_in) / (r_out - r_in)
                col = tuple(int(GOLD[i] + (GOLD2[i] - GOLD[i]) * (1 - abs(t * 2 - 1))) for i in range(3))
            # 准星四向刻线
            if r_out < d <= big * 0.44 and (min(abs(a - 0) , abs(a - 90), abs(a - 180), abs(a - 270)) < 3.2
                                            or min(abs(a - 360), abs(a - 0)) < 3.2):
                col = GOLD2
            if d <= r_in:
                col = DARK if d < r_in * 0.62 else BG
            if col is None:
                px[y][x] = (0, 0, 0, 0)
            else:
                px[y][x] = (col[0], col[1], col[2], 255)
    # 降采样
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            rs = gs = bs = as_ = 0
            for j in range(SS):
                for i in range(SS):
                    p = px[y * SS + j][x * SS + i]
                    rs += p[0]; gs += p[1]; bs += p[2]; as_ += p[3]
            n = SS * SS
            a = as_ // n
            if a == 0:
                row += [0, 0, 0, 0]
            else:
                row += [rs // n, gs // n, bs // n, a]
        rows.append(row)
    return rows


def main():
    for folder, size in SIZES.items():
        rows = render(size)
        png(os.path.join(BASE, folder, "ic_launcher.png"), size, size, rows)
        print("wrote", folder, size)


if __name__ == "__main__":
    main()
