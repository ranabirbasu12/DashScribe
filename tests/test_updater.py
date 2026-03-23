# tests/test_updater.py
"""Tests for the auto-update system."""
import json
import hashlib
import os
import threading
import time
import zipfile
from io import BytesIO
from unittest.mock import MagicMock, patch, PropertyMock
from urllib.error import URLError

import pytest

from updater import (
    UpdateManager,
    _is_newer,
    _parse_sha256_from_body,
    _fetch_sha256_asset,
    GITHUB_API_URL,
)
from version import __version__


# --- Version comparison ---

def test_is_newer_basic():
    assert _is_newer("1.1.0", "1.0.0") is True
    assert _is_newer("2.0.0", "1.9.9") is True
    assert _is_newer("1.0.1", "1.0.0") is True


def test_is_newer_same_version():
    assert _is_newer("1.0.0", "1.0.0") is False


def test_is_newer_older():
    assert _is_newer("0.9.0", "1.0.0") is False
    assert _is_newer("1.0.0", "1.0.1") is False


def test_is_newer_prerelease_suffix():
    assert _is_newer("2.0.0-beta", "1.0.0") is True
    assert _is_newer("1.0.0-beta", "1.0.0") is False


def test_is_newer_invalid():
    assert _is_newer("invalid", "1.0.0") is False
    assert _is_newer("", "1.0.0") is False


# --- SHA256 parsing ---

def test_parse_sha256_from_body():
    body = "Some notes\n\nSHA256: abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890\n"
    result = _parse_sha256_from_body(body)
    assert result == "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"


def test_parse_sha256_from_body_lowercase_key():
    body = "sha256: ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890"
    result = _parse_sha256_from_body(body)
    assert result == "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"


def test_parse_sha256_from_body_no_match():
    assert _parse_sha256_from_body("No checksum here") is None
    assert _parse_sha256_from_body("") is None


def test_parse_sha256_from_body_code_block():
    body = "```\nSHA256: abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890\n```"
    result = _parse_sha256_from_body(body)
    assert result is not None


# --- UpdateManager ---

def _make_manager(**kwargs):
    settings = MagicMock()
    settings.get = MagicMock(side_effect=lambda key, default=None: {
        "update_auto_check": True,
        "update_include_prerelease": False,
        "update_skip_version": "",
    }.get(key, default))
    settings.set = MagicMock()
    mgr = UpdateManager(settings=settings)
    return mgr


def _make_release_response(version="2.0.0", prerelease=False, sha256=None):
    """Create a mock GitHub API release response."""
    body = f"Release notes for {version}"
    if sha256:
        body += f"\n\nSHA256: {sha256}"
    return json.dumps({
        "tag_name": f"v{version}",
        "prerelease": prerelease,
        "draft": False,
        "html_url": f"https://github.com/test/repo/releases/tag/v{version}",
        "body": body,
        "assets": [
            {
                "name": f"DashScribe-{version}.zip",
                "browser_download_url": f"https://github.com/test/repo/releases/download/v{version}/DashScribe-{version}.zip",
                "size": 100000,
            }
        ],
    }).encode("utf-8")


class MockResponse:
    """Mock for urlopen response."""
    def __init__(self, data, status=200, headers=None):
        self._data = data
        self.status = status
        self.headers = headers or {}
    def read(self, size=-1):
        if size == -1:
            return self._data
        chunk = self._data[:size]
        self._data = self._data[size:]
        return chunk
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


def test_manager_initial_state():
    mgr = _make_manager()
    status = mgr.get_status()
    assert status["status"] == "idle"
    assert status["current_version"] == __version__
    assert status["progress"] == 0.0
    assert status["error"] is None


def test_check_finds_newer_version():
    mgr = _make_manager()
    response_data = _make_release_response("2.0.0")

    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    status = mgr.get_status()
    assert status["status"] == "available"
    assert status["latest_version"] == "2.0.0"
    assert status["release"]["zip_url"].endswith(".zip")


def test_check_same_version_stays_idle():
    mgr = _make_manager()
    response_data = _make_release_response(__version__)

    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    assert mgr.get_status()["status"] == "idle"


def test_check_skips_prerelease_by_default():
    mgr = _make_manager()
    response_data = _make_release_response("2.0.0", prerelease=True)

    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    assert mgr.get_status()["status"] == "idle"


def test_check_includes_prerelease_when_opted_in():
    mgr = _make_manager()
    mgr._settings.get = MagicMock(side_effect=lambda key, default=None: {
        "update_auto_check": True,
        "update_include_prerelease": True,
        "update_skip_version": "",
    }.get(key, default))
    response_data = _make_release_response("2.0.0", prerelease=True)

    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    assert mgr.get_status()["status"] == "available"


def test_check_skips_dismissed_version():
    mgr = _make_manager()
    mgr._settings.get = MagicMock(side_effect=lambda key, default=None: {
        "update_auto_check": True,
        "update_include_prerelease": False,
        "update_skip_version": "2.0.0",
    }.get(key, default))
    response_data = _make_release_response("2.0.0")

    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    assert mgr.get_status()["status"] == "idle"


def test_check_network_error_stays_idle():
    mgr = _make_manager()

    from urllib.error import URLError
    with patch("updater.urlopen", side_effect=URLError("Network down")):
        mgr._check_for_updates()

    assert mgr.get_status()["status"] == "idle"


def test_check_no_zip_asset_stays_idle():
    mgr = _make_manager()
    data = json.dumps({
        "tag_name": "v2.0.0",
        "prerelease": False,
        "draft": False,
        "body": "No zip",
        "assets": [
            {"name": "DashScribe-2.0.0.dmg", "browser_download_url": "https://x/y.dmg", "size": 100}
        ],
    }).encode("utf-8")

    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(data)
        mgr._check_for_updates()

    assert mgr.get_status()["status"] == "idle"


def test_check_fires_callback():
    mgr = _make_manager()
    callback = MagicMock()
    mgr.on_update_available(callback)

    response_data = _make_release_response("2.0.0")
    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    callback.assert_called_once()
    assert callback.call_args[0][0]["version"] == "2.0.0"


def test_skip_version_persists_and_clears_state():
    mgr = _make_manager()

    # First set up an available update
    response_data = _make_release_response("2.0.0")
    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    assert mgr.get_status()["status"] == "available"

    # Skip it
    mgr.skip_version("2.0.0")
    mgr._settings.set.assert_called_with("update_skip_version", "2.0.0")
    assert mgr.get_status()["status"] == "idle"


def test_find_app_bundle_dev_mode():
    """In dev mode (not frozen), _find_app_bundle returns None."""
    assert UpdateManager._find_app_bundle() is None


def test_install_fails_in_dev_mode():
    mgr = _make_manager()
    mgr._install_update()
    status = mgr.get_status()
    assert status["status"] == "error"
    assert "development mode" in status["error"]


def test_download_cancel():
    mgr = _make_manager()

    # Set up available update
    response_data = _make_release_response("2.0.0")
    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    # Set cancel before download starts
    mgr._cancel_event.set()

    # Mock download with slow chunks
    zip_content = b"fake zip content" * 100
    mock_resp = MockResponse(
        zip_content, headers={"Content-Length": str(len(zip_content))}
    )

    with patch("updater.urlopen", return_value=mock_resp):
        with patch("updater.os.makedirs"):
            with patch("builtins.open", MagicMock()):
                mgr._download_update()

    status = mgr.get_status()
    assert status["status"] == "available"


def test_sha256_extraction_from_release():
    """SHA256 in release body is correctly picked up during check."""
    mgr = _make_manager()
    expected_hash = "a" * 64
    response_data = _make_release_response("2.0.0", sha256=expected_hash)

    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    status = mgr.get_status()
    assert status["release"]["sha256"] == expected_hash


def test_off_update_available_removes_callback():
    mgr = _make_manager()
    callback = MagicMock()
    mgr.on_update_available(callback)
    mgr.off_update_available(callback)

    response_data = _make_release_response("2.0.0")
    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    callback.assert_not_called()


def test_check_does_not_override_downloading_state():
    mgr = _make_manager()
    with mgr._lock:
        mgr._status = "downloading"

    response_data = _make_release_response("2.0.0")
    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    assert mgr.get_status()["status"] == "downloading"


# =====================================================================
# _is_newer edge cases
# =====================================================================

def test_is_newer_value_error():
    """_is_newer handles ValueError from int() gracefully."""
    assert _is_newer("a.b.c", "1.0.0") is False


# =====================================================================
# _fetch_sha256_asset
# =====================================================================

def test_fetch_sha256_asset_success():
    sha = "a" * 64
    content = f"{sha}  DashScribe.zip\n".encode("utf-8")
    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(content)
        result = _fetch_sha256_asset("https://example.com/file.sha256")
    assert result == sha


def test_fetch_sha256_asset_invalid_content():
    content = b"not-a-sha256"
    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(content)
        result = _fetch_sha256_asset("https://example.com/file.sha256")
    assert result is None


def test_fetch_sha256_asset_network_error():
    with patch("updater.urlopen", side_effect=URLError("fail")):
        result = _fetch_sha256_asset("https://example.com/file.sha256")
    assert result is None


# =====================================================================
# UpdateManager lifecycle
# =====================================================================

def test_start_already_running():
    """start() is idempotent when thread is alive."""
    mgr = _make_manager()
    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True
    mgr._thread = mock_thread
    mgr.start()
    # Should not create a new thread
    assert mgr._thread is mock_thread


def test_stop_joins_thread():
    mgr = _make_manager()
    mock_thread = MagicMock()
    mgr._thread = mock_thread
    mgr.stop()
    mock_thread.join.assert_called_once_with(timeout=5)
    assert mgr._thread is None


def test_stop_without_thread():
    mgr = _make_manager()
    mgr._thread = None
    mgr.stop()  # Should not raise


# =====================================================================
# check_now
# =====================================================================

def test_check_now_sets_event():
    mgr = _make_manager()
    mgr.check_now()
    assert mgr._check_event.is_set()


# =====================================================================
# download_update guards
# =====================================================================

def test_download_update_wrong_state():
    """download_update does nothing if not in 'available' or 'error' state."""
    mgr = _make_manager()
    mgr._status = "idle"
    mgr.download_update()
    assert mgr._status == "idle"


def test_download_update_from_error_state():
    """download_update works from error state."""
    mgr = _make_manager()
    mgr._status = "error"
    mgr._latest_release = {"version": "2.0.0", "zip_url": "https://x/y.zip"}
    with patch.object(mgr, "_download_update"):
        mgr.download_update()
    assert mgr._status == "downloading"


# =====================================================================
# cancel_download
# =====================================================================

def test_cancel_download_sets_event():
    mgr = _make_manager()
    mgr.cancel_download()
    assert mgr._cancel_event.is_set()


# =====================================================================
# _download_update
# =====================================================================

def test_download_update_no_release():
    """_download_update with no release sets error."""
    mgr = _make_manager()
    mgr._latest_release = None
    mgr._download_update()
    status = mgr.get_status()
    assert status["status"] == "error"
    assert "No update available" in status["error"]


def test_download_update_network_error():
    """Download failure sets error status."""
    mgr = _make_manager()
    mgr._latest_release = {
        "version": "2.0.0",
        "zip_url": "https://example.com/DashScribe-2.0.0.zip",
        "sha256": None,
    }

    with patch("updater.urlopen", side_effect=URLError("Network error")):
        with patch("updater.os.makedirs"):
            mgr._download_update()

    status = mgr.get_status()
    assert status["status"] == "error"
    assert "Download failed" in status["error"]


def test_download_update_success_no_sha256():
    """Successful download without SHA256 goes to 'ready'."""
    mgr = _make_manager()
    mgr._latest_release = {
        "version": "2.0.0",
        "zip_url": "https://example.com/DashScribe-2.0.0.zip",
        "sha256": None,
    }

    zip_content = b"PK" + b"\x00" * 100
    mock_resp = MockResponse(zip_content, headers={"Content-Length": str(len(zip_content))})

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("updater.UPDATE_DIR", tmpdir):
            with patch("updater.urlopen", return_value=mock_resp):
                mgr._download_update()

    status = mgr.get_status()
    assert status["status"] == "ready"
    assert status["progress"] == 1.0


def test_download_update_sha256_verification_pass():
    """Download with matching SHA256 goes to 'ready'."""
    mgr = _make_manager()
    zip_content = b"PK" + b"\x00" * 100
    expected_sha = hashlib.sha256(zip_content).hexdigest()
    mgr._latest_release = {
        "version": "2.0.0",
        "zip_url": "https://example.com/DashScribe-2.0.0.zip",
        "sha256": expected_sha,
    }

    mock_resp = MockResponse(zip_content, headers={"Content-Length": str(len(zip_content))})

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("updater.UPDATE_DIR", tmpdir):
            with patch("updater.urlopen", return_value=mock_resp):
                mgr._download_update()

    status = mgr.get_status()
    assert status["status"] == "ready"


def test_download_update_sha256_verification_fail():
    """Download with wrong SHA256 sets error."""
    mgr = _make_manager()
    zip_content = b"PK" + b"\x00" * 100
    mgr._latest_release = {
        "version": "2.0.0",
        "zip_url": "https://example.com/DashScribe-2.0.0.zip",
        "sha256": "0" * 64,  # wrong hash
    }

    mock_resp = MockResponse(zip_content, headers={"Content-Length": str(len(zip_content))})

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("updater.UPDATE_DIR", tmpdir):
            with patch("updater.urlopen", return_value=mock_resp):
                mgr._download_update()

    status = mgr.get_status()
    assert status["status"] == "error"
    assert "SHA256" in status["error"]


def test_download_update_cancel_during_download():
    """Cancelling during download resets to 'available'."""
    mgr = _make_manager()
    mgr._latest_release = {
        "version": "2.0.0",
        "zip_url": "https://example.com/DashScribe-2.0.0.zip",
        "sha256": None,
    }

    # Use a response that provides data but check cancel
    zip_content = b"PK" + b"\x00" * 100
    mock_resp = MockResponse(zip_content, headers={"Content-Length": str(len(zip_content))})

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("updater.UPDATE_DIR", tmpdir):
            with patch("updater.urlopen", return_value=mock_resp):
                mgr._cancel_event.set()
                mgr._download_update()

    status = mgr.get_status()
    assert status["status"] == "available"
    assert status["progress"] == 0.0


def test_download_update_removes_previous():
    """Previous partial download is cleaned up."""
    mgr = _make_manager()
    mgr._latest_release = {
        "version": "2.0.0",
        "zip_url": "https://example.com/DashScribe-2.0.0.zip",
        "sha256": None,
    }

    zip_content = b"PK" + b"\x00" * 100
    mock_resp = MockResponse(zip_content, headers={"Content-Length": str(len(zip_content))})

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "DashScribe-2.0.0.zip")
        with open(zip_path, "w") as f:
            f.write("old partial")

        with patch("updater.UPDATE_DIR", tmpdir):
            with patch("updater.urlopen", return_value=mock_resp):
                mgr._download_update()

    assert mgr.get_status()["status"] == "ready"


# =====================================================================
# _install_update
# =====================================================================

def test_install_update_no_zip_path():
    """Install with no zip path sets error."""
    mgr = _make_manager()
    mgr._zip_path = None

    with patch.object(UpdateManager, "_find_app_bundle", return_value="/Applications/DashScribe.app"):
        mgr._install_update()

    status = mgr.get_status()
    assert status["status"] == "error"
    assert "not found" in status["error"]


def test_install_update_zip_missing():
    """Install when zip file doesn't exist sets error."""
    mgr = _make_manager()
    mgr._zip_path = "/nonexistent/path.zip"

    with patch.object(UpdateManager, "_find_app_bundle", return_value="/Applications/DashScribe.app"):
        mgr._install_update()

    status = mgr.get_status()
    assert status["status"] == "error"
    assert "not found" in status["error"]


def test_install_update_extract_failure():
    """Bad zip file sets error."""
    mgr = _make_manager()

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "bad.zip")
        with open(zip_path, "w") as f:
            f.write("not a zip")
        mgr._zip_path = zip_path

        with patch.object(UpdateManager, "_find_app_bundle", return_value="/Applications/DashScribe.app"):
            with patch("updater.UPDATE_DIR", tmpdir):
                mgr._install_update()

    status = mgr.get_status()
    assert status["status"] == "error"
    assert "Extract failed" in status["error"]


def test_install_update_no_app_in_archive():
    """Zip without .app inside sets error."""
    mgr = _make_manager()

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "DashScribe-2.0.0.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("README.txt", "No app here")
        mgr._zip_path = zip_path

        with patch.object(UpdateManager, "_find_app_bundle", return_value="/Applications/DashScribe.app"):
            with patch("updater.UPDATE_DIR", tmpdir):
                mgr._install_update()

    status = mgr.get_status()
    assert status["status"] == "error"
    assert "No .app found" in status["error"]


def test_install_update_success():
    """Full install flow: extract, write script, launch helper, set installing."""
    mgr = _make_manager()

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "DashScribe-2.0.0.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("DashScribe.app/Contents/Info.plist", "<plist/>")
        mgr._zip_path = zip_path

        with patch.object(UpdateManager, "_find_app_bundle", return_value="/Applications/DashScribe.app"):
            with patch("updater.UPDATE_DIR", tmpdir):
                with patch("updater.subprocess.Popen") as mock_popen:
                    with patch("updater.threading.Timer") as mock_timer:
                        mgr._install_update()

        status = mgr.get_status()
        assert status["status"] == "installing"
        mock_popen.assert_called_once()
        mock_timer.assert_called_once()


# =====================================================================
# _find_app_bundle
# =====================================================================

def test_find_app_bundle_frozen():
    """When frozen, finds the .app by traversing up from executable."""
    with patch("updater.sys") as mock_sys:
        mock_sys.frozen = "macosx_app"
        mock_sys.executable = "/Applications/DashScribe.app/Contents/MacOS/python"
        result = UpdateManager._find_app_bundle()
    assert result == "/Applications/DashScribe.app"


def test_find_app_bundle_frozen_no_app():
    """When frozen but no .app in path returns None."""
    with patch("updater.sys") as mock_sys:
        mock_sys.frozen = "macosx_app"
        mock_sys.executable = "/usr/local/bin/python"
        result = UpdateManager._find_app_bundle()
    assert result is None


# =====================================================================
# _run_loop
# =====================================================================

def test_run_loop_stops_on_stop_event():
    """Background loop exits when stop event is set during startup delay."""
    mgr = _make_manager()
    mgr._stop_event.set()
    mgr._run_loop()  # Should return immediately


def test_run_loop_periodic_check():
    """Background loop performs check and respects stop event."""
    mgr = _make_manager()
    call_count = 0

    def fake_check():
        nonlocal call_count
        call_count += 1
        mgr._stop_event.set()  # Stop after first check

    with patch.object(mgr, "_check_for_updates", side_effect=fake_check):
        with patch("updater.STARTUP_DELAY_S", 0):
            with patch("updater.CHECK_INTERVAL_S", 0):
                mgr._run_loop()

    assert call_count >= 1


def test_run_loop_manual_trigger():
    """Manual trigger via check_now wakes the loop."""
    mgr = _make_manager()
    call_count = 0

    def fake_check():
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            mgr._stop_event.set()

    with patch.object(mgr, "_check_for_updates", side_effect=fake_check):
        with patch("updater.STARTUP_DELAY_S", 0):
            # Set check event before the loop waits
            mgr._check_event.set()
            with patch("updater.CHECK_INTERVAL_S", 0):
                mgr._run_loop()

    assert call_count >= 2


def test_run_loop_auto_check_disabled():
    """When auto_check is False and no manual trigger, skip check."""
    mgr = _make_manager()
    mgr._settings.get = MagicMock(side_effect=lambda key, default=None: {
        "update_auto_check": False,
        "update_include_prerelease": False,
        "update_skip_version": "",
    }.get(key, default))

    call_count = 0

    def fake_check():
        nonlocal call_count
        call_count += 1
        mgr._stop_event.set()

    with patch.object(mgr, "_check_for_updates", side_effect=fake_check):
        with patch("updater.STARTUP_DELAY_S", 0):
            with patch("updater.CHECK_INTERVAL_S", 0):
                mgr._run_loop()

    # First call from initial check (always happens), then loop skips due to auto_check=False
    assert call_count == 1


# =====================================================================
# _check_for_updates with SHA256 from asset file
# =====================================================================

def test_check_uses_sha_asset_when_no_body_hash():
    """When body has no SHA256, falls back to .sha256 asset."""
    mgr = _make_manager()
    sha = "b" * 64
    data = json.dumps({
        "tag_name": "v2.0.0",
        "prerelease": False,
        "body": "No hash in body",
        "html_url": "https://example.com",
        "assets": [
            {
                "name": "DashScribe-2.0.0.zip",
                "browser_download_url": "https://x/DashScribe-2.0.0.zip",
                "size": 100,
            },
            {
                "name": "DashScribe-2.0.0.sha256",
                "browser_download_url": "https://x/DashScribe-2.0.0.sha256",
            },
        ],
    }).encode("utf-8")

    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(data)
        with patch("updater._fetch_sha256_asset", return_value=sha):
            mgr._check_for_updates()

    status = mgr.get_status()
    assert status["status"] == "available"
    assert status["release"]["sha256"] == sha


# =====================================================================
# Callback error handling
# =====================================================================

def test_callback_exception_doesnt_crash():
    """Exception in update callback doesn't crash the check."""
    mgr = _make_manager()
    bad_cb = MagicMock(side_effect=RuntimeError("boom"))
    good_cb = MagicMock()
    mgr.on_update_available(bad_cb)
    mgr.on_update_available(good_cb)

    response_data = _make_release_response("2.0.0")
    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    good_cb.assert_called_once()


def test_off_update_available_nonexistent():
    """Removing a callback that doesn't exist doesn't crash."""
    mgr = _make_manager()
    mgr.off_update_available(lambda: None)  # should not raise


# =====================================================================
# skip_version edge cases
# =====================================================================

def test_skip_version_no_settings():
    """skip_version with no settings doesn't crash."""
    mgr = UpdateManager(settings=None)
    mgr.skip_version("2.0.0")  # should not raise


def test_skip_version_different_version():
    """Skipping a version different from latest doesn't clear state."""
    mgr = _make_manager()
    response_data = _make_release_response("2.0.0")
    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()

    mgr.skip_version("3.0.0")
    assert mgr.get_status()["status"] == "available"  # unchanged


# =====================================================================
# _check_for_updates does not override other active states
# =====================================================================

@pytest.mark.parametrize("state", ["verifying", "ready", "installing"])
def test_check_does_not_override_active_states(state):
    mgr = _make_manager()
    with mgr._lock:
        mgr._status = state
    mgr._check_for_updates()
    assert mgr.get_status()["status"] == state


# =====================================================================
# _check_for_updates with no settings
# =====================================================================

def test_check_no_settings():
    """_check_for_updates works with no settings object."""
    mgr = UpdateManager(settings=None)
    response_data = _make_release_response("2.0.0")
    with patch("updater.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockResponse(response_data)
        mgr._check_for_updates()
    assert mgr.get_status()["status"] == "available"


# =====================================================================
# install_update launches thread
# =====================================================================

def test_install_update_launches_thread():
    mgr = _make_manager()
    with patch.object(mgr, "_install_update") as mock_install:
        mgr.install_update()
        time.sleep(0.05)
    mock_install.assert_called_once()
