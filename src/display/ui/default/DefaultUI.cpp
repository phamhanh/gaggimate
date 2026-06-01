#include "DefaultUI.h"

#include <WiFi.h>
#include <display/core/Controller.h>
#include <display/core/process/BrewProcess.h>
#include <display/core/process/Process.h>
#include <display/core/zones.h>
#include <display/drivers/AmoledDisplayDriver.h>
#include <display/drivers/LilyGoDriver.h>
#include <display/drivers/WaveshareDriver.h>
#include <display/drivers/common/LV_Helper.h>
#include <display/main.h>
#include <display/ui/default/lvgl/ui_theme_manager.h>
#include <display/ui/default/lvgl/ui_themes.h>
#include <display/ui/utils/effects.h>
#include <cmath>
#include <utility>

#include "esp_sntp.h"

static EffectManager effect_mgr;

namespace {
void setTempLabel(lv_obj_t *label, const float celsius) {
    const int tenths = static_cast<int>(lroundf(celsius * 10.0f));
    lv_label_set_text_fmt(label, "%d.%d°C", tenths / 10, abs(tenths % 10));
}

// ---------------------------------------------------------------------------
// Animated brew-status indicator: an espresso cup whose steam communicates the
// thermal state on its own, with NO accompanying word. The animation replaces
// the old "Brew" / "Stabilizing ↑↓" / "Ready to brew" text in ui_BrewScreen_mainLabel3.
//
// The state is conveyed two ways at once (so it reads at a glance and survives
// colour-blindness / glare):
//   * Colour  — Heating = amber, Ready = green, Cooling = blue, Brewing = white.
//   * Motion  — Heating = fast building steam climbing high;
//               Ready   = slow calm curl, green cup;
//               Cooling = slow wide steam drifting and thinning;
//               Brewing = strong steady steam (no fade), the "go" state.
//
// Built entirely here (not in the SquareLine-generated screen files) by parenting
// the widgets onto the brew screen's content panel, so the generated files stay
// untouched. Sits where the caption used to be and fills the gap above
// "Selected profile".
// ---------------------------------------------------------------------------

enum class BrewSteamState { None, Heating, Ready, Cooling, Brewing, FreezeGrace, Venting };

constexpr int kSteamRootW = 120;
constexpr int kSteamRootH = 96;
// Vertical centre of the group within the (centre-aligned) content panel.
// Covers the old caption row plus the empty gap below it. Nudge a few px on-device.
constexpr int kSteamRootY = -104;
constexpr int kSteamWispCount = 3;

const lv_img_dsc_t *kSteamWispSrc[kSteamWispCount] = {
    &ui_img_steamwisp_l,
    &ui_img_steamwisp_m,
    &ui_img_steamwisp_r,
};

struct BrewSteamMotion {
    uint32_t riseTime; // one rise cycle (ms); smaller = faster/more energetic
    int bottomY;       // wisp start Y (near the cup rim)
    int topY;          // wisp end Y (lower number = climbs higher)
    int spread;        // horizontal spread of the outer wisps
    bool fade;         // true = each wisp fades in/out; false = steady full opacity
};

lv_color_t brewSteamColor(BrewSteamState s) {
    switch (s) {
    case BrewSteamState::Heating:
        return lv_color_hex(0xFF9A3D); // amber — warming up
    case BrewSteamState::Ready:
        return lv_color_hex(0x55D17A); // green — good to go
    case BrewSteamState::Cooling:
        return lv_color_hex(0x5BB8FF); // blue — shedding heat / settling
    case BrewSteamState::Brewing:
        return lv_color_hex(0xFFFFFF); // white — actively pulling a shot
    case BrewSteamState::FreezeGrace:
        return lv_color_hex(0x5BE0FF); // cyan — latent heat settling, a waiting state
    case BrewSteamState::Venting:
        return lv_color_hex(0xFFFFFF); // white — releasing pressure
    default:
        return lv_color_hex(0xFFFFFF);
    }
}

BrewSteamMotion brewSteamMotion(BrewSteamState s) {
    switch (s) {
    case BrewSteamState::Heating:
        return BrewSteamMotion{1000, 32, 2, 11, true}; // fast, climbs high, building
    case BrewSteamState::Ready:
        return BrewSteamMotion{2600, 30, 12, 7, true}; // slow, gentle, narrow curl
    case BrewSteamState::Cooling:
        return BrewSteamMotion{2200, 30, 8, 13, true}; // slow, wide, drifting
    case BrewSteamState::Brewing:
        return BrewSteamMotion{850, 32, 0, 10, false}; // fast, steady, full opacity
    default:
        return BrewSteamMotion{1500, 30, 4, 14, true};
    }
}

struct BrewSteamUI {
    lv_obj_t *root = nullptr;
    lv_obj_t *cup = nullptr;
    lv_obj_t *waves = nullptr; // settling water, shown only in Freeze grace
    lv_obj_t *wind = nullptr;  // wind gust, shown only while Venting
    lv_obj_t *wisps[kSteamWispCount] = {nullptr};
    BrewSteamState state = BrewSteamState::None;
    const struct BrewColorGradient *colorGradient = nullptr; // active multi-colour shift, or null for a static hue
    lv_color_t lastDynamicColor{}; // last hue the colour-shift anim actually pushed
    bool hasDynamicColor = false;  // false until the first dynamic frame of a state
};
BrewSteamUI g_brewSteam;

void brewSteamOpaCb(void *obj, int32_t v) {
    lv_obj_set_style_opa(static_cast<lv_obj_t *>(obj), static_cast<lv_opa_t>(v), LV_PART_MAIN | LV_STATE_DEFAULT);
}

// Direct recolour (overrides the theme's NiceWhite) so each state has its own hue.
// Writes both the hue and the (constant) recolor opacity, so it's the one-time
// setter used on state entry. Each lv_obj_set_style_* call invalidates the object,
// so the per-frame colour-shift path must NOT use this — see brewSteamApplyHue.
void brewSteamApplyColor(lv_obj_t *img, lv_color_t color) {
    lv_obj_set_style_img_recolor(img, color, LV_PART_MAIN | LV_STATE_DEFAULT);
    lv_obj_set_style_img_recolor_opa(img, LV_OPA_COVER, LV_PART_MAIN | LV_STATE_DEFAULT);
}

// Per-frame hue update for the colour-shift animation. Only the recolor hue is
// touched (recolor_opa is already LV_OPA_COVER from the state-entry call and never
// changes), so each frame costs a single style invalidation instead of two.
void brewSteamApplyHue(lv_obj_t *img, lv_color_t color) {
    lv_obj_set_style_img_recolor(img, color, LV_PART_MAIN | LV_STATE_DEFAULT);
}

// ---------------------------------------------------------------------------
// Multi-colour spectrum shift. Each dynamic state owns a small list of RGB
// "stops"; an LVGL animation drives a normalised position along that list and
// a callback LERPs the R/G/B channels of the two surrounding stops to produce a
// continuously shifting colour. All maths is integer-only (one multiply +
// divide per channel) to stay light on the ESP32 and hold 30-60 FPS.
// ---------------------------------------------------------------------------
struct BrewColorGradient {
    const uint32_t *stops; // 0xRRGGBB waypoints, traversed in order then back
    uint8_t count;         // number of stops (>= 2)
    uint32_t cycleMs;      // forward sweep duration; playback mirrors it for the return
    bool smooth;           // true = ease-in-out (breathing), false = linear (jitter)
};

// 256 fixed-point steps per segment between adjacent stops.
constexpr int32_t kBrewGradStep = 256;

// Linear interpolation of one 8-bit channel; frac is 0..255.
inline uint8_t brewLerp8(uint8_t a, uint8_t b, uint8_t frac) {
    return static_cast<uint8_t>(static_cast<int>(a) + ((static_cast<int>(b) - static_cast<int>(a)) * frac) / 255);
}

// Sample the gradient at fixed-point position pos in [0, (count-1)*256].
lv_color_t brewGradientColorAt(const BrewColorGradient &g, int32_t pos) {
    const int32_t maxPos = (static_cast<int32_t>(g.count) - 1) * kBrewGradStep;
    if (pos < 0) {
        pos = 0;
    } else if (pos > maxPos) {
        pos = maxPos;
    }
    const int seg = static_cast<int>(pos / kBrewGradStep);
    const uint8_t frac = static_cast<uint8_t>(pos % kBrewGradStep);
    const int segNext = (seg < g.count - 1) ? seg + 1 : seg;
    const uint32_t c0 = g.stops[seg];
    const uint32_t c1 = g.stops[segNext];
    const uint8_t r = brewLerp8((c0 >> 16) & 0xFF, (c1 >> 16) & 0xFF, frac);
    const uint8_t grn = brewLerp8((c0 >> 8) & 0xFF, (c1 >> 8) & 0xFF, frac);
    const uint8_t b = brewLerp8(c0 & 0xFF, c1 & 0xFF, frac);
    return lv_color_make(r, grn, b);
}

// Multi-colour spectrums per dynamic state (see task spec). Stops are 0xRRGGBB.
constexpr uint32_t kHeatingStops[] = {0x990000, 0xFF5500, 0xFFB300};  // deep red -> orange -> amber gold
constexpr uint32_t kCoolingStops[] = {0x110066, 0x4040FF, 0x00DFFF};  // deep indigo -> electric blue -> bright cyan
constexpr uint32_t kFreezeStops[] = {0xA5F2F3, 0xE0E0FF, 0x7FFFD4};   // ice blue -> pale violet/white -> soft mint cyan
constexpr uint32_t kVentingStops[] = {0xFFFFFF, 0xD3D3D3, 0xA0C0F0};  // white -> light silver -> translucent blue

constexpr BrewColorGradient kHeatingGradient{kHeatingStops, 3, 1400, true}; // breathing heating element
constexpr BrewColorGradient kCoolingGradient{kCoolingStops, 3, 1800, true}; // slow liquid ocean shift
constexpr BrewColorGradient kFreezeGradient{kFreezeStops, 3, 2600, true};   // slow settling-ice transition
constexpr BrewColorGradient kVentingGradient{kVentingStops, 3, 280, false}; // rapid chaotic vapour jitter

// Push a freshly-computed colour onto whichever widgets the active state draws.
// The gradient is sampled every animation tick, but on the round 360x360 display
// a redraw of the recoloured images forces a full DMA flush that reads as a
// whole-screen flicker. Most ticks produce a colour that maps to the *same*
// RGB565 value as the previous frame (ease-in-out dwells at the stops, and 16-bit
// colour is coarse), so we skip those frames entirely and only invalidate the
// widgets when the displayed hue genuinely changes.
void brewSteamApplyDynamicColor(lv_color_t c) {
    if (g_brewSteam.hasDynamicColor && g_brewSteam.lastDynamicColor.full == c.full) {
        return; // identical to the last painted frame — nothing to redraw
    }
    g_brewSteam.lastDynamicColor = c;
    g_brewSteam.hasDynamicColor = true;

    switch (g_brewSteam.state) {
    case BrewSteamState::Heating:
    case BrewSteamState::Cooling:
        brewSteamApplyHue(g_brewSteam.cup, c);
        for (int i = 0; i < kSteamWispCount; i++) {
            brewSteamApplyHue(g_brewSteam.wisps[i], c);
        }
        break;
    case BrewSteamState::FreezeGrace:
        brewSteamApplyHue(g_brewSteam.waves, c);
        break;
    case BrewSteamState::Venting:
        brewSteamApplyHue(g_brewSteam.wind, c);
        break;
    default:
        break;
    }
}

// LVGL anim exec callback: v is the fixed-point position along the active gradient.
void brewSteamColorAnimCb(void *var, int32_t v) {
    (void)var;
    if (g_brewSteam.colorGradient == nullptr) {
        return;
    }
    brewSteamApplyDynamicColor(brewGradientColorAt(*g_brewSteam.colorGradient, v));
}

// Start (or restart) the continuous colour shift for the active state. The anim
// is anchored to the root so brewSteamSetState's lv_anim_del(root) cancels it.
void brewSteamStartColorShift(const BrewColorGradient &g) {
    g_brewSteam.colorGradient = &g;
    brewSteamColorAnimCb(g_brewSteam.root, 0); // paint the first frame now (no flash of the static hue)

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, g_brewSteam.root);
    lv_anim_set_values(&a, 0, (static_cast<int32_t>(g.count) - 1) * kBrewGradStep);
    lv_anim_set_time(&a, g.cycleMs);
    lv_anim_set_playback_time(&a, g.cycleMs); // sweep forward then smoothly back
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&a, g.smooth ? lv_anim_path_ease_in_out : lv_anim_path_linear);
    lv_anim_set_exec_cb(&a, brewSteamColorAnimCb);
    lv_anim_start(&a);
}

// (Re)start the rise + fade animations for one wisp according to the state motion.
void brewSteamStartWisp(lv_obj_t *w, int index, const BrewSteamMotion &m) {
    lv_anim_del(w, nullptr); // cancel any previous rise/fade/sway before restarting

    const lv_img_dsc_t *src = kSteamWispSrc[index];
    const int centreX = kSteamRootW / 2;
    const int offset = (index - 1) * m.spread; // -spread, 0, +spread
    const int baseX = centreX + offset - src->header.w / 2;
    lv_obj_set_x(w, baseX);
    lv_obj_set_y(w, m.bottomY);

    const uint32_t delay = (m.riseTime / kSteamWispCount) * index;

    lv_anim_t rise;
    lv_anim_init(&rise);
    lv_anim_set_var(&rise, w);
    lv_anim_set_values(&rise, m.bottomY, m.topY);
    lv_anim_set_time(&rise, m.riseTime);
    lv_anim_set_delay(&rise, delay);
    lv_anim_set_repeat_count(&rise, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&rise, lv_anim_path_ease_out);
    lv_anim_set_exec_cb(&rise, reinterpret_cast<lv_anim_exec_xcb_t>(lv_obj_set_y));
    lv_anim_start(&rise);

    lv_anim_t sway;
    lv_anim_init(&sway);
    lv_anim_set_var(&sway, w);
    lv_anim_set_values(&sway, baseX - 3, baseX + 3);
    lv_anim_set_time(&sway, static_cast<uint32_t>(m.riseTime * 6 / 5));
    lv_anim_set_playback_time(&sway, static_cast<uint32_t>(m.riseTime * 6 / 5));
    lv_anim_set_delay(&sway, delay + index * 80);
    lv_anim_set_repeat_count(&sway, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&sway, lv_anim_path_ease_in_out);
    lv_anim_set_exec_cb(&sway, reinterpret_cast<lv_anim_exec_xcb_t>(lv_obj_set_x));
    lv_anim_start(&sway);

    if (m.fade) {
        lv_anim_t fade;
        lv_anim_init(&fade);
        lv_anim_set_var(&fade, w);
        lv_anim_set_values(&fade, 0, LV_OPA_COVER);
        lv_anim_set_time(&fade, m.riseTime / 2);
        lv_anim_set_playback_time(&fade, m.riseTime / 2);
        lv_anim_set_delay(&fade, delay);
        lv_anim_set_repeat_count(&fade, LV_ANIM_REPEAT_INFINITE);
        lv_anim_set_exec_cb(&fade, brewSteamOpaCb);
        lv_anim_start(&fade);
    } else {
        lv_obj_set_style_opa(w, LV_OPA_COVER, LV_PART_MAIN | LV_STATE_DEFAULT);
    }
}

// Freeze grace: a slow side-to-side sway + breathing fade — water settling while
// the system waits out its latent heat. Positioned absolutely within the root.
void brewSteamStartWaves(lv_obj_t *w) {
    lv_anim_del(w, nullptr);
    const int baseX = (kSteamRootW - ui_img_steamwaves.header.w) / 2;
    const int baseY = (kSteamRootH - ui_img_steamwaves.header.h) / 2;
    lv_obj_set_pos(w, baseX, baseY);

    lv_anim_t sway;
    lv_anim_init(&sway);
    lv_anim_set_var(&sway, w);
    lv_anim_set_values(&sway, baseX - 5, baseX + 5);
    lv_anim_set_time(&sway, 2000);
    lv_anim_set_playback_time(&sway, 2000);
    lv_anim_set_repeat_count(&sway, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&sway, lv_anim_path_ease_in_out);
    lv_anim_set_exec_cb(&sway, reinterpret_cast<lv_anim_exec_xcb_t>(lv_obj_set_x));
    lv_anim_start(&sway);

    lv_anim_t fade;
    lv_anim_init(&fade);
    lv_anim_set_var(&fade, w);
    lv_anim_set_values(&fade, 150, LV_OPA_COVER);
    lv_anim_set_time(&fade, 1600);
    lv_anim_set_playback_time(&fade, 1600);
    lv_anim_set_repeat_count(&fade, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_exec_cb(&fade, brewSteamOpaCb);
    lv_anim_start(&fade);
}

// Venting: a quick opacity flicker — a brief, energetic pressure release.
void brewSteamStartWind(lv_obj_t *w) {
    lv_anim_del(w, nullptr);
    lv_anim_t flicker;
    lv_anim_init(&flicker);
    lv_anim_set_var(&flicker, w);
    lv_anim_set_values(&flicker, 110, LV_OPA_COVER);
    lv_anim_set_time(&flicker, 220);
    lv_anim_set_playback_time(&flicker, 180);
    lv_anim_set_repeat_count(&flicker, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_exec_cb(&flicker, brewSteamOpaCb);
    lv_anim_start(&flicker);
}

void brewSteamBuild(lv_obj_t *parent) {
    lv_obj_t *root = lv_obj_create(parent);
    lv_obj_remove_style_all(root);
    lv_obj_set_size(root, kSteamRootW, kSteamRootH);
    lv_obj_set_align(root, LV_ALIGN_CENTER);
    lv_obj_set_y(root, kSteamRootY);
    lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(root, LV_OBJ_FLAG_HIDDEN);
    g_brewSteam.root = root;

    // Wisps first so the cup draws on top of their base (steam rises out of it).
    for (int i = 0; i < kSteamWispCount; i++) {
        lv_obj_t *w = lv_img_create(root);
        lv_img_set_src(w, kSteamWispSrc[i]);
        g_brewSteam.wisps[i] = w;
    }

    lv_obj_t *cup = lv_img_create(root);
    lv_img_set_src(cup, &ui_img_steamcup);
    lv_obj_align(cup, LV_ALIGN_BOTTOM_MID, 0, 0);
    g_brewSteam.cup = cup;

    // Freeze grace: settling waves (wild river -> calm stream). Centred, hidden
    // until that state. Its own gentle sway + breathing fade convey "waiting".
    lv_obj_t *waves = lv_img_create(root);
    lv_img_set_src(waves, &ui_img_steamwaves);
    lv_obj_add_flag(waves, LV_OBJ_FLAG_HIDDEN); // positioned in brewSteamStartWaves
    g_brewSteam.waves = waves;

    // Venting: a wind gust (reused 80x80 asset), scaled down and centred, hidden
    // until that state. Flickers quickly to read as a brief pressure release.
    lv_obj_t *wind = lv_img_create(root);
    lv_img_set_src(wind, &ui_img_783005998); // assets/wind-80x80
    lv_img_set_pivot(wind, 40, 40);          // centre of the 80x80 source
    lv_img_set_zoom(wind, 176);              // ~55 px (256 = 100%)
    lv_obj_align(wind, LV_ALIGN_CENTER, 0, 0);
    lv_obj_add_flag(wind, LV_OBJ_FLAG_HIDDEN);
    g_brewSteam.wind = wind;

    g_brewSteam.state = BrewSteamState::None;
}

// Make sure the steam group exists and is parented to the current brew panel.
// Rebuilds after a screen re-init (which destroys the previous widgets, and with
// them their animations, so there is nothing to leak).
void brewSteamEnsure(lv_obj_t *parent) {
    if (g_brewSteam.root != nullptr && lv_obj_is_valid(g_brewSteam.root) &&
        lv_obj_get_parent(g_brewSteam.root) == parent) {
        return;
    }
    g_brewSteam = BrewSteamUI{};
    if (parent != nullptr) {
        brewSteamBuild(parent);
    }
}

// Show the indicator in a given state (idempotent per state). Each state owns a
// different glyph: cup + steam for the thermal/brew states, settling waves for
// Freeze grace, a wind gust for Venting. On every transition we cancel all
// sub-animations and hide all sub-widgets, then re-show and re-animate only the
// ones the new state uses, so nothing from the previous state lingers or leaks.
void brewSteamSetState(BrewSteamState s) {
    if (g_brewSteam.root == nullptr || !lv_obj_is_valid(g_brewSteam.root)) {
        return;
    }
    lv_obj_clear_flag(g_brewSteam.root, LV_OBJ_FLAG_HIDDEN);
    if (s == g_brewSteam.state) {
        return; // already in this state; let the running animation continue
    }
    g_brewSteam.state = s;

    // Reset: stop every sub-animation and hide every sub-widget.
    lv_anim_del(g_brewSteam.root, nullptr); // cancels the colour-shift anim (anchored to root)
    g_brewSteam.colorGradient = nullptr;
    g_brewSteam.hasDynamicColor = false; // force the new state's first frame to paint
    lv_anim_del(g_brewSteam.cup, nullptr);
    lv_anim_del(g_brewSteam.waves, nullptr);
    lv_anim_del(g_brewSteam.wind, nullptr);
    lv_obj_add_flag(g_brewSteam.cup, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(g_brewSteam.waves, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(g_brewSteam.wind, LV_OBJ_FLAG_HIDDEN);
    for (int i = 0; i < kSteamWispCount; i++) {
        lv_anim_del(g_brewSteam.wisps[i], nullptr);
        lv_obj_add_flag(g_brewSteam.wisps[i], LV_OBJ_FLAG_HIDDEN);
    }

    const lv_color_t color = brewSteamColor(s);

    // Freeze grace: settling-ice spectrum over the swaying, breathing waves.
    if (s == BrewSteamState::FreezeGrace) {
        brewSteamApplyColor(g_brewSteam.waves, color);
        lv_obj_clear_flag(g_brewSteam.waves, LV_OBJ_FLAG_HIDDEN);
        brewSteamStartWaves(g_brewSteam.waves);
        brewSteamStartColorShift(kFreezeGradient);
        return;
    }

    // Venting: pressure-vapour jitter (white/silver/blue) over the flickering gust.
    if (s == BrewSteamState::Venting) {
        brewSteamApplyColor(g_brewSteam.wind, color);
        lv_obj_clear_flag(g_brewSteam.wind, LV_OBJ_FLAG_HIDDEN);
        brewSteamStartWind(g_brewSteam.wind);
        brewSteamStartColorShift(kVentingGradient);
        return;
    }

    // Thermal / brew states: the cup with rising steam wisps.
    const BrewSteamMotion motion = brewSteamMotion(s);
    brewSteamApplyColor(g_brewSteam.cup, color);
    lv_obj_clear_flag(g_brewSteam.cup, LV_OBJ_FLAG_HIDDEN);
    for (int i = 0; i < kSteamWispCount; i++) {
        brewSteamApplyColor(g_brewSteam.wisps[i], color);
        lv_obj_clear_flag(g_brewSteam.wisps[i], LV_OBJ_FLAG_HIDDEN);
        brewSteamStartWisp(g_brewSteam.wisps[i], i, motion);
    }

    // Heating = breathing thermal glow; Cooling = liquid ocean shift. The cup +
    // wisps recolour continuously; Ready/Brewing keep their flat hue from above.
    if (s == BrewSteamState::Heating) {
        brewSteamStartColorShift(kHeatingGradient);
    } else if (s == BrewSteamState::Cooling) {
        brewSteamStartColorShift(kCoolingGradient);
    }
}
} // namespace

int16_t calculate_angle(int set_temp, int range, int offset) {
    const double percentage = static_cast<double>(set_temp) / static_cast<double>(MAX_TEMP);
    return (percentage * ((double)range)) - range / 2 - offset;
}

void DefaultUI::updateTempHistory() {
    if (currentTemp > 0) {
        if (tempHistoryIndex >= TEMP_HISTORY_LENGTH) {
            tempHistoryIndex = 0;
            isTempHistoryInitialized = true;
        }
        tempHistory[tempHistoryIndex] = currentTemp;
        tempHistoryIndex += 1;
    }

    if (tempHistoryIndex % 4 == 0) {
        heatingFlash = !heatingFlash;
        rerender = true;
    }
}

void DefaultUI::updateTempStableFlag() {
    if (isTempHistoryInitialized) {
        float totalError = 0.0f;
        float maxError = 0.0f;
        for (uint16_t i = 0; i < TEMP_HISTORY_LENGTH; i++) {
            float error = abs(tempHistory[i] - targetTemp);
            totalError += error;
            maxError = error > maxError ? error : maxError;
        }

        const float avgError = totalError / TEMP_HISTORY_LENGTH;
        const float errorMargin = max(2.0f, static_cast<float>(targetTemp) * 0.02f);

        isTemperatureStable = avgError < errorMargin && maxError <= errorMargin;
    }

    // instantly reset stability if setpoint has changed
    if (prevTargetTemp != targetTemp) {
        isTemperatureStable = false;
    }

    prevTargetTemp = targetTemp;
}

void DefaultUI::adjustHeatingIndicator(lv_obj_t *dials) {
    lv_obj_t *heatingIcon = ui_comp_get_child(dials, UI_COMP_DIALS_TEMPICON);
    lv_obj_set_style_img_recolor(heatingIcon, lv_color_hex(isTemperatureStable ? 0x00D100 : 0xF62C2C),
                                 LV_PART_MAIN | LV_STATE_DEFAULT);
    if (!isTemperatureStable) {
        lv_obj_set_style_opa(heatingIcon, heatingFlash ? LV_OPA_50 : LV_OPA_100, LV_PART_MAIN | LV_STATE_DEFAULT);
    }
}

void DefaultUI::reloadProfiles() { profileLoaded = 0; }

DefaultUI::DefaultUI(Controller *controller, Driver *driver, PluginManager *pluginManager)
    : controller(controller), panelDriver(driver), pluginManager(pluginManager) {
    setupPanel();
}

void DefaultUI::init() {
    profileManager = controller->getProfileManager();
    auto triggerRender = [this](Event const &) { rerender = true; };
    pluginManager->on("boiler:currentTemperature:change", [=](Event const &event) {
        const float newTemp = event.getFloat("value");
        if (roundf(newTemp * 10.0f) != roundf(currentTemp * 10.0f)) {
            currentTemp = newTemp;
            rerender = true;
        }
    });
    pluginManager->on("boiler:pressure:change", [=](Event const &event) {
        float newPressure = event.getFloat("value");
        if (round(newPressure * 10.0f) != round(pressure * 10.0f)) {
            pressure = newPressure;
            rerender = true;
        }
    });
    pluginManager->on("boiler:targetTemperature:change", [=](Event const &event) {
        const float newTemp = event.getFloat("value");
        if (roundf(newTemp * 10.0f) != roundf(targetTemp * 10.0f)) {
            targetTemp = newTemp;
            rerender = true;
        }
    });
    pluginManager->on("controller:targetVolume:change", [=](Event const &event) {
        targetVolume = event.getFloat("value");
        rerender = true;
    });
    pluginManager->on("controller:targetDuration:change", [=](Event const &event) {
        targetDuration = event.getFloat("value");
        rerender = true;
    });
    pluginManager->on("controller:grindDuration:change", [=](Event const &event) {
        grindDuration = event.getInt("value");
        rerender = true;
    });
    pluginManager->on("controller:grindVolume:change", [=](Event const &event) {
        grindVolume = event.getFloat("value");
        rerender = true;
    });
    pluginManager->on("controller:process:end", triggerRender);
    pluginManager->on("controller:process:start", triggerRender);
    pluginManager->on("controller:mode:change", [this](Event const &event) {
        mode = event.getInt("value");
        switch (mode) {
        case MODE_STANDBY:
            changeScreen(&ui_StandbyScreen, &ui_StandbyScreen_screen_init);
            break;
        case MODE_BREW:
            changeScreen(&ui_BrewScreen, &ui_BrewScreen_screen_init);
            break;
        case MODE_GRIND:
            changeScreen(&ui_GrindScreen, &ui_GrindScreen_screen_init);
            break;
        case MODE_STEAM:
            changeScreen(&ui_SimpleProcessScreen, &ui_SimpleProcessScreen_screen_init);
            break;
        case MODE_WATER:
            changeScreen(&ui_SimpleProcessScreen, &ui_SimpleProcessScreen_screen_init);
            break;
        default:
            break;
        };
    });
    pluginManager->on("controller:brew:start",
                      [this](Event const &event) { changeScreen(&ui_StatusScreen, &ui_StatusScreen_screen_init); });
    pluginManager->on("controller:brew:clear", [this](Event const &event) {
        if (lv_scr_act() == ui_StatusScreen) {
            changeScreen(&ui_BrewScreen, &ui_BrewScreen_screen_init);
        }
    });
    pluginManager->on("controller:bluetooth:waiting", [this](Event const &) {
        waitingForController = true;
        rerender = true;
    });
    pluginManager->on("controller:bluetooth:connect", [this](Event const &) {
        waitingForController = false;
        rerender = true;
        initialized = true;
        if (lv_scr_act() == ui_StandbyScreen) {
            Settings &settings = controller->getSettings();
            if (settings.getStartupMode() == MODE_BREW) {
                changeScreen(&ui_BrewScreen, &ui_BrewScreen_screen_init);
            } else {
                standbyEnterTime = millis();
            }
        }
        pressureAvailable = controller->getSystemInfo().capabilities.pressure;
    });
    pluginManager->on("controller:bluetooth:disconnect", [this](Event const &) {
        waitingForController = true;
        rerender = true;
    });
    pluginManager->on("controller:wifi:connect", [this](Event const &event) {
        rerender = true;
        apActive = event.getInt("AP");
    });
    pluginManager->on("ota:update:start", [this](Event const &) {
        updateActive = true;
        rerender = true;
        changeScreen(&ui_StandbyScreen, &ui_StandbyScreen_screen_init);
    });
    pluginManager->on("ota:update:end", [this](Event const &) {
        updateActive = false;
        rerender = true;
        changeScreen(&ui_StandbyScreen, &ui_StandbyScreen_screen_init);
    });
    pluginManager->on("ota:update:status", [this](Event const &event) {
        rerender = true;
        updateAvailable = event.getInt("value");
    });
    pluginManager->on("controller:error", [this](Event const &) {
        rerender = true;
        changeScreen(&ui_StandbyScreen, &ui_StandbyScreen_screen_init);
    });
    pluginManager->on("controller:autotune:start",
                      [this](Event const &) { changeScreen(&ui_StandbyScreen, &ui_StandbyScreen_screen_init); });
    pluginManager->on("controller:autotune:result",
                      [this](Event const &) { changeScreen(&ui_StandbyScreen, &ui_StandbyScreen_screen_init); });

    pluginManager->on("profiles:profile:select", [this](Event const &event) {
        profileManager->loadSelectedProfile(selectedProfile);
        selectedProfileId = event.getString("id");
        targetDuration = profileManager->getSelectedProfile().getTotalDuration();
        targetVolume = profileManager->getSelectedProfile().getTotalVolume();
        profileVolumetric = profileManager->getSelectedProfile().hasWeightTarget();
        reloadProfiles();
        rerender = true;
    });
    pluginManager->on("profiles:profile:favorite", [this](Event const &event) { reloadProfiles(); });
    pluginManager->on("profiles:profile:unfavorite", [this](Event const &event) { reloadProfiles(); });
    pluginManager->on("profiles:profile:save", [this](Event const &event) { reloadProfiles(); });
    pluginManager->on("controller:volumetric-measurement:bluetooth:change", [=](Event const &event) {
        double newWeight = event.getFloat("value");
        if (round(newWeight * 10.0) != round(bluetoothWeight * 10.0)) {
            bluetoothWeight = newWeight;
            rerender = true;
        }
    });
    setupState();
    setupReactive();
    xTaskCreatePinnedToCore(loopTask, "DefaultUI::loop", configMINIMAL_STACK_SIZE * 6, this, 1, &taskHandle, 1);
    xTaskCreatePinnedToCore(profileLoopTask, "DefaultUI::loopProfiles", configMINIMAL_STACK_SIZE * 4, this, 1, &profileTaskHandle,
                            0);
}

void DefaultUI::loop() {
    const unsigned long now = millis();
    const unsigned long diff = now - lastRender;

    if (now - lastTempLog > TEMP_HISTORY_INTERVAL) {
        updateTempHistory();
        lastTempLog = now;
    }

    if ((controller->isActive() && diff > RERENDER_INTERVAL_ACTIVE) || diff > RERENDER_INTERVAL_IDLE) {
        rerender = true;
    }

    if (rerender) {
        rerender = false;
        lastRender = now;
        error = controller->isErrorState();
        autotuning = controller->isAutotuning();
        const Settings &settings = controller->getSettings();
        volumetricAvailable = controller->isVolumetricAvailable();
        bluetoothScales = controller->isBluetoothScaleHealthy();
        volumetricMode = volumetricAvailable && settings.isVolumetricTarget();
        brewVolumetric = volumetricAvailable && profileVolumetric;
        grindActive = controller->isGrindActive();
        active = controller->isActive();
        brewIdleVenting = controller->isBrewIdleVenting() ? 1 : 0;
        stableTemp = controller->isStableTemp() ? 1 : 0;
        pidFreezeGraceActive = controller->isPidFreezeGraceActive() ? 1 : 0;
        smartGrindActive = settings.isSmartGrindActive();
        grindAvailable = smartGrindActive || settings.getAltRelayFunction() == ALT_RELAY_GRIND;
        applyTheme();
        if (controller->isErrorState()) {
            changeScreen(&ui_StandbyScreen, &ui_StandbyScreen_screen_init);
        }
        updateTempStableFlag();
        handleScreenChange();
        currentScreen = lv_scr_act();
        if (lv_scr_act() == ui_StandbyScreen)
            updateStandbyScreen();
        if (lv_scr_act() == ui_StatusScreen)
            updateStatusScreen();
        effect_mgr.evaluate_all();
    }

    lv_task_handler();
}

void DefaultUI::loopProfiles() {
    if (!profileLoaded) {
        const auto favoritedIds = profileManager->getFavoritedProfiles();
        favoritedProfileIds.clear();
        favoritedProfiles.clear();
        favoritedProfileIds.reserve(favoritedIds.size() + 1);
        favoritedProfileIds.emplace_back(controller->getSettings().getSelectedProfile());
        for (const auto &id : favoritedIds) {
            if (std::find(favoritedProfileIds.begin(), favoritedProfileIds.end(), id) == favoritedProfileIds.end())
                favoritedProfileIds.emplace_back(id);
        }
        favoritedProfiles.reserve(favoritedProfileIds.size());
        for (const auto &profileId : favoritedProfileIds) {
            Profile profile{};
            profileManager->loadProfile(profileId, profile);
            favoritedProfiles.emplace_back(std::move(profile));
        }
        profileLoaded = 1;
    }
}

void DefaultUI::changeScreen(lv_obj_t **screen, void (*target_init)()) {
    targetScreen = screen;
    targetScreenInit = target_init;
    rerender = true;

    // Reset some submenus
    brewScreenState = BrewScreenState::Brew;
}

void DefaultUI::changeBrewScreenMode(BrewScreenState state) {
    brewScreenState = state;
    rerender = true;
}

void DefaultUI::onProfileSwitch() {
    currentProfileIdx = 0;
    changeScreen(&ui_ProfileScreen, ui_ProfileScreen_screen_init);
}

void DefaultUI::onNextProfile() {
    if (currentProfileIdx < favoritedProfileIds.size() - 1) {
        currentProfileIdx++;
    }
    rerender = true;
}

void DefaultUI::onPreviousProfile() {
    if (currentProfileIdx > 0) {
        currentProfileIdx--;
    }
    rerender = true;
}

void DefaultUI::onProfileSelect() {
    profileManager->selectProfile(favoritedProfileIds[currentProfileIdx]);
    profileDirty = false;
    changeScreen(&ui_BrewScreen, ui_BrewScreen_screen_init);
}

void DefaultUI::onVolumetricDelete() {
    controller->onVolumetricDelete();
    profileVolumetric = profileManager->getSelectedProfile().hasWeightTarget();
    profileDirty = true;
}

void DefaultUI::setupPanel() {
    ui_init();
    lv_task_handler();

    delay(100);
    // Set initial brightness based on settings
    const Settings &settings = controller->getSettings();
    setBrightness(settings.getMainBrightness());
}

void DefaultUI::setupState() {
    error = controller->isErrorState();
    autotuning = controller->isAutotuning();
    const Settings &settings = controller->getSettings();
    volumetricAvailable = controller->isVolumetricAvailable();
    volumetricMode = volumetricAvailable && settings.isVolumetricTarget();
    grindActive = controller->isGrindActive();
    active = controller->isActive();
    brewIdleVenting = controller->isBrewIdleVenting() ? 1 : 0;
    stableTemp = controller->isStableTemp() ? 1 : 0;
    pidFreezeGraceActive = controller->isPidFreezeGraceActive() ? 1 : 0;
    smartGrindActive = settings.isSmartGrindActive();
    grindAvailable = smartGrindActive || settings.getAltRelayFunction() == ALT_RELAY_GRIND;
    mode = controller->getMode();
    currentTemp = controller->getCurrentTemp();
    targetTemp = controller->getTargetTemp();
    targetDuration = profileManager->getSelectedProfile().getTotalDuration();
    targetVolume = profileManager->getSelectedProfile().getTotalVolume();
    grindDuration = settings.getTargetGrindDuration();
    grindVolume = settings.getTargetGrindVolume();
    pressureAvailable = controller->getSystemInfo().capabilities.pressure ? 1 : 0;
    incomingWaterTempC = settings.getIncomingWaterTempC();
    pressureScaling = std::ceil(settings.getPressureScaling());
    selectedProfileId = settings.getSelectedProfile();
    profileManager->loadSelectedProfile(selectedProfile);
    profileVolumetric = selectedProfile.hasWeightTarget();
}

void DefaultUI::setupReactive() {
    effect_mgr.use_effect([=] { return currentScreen == ui_MenuScreen; }, [=]() { adjustDials(ui_MenuScreen_dials); },
                          &pressureAvailable);
    effect_mgr.use_effect([=] { return currentScreen == ui_StatusScreen; }, [=]() { adjustDials(ui_StatusScreen_dials); },
                          &pressureAvailable);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; }, [=]() { adjustDials(ui_BrewScreen_dials); },
                          &pressureAvailable);
    effect_mgr.use_effect([=] { return currentScreen == ui_GrindScreen; }, [=]() { adjustDials(ui_GrindScreen_dials); },
                          &pressureAvailable);
    effect_mgr.use_effect([=] { return currentScreen == ui_SimpleProcessScreen; },
                          [=]() { adjustDials(ui_SimpleProcessScreen_dials); }, &pressureAvailable);
    effect_mgr.use_effect([=] { return currentScreen == ui_ProfileScreen; }, [=]() { adjustDials(ui_ProfileScreen_dials); },
                          &pressureAvailable);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; }, [=]() { adjustHeatingIndicator(ui_BrewScreen_dials); },
                          &isTemperatureStable, &heatingFlash);
    effect_mgr.use_effect([=] { return currentScreen == ui_SimpleProcessScreen; },
                          [=]() { adjustHeatingIndicator(ui_SimpleProcessScreen_dials); }, &isTemperatureStable, &heatingFlash);
    effect_mgr.use_effect([=] { return currentScreen == ui_MenuScreen; }, [=]() { adjustHeatingIndicator(ui_MenuScreen_dials); },
                          &isTemperatureStable, &heatingFlash);
    effect_mgr.use_effect([=] { return currentScreen == ui_ProfileScreen; },
                          [=]() { adjustHeatingIndicator(ui_ProfileScreen_dials); }, &isTemperatureStable, &heatingFlash);
    effect_mgr.use_effect([=] { return currentScreen == ui_GrindScreen; },
                          [=]() { adjustHeatingIndicator(ui_GrindScreen_dials); }, &isTemperatureStable, &heatingFlash);
    effect_mgr.use_effect([=] { return currentScreen == ui_StatusScreen; },
                          [=]() { adjustHeatingIndicator(ui_StatusScreen_dials); }, &isTemperatureStable, &heatingFlash);
    effect_mgr.use_effect([=] { return currentScreen == ui_SimpleProcessScreen; },
                          [=]() { lv_label_set_text(ui_SimpleProcessScreen_mainLabel5, mode == MODE_STEAM ? "Steam" : "Water"); },
                          &mode);
    effect_mgr.use_effect([=] { return currentScreen == ui_MenuScreen; },
                          [=]() {
                              lv_arc_set_value(uic_MenuScreen_dials_tempGauge, currentTemp * 10.0f);
                              setTempLabel(uic_MenuScreen_dials_tempText, currentTemp);
                          },
                          &currentTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_StatusScreen; },
                          [=]() {
                              lv_arc_set_value(uic_StatusScreen_dials_tempGauge, currentTemp * 10.0f);
                              setTempLabel(uic_StatusScreen_dials_tempText, currentTemp);
                          },
                          &currentTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; },
                          [=]() {
                              lv_arc_set_value(uic_BrewScreen_dials_tempGauge, currentTemp * 10.0f);
                              setTempLabel(uic_BrewScreen_dials_tempText, currentTemp);
                          },
                          &currentTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_GrindScreen; },
                          [=]() {
                              lv_arc_set_value(uic_GrindScreen_dials_tempGauge, currentTemp * 10.0f);
                              setTempLabel(uic_GrindScreen_dials_tempText, currentTemp);
                          },
                          &currentTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_SimpleProcessScreen; },
                          [=]() {
                              lv_arc_set_value(uic_SimpleProcessScreen_dials_tempGauge, currentTemp * 10.0f);
                              setTempLabel(uic_SimpleProcessScreen_dials_tempText, currentTemp);
                          },
                          &currentTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_ProfileScreen; },
                          [=]() {
                              lv_arc_set_value(uic_ProfileScreen_dials_tempGauge, currentTemp * 10.0f);
                              setTempLabel(uic_ProfileScreen_dials_tempText, currentTemp);
                          },
                          &currentTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_MenuScreen; }, [=]() { adjustTempTarget(ui_MenuScreen_dials); },
                          &targetTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_StatusScreen; },
                          [=]() {
                              setTempLabel(ui_StatusScreen_targetTemp, targetTemp);
                              adjustTempTarget(ui_StatusScreen_dials);
                          },
                          &targetTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; },
                          [=]() {
                              setTempLabel(ui_BrewScreen_targetTemp, targetTemp);
                              adjustTempTarget(ui_BrewScreen_dials);
                          },
                          &targetTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_GrindScreen; }, [=]() { adjustTempTarget(ui_GrindScreen_dials); },
                          &targetTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_SimpleProcessScreen; },
                          [=]() {
                              setTempLabel(ui_SimpleProcessScreen_targetTemp, targetTemp);
                              adjustTempTarget(ui_SimpleProcessScreen_dials);
                          },
                          &targetTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_ProfileScreen; }, [=]() { adjustTempTarget(ui_ProfileScreen_dials); },
                          &targetTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_MenuScreen; },
                          [=]() {
                              lv_arc_set_value(uic_MenuScreen_dials_pressureGauge, pressure * 10.0f);
                              lv_label_set_text_fmt(uic_MenuScreen_dials_pressureText, "%.1f bar", pressure);
                          },
                          &pressure);
    effect_mgr.use_effect([=] { return currentScreen == ui_StatusScreen; },
                          [=]() {
                              lv_arc_set_value(uic_StatusScreen_dials_pressureGauge, pressure * 10.0f);
                              lv_label_set_text_fmt(uic_StatusScreen_dials_pressureText, "%.1f bar", pressure);
                          },
                          &pressure);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; },
                          [=]() {
                              lv_arc_set_value(uic_BrewScreen_dials_pressureGauge, pressure * 10.0f);
                              lv_label_set_text_fmt(uic_BrewScreen_dials_pressureText, "%.1f bar", pressure);
                          },
                          &pressure);
    effect_mgr.use_effect(
        [=] { return currentScreen == ui_BrewScreen; },
        [=]() {
            lv_obj_t *brewPanel = lv_obj_get_parent(ui_BrewScreen_mainLabel3);
            brewSteamEnsure(brewPanel);
            // Every brew state is now carried by a purpose-built icon + animation,
            // so the caption label stays empty in all of them.
            lv_label_set_text(ui_BrewScreen_mainLabel3, "");
            if (active != 0) {
                brewSteamSetState(BrewSteamState::Brewing); // white, strong steady steam
            } else if (pidFreezeGraceActive != 0) {
                brewSteamSetState(BrewSteamState::FreezeGrace); // cyan waves, settling/waiting
            } else if (pressureAvailable != 0 && brewIdleVenting != 0) {
                brewSteamSetState(BrewSteamState::Venting); // white wind gust, flickering
            } else if (stableTemp == 0) {
                // Not at target yet: amber rising steam when below, blue drifting steam when above.
                brewSteamSetState(currentTemp > targetTemp ? BrewSteamState::Cooling : BrewSteamState::Heating);
            } else {
                brewSteamSetState(BrewSteamState::Ready); // green calm steam
            }
        },
        &active, &pidFreezeGraceActive, &brewIdleVenting, &stableTemp, &pressureAvailable, &currentTemp,
        &targetTemp);
    effect_mgr.use_effect([=] { return currentScreen == ui_GrindScreen; },
                          [=]() {
                              lv_arc_set_value(uic_GrindScreen_dials_pressureGauge, pressure * 10.0f);
                              lv_label_set_text_fmt(uic_GrindScreen_dials_pressureText, "%.1f bar", pressure);
                          },
                          &pressure);
    effect_mgr.use_effect([=] { return currentScreen == ui_SimpleProcessScreen; },
                          [=]() {
                              lv_arc_set_value(uic_SimpleProcessScreen_dials_pressureGauge, pressure * 10.0f);
                              lv_label_set_text_fmt(uic_SimpleProcessScreen_dials_pressureText, "%.1f bar", pressure);
                          },
                          &pressure);
    effect_mgr.use_effect([=] { return currentScreen == ui_ProfileScreen; },
                          [=]() {
                              lv_arc_set_value(uic_ProfileScreen_dials_pressureGauge, pressure * 10.0f);
                              lv_label_set_text_fmt(uic_ProfileScreen_dials_pressureText, "%.1f bar", pressure);
                          },
                          &pressure);
    effect_mgr.use_effect([=] { return currentScreen == ui_StandbyScreen; },
                          [=]() {
                              updateAvailable ? lv_obj_clear_flag(ui_StandbyScreen_updateIcon, LV_OBJ_FLAG_HIDDEN)
                                              : lv_obj_add_flag(ui_StandbyScreen_updateIcon, LV_OBJ_FLAG_HIDDEN);
                          },
                          &updateAvailable);
    effect_mgr.use_effect([=] { return currentScreen == ui_StandbyScreen; },
                          [=]() {
                              bool deactivated = true;
                              if (updateActive) {
                                  lv_label_set_text_fmt(ui_StandbyScreen_mainLabel, "Updating...");
                              } else if (error) {
                                  if (controller->getError() == ERROR_CODE_RUNAWAY) {
                                      lv_label_set_text_fmt(ui_StandbyScreen_mainLabel, "Temperature error, please restart");
                                  }
                              } else if (autotuning) {
                                  lv_label_set_text_fmt(ui_StandbyScreen_mainLabel, "Autotuning...");
                              } else if (waitingForController) {
                                  lv_label_set_text_fmt(ui_StandbyScreen_mainLabel, "Waiting for controller...");
                              } else {
                                  deactivated = !initialized;
                              }
                              _ui_flag_modify(ui_StandbyScreen_mainLabel, LV_OBJ_FLAG_HIDDEN, deactivated);
                              _ui_flag_modify(ui_StandbyScreen_touchIcon, LV_OBJ_FLAG_HIDDEN, !deactivated);
                              _ui_flag_modify(ui_StandbyScreen_statusContainer, LV_OBJ_FLAG_HIDDEN, !deactivated);
                          },
                          &updateAvailable, &error, &autotuning, &waitingForController, &initialized);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; },
                          [=]() {
                              if (brewVolumetric) {
                                  lv_label_set_text_fmt(ui_BrewScreen_targetDuration, "%.1fg", targetVolume);
                              } else {
                                  const double secondsDouble = targetDuration;
                                  const auto minutes = static_cast<int>(secondsDouble / 60.0);
                                  const auto seconds = static_cast<int>(secondsDouble) % 60;
                                  lv_label_set_text_fmt(ui_BrewScreen_targetDuration, "%2d:%02d", minutes, seconds);
                              }
                          },
                          &targetDuration, &targetVolume, &brewVolumetric);
    effect_mgr.use_effect([=] { return currentScreen == ui_GrindScreen; },
                          [=]() {
                              if (volumetricMode) {
                                  lv_label_set_text_fmt(ui_GrindScreen_targetDuration, "%.1fg", grindVolume);
                              } else {
                                  const double secondsDouble = grindDuration / 1000.0;
                                  const auto minutes = static_cast<int>(secondsDouble / 60.0);
                                  const auto seconds = static_cast<int>(secondsDouble) % 60;
                                  lv_label_set_text_fmt(ui_GrindScreen_targetDuration, "%2d:%02d", minutes, seconds);
                              }
                          },
                          &grindDuration, &grindVolume, &volumetricMode);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; },
                          [=]() {
                              lv_img_set_src(ui_BrewScreen_Image4, brewVolumetric ? &ui_img_1424216268 : &ui_img_360122106);
                              _ui_flag_modify(ui_BrewScreen_byTimeButton, LV_OBJ_FLAG_HIDDEN, brewVolumetric);
                          },
                          &brewVolumetric);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; },
                          [=]() {
                              lv_label_set_text_fmt(ui_BrewScreen_inletWaterTemp, "%d°C", incomingWaterTempC);
                          },
                          &incomingWaterTempC);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; },
                          [=]() {
                              _ui_flag_modify(ui_BrewScreen_inletWaterContainer, LV_OBJ_FLAG_HIDDEN, pressureAvailable);
                          },
                          &pressureAvailable);
    effect_mgr.use_effect(
        [=] { return currentScreen == ui_GrindScreen; },
        [=]() {
            lv_img_set_src(ui_GrindScreen_targetSymbol, volumetricMode ? &ui_img_1424216268 : &ui_img_360122106);
            ui_object_set_themeable_style_property(ui_GrindScreen_weightLabel, LV_PART_MAIN | LV_STATE_DEFAULT,
                                                   LV_STYLE_TEXT_COLOR,
                                                   volumetricMode ? _ui_theme_color_Dark : _ui_theme_color_NiceWhite);
            ui_object_set_themeable_style_property(ui_GrindScreen_volumetricButton, LV_PART_MAIN | LV_STATE_DEFAULT,
                                                   LV_STYLE_IMG_RECOLOR,
                                                   volumetricMode ? _ui_theme_color_Dark : _ui_theme_color_NiceWhite);
            ui_object_set_themeable_style_property(ui_GrindScreen_modeSwitch, LV_PART_MAIN | LV_STATE_DEFAULT, LV_STYLE_BG_COLOR,
                                                   volumetricMode ? _ui_theme_color_NiceWhite : _ui_theme_color_Dark);
        },
        &volumetricMode);
    effect_mgr.use_effect([=] { return currentScreen == ui_GrindScreen; },
                          [=]() { _ui_flag_modify(ui_GrindScreen_modeSwitch, LV_OBJ_FLAG_HIDDEN, volumetricAvailable); },
                          &volumetricAvailable);
    effect_mgr.use_effect([=] { return currentScreen == ui_SimpleProcessScreen; },
                          [=]() {
                              if (mode == MODE_STEAM) {
                                  _ui_flag_modify(ui_SimpleProcessScreen_goButton, LV_OBJ_FLAG_HIDDEN, active);
                                  lv_imgbtn_set_src(ui_SimpleProcessScreen_goButton, LV_IMGBTN_STATE_RELEASED, nullptr,
                                                    &ui_img_691326438, nullptr);
                              } else {
                                  lv_imgbtn_set_src(ui_SimpleProcessScreen_goButton, LV_IMGBTN_STATE_RELEASED, nullptr,
                                                    active ? &ui_img_1456692430 : &ui_img_445946954, nullptr);
                              }
                          },
                          &active, &mode);
    effect_mgr.use_effect([=] { return currentScreen == ui_GrindScreen; },
                          [=]() {
                              lv_imgbtn_set_src(ui_GrindScreen_startButton, LV_IMGBTN_STATE_RELEASED, nullptr,
                                                grindActive ? &ui_img_1456692430 : &ui_img_445946954, nullptr);
                          },
                          &grindActive);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; },
                          [=] { lv_label_set_text(ui_BrewScreen_profileName, selectedProfile.label.c_str()); },
                          &selectedProfileId);

    effect_mgr.use_effect(
        [=] { return currentScreen == ui_ProfileScreen; },
        [=] {
            if (profileLoaded) {
                _ui_flag_modify(ui_ProfileScreen_profileDetails, LV_OBJ_FLAG_HIDDEN, _UI_MODIFY_FLAG_REMOVE);
                _ui_flag_modify(ui_ProfileScreen_loadingSpinner, LV_OBJ_FLAG_HIDDEN, _UI_MODIFY_FLAG_ADD);
                lv_label_set_text(ui_ProfileScreen_profileName, favoritedProfiles[currentProfileIdx].label.c_str());
                lv_label_set_text(ui_ProfileScreen_mainLabel, currentProfileIdx == 0 ? "Current profile" : "Select profile");

                const auto minutes = static_cast<int>(favoritedProfiles[currentProfileIdx].getTotalDuration() / 60.0 - 0.5);
                const auto seconds = static_cast<int>(favoritedProfiles[currentProfileIdx].getTotalDuration()) % 60;
                lv_label_set_text_fmt(ui_ProfileScreen_targetDuration2, "%2d:%02d", minutes, seconds);
                lv_label_set_text_fmt(ui_ProfileScreen_targetTemp2, "%d°C",
                                      static_cast<int>(favoritedProfiles[currentProfileIdx].temperature));
                unsigned int phaseCount = favoritedProfiles[currentProfileIdx].getPhaseCount();
                unsigned int stepCount = favoritedProfiles[currentProfileIdx].phases.size();
                lv_label_set_text_fmt(ui_ProfileScreen_stepsLabel, "%d step%s", stepCount, stepCount > 1 ? "s" : "");
                lv_label_set_text_fmt(ui_ProfileScreen_phasesLabel, "%d phase%s", phaseCount, phaseCount > 1 ? "s" : "");
            } else {
                _ui_flag_modify(ui_ProfileScreen_profileDetails, LV_OBJ_FLAG_HIDDEN, _UI_MODIFY_FLAG_ADD);
                _ui_flag_modify(ui_ProfileScreen_loadingSpinner, LV_OBJ_FLAG_HIDDEN, _UI_MODIFY_FLAG_REMOVE);
            }

            ui_object_set_themeable_style_property(ui_ProfileScreen_previousProfileBtn, LV_PART_MAIN | LV_STATE_DEFAULT,
                                                   LV_STYLE_IMG_RECOLOR,
                                                   currentProfileIdx > 0 ? _ui_theme_color_NiceWhite : _ui_theme_color_SemiDark);
            ui_object_set_themeable_style_property(ui_ProfileScreen_previousProfileBtn, LV_PART_MAIN | LV_STATE_DEFAULT,
                                                   LV_STYLE_IMG_RECOLOR_OPA,
                                                   currentProfileIdx > 0 ? _ui_theme_alpha_NiceWhite : _ui_theme_alpha_SemiDark);
            ui_object_set_themeable_style_property(
                ui_ProfileScreen_nextProfileBtn, LV_PART_MAIN | LV_STATE_DEFAULT, LV_STYLE_IMG_RECOLOR,
                currentProfileIdx < favoritedProfiles.size() - 1 ? _ui_theme_color_NiceWhite : _ui_theme_color_SemiDark);
            ui_object_set_themeable_style_property(
                ui_ProfileScreen_nextProfileBtn, LV_PART_MAIN | LV_STATE_DEFAULT, LV_STYLE_IMG_RECOLOR_OPA,
                currentProfileIdx < favoritedProfiles.size() - 1 ? _ui_theme_alpha_NiceWhite : _ui_theme_alpha_SemiDark);
        },
        &currentProfileIdx, &profileLoaded);

    // Show/hide grind button based on SmartGrind setting or Alt Relay function
    effect_mgr.use_effect([=] { return currentScreen == ui_MenuScreen; },
                          [=]() {
                              grindAvailable ? lv_obj_clear_flag(ui_MenuScreen_grindBtn, LV_OBJ_FLAG_HIDDEN)
                                             : lv_obj_add_flag(ui_MenuScreen_grindBtn, LV_OBJ_FLAG_HIDDEN);
                          },
                          &grindAvailable);
    effect_mgr.use_effect([=] { return currentScreen == ui_BrewScreen; },
                          [=]() {
                              if (volumetricAvailable && bluetoothScales) {
                                  lv_label_set_text_fmt(ui_BrewScreen_weightLabel, "%.1fg", bluetoothWeight);
                              } else {
                                  lv_label_set_text(ui_BrewScreen_weightLabel, "-");
                              }
                          },
                          &bluetoothWeight, &volumetricAvailable, &bluetoothScales);
    effect_mgr.use_effect([=] { return currentScreen == ui_GrindScreen; },
                          [=]() {
                              if (volumetricAvailable && bluetoothScales) {
                                  lv_label_set_text_fmt(ui_GrindScreen_weightLabel, "%.1fg", bluetoothWeight);
                              } else {
                                  lv_label_set_text(ui_GrindScreen_weightLabel, "-");
                              }
                          },
                          &bluetoothWeight, &volumetricAvailable, &bluetoothScales);
    effect_mgr.use_effect(
        [=] { return currentScreen == ui_BrewScreen; },
        [=]() {
            _ui_flag_modify(ui_BrewScreen_adjustments, LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Settings);
            _ui_flag_modify(ui_BrewScreen_acceptButton, LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Settings);
            _ui_flag_modify(ui_BrewScreen_saveButton, LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Settings);
            _ui_flag_modify(ui_BrewScreen_saveAsNewButton, LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Settings);
            _ui_flag_modify(ui_BrewScreen_startButton, LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Brew);
            _ui_flag_modify(ui_BrewScreen_profileInfo, LV_OBJ_FLAG_HIDDEN, brewScreenState == BrewScreenState::Brew);
            _ui_flag_modify(ui_BrewScreen_modeSwitch, LV_OBJ_FLAG_HIDDEN,
                            brewScreenState == BrewScreenState::Brew && volumetricAvailable);
            // Move the control container (weight/scale row) below the steam cup during brew so the
            // weight label is not occluded by the cup graphic; restore default position otherwise.
            lv_obj_set_y(ui_BrewScreen_controlContainer, brewScreenState == BrewScreenState::Brew ? 40 : -10);
            if (volumetricAvailable) {
                lv_img_set_src(ui_BrewScreen_volumetricButton, bluetoothScales ? &ui_img_1424216268 : &ui_img_flowmeter_png);
            }
        },
        &brewScreenState, &volumetricAvailable, &bluetoothScales);
    effect_mgr.use_effect(
        [=] { return currentScreen == ui_BrewScreen; },
        [=]() {
            ui_object_set_themeable_style_property(ui_BrewScreen_saveButton, LV_PART_MAIN | LV_STATE_DEFAULT,
                                                   LV_STYLE_IMG_RECOLOR,
                                                   profileDirty ? _ui_theme_color_NiceWhite : _ui_theme_color_SemiDark);
            ui_object_set_themeable_style_property(ui_BrewScreen_saveButton, LV_PART_MAIN | LV_STATE_DEFAULT,
                                                   LV_STYLE_IMG_RECOLOR_OPA,
                                                   profileDirty ? _ui_theme_alpha_NiceWhite : _ui_theme_alpha_SemiDark);
            ui_object_set_themeable_style_property(ui_BrewScreen_saveAsNewButton, LV_PART_MAIN | LV_STATE_DEFAULT,
                                                   LV_STYLE_IMG_RECOLOR,
                                                   profileDirty ? _ui_theme_color_NiceWhite : _ui_theme_color_SemiDark);
            ui_object_set_themeable_style_property(ui_BrewScreen_saveAsNewButton, LV_PART_MAIN | LV_STATE_DEFAULT,
                                                   LV_STYLE_IMG_RECOLOR_OPA,
                                                   profileDirty ? _ui_theme_alpha_NiceWhite : _ui_theme_alpha_SemiDark);
        },
        &brewScreenState, &profileDirty);
}

void DefaultUI::handleScreenChange() {
    lv_obj_t *current = lv_scr_act();

    if (current != *targetScreen) {
        if (*targetScreen == ui_StandbyScreen) {
            standbyEnterTime = millis();
        } else if (current == ui_StandbyScreen) {
            const Settings &settings = controller->getSettings();
            setBrightness(settings.getMainBrightness());
        }

        _ui_screen_change(targetScreen, LV_SCR_LOAD_ANIM_NONE, 0, 0, targetScreenInit);
        // Brew screen is lazy-init; hide the static caption after each creation.
        if (*targetScreen == ui_BrewScreen && ui_BrewScreen_Label1 != nullptr) {
            lv_obj_add_flag(ui_BrewScreen_Label1, LV_OBJ_FLAG_HIDDEN);
        }
        lv_obj_del(current);
        rerender = true;
    }
}

void DefaultUI::updateStandbyScreen() {
    if (standbyEnterTime > 0) {
        const Settings &settings = controller->getSettings();
        const unsigned long now = millis();
        if (now - standbyEnterTime >= settings.getStandbyBrightnessTimeout()) {
            setBrightness(settings.getStandbyBrightness());
        }
    }

    if (!apActive && WiFi.status() == WL_CONNECTED && !updateActive && !error && !autotuning && !waitingForController &&
        initialized) {
        time_t now;
        struct tm timeinfo;

        localtime_r(&now, &timeinfo);
        // allocate enough space for both 12h/24h time formats
        if (getLocalTime(&timeinfo, 500)) {
            char time[9];
            Settings &settings = controller->getSettings();
            const char *format = settings.isClock24hFormat() ? "%H:%M" : "%I:%M %p";
            strftime(time, sizeof(time), format, &timeinfo);
            lv_label_set_text(ui_StandbyScreen_time, time);
            lv_obj_clear_flag(ui_StandbyScreen_time, LV_OBJ_FLAG_HIDDEN);

            christmasMode = (timeinfo.tm_mon == 11 && timeinfo.tm_mday < 27) || (timeinfo.tm_mon == 0 && timeinfo.tm_mday < 6);
        }
    } else {
        lv_obj_add_flag(ui_StandbyScreen_time, LV_OBJ_FLAG_HIDDEN);
    }
    controller->getClientController()->isConnected() ? lv_obj_clear_flag(ui_StandbyScreen_bluetoothIcon, LV_OBJ_FLAG_HIDDEN)
                                                     : lv_obj_add_flag(ui_StandbyScreen_bluetoothIcon, LV_OBJ_FLAG_HIDDEN);
    !apActive &&WiFi.status() == WL_CONNECTED ? lv_obj_clear_flag(ui_StandbyScreen_wifiIcon, LV_OBJ_FLAG_HIDDEN)
                                              : lv_obj_add_flag(ui_StandbyScreen_wifiIcon, LV_OBJ_FLAG_HIDDEN);
}

void DefaultUI::updateStatusScreen() const {
    // Copy process pointers to avoid race conditions with controller thread
    Process *process = controller->getProcess();
    Process *lastProcess = controller->getLastProcess();

    if (process == nullptr) {
        process = lastProcess;
    }
    if (process == nullptr || process->getType() != MODE_BREW) {
        return;
    }

    // Additional safety: Validate that the process pointer is still valid
    // by checking if it matches either current or last process
    if (process != controller->getProcess() && process != controller->getLastProcess()) {
        ESP_LOGW("DefaultUI", "Process pointer became invalid during access, skipping update");
        return;
    }

    auto *brewProcess = static_cast<BrewProcess *>(process);
    if (brewProcess == nullptr) {
        ESP_LOGE("DefaultUI", "brewProcess is null after cast");
        return;
    }

    // Validate the brewProcess object before accessing its members
    // Check if the object is in a reasonable state by validating key fields
    if (brewProcess->profile.phases.empty() || brewProcess->phaseIndex >= brewProcess->profile.phases.size()) {
        ESP_LOGE("DefaultUI", "brewProcess phaseIndex out of bounds: %u >= %zu", brewProcess->phaseIndex,
                 brewProcess->profile.phases.size());
        return;
    }

    // Final safety check before accessing brewProcess members
    if (!brewProcess) {
        ESP_LOGE("DefaultUI", "brewProcess became null after validation");
        return;
    }

    const auto phase = brewProcess->currentPhase;

    unsigned long now = millis();
    if (!process->isActive()) {
        // Add bounds check for finished timestamp
        if (brewProcess && brewProcess->finished > 0) {
            now = brewProcess->finished;
        }
    }

    lv_label_set_text(ui_StatusScreen_stepLabel, phase.phase == PhaseType::PHASE_TYPE_BREW ? "BREW" : "INFUSION");
    String phaseText = "Finished";
    if (process->isActive()) {
        phaseText = phase.name;
    } else if (controller->getSettings().isDelayAdjust() && !process->isComplete()) {
        phaseText = "Calibrating...";
    }
    lv_label_set_text(ui_StatusScreen_phaseLabel, phaseText.c_str());

    // Add bounds check for processStarted timestamp
    if (brewProcess && brewProcess->processStarted > 0 && now >= brewProcess->processStarted) {
        const unsigned long processDuration = now - brewProcess->processStarted;
        const double processSecondsDouble = processDuration / 1000.0;
        const auto processMinutes = static_cast<int>(processSecondsDouble / 60.0);
        const auto processSeconds = static_cast<int>(processSecondsDouble) % 60;
        lv_label_set_text_fmt(ui_StatusScreen_currentDuration, "%2d:%02d", processMinutes, processSeconds);
    } else {
        lv_label_set_text_fmt(ui_StatusScreen_currentDuration, "00:00");
    }

    if (brewProcess && brewProcess->target == ProcessTarget::VOLUMETRIC && phase.hasWeightTarget()) {
        Target target = phase.getDisplayWeightTarget();
        lv_bar_set_value(ui_StatusScreen_brewBar, brewProcess->currentVolume * 10.0, LV_ANIM_OFF);
        lv_bar_set_range(ui_StatusScreen_brewBar, 0, target.value * 10.0 + 1.0);
        lv_label_set_text_fmt(ui_StatusScreen_brewLabel, "%.1f / %.1fg", brewProcess->currentVolume, target.value);
    } else if (brewProcess) {
        // Add bounds check for currentPhaseStarted timestamp
        if (brewProcess->currentPhaseStarted > 0 && now >= brewProcess->currentPhaseStarted) {
            const unsigned long progress = now - brewProcess->currentPhaseStarted;
            lv_bar_set_value(ui_StatusScreen_brewBar, progress, LV_ANIM_OFF);
            lv_bar_set_range(ui_StatusScreen_brewBar, 0, std::max(static_cast<int>(brewProcess->getPhaseDuration()), 1));
            lv_label_set_text_fmt(ui_StatusScreen_brewLabel, "%d / %ds", progress / 1000, brewProcess->getPhaseDuration() / 1000);
        } else {
            lv_bar_set_value(ui_StatusScreen_brewBar, 0, LV_ANIM_OFF);
            lv_bar_set_range(ui_StatusScreen_brewBar, 0, 1);
            lv_label_set_text(ui_StatusScreen_brewLabel, "0s");
        }
    }

    if (brewProcess && brewProcess->target == ProcessTarget::TIME) {
        const unsigned long targetDuration = brewProcess->getTotalDuration();
        const double targetSecondsDouble = targetDuration / 1000.0;
        const auto targetMinutes = static_cast<int>(targetSecondsDouble / 60.0);
        const auto targetSeconds = static_cast<int>(targetSecondsDouble) % 60;
        lv_label_set_text_fmt(ui_StatusScreen_targetDuration, "%2d:%02d", targetMinutes, targetSeconds);
    } else if (brewProcess) {
        lv_label_set_text_fmt(ui_StatusScreen_targetDuration, "%.1fg", brewProcess->getBrewVolume());
    }
    if (brewProcess) {
        lv_img_set_src(ui_StatusScreen_Image8,
                       brewProcess->target == ProcessTarget::TIME ? &ui_img_360122106 : &ui_img_1424216268);
    }

    if (brewProcess && brewProcess->isAdvancedPump()) {
        float pressure = brewProcess->getPumpPressure();
        const double percentage = 1.0 - static_cast<double>(pressure) / static_cast<double>(pressureScaling);
        adjustTarget(uic_StatusScreen_dials_pressureTarget, percentage, -62.0, 124.0);
    } else {
        const double percentage = 1.0 - 0.5;
        adjustTarget(uic_StatusScreen_dials_pressureTarget, percentage, -62.0, 124.0);
    }

    // Brew finished adjustments
    if (process->isActive()) {
        lv_obj_add_flag(ui_StatusScreen_brewVolume, LV_OBJ_FLAG_HIDDEN);
    } else {
        // Re-validate brewProcess pointer before accessing members
        if (brewProcess && brewProcess->target == ProcessTarget::VOLUMETRIC) {
            lv_obj_clear_flag(ui_StatusScreen_brewVolume, LV_OBJ_FLAG_HIDDEN);
        }
        lv_obj_add_flag(ui_StatusScreen_barContainer, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui_StatusScreen_labelContainer, LV_OBJ_FLAG_HIDDEN);
        if (brewProcess) {
            lv_label_set_text_fmt(ui_StatusScreen_brewVolume, "%.1lfg", brewProcess->currentVolume);
        }
        lv_imgbtn_set_src(ui_StatusScreen_pauseButton, LV_IMGBTN_STATE_RELEASED, nullptr, &ui_img_631115820, nullptr);
    }
}

void DefaultUI::adjustDials(lv_obj_t *dials) {
    lv_obj_t *tempGauge = ui_comp_get_child(dials, UI_COMP_DIALS_TEMPGAUGE);
    lv_obj_t *tempText = ui_comp_get_child(dials, UI_COMP_DIALS_TEMPTEXT);
    lv_obj_t *pressureTarget = ui_comp_get_child(dials, UI_COMP_DIALS_PRESSURETARGET);
    lv_obj_t *pressureGauge = ui_comp_get_child(dials, UI_COMP_DIALS_PRESSUREGAUGE);
    lv_obj_t *pressureText = ui_comp_get_child(dials, UI_COMP_DIALS_PRESSURETEXT);
    lv_obj_t *pressureSymbol = ui_comp_get_child(dials, UI_COMP_DIALS_IMAGE6);
    _ui_flag_modify(pressureTarget, LV_OBJ_FLAG_HIDDEN, pressureAvailable);
    _ui_flag_modify(pressureGauge, LV_OBJ_FLAG_HIDDEN, pressureAvailable);
    _ui_flag_modify(pressureText, LV_OBJ_FLAG_HIDDEN, pressureAvailable);
    _ui_flag_modify(pressureSymbol, LV_OBJ_FLAG_HIDDEN, pressureAvailable);
    lv_obj_set_x(tempText, pressureAvailable ? -50 : 0);
    lv_obj_set_y(tempText, pressureAvailable ? -205 : -180);
    lv_arc_set_bg_angles(tempGauge, 118, pressureAvailable ? 242 : 62);
    lv_arc_set_range(pressureGauge, 0, pressureScaling * 10);
}

inline void DefaultUI::adjustTempTarget(lv_obj_t *dials) {
    double gaugeAngle = pressureAvailable ? 124.0 : 304;
    double gaugeStart = pressureAvailable ? 118.0 : -62;
    double percentage = static_cast<double>(targetTemp) / 160.0;
    lv_obj_t *tempTarget = ui_comp_get_child(dials, UI_COMP_DIALS_TEMPTARGET);
    adjustTarget(tempTarget, percentage, gaugeStart, gaugeAngle);
}

void DefaultUI::applyTheme() {
    const Settings &settings = controller->getSettings();
    int newThemeMode = settings.getThemeMode();

    if (newThemeMode != currentThemeMode) {
        currentThemeMode = newThemeMode;
        ui_theme_set(currentThemeMode);

        if (AmoledDisplayDriver::getInstance() == panelDriver && currentThemeMode == UI_THEME_DEFAULT) {
            enable_amoled_black_theme_override(lv_disp_get_default());
        }
    }
}

void DefaultUI::adjustTarget(lv_obj_t *obj, double percentage, double start, double range) const {
    double angle = start + range - range * percentage;

    lv_img_set_angle(obj, angle * -10);
    int x = static_cast<int>(std::cos(angle * M_PI / 180.0f) * 235.0);
    int y = static_cast<int>(std::sin(angle * M_PI / 180.0f) * -235.0);
    lv_obj_set_pos(obj, x, y);
}

void DefaultUI::loopTask(void *arg) {
    auto *ui = static_cast<DefaultUI *>(arg);
    while (true) {
        ui->loop();
        vTaskDelay(25 / portTICK_PERIOD_MS);
    }
}

void DefaultUI::profileLoopTask(void *arg) {
    auto *ui = static_cast<DefaultUI *>(arg);
    while (true) {
        ui->loopProfiles();
        vTaskDelay(25 / portTICK_PERIOD_MS);
    }
}
