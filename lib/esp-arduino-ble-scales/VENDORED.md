# Vendored: esp-arduino-ble-scales

Source: https://github.com/gaggimate/esp-arduino-ble-scales

- Base commit: `67e0aa19` (2026-05-26, "Merge pull request #32 — dot-scale") —
  the last commit that builds against NimBLE-Arduino 1.4.x. Later upstream
  commits require NimBLE 2.5 / esp-nimble-cpp, which this fork's platform
  (espressif32@6.12.0 + NimBLE-Arduino ^1.4.0) cannot build.
- Plus cherry-pick: `c18e6b15` ("feat(dot): decode battery level from FFF1
  stream") — 7 lines in `src/scales/dot.{h,cpp}` only; NimBLE-agnostic.

Why vendored instead of `lib_deps`-pinned: the battery decode landed upstream
*after* the NimBLE 2.5 switch, so no single upstream ref has both "NimBLE 1.4
compatible" and "Dot battery". Vendoring the pin + the one clean patch was the
smallest way to get both.

To update: diff against upstream, or drop this folder and return to a
`lib_deps` ref once the fork moves to NimBLE 2.5.
