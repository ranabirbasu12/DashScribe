# DashScribe Smart Features Design

**Date:** 2026-03-06
**Status:** Approved

## Overview

Add four new feature layers to DashScribe, powered by a local LLM (Qwen3.5-0.8B-MLX-4bit). All processing stays fully offline. Each feature is independently togglable.

## Features

### 1. LLM Engine Layer

**Model:** `mlx-community/Qwen3.5-0.8B-MLX-4bit` (~400MB)
- 0.8B params, 4-bit quantized, Apache 2.0
- 510 tok/s on M4 Max — near-instant for short text tasks
- Same MLX ecosystem as mlx-whisper

**New module: `llm.py`**
- `LocalLLM` class with lazy model loading (not at startup)
- `generate(prompt, system_prompt, max_tokens=256)` -> `str`
- Thread-safe via RLock, `mx.metal.clear_cache()` after each call
- Model downloaded on first use of any LLM feature

**Post-processing pipeline (in `app.py`):**
```
Whisper output -> [smart cleanup] -> [snippet expansion] -> [context formatting] -> paste
```
Smart cleanup, snippet expansion, and context formatting are folded into a single LLM call when all are enabled. Each step is skipped if its toggle is off.

### 2. Smart Cleanup

**Single LLM-powered cleanup pass.** No regex. The model handles filler removal, course correction, and punctuation holistically while preserving the speaker's natural voice.

**System prompt:**
```
Clean up this dictated text. Remove only obvious verbal fillers and
self-corrections while preserving the speaker's natural voice and intent.
Do not rephrase, summarize, or change the meaning. Output only the
cleaned text, nothing else.
```

**Settings:** Single toggle — Settings -> Cleanup -> Smart cleanup (default: ON when LLM is loaded)

**Performance:** Only runs on text > 5 words. ~200ms for 100 words at 510 tok/s.

### 3. Snippets + Personal Dictionary

#### 3a. Snippets
Voice shortcuts that expand trigger phrases into pre-defined text.

**Storage:** `~/.dashscribe/snippets.json`
```json
[
  {"trigger": "my calendar link", "expansion": "https://calendly.com/example/30min"},
  {"trigger": "email signature", "expansion": "Best regards,\nJohn Doe"}
]
```

**Voice trigger:** Snippet triggers are injected into the LLM system prompt. The model replaces trigger phrases with their expansions contextually.

**Keyboard trigger:** Global shortcut (Cmd+Shift+S) opens a quick-search overlay. Type to filter, Enter to insert.

**Settings UI:** Settings -> Snippets tab with add/edit/delete management.

#### 3b. Personal Dictionary
Custom terms fed into Whisper's `initial_prompt` parameter to bias recognition.

**Storage:** `~/.dashscribe/dictionary.txt` (one term per line)

**Implementation:**
```python
initial_prompt = ", ".join(dictionary_terms)
# Passed to mlx_whisper.transcribe(... initial_prompt=initial_prompt)
```

**Settings UI:** Settings -> Dictionary — list with add/delete, or text area.

### 4. Context-Aware Formatting

Detect frontmost app via `NSWorkspace.sharedWorkspace().frontmostApplication()` and adapt LLM output style.

**New module: `context.py`**
- `get_frontmost_app()` -> `(bundle_id, app_name)`
- `get_formatting_style(bundle_id)` -> style string
- `get_style_prompt(style)` -> LLM prompt fragment

**Built-in app-style mapping:**

| Category | Apps | Style |
|----------|------|-------|
| Messaging | Slack, Discord, Messages, Telegram, WhatsApp | Casual, concise |
| Email | Mail, Gmail, Outlook | Professional, complete sentences |
| Documents | Notion, Google Docs, Word, Pages | Structured, paragraph-aware |
| Code editors | VS Code, Cursor, Xcode, Terminal | Minimal formatting, verbatim |
| Default | Everything else | Clean natural speech |

**User overrides:** Per-app style dropdown in Settings -> Formatting. Stored in config.json:
```json
{
  "app_styles": {
    "com.tinyspeck.slackmacgap": "casual",
    "com.microsoft.Outlook": "professional"
  }
}
```

**Integration:** Style prompt appended to cleanup system prompt — single LLM call handles both cleanup and formatting.

**Settings:** Toggle — Settings -> Formatting -> Context-aware formatting (default: ON)

## New Files

| File | Purpose |
|------|---------|
| `llm.py` | LocalLLM class — mlx-lm model loading and generation |
| `context.py` | Frontmost app detection + style mapping |
| `~/.dashscribe/snippets.json` | User snippet definitions |
| `~/.dashscribe/dictionary.txt` | Personal dictionary terms |

## Modified Files

| File | Changes |
|------|---------|
| `app.py` | Post-processing pipeline after transcription, before paste |
| `transcriber.py` | Accept `initial_prompt` from personal dictionary |
| `config.py` | New settings: smart_cleanup, context_formatting, app_styles |
| `static/index.html` | New settings tabs: Snippets, Dictionary, Formatting |
| `static/app.js` | Settings UI logic for new features |
| `static/style.css` | Styles for new settings panels |
| `requirements.txt` | Add `mlx-lm` dependency |

## Architecture Decision: Single LLM Call

When multiple LLM features are enabled, they are combined into one prompt rather than chaining separate calls. This:
- Reduces latency (one call vs three)
- Produces more coherent output (model sees full context)
- Avoids compounding errors from sequential processing

Example combined system prompt (cleanup + snippets + Slack context):
```
Clean up this dictated text for Slack (messaging app). Use a casual,
conversational tone. Remove fillers and self-corrections while preserving
personality. If the text contains these trigger phrases, replace them:
- "my calendar link" -> "https://calendly.com/example/30min"
Output only the final text.
```

## Settings Summary

| Setting | Location | Default |
|---------|----------|---------|
| Smart cleanup | Cleanup section | ON (when LLM loaded) |
| Context-aware formatting | Formatting section | ON |
| App style overrides | Formatting section | Built-in defaults |
| Snippets | Snippets tab | Empty list |
| Personal dictionary | Dictionary tab | Empty list |
