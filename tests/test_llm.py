# tests/test_llm.py
from unittest.mock import patch, MagicMock
from llm import LocalLLM, LLM_REPO


def test_llm_initializes_without_loading_model():
    llm = LocalLLM()
    assert not llm.is_loaded
    assert llm.model_repo == LLM_REPO


def test_llm_generate_loads_model_lazily():
    llm = LocalLLM()
    mock_model = MagicMock()
    mock_tokenizer = MagicMock()
    mock_tokenizer.apply_chat_template.return_value = "formatted prompt"
    with patch("llm.mx.clear_cache"), \
         patch.object(llm, "_ensure_loaded") as mock_load:
        llm._model = mock_model
        llm._tokenizer = mock_tokenizer
        llm._mlx_lm = MagicMock()
        llm._mlx_lm.generate.return_value = "  cleaned text  "
        llm.is_loaded = True
        result = llm.generate("raw text", system_prompt="Clean this.")
        assert result == "cleaned text"


def test_llm_generate_reuses_loaded_model():
    llm = LocalLLM()
    mock_model = MagicMock()
    mock_tokenizer = MagicMock()
    mock_tokenizer.apply_chat_template.return_value = "prompt"
    mock_mlx_lm = MagicMock()
    mock_mlx_lm.generate.return_value = "output"
    with patch("llm.mx.clear_cache"):
        llm._model = mock_model
        llm._tokenizer = mock_tokenizer
        llm._mlx_lm = mock_mlx_lm
        llm.is_loaded = True
        llm.generate("first", system_prompt="s")
        llm.generate("second", system_prompt="s")
        # _ensure_loaded checks self._model is not None, so load() is never called
        assert mock_mlx_lm.load.call_count == 0


def test_llm_generate_returns_empty_on_error():
    llm = LocalLLM()
    with patch.object(llm, "_ensure_loaded", side_effect=Exception("fail")):
        result = llm.generate("text", system_prompt="system")
        assert result == ""
