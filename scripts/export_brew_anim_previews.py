#!/usr/bin/env python3
"""Write docs/previews PNGs for Freeze grace and Venting brew-screen animations."""

from __future__ import annotations

import math
import re
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "previews"
WIND_C = REPO / "src/display/ui/default/lvgl/images/ui_img_783005998.c"
SS = 4
ROOT_W, ROOT_H = 120, 96
WAVES_W, WAVES_H = 44, 26
BASE_X = (ROOT_W - WAVES_W) // 2
BASE_Y = (ROOT_H - WAVES_H) // 2
WIND_ZOOM = 176
FRAMES = 8
SCALE = 4


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def stroke_cover(dist, half):
    return clamp(0.5 - (abs(dist) - half), 0.0, 1.0)


def sd_segment(px, py, ax, ay, bx, by):
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    denom = bax * bax + bay * bay
    h = 0.0 if denom == 0 else clamp((pax * bax + pay * bay) / denom, 0.0, 1.0)
    return math.hypot(pax - bax * h, pay - bay * h)


def waves_alpha(px, py, W, H):
    half = 1.35
    cov = 0.0
    rows = [(H * 0.30, 3.4, W * 0.62), (H * 0.55, 2.1, W * 0.66), (H * 0.80, 0.9, W * 0.72)]
    margin = 3.0
    for cy, amp, wl in rows:
        best = 1e9
        prev = None
        for i in range(65):
            x = margin + (W - 2 * margin) * i / 64
            y = cy + amp * math.sin(2.0 * math.pi * (x - margin) / wl)
            if prev is not None:
                best = min(best, sd_segment(px, py, prev[0], prev[1], x, y))
            prev = (x, y)
        cov = max(cov, stroke_cover(best, half))
    return clamp(cov, 0.0, 1.0)


def ease(u):
    return u * u * (3.0 - 2.0 * u)


def ping_pong(t_ms, fwd, back, v0, v1, use_ease):
    period = fwd + back
    t = t_ms % period if period else 0
    if t <= fwd:
        u = (t / fwd) if fwd else 1.0
    else:
        u = ((t - fwd) / back) if back else 1.0
        v0, v1 = v1, v0
    if use_ease:
        u = ease(u)
    return v0 + (v1 - v0) * u


def grad_rgb(stops, t_ms, cycle_ms, smooth):
    step, max_pos = 256, (len(stops) - 1) * 256
    period = cycle_ms * 2
    t = t_ms % period if period else 0
    u = (t / cycle_ms) if t < cycle_ms else (1.0 - (t - cycle_ms) / cycle_ms)
    if smooth:
        u = ease(u)
    pos = int(clamp(round(u * max_pos), 0, max_pos))
    seg, frac = pos // step, pos % step
    c0, c1 = stops[seg], stops[min(seg + 1, len(stops) - 1)]
    return (
        ((c0 >> 16) & 0xFF) + (((c1 >> 16) & 0xFF) - ((c0 >> 16) & 0xFF)) * frac // 255,
        ((c0 >> 8) & 0xFF) + (((c1 >> 8) & 0xFF) - ((c0 >> 8) & 0xFF)) * frac // 255,
        (c0 & 0xFF) + ((c1 & 0xFF) - (c0 & 0xFF)) * frac // 255,
    )


def sample_alpha(fn, W, H, x, y):
    acc = 0.0
    for sy in range(SS):
        for sx in range(SS):
            acc += fn(x + (sx + 0.5) / SS, y + (sy + 0.5) / SS, W, H)
    return clamp(int(round(255.0 * acc / (SS * SS))), 0, 255)


def raster_glyph(fn, W, H) -> list[list[tuple[int, int, int, int]]]:
    rows = []
    for y in range(H):
        row = []
        for x in range(W):
            a = sample_alpha(fn, W, H, x, y)
            row.append((255, 255, 255, a) if a else (0, 0, 0, 0))
        rows.append(row)
    return rows


def load_wind() -> list[list[tuple[int, int, int, int]]]:
    text = WIND_C.read_text(encoding="utf-8")
    w = int(re.search(r"\.header\.w = (\d+)", text).group(1))
    h = int(re.search(r"\.header\.h = (\d+)", text).group(1))
    block = text.split("_data[]", 1)[1].split("};", 1)[0]
    raw = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", block)]
    rows = []
    i = 0
    for _y in range(h):
        row = []
        for _x in range(w):
            i += 2
            a = raw[i]
            i += 1
            row.append((255, 255, 255, a) if a else (0, 0, 0, 0))
        rows.append(row)
    return rows


def scale_glyph(rows, size):
    h, w = len(rows), len(rows[0])
    out = []
    for oy in range(size):
        sy = oy * h // size
        row = []
        for ox in range(size):
            sx = ox * w // size
            row.append(rows[sy][sx])
        out.append(row)
    return out


def blend_pixel(dst, src, opa):
    sr, sg, sb, sa = src
    if sa == 0 or opa == 0:
        return dst
    sa = sa * opa // 255
    dr, dg, db, da = dst
    if da == 0:
        return (sr, sg, sb, sa)
    ia = 255 - sa
    return (
        (sr * sa + dr * ia) // 255,
        (sg * sa + dg * ia) // 255,
        (sb * sa + db * ia) // 255,
        min(255, sa + da * ia // 255),
    )


def paste(canvas, glyph, x0, y0, rgb, opa):
    for y, row in enumerate(glyph):
        cy = y0 + y
        if cy < 0 or cy >= ROOT_H:
            continue
        for x, px in enumerate(row):
            cx = x0 + x
            if cx < 0 or cx >= ROOT_W:
                continue
            r, g, b, a = px
            if not a:
                continue
            tinted = (rgb[0], rgb[1], rgb[2], a)
            canvas[cy][cx] = blend_pixel(canvas[cy][cx], tinted, opa)


def blank_canvas():
    return [[(0, 0, 0, 0) for _ in range(ROOT_W)] for _ in range(ROOT_H)]


def freeze_frame(t_ms, waves):
    x = int(round(ping_pong(t_ms, 2000, 2000, BASE_X - 5, BASE_X + 5, True)))
    opa = int(round(ping_pong(t_ms, 1600, 1600, 150, 255, True)))
    rgb = grad_rgb([0xA5F2F3, 0xE0E0FF, 0x7FFFD4], t_ms, 2600, True)
    c = blank_canvas()
    paste(c, waves, x, BASE_Y, rgb, opa)
    return c


def vent_frame(t_ms, wind):
    draw = round(len(wind) * WIND_ZOOM / 256)
    scaled = scale_glyph(wind, draw)
    x = (ROOT_W - draw) // 2
    y = (ROOT_H - draw) // 2
    opa = int(round(ping_pong(t_ms, 220, 180, 110, 255, False)))
    rgb = grad_rgb([0xFFFFFF, 0xD3D3D3, 0xA0C0F0], t_ms, 280, False)
    c = blank_canvas()
    paste(c, scaled, x, y, rgb, opa)
    return c


def write_png(path: Path, pixels: list[list[tuple[int, int, int, int]]]) -> None:
    h, w = len(pixels), len(pixels[0])
    rows = []
    for row in pixels:
        buf = bytearray()
        for r, g, b, a in row:
            buf.extend((r, g, b, a))
        rows.append(bytes(buf))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + rows[i] for i in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def upscale(canvas, factor):
    h, w = len(canvas), len(canvas[0])
    out = []
    for oy in range(h * factor):
        row = []
        for ox in range(w * factor):
            row.append(canvas[oy // factor][ox // factor])
        out.append(row)
    return out


def strip(frames):
    cell = upscale(frames[0], SCALE)
    ch, cw = len(cell), len(cell[0])
    out = [[(0, 0, 0, 0) for _ in range(cw * len(frames))] for _ in range(ch)]
    for i, fr in enumerate(frames):
        up = upscale(fr, SCALE)
        for y, row in enumerate(up):
            for x, px in enumerate(row):
                out[y][i * cw + x] = px
    return out


def glyph_png(path, fn, W, H):
    write_png(path, upscale(raster_glyph(fn, W, H), 8))


def main():
    if not WIND_C.is_file():
        sys.exit(f"missing {WIND_C}")
    waves = raster_glyph(waves_alpha, WAVES_W, WAVES_H)
    wind = load_wind()
    glyph_png(OUT / "ui_img_steamwaves.png", waves_alpha, WAVES_W, WAVES_H)
    freeze = [freeze_frame(int(i * 4000 / FRAMES), waves) for i in range(FRAMES)]
    vent = [vent_frame(int(i * 400 / FRAMES), wind) for i in range(FRAMES)]
    write_png(OUT / "brew_freeze_grace.png", strip(freeze))
    write_png(OUT / "brew_venting.png", strip(vent))
    for p in (
        OUT / "ui_img_steamwaves.png",
        OUT / "brew_freeze_grace.png",
        OUT / "brew_venting.png",
    ):
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
