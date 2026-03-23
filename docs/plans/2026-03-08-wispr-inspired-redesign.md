# DashScribe UI Redesign — Wispr Flow-Inspired

## Problem
The current UI has too many competing visual elements: animated blobs, glassmorphism, warm amber-coral gradients, rounded glass cards, and backdrop-filter effects. Together they create an "over-designed" feel that doesn't rival Wispr Flow's fluid, premium aesthetic.

## Design Philosophy
Wispr Flow succeeds through **restraint**:
- Pure flat surfaces (no glass, no blobs, no gradients on backgrounds)
- Generous whitespace as the primary design element
- Strong typographic hierarchy (large page titles, clear labels)
- Simple text rows with thin separators (not card-based lists)
- Monochrome palette with minimal accent color usage
- Settings as a floating modal, not a full page
- Photo-based hero banners for visual warmth (not CSS effects)

## Color System Overhaul

### Dark Theme (Default)
```
--bg: #1a1a1a            (pure dark, no purple tint)
--bg-subtle: #222222
--surface: #2a2a2a       (flat, no transparency)
--surface-hover: #333333
--border: rgba(255,255,255,0.08)
--text-primary: #f5f5f5
--text-secondary: #999999
--text-tertiary: #666666
--accent: #8b5cf6        (purple, like Wispr's active state)
--accent-soft: rgba(139,92,246,0.12)
```

### Light Theme
```
--bg: #faf8f5            (warm off-white, matches Wispr exactly)
--bg-subtle: #f0ece6
--surface: #ffffff        (flat white, no transparency)
--surface-hover: #f5f5f5
--border: rgba(0,0,0,0.06)
--text-primary: #1a1a1a
--text-secondary: #666666
--text-tertiary: #999999
--accent: #8b5cf6
```

## Changes by Component

### 1. Remove Blobs & Glassmorphism
- **HTML**: Remove `.ambient-blobs` div and its 3 `.blob` children from dictate page
- **CSS**: Delete all `.blob*` styles, all `backdrop-filter` properties, all `@keyframes blob-*` animations
- **CSS**: Remove all transparency from `--surface` variables — use solid colors

### 2. Sidebar
**Current**: Glass background, amber accent glow on active item, DM Serif Display brand name
**Target**: Flat solid background, subtle highlight on active item, clean sans-serif

- Remove `backdrop-filter: blur(24px)` from `.sidebar`
- Flat background: `--sidebar-bg` → solid color (dark: `#1a1a1a`, light: `#faf8f5`)
- Active item: subtle background tint, no `box-shadow` glow, no accent icon color
- Active item uses left-side indicator bar (3px solid accent) like Wispr
- Brand icon: keep microphone SVG but remove warm accent color
- Sidebar bottom: add "Settings" and "Help" links at bottom (like Wispr), separate from main nav
- Remove the scale(0.97) active transform

### 3. Page Titles & Layout
**Current**: Titles inside cards with DM Serif Display
**Target**: Large standalone titles top-left with action buttons top-right

Each content page gets a top bar:
```
┌─────────────────────────────────────────┐
│  Page Title                 [Action Btn] │
│  Tab1  Tab2  Tab3                        │
│─────────────────────────────────────────│
│  Content...                              │
```

- Page title: 24px, font-weight 600, system sans-serif (drop DM Serif Display for body, keep for brand only)
- Action buttons: solid black bg (dark: solid white bg), pill-shaped
- Consistent 32px left/right padding

### 4. Dictate Page (Home)
**Current**: Two-column with blobs + mic ring + glass stats
**Target**: Wispr-style home with welcome text + hero banner + history rows

Layout change:
```
┌──────────────────────────────────────────┐
│  Welcome back, [User]      🔥1 📝N  wpm │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │  Hero banner (photo/gradient)      │  │
│  │  "Make DashScribe sound like you" │  │
│  │  [Start now]                       │  │
│  └────────────────────────────────────┘  │
│                                          │
│  Today                                   │
│  ─────────────────────────────────────── │
│  10:23 PM  First transcription text...   │
│  ─────────────────────────────────────── │
│  10:22 PM  Another transcription...      │
│  ─────────────────────────────────────── │
└──────────────────────────────────────────┘
```

Key changes:
- **Single column** instead of two-column split
- Welcome header: "Welcome back" in large text (24px), stats as chips in top-right
- Hero banner: full-width rounded card with dark photo background and white overlay text
- History: simple text rows with timestamp + text, thin separator lines (not cards)
- History has day group headers ("Today", "Yesterday", date)
- Mic button: move to a small floating action button or keep in hero banner area
- Remove mic-ring, mic-label, stats-row from session-area

### 5. Dictionary Page
**Current**: Card with input field and tag pills
**Target**: Wispr-style with page title + hero banner + simple list rows

- Page title "Dictionary" top-left, "Add new" button top-right (solid black pill)
- Tab navigation: "All | Personal" with underline indicator
- Hero banner with photo background: "DashScribe speaks the way you speak."
- Dictionary items as simple text rows with thin separators (not tag pills)
- Each row: just the term text, left-aligned, with delete on hover

### 6. Snippets Page
**Current**: Card with list and "Add Snippet" button
**Target**: Same pattern as Dictionary

- "Snippets" title top-left, "Add new" button top-right
- Tab navigation: "All | Personal"
- Hero banner: "The stuff you shouldn't have to re-type."
- Snippet rows: trigger → expansion, simple text layout with thin separators

### 7. File Page
**Current**: Card with input, browse, transcribe
**Target**: Clean page-level layout

- "Transcribe File" title top-left
- File input area: cleaner, larger drop zone with dashed border
- Progress and result below, no card wrapper

### 8. Settings Page → Floating Modal
**Current**: Full settings page with card groups
**Target**: Wispr-style floating modal that overlays the current page

This is the most significant structural change:
- Settings opens as a centered modal (max-width ~700px, ~70vh height)
- Slightly dimmed backdrop behind it
- Two-column layout inside modal: left nav categories, right content area
- Categories: General, Appearance, Advanced (or similar grouping)
- Setting rows: label + description left, control right, thin separator between rows
- Close button (X) in top-right corner
- Version number at bottom-left of modal
- Remove Settings from sidebar nav; add "Settings" to sidebar footer area

### 9. Typography
- **Drop DM Serif Display** for headings — use DM Sans or system font at larger weight
- Page titles: 24px, weight 600
- Section labels: 11px, weight 600, uppercase, letter-spacing 0.06em, muted color
- Body text: 14px, weight 400
- Keep `font-family: 'DM Sans', -apple-system, ...` as the single typeface

### 10. Buttons
- **Primary**: solid black/white (not gradient), pill border-radius, no shadow
- **Secondary**: outlined with thin border, pill shape
- Remove `--accent-gradient` usage on buttons
- Remove hover transforms (translateY, scale) — just color change on hover

### 11. Form Inputs
- Simpler styling: thin bottom border only (no full border), or very subtle full border
- No focus glow — just border color change
- Remove all `backdrop-filter` from inputs

### 12. History Entries (Redesigned)
- No background or border-radius — flat text rows
- Thin 1px separator line between entries
- Layout: `[timestamp] [text preview]` — remove source badge icons
- Hover: very subtle background tint, show copy button
- Remove entry-slide-in animation

### 13. Light Theme
- Must be the visually dominant/"default feeling" theme
- `--bg: #faf8f5` (warm off-white)
- Content area: pure white `#ffffff`
- Remove all the `body.theme-light` override blocks — rethink so light is natural
- Consider making light the default theme

## Implementation Order

1. **Color system** — Replace CSS variables with flat colors (both themes)
2. **Strip decorations** — Remove blobs HTML, glassmorphism CSS, backdrop-filter
3. **Sidebar** — Flatten, add left-bar active indicator, move Settings to footer
4. **Page headers** — Extract titles from cards, add consistent page header bar
5. **Dictate page** — Convert to single-column, add welcome/hero, simplify history rows
6. **Other pages** — Dictionary, Snippets, File — consistent header + content pattern
7. **Settings modal** — Convert from page to floating modal overlay
8. **Typography & buttons** — Clean up font usage, flatten button styles
9. **Polish** — Responsive behavior, transitions, scrolling

## Files to Modify
- `static/index.html` — Restructure page layouts, remove blobs, add modal
- `static/style.css` — Complete retheme (majority of changes)
- `static/app.js` — Settings modal open/close logic, remove blob references

## What NOT to Change
- Backend (FastAPI, WebSocket, transcription logic)
- Bar window (`bar.html`, `bar.js`)
- Onboarding overlay (keep as-is for now)
- Update banner (keep functional, just flatten styling)
- Functional behavior (dictation, hotkeys, clipboard)
