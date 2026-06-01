#!/usr/bin/env python3
"""
Generate the brew-screen status icons for GaggiMate (LVGL 8.3).

Icons produced (all white + alpha, so the theme/per-state RECOLOR tints them):
    ui_img_steamcup    line-art espresso cup + saucer  (thermal states)
    ui_img_steamwisp   single steam curl               (animated above the cup)
    ui_img_steamwaves  stacked water waves, calming     (Freeze grace "settling")

Output: SquareLine-Studio-compatible C files (LV_IMG_CF_TRUE_COLOR_ALPHA).
With LV_COLOR_DEPTH=16 and LV_COLOR_16_SWAP=0 each pixel is 3 bytes:
    [RGB565 low byte, RGB565 high byte, alpha]
The RGB is stored solid white (0xFFFF); only the alpha channel carries the
shape, so lv_obj_set_style_img_recolor turns the whole glyph any state colour.

Dependency-free: shapes are drawn with signed-distance functions and
SS x SS supersampling, then emitted directly as C arrays.

Regenerate everything (writes straight into the LVGL images folder):
    python3 scripts/gen_steam_icons.py src/display/ui/default/lvgl/images
Preview only (ASCII, no files written): pass --preview as the 2nd arg.
"""

import math
import sys

SS = 4  # supersampling factor per axis (higher = smoother edges)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def smooth_cover(dist):
    """Anti-aliased inside coverage from a signed distance (negative = inside)."""
    return clamp(0.5 - dist, 0.0, 1.0)


def stroke_cover(dist, half):
    """Coverage of a stroke of half-width `half` centred on a curve at `dist`."""
    return smooth_cover(abs(dist) - half)


# ---------- SDF primitives (icon-local pixel coords) ----------

def sd_circle(px, py, cx, cy, r):
    return math.hypot(px - cx, py - cy) - r


def sd_ellipse(px, py, cx, cy, rx, ry):
    """Cheap signed distance to an ellipse boundary (negative inside)."""
    nx = (px - cx) / rx
    ny = (py - cy) / ry
    return (math.hypot(nx, ny) - 1.0) * min(rx, ry)


def sd_segment(px, py, ax, ay, bx, by):
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    denom = bax * bax + bay * bay
    h = 0.0 if denom == 0 else clamp((pax * bax + pay * bay) / denom, 0.0, 1.0)
    return math.hypot(pax - bax * h, pay - bay * h)


# ---------- Shape coverage functions ----------

def cup_alpha(px, py, W, H):
    """An elegant line-art espresso cup: open rim, gently curved body,
    a rounded base, an open C-handle on the right, and a wide saucer."""
    cx = W / 2.0 - 3.0          # nudge left to leave room for the handle
    rim_y = 9.0
    base_y = H - 11.0
    rim_rx, rim_ry = 12.5, 3.0  # the open top (an ellipse read as the opening)
    base_rx = 8.0
    half = 1.45                 # stroke half-width
    cov = 0.0

    # Rim ellipse — the cup's opening.
    cov = max(cov, stroke_cover(sd_ellipse(px, py, cx, rim_y, rim_rx, rim_ry), half))

    # Body: two slightly inward-tapering walls from the rim edge to the base.
    cov = max(cov, stroke_cover(sd_segment(px, py, cx - rim_rx, rim_y, cx - base_rx, base_y), half))
    cov = max(cov, stroke_cover(sd_segment(px, py, cx + rim_rx, rim_y, cx + base_rx, base_y), half))

    # Rounded base — only the lower arc of a flat ellipse.
    if py >= base_y - 0.5:
        cov = max(cov, stroke_cover(sd_ellipse(px, py, cx, base_y, base_rx, 3.2), half))

    # Handle: an open C-ring on the right side of the body.
    hx, hy, hr = cx + rim_rx + 2.5, (rim_y + base_y) / 2.0, 5.6
    if px >= hx - 1.5:  # keep the right-facing arc only
        cov = max(cov, stroke_cover(sd_circle(px, py, hx, hy, hr), half - 0.15))

    # Saucer: a wide shallow ellipse line beneath the cup.
    if py >= base_y + 1.5:
        cov = max(cov, stroke_cover(sd_ellipse(px, py, cx, base_y + 6.5, W / 2.0 - 2.0, 2.4), half))

    return clamp(cov, 0.0, 1.0)


def wisp_alpha(px, py, W, H):
    """A single wavy steam curl, thicker/brighter at the bottom, wispy at the top."""
    N = 96
    best = 1e9
    best_t = 0.0
    pts = []
    for i in range(N + 1):
        t = i / N                       # 0 at bottom, 1 at top
        y = H - t * H
        x = W / 2.0 + 3.0 * math.sin(2.0 * math.pi * t + 0.35)
        pts.append((x, y, t))
    for i in range(N):
        ax, ay, ta = pts[i]
        bx, by, tb = pts[i + 1]
        d = sd_segment(px, py, ax, ay, bx, by)
        if d < best:
            best = d
            best_t = (ta + tb) * 0.5
    half = 1.3 - 0.6 * best_t            # tapers from ~1.3 (bottom) to ~0.7 (top)
    cover = smooth_cover(best - half)
    taper = 1.0 - 0.45 * best_t          # extra wispy fade towards the top
    return clamp(cover * taper, 0.0, 1.0)


def waves_alpha(px, py, W, H):
    """Three stacked water waves whose amplitude shrinks from top to bottom —
    a wild surface settling into a calm stream. Animated with a slow sway in the
    UI; the static composition already reads top->bottom as 'calming down'."""
    half = 1.35
    cov = 0.0
    # (centre_y, amplitude, wavelength) — top is wildest, bottom nearly flat.
    rows = [
        (H * 0.30, 3.4, W * 0.62),
        (H * 0.55, 2.1, W * 0.66),
        (H * 0.80, 0.9, W * 0.72),
    ]
    margin = 3.0
    for cy, amp, wl in rows:
        # nearest distance to this sine curve, sampled as a polyline
        N = 64
        best = 1e9
        prev = None
        for i in range(N + 1):
            x = margin + (W - 2 * margin) * i / N
            y = cy + amp * math.sin(2.0 * math.pi * (x - margin) / wl)
            if prev is not None:
                d = sd_segment(px, py, prev[0], prev[1], x, y)
                if d < best:
                    best = d
            prev = (x, y)
        cov = max(cov, stroke_cover(best, half))
    return clamp(cov, 0.0, 1.0)


# ---------- Rasterize + emit ----------

def rasterize(W, H, fn):
    px_bytes = bytearray()
    for y in range(H):
        for x in range(W):
            acc = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    acc += fn(x + (sx + 0.5) / SS, y + (sy + 0.5) / SS, W, H)
            a = clamp(int(round(255.0 * acc / (SS * SS))), 0, 255)
            px_bytes.append(0xFF)  # RGB565 low  (white)
            px_bytes.append(0xFF)  # RGB565 high (white)
            px_bytes.append(a)     # alpha
    return bytes(px_bytes)


def emit_c(path, name, W, H, data, src_comment):
    lines = [
        "// This file was generated by GaggiMate (scripts/gen_steam_icons.py)",
        "// Brew-screen status indicator asset (white + alpha, theme-recolorable).",
        "// LVGL version: 8.3.11  /  LV_IMG_CF_TRUE_COLOR_ALPHA (16bpp, 3 bytes/px)",
        "",
        '#include "../ui.h"',
        "",
        "#ifndef LV_ATTRIBUTE_MEM_ALIGN",
        "#define LV_ATTRIBUTE_MEM_ALIGN",
        "#endif",
        "",
        f"// IMAGE DATA: {src_comment}",
        f"const LV_ATTRIBUTE_MEM_ALIGN uint8_t {name}_data[] = {{",
    ]
    per = 12 * 3  # 12 pixels per text row
    row = []
    for i, b in enumerate(data):
        row.append(f"0x{b:02X}")
        if (i + 1) % per == 0:
            lines.append("    " + ", ".join(row) + ",")
            row = []
    if row:
        lines.append("    " + ", ".join(row) + ",")
    lines += [
        "};",
        "",
        f"const lv_img_dsc_t {name} = {{",
        "    .header.always_zero = 0,",
        f"    .header.w = {W},",
        f"    .header.h = {H},",
        f"    .data_size = sizeof({name}_data),",
        "    .header.cf = LV_IMG_CF_TRUE_COLOR_ALPHA,",
        f"    .data = {name}_data,",
        "};",
        "",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return len(data)


def ascii_preview(W, H, fn, title):
    ramp = " .:-=+*#%@"
    print(f"--- {title} ({W}x{H}) ---")
    for y in range(H):
        line = []
        for x in range(W):
            acc = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    acc += fn(x + (sx + 0.5) / SS, y + (sy + 0.5) / SS, W, H)
            a = acc / (SS * SS)
            line.append(ramp[clamp(int(a * (len(ramp) - 1)), 0, len(ramp) - 1)])
        print("".join(line))
    print()


# Each icon: (symbol name, W, H, coverage fn, source comment)
ICONS = [
    ("ui_img_steamcup", 50, 42, cup_alpha, "generated/espresso-cup line-art (brew thermal states)"),
    ("ui_img_steamwisp", 14, 28, wisp_alpha, "generated/steam-wisp (animated above the cup)"),
    ("ui_img_steamwaves", 44, 26, waves_alpha, "generated/settling-waves (Freeze grace)"),
]


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    preview_only = "--preview" in sys.argv[2:]

    for name, W, H, fn, comment in ICONS:
        ascii_preview(W, H, fn, name)

    if preview_only:
        sys.exit(0)

    for name, W, H, fn, comment in ICONS:
        data = rasterize(W, H, fn)
        n = emit_c(f"{out_dir}/{name}.c", name, W, H, data, comment)
        assert n == W * H * 3, (name, n, W * H * 3)
        print(f"{name}.c  bytes={n} (expect {W*H*3})")
