# Visual Revamp Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform DashScribe from a compact utility (450x650, top tabs) into a full-sized desktop app (1000x700, sidebar nav) with Apple-ecosystem-worthy aesthetics, glassmorphism, and rich animations.

**Architecture:** Sidebar-based navigation replaces top tabs. Dictate mode uses a two-column split (session area + history feed). Other modes (File, Dictionary, Snippets, Settings) use the full content area. All existing WebSocket, settings, and API functionality is preserved unchanged.

**Tech Stack:** Vanilla HTML/CSS/JS. CSS custom properties for theming. CSS keyframes for animations. No frameworks.

---

### Task 1: Update window dimensions in main.py

**Files:**
- Modify: `main.py:259-266`

**Step 1: Update window creation parameters**

Change the main window creation from:
```python
main_window = webview.create_window(
    "DashScribe",
    f"http://{HOST}:{PORT}",
    width=450,
    height=650,
    resizable=True,
    min_size=(390, 500),
)
```
To:
```python
main_window = webview.create_window(
    "DashScribe",
    f"http://{HOST}:{PORT}",
    width=1000,
    height=700,
    resizable=True,
    min_size=(800, 550),
)
```

**Step 2: Commit**

```bash
git add main.py
git commit -m "feat(ui): enlarge main window to 1000x700"
```

---

### Task 2: Restructure HTML for sidebar layout

**Files:**
- Modify: `static/index.html`

**Step 1: Rewrite the HTML structure**

Replace the current layout (header + nav-tabs + panels + history-section) with a sidebar + content layout. The new structure:

```html
<div id="app">
    <!-- Onboarding overlay (keep as-is) -->
    ...

    <!-- Main layout -->
    <div class="app-layout">
        <!-- Sidebar -->
        <aside class="sidebar">
            <div class="sidebar-brand">
                <svg ...mic icon.../>
                <span class="sidebar-brand-name">DashScribe</span>
            </div>
            <nav class="sidebar-nav">
                <button class="sidebar-item active" data-mode="dictate">
                    <svg ...mic icon.../>
                    <span>Dictate</span>
                </button>
                <button class="sidebar-item" data-mode="file">
                    <svg ...file icon.../>
                    <span>File</span>
                </button>
                <button class="sidebar-item" data-mode="dictionary">
                    <svg ...book icon.../>
                    <span>Dictionary</span>
                </button>
                <button class="sidebar-item" data-mode="snippets">
                    <svg ...snippets icon.../>
                    <span>Snippets</span>
                </button>
                <button class="sidebar-item" data-mode="settings">
                    <svg ...gear icon.../>
                    <span>Settings</span>
                </button>
            </nav>
            <footer class="sidebar-footer">
                <span id="model-status" class="model-status">
                    <span class="dot loading"></span> Loading...
                </span>
            </footer>
        </aside>

        <!-- Content area -->
        <main class="content">
            <!-- Dictate: two-column -->
            <div id="dictate-mode" class="page active">
                <div class="dictate-layout">
                    <div class="session-area">
                        <div class="ambient-blobs">
                            <div class="blob blob-1"></div>
                            <div class="blob blob-2"></div>
                            <div class="blob blob-3"></div>
                        </div>
                        <div class="mic-area">
                            <div class="mic-ring">
                                <button id="mic-btn" class="mic-btn">
                                    <svg ...mic icon.../>
                                </button>
                            </div>
                            <p id="mic-label" class="mic-label">Hold to Record</p>
                        </div>
                        <div id="stats-section" class="stats-row">
                            ...stat chips (keep existing)...
                        </div>
                    </div>
                    <div class="history-feed">
                        <div class="history-header">
                            <h2 class="history-heading">History</h2>
                            <input id="history-search" .../>
                        </div>
                        <div id="history-list" class="history-list"></div>
                        <button id="load-more-btn" class="load-more-btn hidden">Load more</button>
                    </div>
                </div>
            </div>

            <!-- File page (full width) -->
            <div id="file-mode" class="page">
                ...file card (keep existing content)...
            </div>

            <!-- Dictionary page (promoted from settings) -->
            <div id="dictionary-mode" class="page">
                <div class="page-card">
                    <h2 class="page-heading">Dictionary</h2>
                    <p class="page-desc">Custom terms to improve recognition accuracy.</p>
                    <div class="dictionary-input-row">
                        <input id="dictionary-input" .../>
                        <button id="dictionary-add-btn" ...>Add</button>
                    </div>
                    <div id="dictionary-tags" class="dictionary-tags"></div>
                </div>
            </div>

            <!-- Snippets page (promoted from settings) -->
            <div id="snippets-mode" class="page">
                <div class="page-card">
                    <h2 class="page-heading">Snippets</h2>
                    <p class="page-desc">Define text shortcuts that expand when dictated.</p>
                    <div id="snippet-list" class="snippet-list"></div>
                    <button id="add-snippet-btn" ...>Add Snippet</button>
                </div>
            </div>

            <!-- Settings page (minus dictionary & snippets) -->
            <div id="settings-mode" class="page">
                <div class="settings-list">
                    ...Appearance, Hotkey, Insertion, Updates, Smart Cleanup, Context Formatting groups...
                </div>
            </div>
        </main>
    </div>

    <!-- Overlays: snippet picker, toast, update banner, progress (keep as-is) -->
    ...
</div>
```

Key changes:
- Header and nav-tabs removed, replaced by sidebar
- `.history-section` moves inside `#dictate-mode` as `.history-feed`
- Dictionary and Snippets become their own `<div class="page">` sections
- Dictionary & Snippets HTML removed from settings panel
- Stats row moves inside `.session-area`
- Ambient blob divs added inside session area
- Status bar moves to sidebar footer

**Step 2: Commit**

```bash
git add static/index.html
git commit -m "feat(ui): restructure HTML for sidebar layout"
```

---

### Task 3: Complete CSS rewrite for new design system

**Files:**
- Modify: `static/style.css` (complete rewrite)

**Step 1: Write the new CSS**

The CSS needs these major sections:

1. **CSS Custom Properties** - Updated color system with warm ivory light mode, charcoal dark mode, gradient accents
2. **Base & Reset** - Same reset, updated body styles
3. **App Layout** - `display: flex` horizontal: sidebar (200px fixed) + content (flex: 1)
4. **Sidebar** - Frosted glass (`backdrop-filter: blur(20px)`), brand area, nav items with active glow, footer
5. **Content Area** - Full height, overflow-y auto, page transition animations
6. **Dictate Layout** - Two-column flex: session-area (35%) + history-feed (65%)
7. **Session Area** - Centered mic with ambient blobs, stats chips with glassmorphism
8. **Ambient Blobs** - 3 absolutely positioned gradient circles with slow morphing keyframes
9. **History Feed** - Search bar, scrollable list, entry hover effects, staggered slide-in
10. **Page Cards** - Glass-effect cards for File, Dictionary, Snippets
11. **Settings** - Grouped cards (existing styles adapted)
12. **Animations** - Page crossfade, micro-interactions, ambient blobs, recording pulse
13. **Light/Dark Themes** - CSS custom property overrides
14. **Reduced Motion** - `@media (prefers-reduced-motion)` disables ambient anims
15. **Onboarding, Toast, Update Banner, Progress, Snippet Overlay** - Adapted from existing

Key animation keyframes needed:
- `blob-morph-1/2/3` — slow position + scale morphing (~20s)
- `blob-pulse` — faster pulsing for recording state (~3s)
- `page-enter` — fade + translateY for page transitions
- `entry-slide-in` — fade + translateY for history entries
- `sidebar-glow` — subtle glow pulse on active item

**Step 2: Commit**

```bash
git add static/style.css
git commit -m "feat(ui): complete CSS rewrite with glassmorphism and animations"
```

---

### Task 4: Update JavaScript for sidebar navigation

**Files:**
- Modify: `static/app.js`

**Step 1: Update navigation logic**

Replace the tab-based navigation with sidebar-based:

1. Update `setMode()` to handle 5 modes (dictate, file, dictionary, snippets, settings) instead of 3
2. Replace `.nav-tab` selectors with `.sidebar-item`
3. Replace `.panel` selectors with `.page`
4. History section visibility: always visible in dictate (it's inside the page now), hidden in others (they're separate pages)
5. Stats visibility: same — part of dictate page, auto-hidden
6. Remove the `panels-shrink` class logic (no longer needed)
7. Add staggered animation class to history entries on load

Updated `setMode()`:
```javascript
function setMode(mode) {
    activeMode = mode;
    // Update sidebar
    document.querySelectorAll('.sidebar-item').forEach(function(btn) {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    // Update pages with transition
    document.querySelectorAll('.page').forEach(function(p) {
        if (p.id === mode + '-mode') {
            p.classList.add('active');
        } else {
            p.classList.remove('active');
        }
    });
}
```

Updated sidebar click handlers:
```javascript
document.querySelectorAll('.sidebar-item').forEach(function(btn) {
    btn.addEventListener('click', function() {
        setMode(btn.dataset.mode);
    });
});
```

2. Update element references for moved elements:
   - `historySection` no longer exists as separate element; history is inside `#dictate-mode`
   - `statsSection` same — inside session area
   - `snippetList`, `addSnippetBtn` — now in `#snippets-mode`
   - `dictionaryInput`, `dictionaryAddBtn`, `dictionaryTags` — now in `#dictionary-mode`
   - Remove references to `.nav-tab`, `.panels`, `.panel`

3. Add staggered animation to history entries:
```javascript
function appendHistoryEntries(entries) {
    entries.forEach(function(entry, i) {
        var dayKey = dayKeyFromIso(entry.timestamp);
        if (dayKey !== lastRenderedDayKey) {
            historyList.appendChild(createHistoryDayHeader(formatHistoryDay(dayKey)));
            lastRenderedDayKey = dayKey;
        }
        var el = createHistoryEntry(entry);
        el.style.animationDelay = (i * 0.03) + 's';
        historyList.appendChild(el);
    });
}
```

**Step 2: Commit**

```bash
git add static/app.js
git commit -m "feat(ui): update JS for sidebar navigation and entry animations"
```

---

### Task 5: Visual polish and integration testing

**Files:**
- Possibly tweak: `static/style.css`, `static/app.js`, `static/index.html`

**Step 1: Launch the app and test**

```bash
python3 main.py
```

**Step 2: Visual verification checklist**

Use Playwright to take screenshots and verify:
- [ ] Sidebar renders with frosted glass background
- [ ] Active sidebar item has gradient highlight
- [ ] Dictate mode shows two-column layout (session + history)
- [ ] Ambient gradient blobs visible behind mic
- [ ] Stats chips render below mic with glassmorphism
- [ ] History entries appear with slide-in animation
- [ ] Switching to File/Dictionary/Snippets/Settings shows full-width pages
- [ ] Page transitions are smooth crossfade
- [ ] Light mode looks correct (warm ivory base)
- [ ] Dark mode looks correct (charcoal base)
- [ ] Onboarding overlay still works
- [ ] Update banner still works
- [ ] Toast notifications still appear
- [ ] Mic button recording state works (pulse + color change)

**Step 3: Fix any issues found**

Iterate on CSS/JS as needed based on visual testing.

**Step 4: Final commit**

```bash
git add static/
git commit -m "feat(ui): visual polish and integration fixes"
```

---

### Task 6: Run existing tests

**Step 1: Run the test suite**

```bash
python3 -m pytest tests/ -v
```

Expected: All existing tests pass. The visual revamp only touches frontend files (HTML/CSS/JS) and `main.py` window dimensions — no backend logic changes.

**Step 2: Commit any test fixes if needed**
