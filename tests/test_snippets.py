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
             patch("config.SNIPPETS_PATH", os.path.join(d, "snippets.json")), \
             patch("config.DICTIONARY_PATH", os.path.join(d, "dictionary.txt")):
            sm = SettingsManager()
            assert sm.snippets == []


def test_snippets_load_and_save():
    with tempfile.TemporaryDirectory() as d:
        snippets_path = os.path.join(d, "snippets.json")
        with patch("config.CONFIG_DIR", d), \
             patch("config.CONFIG_PATH", os.path.join(d, "config.json")), \
             patch("config.SNIPPETS_PATH", snippets_path), \
             patch("config.DICTIONARY_PATH", os.path.join(d, "dictionary.txt")):
            sm = SettingsManager()
            sm.set_snippets(SNIPPETS_FIXTURE)
            assert os.path.exists(snippets_path)
            # Reload from disk
            sm2 = SettingsManager()
            assert sm2.snippets == SNIPPETS_FIXTURE


def test_snippets_prompt_fragment():
    with tempfile.TemporaryDirectory() as d:
        with patch("config.CONFIG_DIR", d), \
             patch("config.CONFIG_PATH", os.path.join(d, "config.json")), \
             patch("config.SNIPPETS_PATH", os.path.join(d, "snippets.json")), \
             patch("config.DICTIONARY_PATH", os.path.join(d, "dictionary.txt")):
            sm = SettingsManager()
            sm.set_snippets(SNIPPETS_FIXTURE)
            fragment = sm.snippets_prompt_fragment
            assert "my cal" in fragment
            assert "calendly" in fragment


def test_snippets_prompt_fragment_empty():
    with tempfile.TemporaryDirectory() as d:
        with patch("config.CONFIG_DIR", d), \
             patch("config.CONFIG_PATH", os.path.join(d, "config.json")), \
             patch("config.SNIPPETS_PATH", os.path.join(d, "snippets.json")), \
             patch("config.DICTIONARY_PATH", os.path.join(d, "dictionary.txt")):
            sm = SettingsManager()
            assert sm.snippets_prompt_fragment is None
