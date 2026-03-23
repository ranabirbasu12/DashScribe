# llm.py
"""Local LLM for text post-processing via mlx-lm."""
import os
import threading

import mlx.core as mx

LLM_REPO = "mlx-community/Qwen3.5-0.8B-MLX-4bit"

# Token headroom: output ≈ input length + some margin for formatting changes
_TOKEN_HEADROOM = 1.3
_MIN_TOKENS = 256
_MAX_TOKENS = 8192


class LocalLLM:
    def __init__(self, model_repo: str = LLM_REPO):
        self.model_repo = model_repo
        self.is_loaded = False
        self._model = None
        self._tokenizer = None
        self._mlx_lm = None
        self._lock = threading.RLock()

        # Background download state
        self.download_status = "idle"  # idle, downloading, loading, ready, error
        self.download_message = ""
        self.download_progress = 0.0  # 0.0 to 1.0
        self._download_thread = None
        # Expected total model size in bytes (Qwen3.5-0.8B-MLX-4bit)
        self._expected_size_bytes = 600_000_000

    def _cache_dir(self) -> str:
        """Return the HuggingFace cache directory for this model."""
        cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
        safe_name = "models--" + self.model_repo.replace("/", "--")
        return os.path.join(cache_dir, safe_name)

    def is_cached(self) -> bool:
        """Check if the model is already in the HuggingFace cache."""
        snapshots = os.path.join(self._cache_dir(), "snapshots")
        return os.path.isdir(snapshots) and len(os.listdir(snapshots)) > 0

    def get_download_progress(self) -> float:
        """Calculate download progress by measuring cache folder size on disk."""
        model_dir = self._cache_dir()
        if not os.path.isdir(model_dir):
            return 0.0
        total = 0
        for dirpath, _, filenames in os.walk(model_dir):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        return min(total / self._expected_size_bytes, 1.0) if self._expected_size_bytes > 0 else 0.0

    def download_in_background(self):
        """Start downloading and loading the model in a background thread."""
        if self._download_thread and self._download_thread.is_alive():
            return  # Already in progress
        if self.is_loaded:
            self.download_status = "ready"
            self.download_message = "Model loaded"
            self.download_progress = 1.0
            return
        self._download_thread = threading.Thread(target=self._bg_download, daemon=True)
        self._download_thread.start()

    def _bg_download(self):
        """Background thread: download + load the model."""
        try:
            if self.is_cached():
                self.download_status = "loading"
                self.download_message = "Loading model into memory..."
                self.download_progress = 1.0
            else:
                self.download_status = "downloading"
                self.download_message = "Downloading language model (~600 MB)..."
                self.download_progress = 0.0
            self._ensure_loaded()
            self.download_status = "ready"
            self.download_message = "Model loaded"
            self.download_progress = 1.0
        except Exception as e:
            self.download_status = "error"
            self.download_message = f"Failed: {e}"

    def _ensure_loaded(self):
        if self._model is not None:
            return
        # Suppress transformers 5.x startup checks that can hang in py2app bundles
        os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        import mlx_lm
        self._mlx_lm = mlx_lm
        self._model, self._tokenizer = mlx_lm.load(self.model_repo)
        self.is_loaded = True

    def _estimate_max_tokens(self, text: str) -> int:
        """Set output limit based on input length — formatting shouldn't
        change the length much, so we allow input * 1.3 with a floor and cap."""
        try:
            input_tokens = len(self._tokenizer.encode(text))
        except Exception:
            # Rough estimate: ~1.3 tokens per word
            input_tokens = int(len(text.split()) * 1.3)
        limit = int(input_tokens * _TOKEN_HEADROOM)
        return max(_MIN_TOKENS, min(limit, _MAX_TOKENS))

    def generate(self, text: str, system_prompt: str, max_tokens: int | None = None) -> str:
        with self._lock:
            try:
                self._ensure_loaded()
                if max_tokens is None:
                    max_tokens = self._estimate_max_tokens(text)
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
                mx.clear_cache()
                return result.strip()
            except Exception as e:
                print(f"LLM generation failed: {e}")
                return ""
