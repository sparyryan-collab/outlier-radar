#!/usr/bin/env python3
"""Draw the app icons (red tile, white flame) as PNGs with the standard library only.

Usage: python web/scanner/make_icons.py      writes outlier-radar/static/icons/*.png
"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "outlier-radar" / "static" / "icons"
RED = (255, 0, 51)
SS = 4   # samples per pixel along each axis (anti-aliasing)


def flame(steps=24):
    """The logo's flame (same path as the page's SVG, 24x24 box) as a polygon."""
    def cubic(p0, p1, p2, p3):
        return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t * t * c + t ** 3 * d for a, b, c, d in zip(p0, p1, p2, p3))
                for t in (i / steps for i in range(1, steps + 1))]
    pts = [(12, 2)]
    pts += cubic((12, 2), (13, 6), (18.5, 8.5), (18.5, 15))
    pts += [(12 + 6.5 * math.cos(a), 15 + 6.5 * math.sin(a)) for a in (math.pi * i / (steps * 2) for i in range(1, steps * 2 + 1))]
    pts += cubic((5.5, 15), (5.5, 11.5), (7.7, 9.4), (8.8, 8.4))
    pts += cubic((8.8, 8.4), (8.8, 10.7), (9.8, 12), (11.1, 12))
    pts += cubic((11.1, 12), (11.1, 8.6), (10.1, 5.4), (12, 2))
    return pts


def coverage(size, poly):
    """Fraction of each pixel inside the polygon (even-odd scanline fill on a supersampled grid)."""
    cov = [[0] * size for _ in range(size)]
    edges = list(zip(poly, poly[1:] + poly[:1]))
    for sy in range(size * SS):
        y = (sy + 0.5) / SS
        xs = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0) for (x0, y0), (x1, y1) in edges if (y0 <= y < y1) or (y1 <= y < y0))
        row = cov[sy // SS]
        for a, b in zip(xs[0::2], xs[1::2]):
            for sx in range(max(0, math.ceil(a * SS - 0.5)), min(size * SS, math.floor(b * SS - 0.5) + 1)):
                row[sx // SS] += 1
    return [[v / (SS * SS) for v in row] for row in cov]


def rounded_rect(size, radius):
    pts = []
    for cx, cy, a0 in ((size - radius, radius, -90), (size - radius, size - radius, 0), (radius, size - radius, 90), (radius, radius, 180)):
        pts += [(cx + radius * math.cos(math.radians(a0 + i * 90 / 16)), cy + radius * math.sin(math.radians(a0 + i * 90 / 16))) for i in range(17)]
    return pts


def png(path, size, full_bleed, flame_scale):
    tile = [[1.0] * size for _ in range(size)] if full_bleed else coverage(size, rounded_rect(size, size * 0.22))
    k = size * flame_scale / 24
    off = (size - 24 * k) / 2
    fire = coverage(size, [(off + x * k, off + y * k + size * 0.01) for x, y in flame()])
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            f, a = fire[y][x], tile[y][x]
            raw += bytes(round(c * (1 - f) + 255 * f) for c in RED) + bytes([round(255 * a)])
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    png(OUT / "icon-192.png", 192, False, 0.72)
    png(OUT / "icon-512.png", 512, False, 0.72)
    png(OUT / "icon-maskable-512.png", 512, True, 0.56)   # a phone may crop it to a circle: keep the flame in the middle
    png(OUT / "apple-touch-icon.png", 180, True, 0.64)
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
