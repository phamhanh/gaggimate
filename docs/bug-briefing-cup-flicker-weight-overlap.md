# Bug briefing: cup flicker + weight/scale overlap

_Introduced by commits `35c44bb5` and `1c65d153`._

---

## Bug 1 — Screen flicker during Heating state

### What happens
When the boiler is heating (`BrewSteamState::Heating`), the screen flickers.

### Root-cause hypothesis
`brewSteamStartColorShift()` (`DefaultUI.cpp` ~line 205) runs a continuous LVGL
animation (`lv_anim_set_repeat_count LV_ANIM_REPEAT_INFINITE`) that calls
`brewSteamColorAnimCb` on every tick. That callback calls
`brewSteamApplyDynamicColor` → `brewSteamApplyColor`, which calls
`lv_obj_set_style_img_recolor` **and** `lv_obj_set_style_img_recolor_opa` on the
cup and all three wisps — **5 style-invalidation calls per animation frame**.

LVGL recolour on an image is implemented via a full software blit to a temporary
buffer; doing it on 4 objects simultaneously every ~16 ms (60 fps) forces LVGL to
dirty-mark large areas of the screen on each frame, which the display driver
flushes in a way that causes visible flicker — especially on the round 360×360
display with DMA transfers.

The `kHeatingGradient` has `cycleMs = 1400` with `lv_anim_path_ease_in_out`,
meaning the animation fires very frequently. The `kVentingGradient` at `280 ms`
is even faster and would flicker worse.

### Where to look / fix
- **`src/display/ui/default/DefaultUI.cpp`**
  - `brewSteamApplyColor()` ~line 116 — calls two `lv_obj_set_style_*` per object,
    which each trigger an invalidation.
  - `brewSteamStartColorShift()` ~line 205 — launches the repeating anim.
  - `brewSteamColorAnimCb()` ~line 195 — called every frame; touches up to 4 objects.

**Fix approaches (pick one):**
1. **Single recolour target**: make the wisps and cup children of a single
   `lv_obj_t` container whose `LV_STYLE_IMG_RECOLOR` is set — then only one style
   write per frame. (LVGL inherits `img_recolor` down the tree.)
2. **Reduce animation rate**: use a much longer `cycleMs` (e.g. 3000 ms) and
   throttle with `lv_anim_set_early_apply` + `lv_anim_set_time` so the colour
   ticks less often.
3. **Avoid per-frame style writes**: pre-render the three gradient colours as
   static `lv_color_t` values and switch between them with `lv_anim_path_step`
   (discrete steps) rather than continuous interpolation — far fewer invalidations.

---

## Bug 2 — Cup animation overlaps the weight/scale display

### What happens
The steam-cup widget (`g_brewSteam.root`) is positioned at `LV_ALIGN_CENTER` with
`y = kSteamRootY = -104` within `brewPanel` (= `lv_obj_get_parent(ui_BrewScreen_mainLabel3)`
= `ui_BrewScreen_contentPanel4`, a 360×360 panel).

The `ui_BrewScreen_controlContainer` (also a child of `contentPanel4`) is a
column-flex container at `y = -10`, 196 px tall, with two children:
1. `ui_BrewScreen_modeSwitch` — 160×50 px, contains the **scale icon** +
   **`ui_BrewScreen_weightLabel`** (the weight number). Hidden when not
   volumetric-brewing.
2. `ui_BrewScreen_profileInfo` — 360×120 px "Selected profile" row. **Hidden
   while brewing** (`brewScreenState == BrewScreenState::Brew`).

The cup root is 120×96 px centred at y = −104 within a 360-px-tall panel, so its
visible region spans roughly y ≈ 132..228 (panel-local). The `controlContainer`
(top at y ≈ 360/2 − 10 − 98 = 74) overlaps that band, putting `modeSwitch` and
the weight label directly behind/over the cup image.

### What the user wants
Move the **weight/scale widget** (`modeSwitch`) down to where the "Selected
profile" line used to be, i.e. below the cup — not on top of it.

### Where to look / fix
- **`src/display/ui/default/lvgl/screens/ui_BrewScreen.c`**
  - `ui_BrewScreen_controlContainer` — defined ~line 255:
    currently `y = -10`, `height = 196`, `pad_row = 20`.
  - `ui_BrewScreen_modeSwitch` — child 1 of `controlContainer`, 160×50.
  - `ui_BrewScreen_profileInfo` — child 2, 360×120, hidden while brewing.

- **`src/display/ui/default/DefaultUI.cpp`** ~line 1125:
  ```cpp
  _ui_flag_modify(ui_BrewScreen_profileInfo, LV_OBJ_FLAG_HIDDEN,
                  brewScreenState == BrewScreenState::Brew);
  _ui_flag_modify(ui_BrewScreen_modeSwitch, LV_OBJ_FLAG_HIDDEN, ...);
  ```

**Fix**: when in brewing/heating state, push `modeSwitch` (the weight row) to the
position previously occupied by `profileInfo`. Two options:

1. **Move `controlContainer` down**: increase its `y` offset (currently `−10`) to
   something like `+60` so both children sit below the cup. Also increase container
   height or adjust `pad_row` accordingly. The `profileInfo` is hidden during brew,
   so the flex column will only show `modeSwitch`.

2. **Remove `modeSwitch` from the flex column and position it absolutely**:
   detach it from `controlContainer` and place it as a direct child of
   `contentPanel4` with an absolute y near the bottom of the circle (e.g.
   `LV_ALIGN_CENTER, y = +80`), matching where the "Selected profile" label used
   to sit in the original upstream UI.

---

## Key files

| File | Purpose |
|------|---------|
| `src/display/ui/default/DefaultUI.cpp` | All animation logic; `brewSteamBuild`, `brewSteamSetState`, `brewSteamStartColorShift` |
| `src/display/ui/default/lvgl/screens/ui_BrewScreen.c` | LVGL widget tree; `controlContainer`, `modeSwitch`, `weightLabel`, `profileInfo` |
| `src/display/ui/default/lvgl/screens/ui_BrewScreen.h` | Extern declarations |
| `src/display/ui/default/lvgl/images/ui_img_steamcup.c` | Cup image asset |

## Constants / symbols to know

```
kSteamRootW = 120, kSteamRootH = 96, kSteamRootY = -104   (DefaultUI.cpp ~line 50)
ui_BrewScreen_contentPanel4  — 360×360 parent panel
ui_BrewScreen_controlContainer — y=-10, h=196, flex-column, pad_row=20
ui_BrewScreen_modeSwitch     — w=160, h=50, contains scale icon + weightLabel
ui_BrewScreen_profileInfo    — w=360, h=120, "Selected profile", hidden during brew
```
