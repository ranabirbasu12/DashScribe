# Smart Features Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add local LLM-powered smart cleanup, snippets, personal dictionary, and context-aware formatting to DashScribe.

**Architecture:** New `llm.py` module wraps mlx-lm for text generation. New `context.py` detects frontmost app. Post-processing pipeline in `app.py` runs after transcription, before paste. Each feature is independently togglable via `config.py` settings.

**Tech Stack:** mlx-lm, Qwen3.5-0.8B-MLX-4bit, NSWorkspace (PyObjC), existing mlx/FastAPI stack.

---

### Task 1: LLM Engine (`llm.py`)

**Files:**
- Create: `llm.py`
- Create: `tests/test_llm.py`
- Modify: `requirements.txt`

**Step 1: Add mlx-lm dependency**

In `requirements.txt`, add:
```
mlx-lm
```

Run: `pip install mlx-lm`

**Step 2: Write the failing test**

```python
# tests/test_llm.py
from unittest.mock import patch, MagicMock
from llm import LocalLLM

LLM_REPO = "mlx-community/Qwen3.5-0.8B-MLX-4bit"


def test_llm_initializes_without_loading_model():
    llm = LocalLLM()
    assert not llm.is_loaded
    assert llm.model_repo == LLM_REPO


def test_llm_generate_loads_model_lazily():
    llm = LocalLLM()
    mock_model = MagicMock()
    mock_tokenizer = MagicMock()
    with patch("llm.mlx_lm") as mock_mlx_lm:
        mock_mlx_lm.load.return_value = (mock_model, mock_tokenizer)
        mock_mlx_lm.generate.return_value = "cleaned text"
        result = llm.generate("raw text", system_prompt="Clean this up.")
        mock_mlx_lm.load.assert_called_once()
        assert result == "cleaned text"
        assert llm.is_loaded


def test_llm_generate_reuses_loaded_model():
    llm = LocalLLM()
    mock_model = MagicMock()
    mock_tokenizer = MagicMock()
    with patch("llm.mlx_lm") as mock_mlx_lm:
        mock_mlx_lm.load.return_value = (mock_model, mock_tokenizer)
        mock_mlx_lm.generate.return_value = "output"
        llm.generate("first call", system_prompt="system")
        llm.generate("second call", system_prompt="system")
        assert mock_mlx_lm.load.call_count == 1


def test_llm_generate_returns_empty_on_error():
    llm = LocalLLM()
    with patch("llm.mlx_lm") as mock_mlx_lm:
        mock_mlx_lm.load.side_effect = Exception("model not found")
        result = llm.generate("text", system_prompt="system")
        assert result == ""
```

**Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm'`

**Step 4: Write the implementation**

```python
# llm.py
"""Local LLM for text post-processing via mlx-lm."""
import threading
import importlib

import mlx.core as mx

LLM_REPO = "mlx-community/Qwen3.5-0.8B-MLX-4bit"


class LocalLLM:
    def __init__(self, model_repo: str = LLM_REPO):
        self.model_repo = model_repo
        self.is_loaded = False
        self._model = None
        self._tokenizer = None
        self._lock = threading.RLock()

    def _ensure_loaded(self):
        if self._model is not None:
            return
        import mlx_lm
        self._mlx_lm = mlx_lm
        self._model, self._tokenizer = mlx_lm.load(self.model_repo)
        self.is_loaded = True

    def generate(self, text: str, system_prompt: str, max_tokens: int = 512) -> str:
        with self._lock:
            try:
                self._ensure_loaded()
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ]
                prompt = self._tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
                result = self._mlx_lm.generate(
                    self._model,
                    self._tokenizer,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    verbose=False,
                )
                mx.metal.clear_cache()
                return result.strip()
            except Exception as e:
                print(f"LLM generation failed: {e}")
                return ""
```

**Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_llm.py -v`
Expected: All 4 PASS

**Step 6: Commit**

```bash
git add llm.py tests/test_llm.py requirements.txt
git commit -m "feat: add local LLM engine (llm.py) with lazy model loading"
```

---

### Task 2: Personal Dictionary (`config.py` + `transcriber.py`)

**Files:**
- Modify: `config.py`
- Modify: `transcriber.py`
- Modify: `tests/test_transcriber.py`

**Step 1: Write the failing test**

Add to `tests/test_transcriber.py`:
```python
def test_transcribe_array_passes_initial_prompt(mock_mlx_whisper):
    txr = WhisperTranscriber()
    txr._mlx_whisper = mock_mlx_whisper
    audio = np.zeros(16000, dtype=np.float32)
    txr.transcribe_array(audio, initial_prompt="DashScribe, FastAPI")
    call_kwargs = mock_mlx_whisper.transcribe.call_args[1]
    assert call_kwargs.get("initial_prompt") == "DashScribe, FastAPI"


def test_transcribe_array_no_initial_prompt_by_default(mock_mlx_whisper):
    txr = WhisperTranscriber()
    txr._mlx_whisper = mock_mlx_whisper
    audio = np.zeros(16000, dtype=np.float32)
    txr.transcribe_array(audio)
    call_kwargs = mock_mlx_whisper.transcribe.call_args[1]
    assert "initial_prompt" not in call_kwargs or call_kwargs["initial_prompt"] is None
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_transcriber.py::test_transcribe_array_passes_initial_prompt -v`
Expected: FAIL — `TypeError: transcribe_array() got an unexpected keyword argument 'initial_prompt'`

**Step 3: Modify `transcriber.py`**

Update `transcribe_array` signature:
```python
def transcribe_array(self, audio: np.ndarray, initial_prompt: str | None = None) -> str:
```

Inside the `self._backend().transcribe(...)` call, add:
```python
**({"initial_prompt": initial_prompt} if initial_prompt else {}),
```

**Step 4: Add dictionary loading to `config.py`**

Add near the top constants:
```python
DICTIONARY_PATH = os.path.join(CONFIG_DIR, "dictionary.txt")
```

Add to `SettingsManager`:
```python
@property
def dictionary_prompt(self) -> str | None:
    """Return comma-separated dictionary terms for Whisper initial_prompt."""
    if not os.path.exists(DICTIONARY_PATH):
        return None
    try:
        with open(DICTIONARY_PATH, "r") as f:
            terms = [line.strip() for line in f if line.strip()]
        return ", ".join(terms) if terms else None
    except OSError:
        return None

def set_dictionary(self, terms: list[str]):
    """Save dictionary terms to file."""
    with open(DICTIONARY_PATH, "w") as f:
        f.write("\n".join(terms) + "\n")
```

**Step 5: Run all transcriber tests**

Run: `python3 -m pytest tests/test_transcriber.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add transcriber.py config.py tests/test_transcriber.py
git commit -m "feat: add personal dictionary support via initial_prompt"
```

---

### Task 3: Context Detection (`context.py`)

**Files:**
- Create: `context.py`
- Create: `tests/test_context.py`

**Step 1: Write the failing test**

```python
# tests/test_context.py
from unittest.mock import patch, MagicMock
from context import get_frontmost_app, get_formatting_style, get_style_prompt

STYLE_MAP = {
    "casual": "Use a casual, conversational tone. Keep it concise.",
    "professional": "Use a professional tone with complete sentences.",
    "structured": "Use structured formatting with proper paragraphs.",
    "verbatim": "Output the text as-is with minimal formatting changes.",
}


def test_get_formatting_style_slack():
    assert get_formatting_style("com.tinyspeck.slackmacgap") == "casual"


def test_get_formatting_style_mail():
    assert get_formatting_style("com.apple.mail") == "professional"


def test_get_formatting_style_vscode():
    assert get_formatting_style("com.microsoft.VSCode") == "verbatim"


def test_get_formatting_style_unknown():
    assert get_formatting_style("com.unknown.app") == "default"


def test_get_formatting_style_user_override():
    overrides = {"com.unknown.app": "casual"}
    assert get_formatting_style("com.unknown.app", overrides) == "casual"


def test_get_style_prompt_returns_string():
    prompt = get_style_prompt("casual")
    assert "casual" in prompt.lower() or "conversational" in prompt.lower()


def test_get_style_prompt_default():
    prompt = get_style_prompt("default")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_get_frontmost_app_returns_tuple():
    with patch("context.NSWorkspace") as mock_ws:
        mock_app = MagicMock()
        mock_app.bundleIdentifier.return_value = "com.test.app"
        mock_app.localizedName.return_value = "TestApp"
        mock_ws.sharedWorkspace.return_value.frontmostApplication.return_value = mock_app
        bundle_id, name = get_frontmost_app()
        assert bundle_id == "com.test.app"
        assert name == "TestApp"
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'context'`

**Step 3: Write the implementation**

```python
# context.py
"""Detect frontmost application and map to formatting style."""
from AppKit import NSWorkspace

# Bundle ID prefixes → style
_APP_STYLES = {
    # Messaging
    "com.tinyspeck.slackmacgap": "casual",
    "com.hnc.Discord": "casual",
    "com.apple.MobileSMS": "casual",
    "org.telegram.desktop": "casual",
    "net.whatsapp.WhatsApp": "casual",
    "com.facebook.archon": "casual",  # Messenger
    # Email
    "com.apple.mail": "professional",
    "com.microsoft.Outlook": "professional",
    "com.google.Chrome": "professional",  # fallback for Gmail
    # Documents
    "notion.id": "structured",
    "com.microsoft.Word": "structured",
    "com.apple.iWork.Pages": "structured",
    # Code
    "com.microsoft.VSCode": "verbatim",
    "com.todesktop.230313mzl4w4u92": "verbatim",  # Cursor
    "com.apple.dt.Xcode": "verbatim",
    "com.googlecode.iterm2": "verbatim",
    "com.apple.Terminal": "verbatim",
}

_STYLE_PROMPTS = {
    "casual": "The user is typing in a messaging app. Use a casual, conversational tone. Keep it concise.",
    "professional": "The user is composing an email. Use a professional tone with complete sentences and proper formatting.",
    "structured": "The user is writing a document. Use structured formatting with proper paragraphs and clear organization.",
    "verbatim": "The user is in a code editor. Output the text with minimal formatting changes. Preserve technical terms exactly.",
    "default": "Clean up the text naturally while preserving the speaker's voice.",
}


def get_frontmost_app() -> tuple[str, str]:
    """Return (bundle_id, app_name) of the frontmost application."""
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle_id = app.bundleIdentifier() or ""
        name = app.localizedName() or ""
        return bundle_id, name
    except Exception:
        return "", ""


def get_formatting_style(bundle_id: str, user_overrides: dict | None = None) -> str:
    """Map a bundle ID to a formatting style string."""
    if user_overrides and bundle_id in user_overrides:
        return user_overrides[bundle_id]
    return _APP_STYLES.get(bundle_id, "default")


def get_style_prompt(style: str) -> str:
    """Return the LLM prompt fragment for a given style."""
    return _STYLE_PROMPTS.get(style, _STYLE_PROMPTS["default"])
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_context.py -v`
Expected: All 8 PASS

**Step 5: Commit**

```bash
git add context.py tests/test_context.py
git commit -m "feat: add frontmost app detection and style mapping (context.py)"
```

---

### Task 4: Snippets Storage (`config.py`)

**Files:**
- Modify: `config.py`
- Create: `tests/test_snippets.py`

**Step 1: Write the failing test**

```python
# tests/test_snippets.py
import os
import json
import tempfile
from unittest.mock import patch
from config import SettingsManager

SNIPPETS_FIXTURE = [
    {"trigger": "my cal", "expansion": "https://calendly.com/test"},
    {"trigger": "sig", "expansion": "Best,\nTest User"},
]


def test_snippets_empty_by_default():
    with tempfile.TemporaryDirectory() as d:
        with patch("config.CONFIG_DIR", d), \
             patch("config.CONFIG_PATH", os.path.join(d, "config.json")), \
             patch("config.SNIPPETS_PATH", os.path.join(d, "snippets.json")):
            sm = SettingsManager()
            assert sm.snippets == []


def test_snippets_load_and_save():
    with tempfile.TemporaryDirectory() as d:
        snippets_path = os.path.join(d, "snippets.json")
        with patch("config.CONFIG_DIR", d), \
             patch("config.CONFIG_PATH", os.path.join(d, "config.json")), \
             patch("config.SNIPPETS_PATH", snippets_path):
            sm = SettingsManager()
            sm.set_snippets(SNIPPETS_FIXTURE)
            assert os.path.exists(snippets_path)
            sm2 = SettingsManager()
            assert sm2.snippets == SNIPPETS_FIXTURE


def test_snippets_prompt_fragment():
    with tempfile.TemporaryDirectory() as d:
        with patch("config.CONFIG_DIR", d), \
             patch("config.CONFIG_PATH", os.path.join(d, "config.json")), \
             patch("config.SNIPPETS_PATH", os.path.join(d, "snippets.json")):
            sm = SettingsManager()
            sm.set_snippets(SNIPPETS_FIXTURE)
            fragment = sm.snippets_prompt_fragment
            assert "my cal" in fragment
            assert "calendly" in fragment


def test_snippets_prompt_fragment_empty():
    with tempfile.TemporaryDirectory() as d:
        with patch("config.CONFIG_DIR", d), \
             patch("config.CONFIG_PATH", os.path.join(d, "config.json")), \
             patch("config.SNIPPETS_PATH", os.path.join(d, "snippets.json")):
            sm = SettingsManager()
            assert sm.snippets_prompt_fragment is None
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_snippets.py -v`
Expected: FAIL — `AttributeError: 'SettingsManager' object has no attribute 'snippets'`

**Step 3: Add snippets support to `config.py`**

Add constant:
```python
SNIPPETS_PATH = os.path.join(CONFIG_DIR, "snippets.json")
```

Add to `SettingsManager`:
```python
@property
def snippets(self) -> list[dict]:
    if not os.path.exists(SNIPPETS_PATH):
        return []
    try:
        with open(SNIPPETS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

def set_snippets(self, snippets: list[dict]):
    with open(SNIPPETS_PATH, "w") as f:
        json.dump(snippets, f, indent=2)

@property
def snippets_prompt_fragment(self) -> str | None:
    """Return LLM prompt fragment for snippet expansion, or None if no snippets."""
    snips = self.snippets
    if not snips:
        return None
    lines = [f'- "{s["trigger"]}" -> "{s["expansion"]}"' for s in snips]
    return "If the text contains these trigger phrases, replace them with their expansions:\n" + "\n".join(lines)
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_snippets.py -v`
Expected: All 4 PASS

**Step 5: Commit**

```bash
git add config.py tests/test_snippets.py
git commit -m "feat: add snippets storage and prompt generation to SettingsManager"
```

---

### Task 5: New Settings Properties (`config.py`)

**Files:**
- Modify: `config.py`

**Step 1: Add new settings properties**

Add to `SettingsManager`:
```python
@property
def smart_cleanup(self) -> bool:
    return self._data.get("smart_cleanup", False)

@smart_cleanup.setter
def smart_cleanup(self, value: bool):
    with self._lock:
        self._data["smart_cleanup"] = value
        self._save()

@property
def context_formatting(self) -> bool:
    return self._data.get("context_formatting", False)

@context_formatting.setter
def context_formatting(self, value: bool):
    with self._lock:
        self._data["context_formatting"] = value
        self._save()

@property
def app_styles(self) -> dict:
    return self._data.get("app_styles", {})

@app_styles.setter
def app_styles(self, value: dict):
    with self._lock:
        self._data["app_styles"] = value
        self._save()
```

**Step 2: Run existing config tests**

Run: `python3 -m pytest tests/ -v -k "config or snippets"`
Expected: All PASS

**Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add smart_cleanup, context_formatting, app_styles settings"
```

---

### Task 6: Post-Processing Pipeline (`app.py`)

**Files:**
- Modify: `app.py`

**Step 1: Add post-processing function**

Add a new function near `_stop_and_transcribe`:

```python
def _post_process(text: str, llm, settings) -> str:
    """Run LLM post-processing pipeline on transcribed text."""
    if not text or len(text.split()) <= 5:
        return text
    if not settings:
        return text

    needs_llm = settings.smart_cleanup or settings.context_formatting or settings.snippets_prompt_fragment
    if not needs_llm:
        return text

    # Build system prompt
    parts = []
    parts.append("Clean up this dictated text.")

    # Context-aware formatting
    if settings.context_formatting:
        try:
            from context import get_frontmost_app, get_formatting_style, get_style_prompt
            bundle_id, app_name = get_frontmost_app()
            style = get_formatting_style(bundle_id, settings.app_styles)
            style_prompt = get_style_prompt(style)
            if app_name:
                parts.append(f"The user is typing in {app_name}.")
            parts.append(style_prompt)
        except Exception:
            pass

    # Smart cleanup
    if settings.smart_cleanup:
        parts.append(
            "Remove obvious verbal fillers and self-corrections while "
            "preserving the speaker's natural voice and intent."
        )
    else:
        parts.append("Do not change the wording.")

    # Snippets
    snippet_fragment = settings.snippets_prompt_fragment
    if snippet_fragment:
        parts.append(snippet_fragment)

    parts.append("Output only the final text, nothing else.")
    system_prompt = " ".join(parts)

    result = llm.generate(text, system_prompt=system_prompt)
    return result if result else text
```

**Step 2: Integrate into `create_app`**

In `create_app()`, add `llm` parameter:
```python
def create_app(
    ...,
    llm=None,
) -> FastAPI:
```

**Step 3: Wire into `_bar_stop_and_transcribe`**

After `text, elapsed, audio_duration = _stop_and_transcribe(rec, txr, pipe)` and before `app_clip.set_text(text)`, add:
```python
if text and llm is not None:
    text = _post_process(text, llm, settings)
```

Do the same in `_ws_stop_and_transcribe`.

**Step 4: Wire into `_stop_and_transcribe` for dictionary**

Before the transcribe calls, get the dictionary prompt:
```python
initial_prompt = settings.dictionary_prompt if settings else None
```

Pass it to `txr.transcribe_array(mic_audio, initial_prompt=initial_prompt)` and `txr.transcribe(wav_path)` (add the parameter to `transcribe()` too).

**Step 5: Run existing app tests**

Run: `python3 -m pytest tests/ -v --ignore=tests/test_app.py --ignore=tests/test_hotkey.py`
Expected: All PASS

**Step 6: Commit**

```bash
git add app.py
git commit -m "feat: integrate LLM post-processing pipeline into transcription flow"
```

---

### Task 7: Wire LLM in `main.py`

**Files:**
- Modify: `main.py`

**Step 1: Import and create LLM instance**

In `main.py`, alongside existing imports and object creation:
```python
from llm import LocalLLM
llm = LocalLLM()
```

Pass it to `create_app`:
```python
app = create_app(
    ...,
    llm=llm,
)
```

**Step 2: Run the app manually to verify**

Run: `python3 main.py`
Expected: App starts normally. LLM is NOT loaded at startup (lazy loading).

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat: wire LocalLLM into app startup"
```

---

### Task 8: Settings API Endpoints (`app.py`)

**Files:**
- Modify: `app.py`

**Step 1: Add REST endpoints for new settings**

Add these endpoints in `create_app` alongside existing settings endpoints:

```python
@app.get("/api/settings/smart-cleanup")
async def get_smart_cleanup():
    return {"enabled": settings.smart_cleanup if settings else False}

@app.post("/api/settings/smart-cleanup")
async def set_smart_cleanup(request: Request):
    data = await request.json()
    if settings:
        settings.smart_cleanup = data.get("enabled", False)
    return {"ok": True}

@app.get("/api/settings/context-formatting")
async def get_context_formatting():
    return {"enabled": settings.context_formatting if settings else False}

@app.post("/api/settings/context-formatting")
async def set_context_formatting(request: Request):
    data = await request.json()
    if settings:
        settings.context_formatting = data.get("enabled", False)
    return {"ok": True}

@app.get("/api/settings/snippets")
async def get_snippets():
    return {"snippets": settings.snippets if settings else []}

@app.post("/api/settings/snippets")
async def set_snippets(request: Request):
    data = await request.json()
    if settings:
        settings.set_snippets(data.get("snippets", []))
    return {"ok": True}

@app.get("/api/settings/dictionary")
async def get_dictionary():
    prompt = settings.dictionary_prompt if settings else None
    terms = prompt.split(", ") if prompt else []
    return {"terms": terms}

@app.post("/api/settings/dictionary")
async def set_dictionary(request: Request):
    data = await request.json()
    if settings:
        settings.set_dictionary(data.get("terms", []))
    return {"ok": True}
```

**Step 2: Commit**

```bash
git add app.py
git commit -m "feat: add REST API endpoints for smart features settings"
```

---

### Task 9: Settings UI — Smart Features Tabs

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/style.css`

**Step 1: Add settings sections to `index.html`**

In the settings panel, add new sections after the existing ones (Appearance, Hotkey, Insertion, Updates):

- **Cleanup section:** Toggle for "Smart cleanup" with description
- **Formatting section:** Toggle for "Context-aware formatting" with description
- **Snippets section:** List of snippets with add/edit/delete buttons, trigger + expansion fields
- **Dictionary section:** Text area or tag-list for custom terms, save button

**Step 2: Add JS logic to `app.js`**

- Fetch settings on load: `GET /api/settings/smart-cleanup`, etc.
- Toggle handlers that `POST` to the corresponding endpoints
- Snippets CRUD: add/edit/delete snippets with inline editing
- Dictionary: save button POSTs terms array

**Step 3: Add CSS to `style.css`**

Style the new settings sections consistently with existing panels. Use same toggle, input, and button patterns.

**Step 4: Test manually**

Open app, navigate to Settings. Verify:
- Toggles save and persist across page refresh
- Snippets can be added, edited, deleted
- Dictionary terms save and load

**Step 5: Commit**

```bash
git add static/index.html static/app.js static/style.css
git commit -m "feat: add settings UI for smart cleanup, snippets, dictionary, formatting"
```

---

### Task 10: Snippet Keyboard Shortcut (Global)

**Files:**
- Modify: `hotkey.py` (or new `snippet_overlay.py`)
- Modify: `static/index.html`
- Modify: `static/app.js`

**Step 1: Add snippet overlay to bar or main window**

Create a WebSocket message type `{"type": "show_snippets"}` that triggers a searchable overlay in the main window showing all snippets. User types to filter, Enter inserts the expansion.

**Step 2: Register global shortcut**

Add Cmd+Shift+S as a global hotkey (via CGEventTap or pynput) that sends the `show_snippets` message to the main window WebSocket.

**Step 3: Test manually**

Press Cmd+Shift+S → overlay appears → type to filter → Enter inserts.

**Step 4: Commit**

```bash
git add hotkey.py static/index.html static/app.js
git commit -m "feat: add Cmd+Shift+S global shortcut for snippet picker overlay"
```

---

## Task Dependencies

```
Task 1 (LLM engine)
  ├── Task 2 (Personal dictionary) — independent
  ├── Task 3 (Context detection) — independent
  ├── Task 4 (Snippets storage) — independent
  └── Task 5 (Settings properties) — independent
        └── Task 6 (Post-processing pipeline) — depends on 1-5
              └── Task 7 (Wire in main.py) — depends on 6
              └── Task 8 (API endpoints) — depends on 5
                    └── Task 9 (Settings UI) — depends on 8
                          └── Task 10 (Snippet shortcut) — depends on 9
```

Tasks 1-5 can be executed in parallel. Tasks 6-10 are sequential.

## Human Checkpoints

- **After Task 1:** Verify mlx-lm loads Qwen3.5-0.8B and generates text
- **After Task 6:** Test full pipeline: dictate → cleanup → paste. Verify latency is acceptable
- **After Task 9:** Review all settings UI panels for consistency and usability
- **After Task 10:** Full end-to-end test of all features
