# Bug: Weight/scale widget overlaps the steam-cup animation during brewing

## Goal

During the **Brew** and **Heating** states, the weight/scale row (`ui_BrewScreen_modeSwitch`) is
positioned directly on top of the steam-cup animation. Move it below the cup so it is
visible and not occluded.

---

## Display geometry (360×360 round panel)

```
Parent: ui_BrewScreen_contentPanel4  — 360×360 px, centred on screen

Steam cup (g_brewSteam.root):
  size   120×96 px
  align  LV_ALIGN_CENTER, y = -104   (kSteamRootY)
  → occupies panel-local y ≈ 84..180

controlContainer:
  size   360×196 px
  align  LV_ALIGN_CENTER, y = -10
  → top edge at panel-local y ≈ 74  ← overlaps cup

  Child 1: modeSwitch (scale icon + weightLabel)
    size   160×50 px
    → first item in flex column, sits at top of container ≈ y 74..124

  Child 2: profileInfo ("Selected profile" row)
    size   360×120 px
    → HIDDEN during Brew state
```

The `modeSwitch` lands at ~y 74–124, right over the cup (y 84–180). The user sees the
weight number on top of the cup graphic.

---

## What to fix

When in **Brew state** (`brewScreenState == BrewScreenState::Brew`), the `modeSwitch`
should appear **below** the cup, not above/over it.

The "Selected profile" row (`profileInfo`) is already hidden during brew, so there is
vertical room below the cup (~y 180–300 in panel-local coords, i.e. LV_ALIGN_CENTER
y ≈ +0 to +120).

**Preferred fix — move `controlContainer` down during brew state:**

In `DefaultUI.cpp`, inside the `effect_mgr.use_effect` block that already watches
`brewScreenState` (~line 1144), add a positional update:

```cpp
if (brewScreenState == BrewScreenState::Brew) {
    lv_obj_set_y(ui_BrewScreen_controlContainer, 70);   // push below cup
} else {
    lv_obj_set_y(ui_BrewScreen_controlContainer, -10);  // restore default
}
```

Adjust the `70` value if needed so the weight label lands visually below the cup wisps
(cup bottom is at panel-local y ≈ 180, i.e. LV_ALIGN_CENTER y ≈ 0; a container y of
+60..+80 should clear it).

**Alternative fix — absolute positioning:**

Remove `modeSwitch` from the flex column and place it as a direct child of
`contentPanel4` with `LV_ALIGN_CENTER, y = +90` (below the cup). This is more
surgical but requires re-parenting the widget at runtime, which is messier.

---

## Key files

### `src/display/ui/default/lvgl/screens/ui_BrewScreen.c` (generated, but editable)

`controlContainer` creation (~line 255):
```c
ui_BrewScreen_controlContainer = lv_obj_create(ui_BrewScreen_contentPanel4);
lv_obj_set_width(ui_BrewScreen_controlContainer, 360);
lv_obj_set_height(ui_BrewScreen_controlContainer, 196);
lv_obj_set_x(ui_BrewScreen_controlContainer, 0);
lv_obj_set_y(ui_BrewScreen_controlContainer, -10);           // ← this is the default y
lv_obj_set_align(ui_BrewScreen_controlContainer, LV_ALIGN_CENTER);
lv_obj_set_flex_flow(ui_BrewScreen_controlContainer, LV_FLEX_FLOW_COLUMN);
lv_obj_set_flex_align(ui_BrewScreen_controlContainer,
    LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
lv_obj_set_style_pad_row(ui_BrewScreen_controlContainer, 20, LV_PART_MAIN | LV_STATE_DEFAULT);
```

`modeSwitch` creation (~line 268):
```c
ui_BrewScreen_modeSwitch = lv_obj_create(ui_BrewScreen_controlContainer);
lv_obj_set_width(ui_BrewScreen_modeSwitch, 160);
lv_obj_set_height(ui_BrewScreen_modeSwitch, 50);
lv_obj_add_flag(ui_BrewScreen_modeSwitch, LV_OBJ_FLAG_HIDDEN);  // hidden by default
```

`profileInfo` creation (~line 308):
```c
ui_BrewScreen_profileInfo = lv_obj_create(ui_BrewScreen_controlContainer);
lv_obj_set_width(ui_BrewScreen_profileInfo, 360);
lv_obj_set_height(ui_BrewScreen_profileInfo, 120);
lv_obj_add_flag(ui_BrewScreen_profileInfo, LV_OBJ_FLAG_HIDDEN);  // hidden during brew
```

### `src/display/ui/default/DefaultUI.cpp`

Effect block that controls visibility (~line 1142):
```cpp
effect_mgr.use_effect(
    [=] { return currentScreen == ui_BrewScreen; },
    [=]() {
        _ui_flag_modify(ui_BrewScreen_adjustments,   LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Settings);
        _ui_flag_modify(ui_BrewScreen_acceptButton,  LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Settings);
        _ui_flag_modify(ui_BrewScreen_saveButton,    LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Settings);
        _ui_flag_modify(ui_BrewScreen_saveAsNewButton, LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Settings);
        _ui_flag_modify(ui_BrewScreen_startButton,   LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Brew);
        _ui_flag_modify(ui_BrewScreen_profileInfo,   LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Brew);
        _ui_flag_modify(ui_BrewScreen_modeSwitch,    LV_OBJ_FLAG_HIDDEN,
                        brewScreenState == BrewScreenState::Brew && volumetricAvailable);
        // ← ADD position update here
        if (volumetricAvailable) {
            lv_img_set_src(ui_BrewScreen_volumetricButton,
                           bluetoothScales ? &ui_img_1424216268 : &ui_img_flowmeter_png);
        }
    },
    &brewScreenState, &volumetricAvailable, &bluetoothScales);
```

Steam cup constants (top of DefaultUI.cpp ~line 50):
```cpp
constexpr int kSteamRootW = 120;
constexpr int kSteamRootH = 96;
constexpr int kSteamRootY = -104;   // LV_ALIGN_CENTER y within contentPanel4
// Cup bottom edge ≈ LV_ALIGN_CENTER y = kSteamRootY + kSteamRootH/2 = -104 + 48 = -56
// In 360px panel coords: 180 + (-56) = 124 px from top — but wisps extend ~60px above
```

---

## Constraints

- LVGL 8.4.0, ESP32 target, PlatformIO build.
- `ui_BrewScreen.c` is SquareLine-generated but is checked in and can be edited directly
  (the project does not regenerate from SLS).
- Do **not** change the cup geometry or `kSteamRootY` — the flicker fix already relied
  on those values being stable.
- Follow the project's git discipline (CLAUDE.md): state modified files, update
  `docs/this-fork.md` if this is new upstream divergence, then ask before committing.

---

## Acceptance criteria

1. During brew/heating: weight label is fully visible, below the cup animation, not
   overlapping the cup graphic or wisps.
2. Outside brew (idle / Settings state): layout is unchanged — `profileInfo` shows
   normally, `modeSwitch` at its original position.
3. No LVGL compile errors. No new per-frame style invalidations introduced.
