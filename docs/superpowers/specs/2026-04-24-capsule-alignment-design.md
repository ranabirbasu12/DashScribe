# Capsule Alignment Fix — Design

**Date:** 2026-04-24
**Scope:** Fix incorrect capsule height and button alignment in the floating bar's recording, processing, and error states.
**Out of scope:** Idle capsule, toast, warning, tooltip, and any app-wide (main window) styling.

## Problem

On the floating bar, the recording/processing/error capsules render approximately 86px tall instead of the intended 42px. The result: buttons that were sized (24×24) for a 42px-tall pill look stranded inside a much taller oval, and their centers no longer align with the centers of the capsule's end curvatures.

### Root cause (CSS layout bug)

`main.py` sizes the pywebview bar window to `BAR_RECORDING_H + BAR_TOAST_HEADROOM` = `42 + 44 = 86px` so that the device-change toast has transparent space to render above the capsule.

`static/bar.css` defines the shared per-state rule:

```css
.bar-idle, .bar-recording, .bar-processing, .bar-error {
    position: absolute;
    inset: 0;
    ...
}
```

`inset: 0` makes each state element fill the entire `.bar` container, which is the full 86px window — not just the bottom 42px where the capsule is supposed to live. The idle state overrides this with `inset: auto` plus explicit dimensions, so it's unaffected. Recording, processing, and error do not override it and consequently render as 86px-tall pills.

Secondary issue: even at the intended 42px height, the existing horizontal padding of `12px` around the 24px buttons is wrong. Correct alignment math requires `(cap_height − button_size) / 2 = (42 − 24) / 2 = 9px`. The 12px value puts button centers 24px from the capsule edge while cap centers sit 21px from the edge — a 3px drift that existed before this work.

### Why it looked fine on the external monitor

The toast headroom was added after the initial bar design was validated. On the larger physical size of an external display the distortion was less visually obvious; at closer laptop-display viewing distance the enlarged aspect ratio becomes plainly visible.

## Goals

1. Recording, processing, and error capsules render at exactly the intended 42px height, occupying only the bottom 42px of the bar window.
2. Buttons that sit against the pill caps (recording state: `cancel-btn` on the left and `stop-btn` on the right; processing state: `processing-cancel-btn` on the left) have their centers exactly on the centers of the respective cap curvatures.
3. The error capsule (42×42 square) continues to flex-center its single 28px retry button as today; its alignment is trivially correct since the container is square. No cap-inset math required there.
4. The 44px toast headroom above the capsule remains fully transparent (toast continues to render there).
5. The fix is self-correcting: changing `--cap-h` or `--btn-size` in one place keeps cap-adjacent button alignment correct without further edits.

## Non-goals

- Changing button sizes, colors, or icons.
- Changing Python window sizing, `BAR_TOAST_HEADROOM`, or any `main.py` constants.
- Touching the idle capsule, error flash, warning, toast, or tooltip styles.
- Making the bar responsive to display DPI. On macOS pywebview, CSS pixels already map 1:1 to window points regardless of retina scaling, so no DPI logic is needed.

## Design

### Sizing model — design tokens on `.bar`

Declare three CSS custom properties at the `.bar` selector. They are the single source of truth for capsule geometry:

| Property | Value | Meaning |
|---|---|---|
| `--cap-h` | `42px` | Capsule height (also: pill end-cap diameter). |
| `--btn-size` | `24px` | Button diameter. |
| `--cap-inset` | `calc((var(--cap-h) - var(--btn-size)) / 2)` | Horizontal padding between capsule edge and button edge. Derived, never typed. Equals `9px` at current values. |

**Geometric invariant:** the button's center X from the capsule edge is `cap-inset + btn-size/2` = `(cap-h − btn-size)/2 + btn-size/2` = `cap-h/2`. The cap center X is also `cap-h/2` (a semicircle of radius `cap-h/2`). Therefore button center ≡ cap center, by construction. Change `--cap-h` or `--btn-size` and the inset recomputes automatically; alignment is preserved.

Vertical centering is already handled by the existing `align-items: center` on the per-state flex container.

### Layout change — pin capsule to the bottom

Replace `inset: 0` with explicit bottom positioning and capsule-height for the three affected states. The shared rule is split so idle keeps its current behavior:

**Before** (current, buggy):
```css
.bar-idle, .bar-recording, .bar-processing, .bar-error {
    position: absolute;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    pointer-events: none;
}
```

**After:**
```css
.bar-idle, .bar-recording, .bar-processing, .bar-error {
    position: absolute;
    display: none;
    align-items: center;
    justify-content: center;
    pointer-events: none;
}

/* Idle keeps its explicit centered positioning (unchanged) */

/* Recording, processing, error pin to the bottom 42px only */
.bar-recording, .bar-processing, .bar-error {
    bottom: 0;
    left: 0;
    right: 0;
    height: var(--cap-h);
}
```

Horizontal padding for recording and processing changes from `0 12px` to `0 var(--cap-inset)`.

The `height: 100%` currently set on `.bar-recording, .bar-processing` in the existing stylesheet is removed (now redundant with `height: var(--cap-h)`), and the `height: 100%` on `.bar-error` is replaced the same way.

### What stays the same

- Button sizes (24×24 for stop/cancel, 28×28 for retry — retry stays as-is since it's error-state-specific).
- Button border-radii, colors, hover behavior.
- Idle capsule (28×5, explicitly positioned, `inset: auto`) — untouched.
- Error flash overlay (`.error-flash` uses `inset: 0` but is scoped inside `.bar-error`, which is now 42px; it will correctly cover the capsule only).
- Toast, warning, tooltip — all positioned relative to `.bar` with `bottom: 100%`, which continues to reference the full 86px bar. Their relative placement above the capsule is preserved because the capsule still sits at the bottom of the bar.
- All Python (`main.py`) constants and sizing.
- `bar.js` — no JavaScript changes needed.

### File touched

Only `static/bar.css`.

## Verification plan

1. **Visual spot check.** With the app running on the laptop, trigger each state (idle → recording → processing → error) via the hotkey and the capsule buttons. Confirm the capsule occupies roughly half the window vertically (42 of 86px) and buttons look seated in the cap curves.
2. **Measurement via DevTools.** Open pywebview inspector on the bar. Read computed style of `.bar-recording`: `height` should be `42px`, `padding-left`/`padding-right` should be `9px`. Inspect the `#stop-btn` and `#cancel-btn`: their computed bounding boxes' center X from the capsule's left/right edges should both be `21px` (= cap-h/2).
3. **Cross-state consistency.** Switch between recording, processing, and error states. Confirm capsule height does not change between states (all three use `var(--cap-h)`).
4. **Toast still works.** Trigger a device-change toast while recording. Confirm it renders cleanly in the transparent 44px region above the capsule, not overlapping the pill.
5. **No regression on external monitor.** Plug in the external display, move the app to it, repeat step 1. Expect identical behavior — the fix is display-agnostic.

## Risks

- **`.bar-error` uses `inset: 0` on its nested `.error-flash`.** With `.bar-error` now 42px tall instead of 86px, the flash overlay correctly covers only the capsule. This is the desired behavior (the flash was probably also oversized before and we didn't notice).
- **Tooltip / warning / toast positioning.** All three use `bottom: 100%` relative to `.bar` (not relative to the capsule), so they sit above the full 86px bar — which is above the toast headroom. Verification step 4 confirms this still looks right; no change expected.

## Implementation order

1. Edit `static/bar.css`: add the three custom properties on `.bar`, split the shared per-state rule, replace `inset: 0` / `height: 100%` with `bottom: 0; left: 0; right: 0; height: var(--cap-h)` for the three affected states, change their horizontal padding to `var(--cap-inset)`.
2. Run the app, walk through the verification plan.
3. Commit.
