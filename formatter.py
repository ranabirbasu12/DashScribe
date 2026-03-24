# formatter.py
"""Stage 1 text formatter: punctuation, capitalization, and sentence segmentation.

Uses 1-800-BAD-CODE/punctuation_fullstop_truecase_english via ONNX Runtime.
No torch dependency — reimplements the inference pipeline with numpy only.
"""
import os
import threading

import numpy as np
import yaml


MODEL_REPO = "1-800-BAD-CODE/punctuation_fullstop_truecase_english"
ONNX_FILE = "punct_cap_seg_en.onnx"
SPE_FILE = "spe_32k_lc_en.model"
CONFIG_FILE = "config.yaml"
EXPECTED_SIZE_BYTES = 210_000_000  # ~210 MB
OVERLAP = 16  # tokens of overlap between windows


class PunctFormatter:
    """ONNX-based punctuation, capitalization, and sentence segmentation."""

    def __init__(self, model_repo: str = MODEL_REPO):
        self.model_repo = model_repo
        self.is_loaded = False
        self._session = None
        self._sp = None
        self._config = None
        self._max_length = 256
        self._pre_labels = None
        self._post_labels = None
        self._lock = threading.RLock()

        # Download state
        self.download_status = "idle"
        self.download_message = ""
        self.download_progress = 0.0
        self._download_thread = None

    def _cache_dir(self) -> str:
        cache = os.path.expanduser("~/.cache/huggingface/hub")
        safe = "models--" + self.model_repo.replace("/", "--")
        return os.path.join(cache, safe)

    def is_cached(self) -> bool:
        snapshots = os.path.join(self._cache_dir(), "snapshots")
        return os.path.isdir(snapshots) and len(os.listdir(snapshots)) > 0

    def get_download_progress(self) -> float:
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
        return min(total / EXPECTED_SIZE_BYTES, 1.0)

    def download_in_background(self):
        if self._download_thread and self._download_thread.is_alive():
            return
        if self.is_loaded:
            self.download_status = "ready"
            self.download_message = "Formatter loaded"
            self.download_progress = 1.0
            return
        self._download_thread = threading.Thread(target=self._bg_download, daemon=True)
        self._download_thread.start()

    def _bg_download(self):
        try:
            if self.is_cached():
                self.download_status = "loading"
                self.download_message = "Loading formatter..."
                self.download_progress = 1.0
            else:
                self.download_status = "downloading"
                self.download_message = "Downloading punctuation model (~210 MB)..."
                self.download_progress = 0.0
            self._ensure_loaded()
            self.download_status = "ready"
            self.download_message = "Formatter loaded"
            self.download_progress = 1.0
        except Exception as e:
            self.download_status = "error"
            self.download_message = f"Failed: {e}"

    def _ensure_loaded(self):
        if self._session is not None:
            return
        from huggingface_hub import hf_hub_download
        import sentencepiece as spm
        import onnxruntime as ort

        # Download model files
        onnx_path = hf_hub_download(self.model_repo, ONNX_FILE)
        spe_path = hf_hub_download(self.model_repo, SPE_FILE)
        config_path = hf_hub_download(self.model_repo, CONFIG_FILE)

        # Load config
        with open(config_path, "r") as f:
            self._config = yaml.safe_load(f)
        self._max_length = self._config.get("max_length", 256)
        raw_pre = self._config.get("pre_labels", ["<NULL>"])
        raw_post = self._config.get("post_labels", ["<NULL>"])
        # Map <NULL> to None
        self._pre_labels = [None if l == "<NULL>" else l for l in raw_pre]
        self._post_labels = [None if l == "<NULL>" else l for l in raw_post]

        # Load SentencePiece
        self._sp = spm.SentencePieceProcessor()
        self._sp.Load(spe_path)

        # Load ONNX
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 2
        self._session = ort.InferenceSession(onnx_path, sess_options=opts)
        self.is_loaded = True

    def format(self, text: str) -> str:
        """Format raw text with punctuation, capitalization, and sentence boundaries.

        Args:
            text: Raw text (typically from Whisper, may have inconsistent punct/caps).

        Returns:
            Formatted text with proper punctuation, capitalization, and sentence segmentation.
        """
        if not text or not text.strip():
            return text

        with self._lock:
            try:
                self._ensure_loaded()
            except Exception as e:
                print(f"Formatter load failed: {e}")
                return text

            # Tokenize
            input_text = text.lower().strip()
            token_ids = self._sp.EncodeAsIds(input_text)

            if not token_ids:
                return text

            # Split into windows if needed
            max_content = self._max_length - 2  # room for BOS + EOS
            windows = self._make_windows(token_ids, max_content)

            # Run inference on each window
            all_token_ids = []
            all_pre = []
            all_post = []
            all_cap = []
            all_seg = []

            for window_ids in windows:
                # Add BOS and EOS
                padded_ids = [self._sp.bos_id()] + window_ids + [self._sp.eos_id()]
                seq_len = len(padded_ids)

                input_array = np.array([padded_ids], dtype=np.int64)
                pre_preds, post_preds, cap_preds, seg_preds = self._session.run(
                    None, {"input_ids": input_array}
                )

                # Strip BOS and EOS predictions (positions 0 and seq_len-1)
                all_token_ids.append(window_ids)
                all_pre.append(pre_preds[0, 1:seq_len - 1])
                all_post.append(post_preds[0, 1:seq_len - 1])
                all_cap.append(cap_preds[0, 1:seq_len - 1])
                all_seg.append(seg_preds[0, 1:seq_len - 1])

            # Resolve overlaps
            token_ids_flat, pre_flat, post_flat, cap_flat, seg_flat = self._resolve_overlaps(
                all_token_ids, all_pre, all_post, all_cap, all_seg, len(windows)
            )

            # Decode to text
            return self._decode(token_ids_flat, pre_flat, post_flat, cap_flat, seg_flat)

    def _make_windows(self, token_ids: list[int], max_content: int) -> list[list[int]]:
        """Split token_ids into overlapping windows of max_content length."""
        if len(token_ids) <= max_content:
            return [token_ids]

        windows = []
        start = 0
        while start < len(token_ids):
            end = min(start + max_content, len(token_ids))
            windows.append(token_ids[start:end])
            if end >= len(token_ids):
                break
            start = end - OVERLAP
        return windows

    def _resolve_overlaps(self, all_ids, all_pre, all_post, all_cap, all_seg, n_windows):
        """Merge overlapping windows by splitting overlaps at the midpoint."""
        if n_windows == 1:
            return all_ids[0], all_pre[0], all_post[0], all_cap[0], all_seg[0]

        ids_out = []
        pre_out = []
        post_out = []
        cap_out = []
        seg_out = []
        half = OVERLAP // 2

        for i in range(n_windows):
            start = half if i > 0 else 0
            end = len(all_ids[i]) - half if i < n_windows - 1 else len(all_ids[i])
            ids_out.extend(all_ids[i][start:end])
            pre_out.append(all_pre[i][start:end])
            post_out.append(all_post[i][start:end])
            cap_out.append(all_cap[i][start:end])
            seg_out.append(all_seg[i][start:end])

        return (
            ids_out,
            np.concatenate(pre_out),
            np.concatenate(post_out),
            np.concatenate(cap_out) if len(cap_out) > 1 else cap_out[0],
            np.concatenate(seg_out),
        )

    def _decode(self, token_ids, pre_preds, post_preds, cap_preds, seg_preds) -> str:
        """Reconstruct formatted text from tokens and predictions."""
        sentences = []
        current = []
        WORD_BOUNDARY = "\u2581"  # SentencePiece word boundary marker

        for idx, tid in enumerate(token_ids):
            piece = self._sp.IdToPiece(tid)
            pre_label = self._pre_labels[int(pre_preds[idx])] if int(pre_preds[idx]) < len(self._pre_labels) else None
            post_label = self._post_labels[int(post_preds[idx])] if int(post_preds[idx]) < len(self._post_labels) else None
            is_acronym = post_label == "<ACRONYM>"
            cap_flags = cap_preds[idx]  # array of per-char flags
            is_sentence_end = bool(seg_preds[idx])

            # Handle word boundary
            if piece.startswith(WORD_BOUNDARY) and current:
                current.append(" ")
            char_start = 1 if piece.startswith(WORD_BOUNDARY) else 0
            chars = piece[char_start:]

            for ci, ch in enumerate(chars):
                # Pre-punctuation (e.g., inverted question mark) at first char
                if ci == 0 and pre_label is not None:
                    current.append(pre_label)

                # Capitalization
                if ci < len(cap_flags) and cap_flags[ci]:
                    ch = ch.upper()
                current.append(ch)

                # Post-punctuation
                if is_acronym:
                    current.append(".")
                elif ci == len(chars) - 1 and post_label is not None:
                    current.append(post_label)

            # Sentence boundary
            if is_sentence_end:
                sentences.append("".join(current))
                current = []

        # Remaining text
        if current:
            sentences.append("".join(current))

        return " ".join(s.strip() for s in sentences if s.strip())

    def reset(self):
        """Reset model state."""
        self._session = None
        self._sp = None
        self.is_loaded = False
        self.download_status = "idle"
        self.download_message = ""
        self.download_progress = 0.0
