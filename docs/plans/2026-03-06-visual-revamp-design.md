# DashScribe Visual Revamp Design

## Goal

Transform DashScribe from a compact 450x650 utility into a full-sized, Apple-ecosystem-worthy desktop app with sidebar navigation, rich animations, and a warm-yet-vibrant aesthetic.

## Window

- Default: 1000x700, min: 800x550
- Fully resizable and full-screen capable
- Native macOS title bar

## Visual Direction

Clean & Warm foundation with vibrant glassmorphism accents.

### Color System

**Light mode:**
- Base surface: warm ivory (#FAF8F5)
- Cards: white/cream with subtle warm borders
- Sidebar: frosted glass with warm tint

**Dark mode:**
- Base surface: rich charcoal (#1C1B1F)
- Cards: warm-tinted elevated surfaces
- Sidebar: frosted glass with dark tint

**Accents:**
- Primary: amber-to-coral gradient for actions and highlights
- Ambient: soft gradient blobs (amber, coral, soft violet) behind mic area
- Glassmorphism: `backdrop-filter: blur` on sidebar and floating cards

### Typography

- Headings: DM Serif Display (keep existing)
- Body: DM Sans (keep existing)

## Layout

Three-column layout when in Dictate mode:

```
+----------+-----------------+----------------------+
| Sidebar  | Session Area    | History Feed         |
| (~200px) | (~35%)          | (~65%)               |
+----------+-----------------+----------------------+
```

### Sidebar (~200px fixed)

- Brand logo at top
- Nav items with icons: Dictate, File, Dictionary, Snippets, Settings
- Active item has warm gradient highlight + subtle glow
- Status indicator (Ready/Loading) at bottom
- Frosted glass background

### Session Area (Dictate view, left column ~35%)

- Centered mic button with animated gradient blobs behind it
- Blobs: 2-3 soft colored shapes, slow morphing CSS animation
- Stats chips (streak, words, WPM) below mic with glassmorphism
- During recording: mic ring pulses with amplitude, blobs intensify, waveform visualization appears

### History Feed (Dictate view, right column ~65%)

- Search bar at top
- Scrollable entry list, full height
- Entries slide in with staggered animation on load
- Hover: entry elevates with shadow transition, copy/undo buttons appear
- Load more button at bottom

### Other Views

- **File:** Centered card with drag-and-drop zone, progress indicator
- **Dictionary:** Full-width term management, tag-style chips, add input
- **Snippets:** Card/list view with inline editing
- **Settings:** Grouped cards (Appearance, Hotkey, Insertion, Updates, Smart Cleanup, Context Formatting)

Non-Dictate views use the full content area (no two-column split).

## Animations

### Page Transitions
- Crossfade + subtle vertical slide (150ms) between sidebar sections
- CSS `cubic-bezier(0.4, 0, 0.2, 1)` easing throughout

### Micro-interactions
- Buttons: scale(0.97) on press, subtle background transition on hover
- Toggle switches: spring-physics slide animation
- History entries: fade + translateY slide-in, staggered by index
- Copy/undo buttons: fade-in on entry hover

### Ambient
- Gradient blobs behind mic: slow morphing keyframe animation (~20s cycle)
- Blobs use warm colors: amber, soft coral, muted violet
- Recording state: blobs pulse faster, colors intensify
- Active sidebar item: soft glow animation

### Accessibility
- `prefers-reduced-motion`: disable ambient animations, keep functional transitions

## Information Architecture Change

### Promoted to sidebar (from Settings)
- Dictionary (custom terms)
- Snippets (text shortcuts)

### Remains in Settings
- Appearance (theme toggle)
- Hotkey configuration
- Insertion (auto-insert toggle, repaste key)
- Updates
- Smart Cleanup
- Context Formatting

## Files Changed

- `static/index.html` — restructure to sidebar layout
- `static/style.css` — complete rewrite for new design system
- `static/app.js` — sidebar navigation, animations, two-column layout logic
- `main.py` — update window dimensions (1000x700, min 800x550)
- `static/bar.html` / `static/bar.css` — no changes (floating bar is independent)

## Constraints

- Vanilla HTML/CSS/JS only (no frameworks)
- Must preserve all existing functionality (onboarding, WebSocket, hotkey, file transcription, settings persistence, update banner)
- Dark/light theme support via CSS custom properties
- `prefers-reduced-motion` support
