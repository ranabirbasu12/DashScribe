# Capsule Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `static/bar.css` so the recording, processing, and error capsules render at exactly 42px tall and keep their buttons centered on the pill's end-cap curvatures, regardless of the bar window's overall height.

**Architecture:** Single-file CSS change. Introduce three CSS custom properties (`--cap-h`, `--btn-size`, `--cap-inset`) at `.bar` as the single source of truth for capsule geometry. Stop `.bar-recording` / `.bar-processing` / `.bar-error` from filling the whole 86px bar window and instead pin them to the bottom `var(--cap-h)` region. Replace the hardcoded `0 12px` horizontal padding with `0 var(--cap-inset)` so button centers sit exactly on cap centers by construction.

**Tech Stack:** Plain CSS (custom properties, `calc()`) in `static/bar.css`. No build step. App runs via `source venv/bin/activate && python3 main.py` from the repo root.

**Why this is small:** The full change touches one file and roughly 20 lines. The tasks below split it into two visually-verifiable commits: one that fixes button alignment (3px inward shift), one that fixes capsule height (86px → 42px). Either commit alone leaves the app in a working, launchable state.

**Reference spec:** `docs/superpowers/specs/2026-04-24-capsule-alignment-design.md`

---

## Background for the engineer

The floating bar is a pywebview window sized by `main.py` to `BAR_RECORDING_H + BAR_TOAST_HEADROOM` = `42 + 44 = 86px` tall. The top 44px is meant to be transparent (space for a device-change toast that renders above the capsule); the bottom 42px is meant to hold the pill-shaped capsule. Currently, the CSS puts `.bar-recording`, `.bar-processing`, and `.bar-error` at `position: absolute; inset: 0;`, which makes them fill the entire 86px bar. Result: the capsule is visually ~86px tall, not 42px, and the 24×24 buttons look stranded inside an oversized pill. This plan fixes that.

For cap-to-button geometry: the pill's end caps are semicircles of radius `cap_height / 2`. For a button's center to sit exactly on a cap's center, the button's horizontal distance from the capsule edge must equal `cap_height / 2`. With button diameter `btn_size`, that means `padding = (cap_height − btn_size) / 2`. At 42px capsule and 24px button, the correct padding is 9px. The current stylesheet uses 12px — a pre-existing 3px drift that this plan also fixes.

CSS variables aren't used anywhere else in `bar.css` today; we're introducing them for the first time. That's fine — they're plain CSS, no tooling or polyfill needed, and WebKit (pywebview's engine on macOS) has supported them since forever.

---

## File Structure

**Modify:** `static/bar.css` — single file, single responsibility (floating-bar styling). No new files. No other files touched.

**Do not touch:**
- `main.py` (window sizing, `BAR_TOAST_HEADROOM`, etc.)
- `static/bar.html` (DOM structure is already correct)
- `static/bar.js` (no JS changes needed)
- Idle capsule rules in `bar.css` (`.bar-idle`) — unchanged
- `.error-flash`, `.bar-warning`, `.bar-toast`, `.tooltip` — unchanged

---

## Task 1: Introduce design tokens and fix button-alignment inset

**Files:**
- Modify: `static/bar.css` — add variables to `.bar`, change padding on `.bar-recording` and `.bar-processing`

**What this task accomplishes:** Declares the geometric tokens and fixes the 3px button-alignment drift. Does NOT yet fix the capsule height — that stays broken until Task 2. Visual result after this task: buttons move 3px inward (toward the capsule center) on the recording and processing states. The capsule still renders as ~86px tall.

- [ ] **Step 1.1: Add CSS custom properties to the `.bar` rule**

Open `static/bar.css`. Find the `.bar` block (starts around line 12). It currently looks like:

```css
.bar {
    --capsule-bg: rgba(30, 30, 30, 0.9);
    display: flex;
    align-items: flex-end;
    justify-content: center;
    height: 100%;
    width: 100%;
    position: relative;
    overflow: visible;
    border-radius: 0;
}
```

Add three new custom properties alongside the existing `--capsule-bg`:

```css
.bar {
    --capsule-bg: rgba(30, 30, 30, 0.9);
    --cap-h: 42px;
    --btn-size: 24px;
    --cap-inset: calc((var(--cap-h) - var(--btn-size)) / 2);
    display: flex;
    align-items: flex-end;
    justify-content: center;
    height: 100%;
    width: 100%;
    position: relative;
    overflow: visible;
    border-radius: 0;
}
```

- [ ] **Step 1.2: Change `.bar-recording` horizontal padding to use the inset token**

Find the `.bar-recording` block (starts around line 82). It currently reads:

```css
.bar-recording {
    position: relative;
    padding: 0 12px;
    gap: 10px;
}
```

Change `padding: 0 12px;` to `padding: 0 var(--cap-inset);`:

```css
.bar-recording {
    position: relative;
    padding: 0 var(--cap-inset);
    gap: 10px;
}
```

- [ ] **Step 1.3: Change `.bar-processing` horizontal padding to use the inset token**

Find the `.bar-processing` block (starts around line 152). It currently reads:

```css
.bar-processing {
    padding: 0 12px;
    gap: 10px;
}
```

Change to:

```css
.bar-processing {
    padding: 0 var(--cap-inset);
    gap: 10px;
}
```

- [ ] **Step 1.4: Launch the app and eyeball the result**

Run:

```bash
cd /Users/ranabirbasu/GitHub/DashScribe
source venv/bin/activate && python3 main.py
```

Trigger a recording (Hold Right Option, the default hotkey) so the recording capsule appears in the floating bar at the bottom of the screen. Expected observation: the cancel button (left) and stop button (right) are each 3px closer to the capsule center than before. The capsule itself is still visibly too tall — that's fixed in Task 2. Quit the app (right-click dock icon → Quit DashScribe, or main-window close).

- [ ] **Step 1.5: Commit**

```bash
git add static/bar.css
git commit -m "style(bar): introduce capsule design tokens and correct button inset

Adds --cap-h, --btn-size, --cap-inset custom properties at .bar and
replaces hardcoded 0 12px padding on .bar-recording and .bar-processing
with 0 var(--cap-inset) (= 9px). Button centers now sit at exactly
cap-h/2 from the capsule edge, aligned with the pill cap centers.

Capsule height is still incorrect — fixed in the next commit."
```

---

## Task 2: Pin capsule states to the bottom 42px of the bar

**Files:**
- Modify: `static/bar.css` — split the shared per-state rule, add explicit bottom-pinned sizing to `.bar-recording`, `.bar-processing`, `.bar-error`, remove now-redundant `height: 100%` declarations.

**What this task accomplishes:** Stops the three active-state capsules from filling the 86px bar window. Pins them to `bottom: 0` with `height: var(--cap-h)`, leaving the 44px toast headroom transparent. Visual result: the capsule shrinks to its intended 42px height, cap curvatures get tighter (radius 21px instead of ~43px), and the buttons (already correctly inset from Task 1) now sit exactly on the cap centers.

- [ ] **Step 2.1: Split the shared per-state rule and add explicit sizing for the three active states**

Find the shared rule (starts around line 25) and the `.bar-recording, .bar-processing` rule (starts around line 47). They currently read:

```css
/* Shared state container behavior */
.bar-idle,
.bar-recording,
.bar-processing,
.bar-error {
    position: absolute;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    pointer-events: none;
}

/* Show active state */
.bar.idle .bar-idle,
.bar.recording .bar-recording,
.bar.processing .bar-processing,
.bar.error .bar-error {
    display: flex;
    pointer-events: auto;
}

/* Recording + processing share the same capsule tone */
.bar-recording,
.bar-processing {
    background: var(--capsule-bg);
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    height: 100%;
}
```

Replace the shared rule AND the `.bar-recording, .bar-processing` rule with this (leaving the `/* Show active state */` block between them unchanged):

```css
/* Shared state container behavior (sizing handled per state below) */
.bar-idle,
.bar-recording,
.bar-processing,
.bar-error {
    position: absolute;
    display: none;
    align-items: center;
    justify-content: center;
    pointer-events: none;
}

/* Show active state */
.bar.idle .bar-idle,
.bar.recording .bar-recording,
.bar.processing .bar-processing,
.bar.error .bar-error {
    display: flex;
    pointer-events: auto;
}

/* Recording + processing share the same capsule tone */
.bar-recording,
.bar-processing {
    background: var(--capsule-bg);
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.12);
}

/* Pin the three active-state capsules to the bottom cap-h region only.
   Leaves the toast headroom above them fully transparent. */
.bar-recording,
.bar-processing,
.bar-error {
    bottom: 0;
    left: 0;
    right: 0;
    height: var(--cap-h);
}
```

Note three changes here: (1) `inset: 0` is removed from the shared rule; (2) `height: 100%` is removed from the recording/processing tone rule; (3) a new rule explicitly sizes all three active states to the bottom `var(--cap-h)` band.

- [ ] **Step 2.2: Remove the now-redundant `height: 100%` from `.bar-error`**

Find the `.bar-error` block (starts around line 180). It currently reads:

```css
.bar-error {
    background: rgba(229, 57, 53, 0.92);
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    height: 100%;
}
```

Delete the `height: 100%;` line (sizing is now handled by the rule added in Step 2.1):

```css
.bar-error {
    background: rgba(229, 57, 53, 0.92);
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.14);
}
```

- [ ] **Step 2.3: Launch the app and verify all three states render correctly**

Run:

```bash
cd /Users/ranabirbasu/GitHub/DashScribe
source venv/bin/activate && python3 main.py
```

Exercise each state:

1. **Idle:** The small 28×5 pill should still appear at the bottom of the screen, visually unchanged from before Task 2.
2. **Recording:** Press and hold Right Option. The recording capsule should now look like a proper short pill (244 wide × 42 tall), with cancel and stop buttons seated in the end caps — their centers visually on the cap centers.
3. **Processing:** Release the hotkey to trigger processing. The processing capsule (120×42) should show the bouncing dots and the cancel button on the left, with the cancel button center aligned to the left cap center.
4. **Error:** To force an error state, simulate one: in the repo root, temporarily disable the network / or just trust that the rendering is driven by the same rule — the 42×42 square error capsule should look like a proper circle (equal width and height) with the 28px retry button flex-centered.

Check that in all three cases, the capsule sits at the bottom of the bar and the 44px space above is transparent.

- [ ] **Step 2.4: Measure alignment with DevTools**

Open the pywebview debug inspector on the bar window (if the build has `debug=True` — check `webview.start(debug=True)` in `main.py`; if it's off, enable it temporarily for this measurement step, then revert).

While the capsule is in the recording state, inspect `.bar-recording`. Confirm in the Computed pane:

- `height: 42px`
- `padding-left: 9px`
- `padding-right: 9px`
- `bottom: 0px`

Inspect `#stop-btn`. The button's bounding box right edge should be at `capsule_width − 9px` from the capsule's left edge. Its center X should therefore be at `capsule_width − 9px − 12px = capsule_width − 21px` — which equals `capsule_width − cap-h/2`, the exact center X of the right end cap.

If debug was toggled on just for this step, revert `debug=True` → `debug=False` (or whatever it was) before committing.

- [ ] **Step 2.5: Commit**

```bash
git add static/bar.css
git commit -m "fix(bar): pin active capsule states to bottom cap-h region

Replaces inset: 0 and height: 100% on .bar-recording, .bar-processing,
and .bar-error with an explicit bottom: 0; left: 0; right: 0;
height: var(--cap-h) rule. The three active states no longer fill
the entire bar window (which is 86px tall to leave room for the
device-change toast above); they now occupy only the intended 42px
at the bottom, restoring the correct pill aspect ratio and making
button centers align with cap centers.

Fixes the visual regression where the capsule looked vertically
enlarged, especially on laptop displays."
```

---

## Post-plan checks

After both tasks land, sanity-check from a fresh app launch:

- [ ] Idle capsule unchanged (28×5 at window center).
- [ ] Recording capsule: 42px tall, 9px horizontal padding, button centers on cap centers.
- [ ] Processing capsule: 42px tall, 9px horizontal padding, cancel button center on left cap center.
- [ ] Error capsule: 42×42 square, retry centered.
- [ ] Toast continues to render above the capsule when a device change fires (e.g., unplug/replug a USB mic mid-recording) — no overlap with the pill.
- [ ] Warning and tooltip remain positioned correctly above the bar.
- [ ] No console errors in pywebview.
- [ ] External-monitor sanity: plug in the external display, drag the app over (the bar repositions on mainScreen at launch so a restart may be needed), exercise the same states, confirm identical behavior.

No automated tests are added. This is pure CSS; the project has no CSS/visual-regression test harness, and adding one just for this change would violate YAGNI. If a visual-regression tool is later adopted, the DevTools measurements in Step 2.4 translate directly into assertions.

---

## Self-review

**Spec coverage:**
- Goal 1 (capsule = 42px) → Task 2 Step 2.1 + verification 2.3, 2.4 ✓
- Goal 2 (cap-adjacent button centers on cap centers) → Task 1 Step 1.2, 1.3 + verification 2.4 ✓
- Goal 3 (error capsule unchanged behavior) → Task 2 Step 2.1 sizes it to `var(--cap-h)` (42px), which at 42px width makes it the same 42×42 square; retry stays flex-centered ✓
- Goal 4 (toast headroom transparent) → Task 2 Step 2.1 pins capsule with `bottom: 0` + explicit height, leaving everything above transparent ✓
- Goal 5 (self-correcting geometry) → Task 1 Step 1.1 introduces `--cap-inset` as `calc()` over the other tokens ✓

**Placeholder scan:** No TBDs, no "add error handling", no "similar to Task N" — every step has concrete code or commands. ✓

**Type/identifier consistency:** Variable names (`--cap-h`, `--btn-size`, `--cap-inset`) used identically across Tasks 1 and 2. Selector names match what's actually in `bar.css`. ✓

**Edge case noted:** `BAR_ERROR_W = BAR_ERROR_H = 42` in `main.py`, so after Task 2 the error window is 42 wide + 44 headroom = `42×86`, and the capsule is `42×42` at the bottom — still a circle as intended. If `BAR_ERROR_W` ever changes without matching `BAR_ERROR_H`, the error "circle" would become an oval, but that's pre-existing behavior outside the scope of this plan.
