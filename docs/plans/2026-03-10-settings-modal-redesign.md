# Settings Modal Redesign

**Date:** 2026-03-10
**Status:** Approved

## Goal

Convert the current single-column scrolling settings modal into a Wispr Flow-style two-column layout with sidebar navigation and grouped categories.

## Layout

- Floating modal: ~700px wide, ~70vh tall, centered with dimmed backdrop (reuses existing overlay)
- Two-column layout: 180px left sidebar + scrollable right content area
- Close button (X) top-right corner of modal

## Sidebar

Two section groups with uppercase labels:

| Section | Item | Icon |
|---|---|---|
| SETTINGS | General | sliders/tune icon |
| SETTINGS | AI Features | sparkle icon |
| SETTINGS | Appearance | sun/moon icon |
| ACCOUNT | Account | user icon |

- Active item: purple accent background tint (`var(--accent)`)
- Version number pinned to sidebar bottom-left (e.g., "v1.0.0")

## Content Area

- Category title at top (large heading)
- Setting rows: label + description left, control right, thin separators
- Content scrolls independently

## Category Contents

### General
- **Shortcuts** — hotkey capture button (label: current key, description: "Press to change the global dictation trigger key")
- **Insertion** — auto-insert toggle + repaste key capture
- **Updates** — auto-update toggle + check for updates button

### AI Features
- **Smart Cleanup** — toggle (description: "Uses a local AI model to clean up filler words and self-corrections")
- **Context Formatting** — toggle (description: "Automatically adapts formatting style based on which app you're dictating into")

### Appearance
- **Theme** — auto/light/dark radio buttons

### Account
- **Signed-out state:** sign-in button
- **Signed-in state:** email display, display name input + save, sign out button
- **Danger zone:** delete account (separated at bottom)

## Behavior
- Clicking sidebar item switches right content panel (JS show/hide, no navigation)
- Default selection: General
- All existing JS logic preserved — DOM restructure only
- Existing CSS classes (`.settings-sidebar`, `.settings-sidebar-item`, etc.) already partially defined

## Files to Modify
- `static/index.html` — restructure settings modal HTML
- `static/style.css` — update settings modal styles for two-column layout, setting rows
- `static/app.js` — sidebar tab switching logic, preserve all existing handlers

## Reference
- Wispr Flow settings screenshots in `assets/wisprflow_reference_images/new_images/Some screenshots from Settings/`
