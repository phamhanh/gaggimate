# Brew Screen Icon Redesign — Implementation Prompt

Project: GaggiMate (ESP32, LVGL 8.3.11). Created by Buddhist monk Br. Pham Hanh (Dharma Modder, Dutch), inventor of a custom fluidbed roaster and ultra-slow snow grinder.

## Task

Replace ALL state labels on the brew screen with custom icons and animations. Remove the "Selected profile" header label. The label `ui_BrewScreen_mainLabel3` currently shows these states — replace each with a purpose-built icon:

| State | Current text | Replace with |
|-------|-------------|--------------|
| Heating | `LV_SYMBOL_UP " Stabilizing"` | Animated flame/steam, orange |
| Cooling | `LV_SYMBOL_DOWN " Stabilizing"` | Animated cool wisp, blue |
| Ready | `"Ready to brew"` | Check icon, green pulse |
| Freeze grace | `"Freeze grace"` | Snowflake icon |
| Venting | `"Venting..."` | Vent/wind icon |
| Brewing | `""` (hidden) | Hidden |

## Where the current code is

- `src/display/ui/default/DefaultUI.cpp` ~lines 709–730 — the `lv_label_set_text` calls for each state on `ui_BrewScreen_mainLabel3`. The conditional at ~line 725 tells you whether temp is above or below target (heating vs cooling).
- `src/display/ui/default/lvgl/screens/ui_BrewScreen.c` lines 229–240 — definition of `ui_BrewScreen_mainLabel3` and its parent `ui_BrewScreen_contentPanel4`.

## Layout

`ui_BrewScreen_mainLabel3` sits at `y=-140` inside `ui_BrewScreen_contentPanel4` (360×360 circle panel). Below it is a blank gap of roughly one line, then `ui_BrewScreen_Label1` ("Selected profile") and `ui_BrewScreen_profileName`.

**Hide `ui_BrewScreen_Label1` permanently** by adding `lv_obj_add_flag(ui_BrewScreen_Label1, LV_OBJ_FLAG_HIDDEN)` in `DefaultUI.cpp` during screen init. Do not touch the `.c` file. The icon and animation can use the reclaimed space.

**Collision awareness:** `ui_BrewScreen_Image4` is an existing image widget on the brew screen (the volumetric/scale icon, switches between `ui_img_1424216268` / `ui_img_360122106` depending on brew mode). Position any new widgets so they do not overlap it.

## Asset pipeline

`scripts/png_gif_to_lvgl.py` already exists in the repo. Use it to convert any PNG or GIF sources into LVGL C arrays:

```bash
# Single PNG → recolorable white icon (40x40 default)
python3 scripts/png_gif_to_lvgl.py flame.png --white

# GIF → per-frame .c files + lv_animimg wiring printed to stdout
python3 scripts/png_gif_to_lvgl.py steam.gif --name steamwisp --size 40x40 --white

# PNG with white background → transparent
python3 scripts/png_gif_to_lvgl.py icon.png --bg 255,255,255 --white
```

Pixel format: `LV_COLOR_DEPTH=16`, `LV_IMG_CF_TRUE_COLOR_ALPHA` = 3 bytes/pixel `[RGB565_lo, RGB565_hi, alpha8]`. Store all icons as white+alpha so `lv_obj_set_style_img_recolor` can tint them per state at runtime.

Place generated `.c` files into `src/display/ui/default/lvgl/images/` and declare each in `src/display/ui/default/lvgl/ui.h` with `LV_IMG_DECLARE(name)`.

## Existing assets to reuse

Already declared in `ui.h` — use these before generating new ones:

| Symbol | Asset | Size |
|--------|-------|------|
| `ui_img_631115820` | check-40x40.png | 40×40 |
| `ui_img_783005998` | wind-80x80.png | 80×80 |
| `ui_img_545340440` | raindrops-80x80.png | 80×80 |
| `ui_img_1951499226` | thermometer-half-40x40.png | 40×40 |

## Animation

`LV_USE_ANIMIMG 1` is enabled. Animation helpers available in `ui_helpers.c/.h`:
- `_ui_anim_callback_set_opacity`
- `_ui_anim_callback_set_y`
- `_ui_anim_callback_set_image_frame`

Thermal state visual language:

| State | Icon color | Motion | Extra |
|-------|-----------|--------|-------|
| Heating | Orange `0xFFA500` | Fast rise, 600ms loop | — |
| Cooling | Deep blue `0x4040FF` | Slow drift, 1500ms loop | — |
| Ready | White → green `0x00FF00` | Calm curl, 2500ms loop | Check badge pulses green |
| Freeze grace | Cyan/white | Slow fade in/out | — |
| Venting | White | Quick flicker | — |

Clean up all animations and dynamically created widgets on state exit using `lv_anim_del` and `lv_obj_del`. Use `lv_obj_is_valid` before touching any pointer. Object deletion auto-cancels its animations in LVGL 8.

## Constraints

- Do **not** modify any SquareLine Studio-generated `.c` files (`ui_BrewScreen.c`, etc.)
- Only touch: `DefaultUI.cpp`, `ui.h`, `src/display/ui/default/lvgl/images/`, and new scripts
- All new widgets must be parented to `lv_obj_get_parent(ui_BrewScreen_mainLabel3)` at runtime
