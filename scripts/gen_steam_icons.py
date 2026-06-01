#!/usr/bin/env python3
"""
Generate the brew-screen status icons for GaggiMate (LVGL 8.3).

Icons produced (all white + alpha, so the theme/per-state RECOLOR tints them):
    ui_img_steamcup       side-view mug from icons/brew-steam-cup.svg (thermal states)
    ui_img_steamwisp_l/m/r  three steam curves from the same SVG (animated above the cup)
    ui_img_steamwaves     stacked water waves, calming (Freeze grace "settling")

Output: SquareLine-Studio-compatible C files (LV_IMG_CF_TRUE_COLOR_ALPHA).
With LV_COLOR_DEPTH=16 and LV_COLOR_16_SWAP=0 each pixel is 3 bytes:
    [RGB565 low byte, RGB565 high byte, alpha]
The RGB is stored solid white (0xFFFF); only the alpha channel carries the
shape, so lv_obj_set_style_img_recolor turns the whole glyph any state colour.

Cup + wisps: SVG master at docs/assets/espresso-cup.svg (stroke groups or potrace fills).
Waves: legacy SDF when no SVG override. If the SVG is missing, cup/wisps fall back to SDF.

Regenerate everything (writes straight into the LVGL images folder):
    python3 scripts/gen_steam_icons.py src/display/ui/default/lvgl/images
Preview only (ASCII, no files written):
    python3 scripts/gen_steam_icons.py . --preview
Docs PNG previews (Freeze grace / Venting animation strips + waves glyph):
    python3 scripts/export_brew_anim_previews.py
    # or: python3 scripts/gen_steam_icons.py --export-previews
Optional master SVG:
    python3 scripts/gen_steam_icons.py OUT --svg icons/brew-steam-cup.svg
"""

from __future__ import annotations

import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SS = 4  # supersampling factor per axis (higher = smoother edges)
STROKE_HALF = 1.4
ICON_PAD = 3.0
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SVG = REPO_ROOT / "docs" / "assets" / "espresso-cup.svg"

# Segment = (ax, ay, bx, by) in icon pixel space
Segment = tuple[float, float, float, float]


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def smooth_cover(dist):
    """Anti-aliased inside coverage from a signed distance (negative = inside)."""
    return clamp(0.5 - dist, 0.0, 1.0)


def stroke_cover(dist, half):
    """Coverage of a stroke of half-width `half` centred on a curve at `dist`."""
    return smooth_cover(abs(dist) - half)


def sd_circle(px, py, cx, cy, r):
    return math.hypot(px - cx, py - cy) - r


def sd_ellipse(px, py, cx, cy, rx, ry):
    nx = (px - cx) / rx
    ny = (py - cy) / ry
    return (math.hypot(nx, ny) - 1.0) * min(rx, ry)


def sd_segment(px, py, ax, ay, bx, by):
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    denom = bax * bax + bay * bay
    h = 0.0 if denom == 0 else clamp((pax * bax + pay * bay) / denom, 0.0, 1.0)
    return math.hypot(pax - bax * h, pay - bay * h)


def segments_nearest(px, py, segments: list[Segment]) -> float:
    best = 1e9
    for ax, ay, bx, by in segments:
        d = sd_segment(px, py, ax, ay, bx, by)
        if d < best:
            best = d
    return best


def segments_alpha(px, py, segments: list[Segment], half: float = STROKE_HALF) -> float:
    if not segments:
        return 0.0
    return clamp(stroke_cover(segments_nearest(px, py, segments), half), 0.0, 1.0)


# ---------- SVG path parsing (M/L/C/Q/H/V/Z, relative + absolute) ----------

_PATH_RE = re.compile(
    r"([MmLlHhVvCcQqZz])|([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)"
)


def _path_tokens(d: str) -> list[str]:
    return _PATH_RE.findall(d)


def _sample_line(p0, p1, steps: int = 8) -> list[tuple[float, float]]:
    return [
        (p0[0] + (p1[0] - p0[0]) * i / steps, p0[1] + (p1[1] - p0[1]) * i / steps)
        for i in range(steps + 1)
    ]


def _sample_quad(p0, p1, p2, steps: int = 12) -> list[tuple[float, float]]:
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
        y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts


def _sample_cubic(p0, p1, p2, p3, steps: int = 16) -> list[tuple[float, float]]:
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        x = (
            u ** 3 * p0[0]
            + 3 * u * u * t * p1[0]
            + 3 * u * t * t * p2[0]
            + t ** 3 * p3[0]
        )
        y = (
            u ** 3 * p0[1]
            + 3 * u * u * t * p1[1]
            + 3 * u * t * t * p2[1]
            + t ** 3 * p3[1]
        )
        pts.append((x, y))
    return pts


def _polyline_to_segments(pts: list[tuple[float, float]]) -> list[Segment]:
    segs: list[Segment] = []
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        segs.append((ax, ay, bx, by))
    return segs


def path_d_to_segments(d: str) -> list[Segment]:
    tokens = _path_tokens(d)
    segs: list[Segment] = []
    i = 0
    cmd = "M"
    cx = cy = 0.0
    start_x = start_y = 0.0
    sub_start = (0.0, 0.0)

    def read_float():
        nonlocal i
        val = float(tokens[i][1])
        i += 1
        return val

    def append_poly(pts: list[tuple[float, float]]):
        segs.extend(_polyline_to_segments(pts))

    while i < len(tokens):
        tok = tokens[i]
        if tok[0]:
            cmd = tok[0]
            i += 1
            if cmd == "Z" or cmd == "z":
                append_poly([(cx, cy), sub_start])
                cx, cy = sub_start
            continue

        rel = cmd.islower()
        c = cmd.upper()

        if c == "M":
            x = read_float()
            y = read_float()
            if rel:
                x += cx
                y += cy
            cx, cy = x, y
            start_x, start_y = x, y
            sub_start = (x, y)
            while i < len(tokens) and not tokens[i][0]:
                x2 = read_float()
                y2 = read_float()
                if rel:
                    x2 += cx
                    y2 += cy
                append_poly([(cx, cy), (x2, y2)])
                cx, cy = x2, y2
        elif c == "L":
            x = read_float()
            y = read_float()
            if rel:
                x += cx
                y += cy
            append_poly([(cx, cy), (x, y)])
            cx, cy = x, y
        elif c == "H":
            x = read_float()
            if rel:
                x += cx
            append_poly([(cx, cy), (x, cy)])
            cx = x
        elif c == "V":
            y = read_float()
            if rel:
                y += cy
            append_poly([(cx, cy), (cx, y)])
            cy = y
        elif c == "Q":
            x1 = read_float()
            y1 = read_float()
            x = read_float()
            y = read_float()
            if rel:
                x1 += cx
                y1 += cy
                x += cx
                y += cy
            append_poly(_sample_quad((cx, cy), (x1, y1), (x, y)))
            cx, cy = x, y
        elif c == "C":
            x1 = read_float()
            y1 = read_float()
            x2 = read_float()
            y2 = read_float()
            x = read_float()
            y = read_float()
            if rel:
                x1 += cx
                y1 += cy
                x2 += cx
                y2 += cy
                x += cx
                y += cy
            append_poly(_sample_cubic((cx, cy), (x1, y1), (x2, y2), (x, y)))
            cx, cy = x, y
        else:
            raise ValueError(f"unsupported path command: {cmd}")

    return segs


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# Polygon = closed outline in SVG/viewBox coordinates (filled art, e.g. potrace).
Polygon = list[tuple[float, float]]
FILL_EDGE = 0.55  # edge softening for filled shapes (icon pixel units after scale)


def _parse_transform(transform: str | None):
    """Parse translate(tx,ty) scale(sx,sy) from potrace-style exports."""
    tx = ty = 0.0
    sx = sy = 1.0
    if not transform:
        return lambda x, y: (x, y)
    for m in re.finditer(
        r"(translate|scale)\s*\(\s*([-\d.eE+]+)(?:\s*,\s*([-\d.eE+]+))?\s*\)",
        transform,
    ):
        op = m.group(1)
        a = float(m.group(2))
        b = float(m.group(3)) if m.group(3) is not None else 0.0
        if op == "translate":
            tx, ty = a, b
        else:
            sx, sy = a, b if m.group(3) is not None else a
    return lambda x, y: (tx + sx * x, ty + sy * y)


def segments_to_polygon(segs: list[Segment]) -> Polygon:
    if not segs:
        return []
    poly: Polygon = [(segs[0][0], segs[0][1])]
    for _ax, _ay, bx, by in segs:
        if not poly or (bx, by) != poly[-1]:
            poly.append((bx, by))
    if len(poly) > 2 and poly[0] != poly[-1]:
        poly.append(poly[0])
    return poly


def simplify_polygon(poly: Polygon, max_pts: int = 280) -> Polygon:
    """Decimate potrace outlines so rasterize stays fast at icon size."""
    if len(poly) <= max_pts:
        return poly
    step = max(1, len(poly) // max_pts)
    slim = poly[::step]
    if slim[0] != slim[-1]:
        slim.append(slim[0])
    return slim


def _point_in_polygon(px: float, py: float, poly: Polygon) -> bool:
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (
            px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def _polygon_signed_dist(px: float, py: float, poly: Polygon) -> float:
    inside = _point_in_polygon(px, py, poly)
    best = 1e9
    for i in range(len(poly) - 1):
        ax, ay = poly[i]
        bx, by = poly[i + 1]
        d = sd_segment(px, py, ax, ay, bx, by)
        if d < best:
            best = d
    return -best if inside else best


def polygons_alpha(
    px: float,
    py: float,
    polygons: list[Polygon],
    bboxes: list[tuple[float, float, float, float]] | None = None,
) -> float:
    if not polygons:
        return 0.0
    best = 1e9
    for idx, poly in enumerate(polygons):
        if len(poly) < 3:
            continue
        if bboxes is not None:
            min_x, min_y, max_x, max_y = bboxes[idx]
            if px < min_x - 2 or px > max_x + 2 or py < min_y - 2 or py > max_y + 2:
                continue
        d = _polygon_signed_dist(px, py, poly)
        if d < best:
            best = d
    if best > 1e8:
        return 0.0
    return clamp(smooth_cover(best - FILL_EDGE), 0.0, 1.0)


def polygons_bbox(polygons: list[Polygon]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for poly in polygons:
        for x, y in poly:
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def normalize_polygons(
    polygons: list[Polygon],
    target_h: float,
    pad: float = ICON_PAD,
) -> tuple[list[Polygon], int, int]:
    min_x, min_y, max_x, max_y = polygons_bbox(polygons)
    h = max_y - min_y
    scale = target_h / h if h > 0 else 1.0
    ox = pad - min_x * scale
    oy = pad - min_y * scale
    scaled: list[Polygon] = [
        [(x * scale + ox, y * scale + oy) for x, y in poly] for poly in polygons
    ]
    W = int(math.ceil((max_x - min_x) * scale + 2 * pad))
    H = int(math.ceil((max_y - min_y) * scale + 2 * pad))
    return scaled, W, H


def icon_from_polygons(
    name: str,
    polygons: list[Polygon],
    target_h: float,
    comment: str,
) -> tuple[str, int, int, object, str]:
    slim = [simplify_polygon(p) for p in polygons]
    norm, W, H = normalize_polygons(slim, target_h)
    bboxes = [polygons_bbox([p]) for p in norm]

    def fn(px, py, _W, _H, polys=norm, boxes=bboxes):
        return polygons_alpha(px, py, polys, boxes)

    return (name, W, H, fn, comment)


def load_svg_stroke_groups(svg_path: Path) -> dict[str, list[Segment]]:
    tree = ET.parse(svg_path)
    root = tree.getroot()
    groups: dict[str, list[Segment]] = {}

    for elem in root.iter():
        if _local_tag(elem.tag) != "g":
            continue
        gid = elem.get("id")
        if not gid:
            continue
        segs: list[Segment] = []
        for child in elem:
            if _local_tag(child.tag) == "path" and child.get("d"):
                segs.extend(path_d_to_segments(child.get("d", "")))
        if segs:
            groups[gid] = segs

    return groups


def _svg_uses_fill(root) -> bool:
    for elem in root.iter():
        tag = _local_tag(elem.tag)
        if tag not in ("path", "g"):
            continue
        fill = (elem.get("fill") or "").lower()
        if fill not in ("", "none", "inherit"):
            return True
    return False


def load_espresso_cup_potrace(svg_path: Path) -> dict[str, list[Polygon]]:
    """Split docs/assets/espresso-cup.svg (potrace) into cup + three steam wisps."""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    xf = _parse_transform(None)
    path_elems: list = []
    for elem in root.iter():
        if _local_tag(elem.tag) == "g" and elem.get("transform"):
            xf = _parse_transform(elem.get("transform"))
            path_elems = [c for c in elem if _local_tag(c.tag) == "path"]
            break

    polygons: list[Polygon] = []
    for p in path_elems:
        segs = path_d_to_segments(p.get("d", ""))
        poly = [xf(x, y) for x, y in segments_to_polygon(segs)]
        if len(poly) >= 3:
            polygons.append(poly)

    if len(polygons) < 6:
        raise ValueError(f"expected 6 potrace paths in {svg_path}, got {len(polygons)}")

    # Potrace steam paths 0,1,2 → screen left→right must be 3, 1, 2 (path indices 2, 0, 1).
    return {
        "steam-left": [polygons[2]],
        "steam-mid": [polygons[0]],
        "steam-right": [polygons[1]],
        "cup": [polygons[3], polygons[4], polygons[5]],
    }


def load_svg_paths(svg_path: Path) -> dict[str, list[Segment]]:
    groups = load_svg_stroke_groups(svg_path)
    if not groups:
        raise ValueError(f"no <g id=...> stroke groups in {svg_path}")
    return groups


def segments_bbox(segments: list[Segment]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for ax, ay, bx, by in segments:
        xs.extend((ax, bx))
        ys.extend((ay, by))
    return min(xs), min(ys), max(xs), max(ys)


def normalize_segments(
    segments: list[Segment], pad: float = ICON_PAD
) -> tuple[list[Segment], int, int]:
    min_x, min_y, max_x, max_y = segments_bbox(segments)
    ox = pad - min_x
    oy = pad - min_y
    W = int(math.ceil(max_x - min_x + 2 * pad))
    H = int(math.ceil(max_y - min_y + 2 * pad))
    shifted = [
        (ax + ox, ay + oy, bx + ox, by + oy) for ax, ay, bx, by in segments
    ]
    return shifted, W, H


def icon_from_segments(
    name: str, segments: list[Segment], comment: str
) -> tuple[str, int, int, object, str]:
    norm, W, H = normalize_segments(segments)

    def fn(px, py, _W, _H, segs=norm):
        return segments_alpha(px, py, segs)

    return (name, W, H, fn, comment)


def icons_from_svg(svg_path: Path) -> list[tuple[str, int, int, object, str]]:
    root = ET.parse(svg_path).getroot()
    src = f"SVG ({svg_path.relative_to(REPO_ROOT)})"
    mapping = [
        ("cup", "ui_img_steamcup", 42.0),
        ("steam-left", "ui_img_steamwisp_l", 22.0),
        ("steam-mid", "ui_img_steamwisp_m", 22.0),
        ("steam-right", "ui_img_steamwisp_r", 22.0),
    ]

    if _svg_uses_fill(root):
        groups = load_espresso_cup_potrace(svg_path)
        return [
            icon_from_polygons(sym, groups[gid], target_h, f"{src} / {gid}")
            for gid, sym, target_h in mapping
        ]

    stroke_groups = load_svg_stroke_groups(svg_path)
    if not stroke_groups:
        raise ValueError(f"no usable paths in {svg_path}")
    icons = []
    for gid, sym, _target_h in mapping:
        if gid not in stroke_groups:
            raise KeyError(f"SVG group id={gid!r} missing in {svg_path}")
        icons.append(icon_from_segments(sym, stroke_groups[gid], f"{src} / {gid}"))
    return icons


# ---------- Legacy SDF cup + wisp (fallback) ----------

def cup_alpha(px, py, W, H):
    cx = W / 2.0 - 3.0
    rim_y = 9.0
    base_y = H - 11.0
    rim_rx, rim_ry = 11.0, 2.5
    base_rx = 8.0
    half = 1.45
    cov = 0.0
    if (py - rim_y) / rim_ry <= 0.12:
        cov = max(cov, stroke_cover(sd_ellipse(px, py, cx, rim_y, rim_rx, rim_ry), half))
    cov = max(cov, stroke_cover(sd_segment(px, py, cx - rim_rx, rim_y, cx - base_rx, base_y), half))
    cov = max(cov, stroke_cover(sd_segment(px, py, cx + rim_rx, rim_y, cx + base_rx, base_y), half))
    if py >= base_y - 0.5:
        cov = max(cov, stroke_cover(sd_ellipse(px, py, cx, base_y, base_rx, 3.2), half))
    hx, hy, hr = cx + rim_rx + 2.5, (rim_y + base_y) / 2.0, 5.6
    if px >= hx - 1.5:
        cov = max(cov, stroke_cover(sd_circle(px, py, hx, hy, hr), half - 0.15))
    if py >= base_y + 1.5:
        cov = max(cov, stroke_cover(sd_ellipse(px, py, cx, base_y + 6.5, W / 2.0 - 2.0, 2.4), half))
    return clamp(cov, 0.0, 1.0)


def wisp_alpha(px, py, W, H):
    N = 96
    best = 1e9
    best_t = 0.0
    pts = []
    for i in range(N + 1):
        t = i / N
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
    half = 1.3 - 0.6 * best_t
    cover = smooth_cover(best - half)
    taper = 1.0 - 0.45 * best_t
    return clamp(cover * taper, 0.0, 1.0)


def waves_alpha(px, py, W, H):
    half = 1.35
    cov = 0.0
    rows = [
        (H * 0.30, 3.4, W * 0.62),
        (H * 0.55, 2.1, W * 0.66),
        (H * 0.80, 0.9, W * 0.72),
    ]
    margin = 3.0
    for cy, amp, wl in rows:
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


SDF_ICONS = [
    ("ui_img_steamcup", 50, 42, cup_alpha, "generated/espresso-cup line-art (brew thermal states)"),
    ("ui_img_steamwisp", 14, 28, wisp_alpha, "generated/steam-wisp (animated above the cup)"),
]

WAVES_ICON = (
    "ui_img_steamwaves",
    44,
    26,
    waves_alpha,
    "generated/settling-waves (Freeze grace)",
)

LEGACY_WISP_FILE = "ui_img_steamwisp.c"


def build_icon_list(svg_path: Path | None) -> list[tuple[str, int, int, object, str]]:
    if svg_path is not None and svg_path.is_file():
        return icons_from_svg(svg_path) + [WAVES_ICON]
    return SDF_ICONS + [WAVES_ICON]


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
            px_bytes.append(0xFF)
            px_bytes.append(0xFF)
            px_bytes.append(a)
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
    per = 12 * 3
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
    path.write_text("\n".join(lines))
    return len(data)


def ascii_preview(W, H, fn, title):
    ink_threshold = 0.12
    half_ss = SS // 2
    samples_per_half = half_ss * SS
    print(f"--- {title} ({W}x{H}) ---")
    for y in range(H):
        row = []
        for x in range(W):
            top_acc = 0.0
            bottom_acc = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    a = fn(x + (sx + 0.5) / SS, y + (sy + 0.5) / SS, W, H)
                    if sy < half_ss:
                        top_acc += a
                    else:
                        bottom_acc += a
            top_ink = (top_acc / samples_per_half) > ink_threshold
            bottom_ink = (bottom_acc / samples_per_half) > ink_threshold
            if top_ink and bottom_ink:
                row.append("█")
            elif top_ink:
                row.append("▀")
            elif bottom_ink:
                row.append("▄")
            else:
                row.append(" ")
        print("".join(row))
    print()


def export_preview_png(path: Path, W: int, H: int, fn) -> None:
    """Write an upscaled RGBA PNG for docs/previews (stdlib only)."""
    import struct
    import zlib

    scale = 8
    out_w, out_h = W * scale, H * scale
    rows = []
    for oy in range(out_h):
        y = oy // scale
        row = bytearray()
        for ox in range(out_w):
            x = ox // scale
            acc = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    acc += fn(x + (sx + 0.5) / SS, y + (sy + 0.5) / SS, W, H)
            a = clamp(int(round(255.0 * acc / (SS * SS))), 0, 255)
            row.extend((255, 255, 255, a))
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + rows[i] for i in range(out_h))
    compressed = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", out_w, out_h, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


PREVIEW_DIR = REPO_ROOT / "docs" / "previews"
BREW_ANIM_PREVIEW_SCRIPT = REPO_ROOT / "scripts" / "export_brew_anim_previews.py"


def parse_cli(argv: list[str]) -> tuple[Path, Path | None, bool, bool, bool]:
    out_dir = Path(".")
    svg_path: Path | None = DEFAULT_SVG if DEFAULT_SVG.is_file() else None
    preview_only = False
    export_cup_png = False
    export_previews = False
    args = list(argv)
    if "--preview" in argv:
        preview_only = True
        args = [a for a in args if a != "--preview"]
    if "--export-cup-png" in argv:
        export_cup_png = True
        args = [a for a in args if a != "--export-cup-png"]
    if "--export-previews" in argv:
        export_previews = True
        args = [a for a in args if a != "--export-previews"]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--svg" and i + 1 < len(args):
            svg_path = Path(args[i + 1])
            i += 2
            continue
        if a.startswith("--svg="):
            svg_path = Path(a.split("=", 1)[1])
        elif not a.startswith("-"):
            out_dir = Path(a)
        i += 1
    return out_dir, svg_path, preview_only, export_cup_png, export_previews


if __name__ == "__main__":
    out_dir, svg_path, preview_only, export_cup_png, export_previews = parse_cli(sys.argv[1:])

    if export_previews:
        import subprocess

        subprocess.run([sys.executable, str(BREW_ANIM_PREVIEW_SCRIPT)], check=True)
        sys.exit(0)

    icons = build_icon_list(svg_path)

    if not preview_only:
        for name, W, H, fn, comment in icons:
            ascii_preview(W, H, fn, name)

    if export_cup_png:
        for name, W, H, fn, _ in icons:
            if name == "ui_img_steamcup":
                png_path = PREVIEW_DIR / "ui_img_steamcup.png"
                export_preview_png(png_path, W, H, fn)
                print(f"wrote {png_path}")
                break

    if preview_only:
        sys.exit(0)

    out_dir.mkdir(parents=True, exist_ok=True)
    emitted = {name for name, *_ in icons}
    legacy_wisp = out_dir / LEGACY_WISP_FILE
    if "ui_img_steamwisp" not in emitted and legacy_wisp.is_file():
        legacy_wisp.unlink()
        print(f"removed {legacy_wisp.name}")

    for name, W, H, fn, comment in icons:
        data = rasterize(W, H, fn)
        n = emit_c(out_dir / f"{name}.c", name, W, H, data, comment)
        assert n == W * H * 3, (name, n, W * H * 3)
        print(f"{name}.c  {W}x{H}  bytes={n}")
