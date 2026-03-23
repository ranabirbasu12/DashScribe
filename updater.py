# updater.py
"""Auto-update system for DashScribe via GitHub Releases."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from version import __version__

GITHUB_OWNER = "ranabirbasu12"
GITHUB_REPO = "DashScribe"
GITHUB_API_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
UPDATE_DIR = os.path.expanduser("~/.dashscribe/updates")
CHECK_INTERVAL_S = 86400  # 24 hours
STARTUP_DELAY_S = 30  # Wait for model warmup before first check


def _is_newer(remote: str, local: str) -> bool:
    """Compare semver strings. Returns True if remote > local."""
    def _parse(v):
        parts = v.split("-")[0].split(".")
        return tuple(int(p) for p in parts if p.isdigit())
    try:
        return _parse(remote) > _parse(local)
    except (ValueError, IndexError):
        return False


def _parse_sha256_from_body(body: str) -> str | None:
    """Extract SHA256 hex digest from release notes text."""
    match = re.search(r"(?:SHA256|sha256)[:\s]+([a-fA-F0-9]{64})", body)
    return match.group(1).lower() if match else None


def _fetch_sha256_asset(url: str) -> str | None:
    """Download a .sha256 asset file and parse the hex digest."""
    try:
        req = Request(url, headers={"User-Agent": "DashScribe-Updater"})
        with urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8").strip()
            parts = content.split()
            if parts and len(parts[0]) == 64:
                return parts[0].lower()
    except Exception:
        pass
    return None


class UpdateManager:
    """Checks GitHub Releases for updates, downloads, verifies, and installs."""

    def __init__(self, settings=None):
        self._settings = settings
        self._lock = threading.Lock()
        self._status = "idle"
        self._latest_release = None
        self._download_progress = 0.0
        self._error_message = None
        self._zip_path = None

        self._stop_event = threading.Event()
        self._cancel_event = threading.Event()
        self._check_event = threading.Event()
        self._thread = None
        self._update_callbacks = []

    # -- Lifecycle --

    def start(self):
        """Start the background update check loop."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the background thread."""
        self._stop_event.set()
        self._check_event.set()  # Wake up if sleeping
        self._cancel_event.set()  # Cancel any active download
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    # -- Public API --

    def check_now(self):
        """Trigger an immediate update check."""
        self._check_event.set()

    def download_update(self):
        """Start downloading the available update in a background thread."""
        with self._lock:
            if self._status not in ("available", "error"):
                return
            self._status = "downloading"
            self._download_progress = 0.0
            self._error_message = None
        self._cancel_event.clear()
        threading.Thread(target=self._download_update, daemon=True).start()

    def cancel_download(self):
        """Cancel an in-progress download."""
        self._cancel_event.set()

    def install_update(self):
        """Trigger the self-replace + relaunch sequence."""
        threading.Thread(target=self._install_update, daemon=True).start()

    def skip_version(self, version: str):
        """Mark a version to not prompt again."""
        if self._settings:
            self._settings.set("update_skip_version", version)
        with self._lock:
            if (
                self._latest_release
                and self._latest_release.get("version") == version
            ):
                self._status = "idle"
                self._latest_release = None

    def get_status(self) -> dict:
        """Thread-safe snapshot of current update state."""
        with self._lock:
            result = {
                "status": self._status,
                "progress": self._download_progress,
                "error": self._error_message,
                "current_version": __version__,
            }
            if self._latest_release:
                result["latest_version"] = self._latest_release["version"]
                result["release"] = self._latest_release
            return result

    # -- Callbacks --

    def on_update_available(self, callback):
        if callback not in self._update_callbacks:
            self._update_callbacks.append(callback)

    def off_update_available(self, callback):
        try:
            self._update_callbacks.remove(callback)
        except ValueError:
            pass

    # -- Background loop --

    def _run_loop(self):
        """Background thread: initial delay, then periodic checks."""
        if self._stop_event.wait(STARTUP_DELAY_S):
            return

        self._check_for_updates()

        while not self._stop_event.is_set():
            # Wait for either the check interval or a manual trigger
            triggered = self._check_event.wait(timeout=CHECK_INTERVAL_S)
            if self._stop_event.is_set():
                break
            if triggered:
                self._check_event.clear()

            auto_check = True
            if self._settings:
                auto_check = self._settings.get("update_auto_check", True)

            if auto_check or triggered:
                self._check_for_updates()

    # -- Check --

    def _check_for_updates(self):
        """Fetch latest release from GitHub API."""
        with self._lock:
            # Don't overwrite downloading/ready states
            if self._status in ("downloading", "verifying", "ready", "installing"):
                return
            self._status = "checking"
            self._error_message = None

        try:
            req = Request(
                GITHUB_API_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "DashScribe-Updater",
                },
            )
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, OSError, json.JSONDecodeError):
            with self._lock:
                self._status = "idle"
            return

        tag = data.get("tag_name", "")
        version = tag.lstrip("v")
        prerelease = data.get("prerelease", False)

        # Skip pre-releases unless opted in
        include_pre = False
        if self._settings:
            include_pre = self._settings.get("update_include_prerelease", False)
        if prerelease and not include_pre:
            with self._lock:
                self._status = "idle"
            return

        # Compare versions
        if not _is_newer(version, __version__):
            with self._lock:
                self._status = "idle"
                self._latest_release = None
            return

        # Check if user skipped this version
        skip_version = ""
        if self._settings:
            skip_version = self._settings.get("update_skip_version", "")
        if version == skip_version:
            with self._lock:
                self._status = "idle"
            return

        # Find .zip asset
        zip_asset = None
        sha_asset = None
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.endswith(".zip") and "DashScribe" in name:
                zip_asset = asset
            if name.endswith(".sha256"):
                sha_asset = asset

        if zip_asset is None:
            with self._lock:
                self._status = "idle"
            return

        # Parse SHA256
        expected_sha256 = _parse_sha256_from_body(data.get("body", ""))
        if not expected_sha256 and sha_asset:
            expected_sha256 = _fetch_sha256_asset(
                sha_asset["browser_download_url"]
            )

        release_info = {
            "version": version,
            "tag": tag,
            "prerelease": prerelease,
            "zip_url": zip_asset["browser_download_url"],
            "zip_size": zip_asset.get("size", 0),
            "sha256": expected_sha256,
            "release_notes": data.get("body", ""),
            "html_url": data.get("html_url", ""),
        }

        with self._lock:
            self._latest_release = release_info
            self._status = "available"

        # Persist check time
        if self._settings:
            self._settings.set("update_last_check", time.time())

        # Notify listeners
        for cb in tuple(self._update_callbacks):
            try:
                cb(release_info)
            except Exception:
                pass

    # -- Download --

    def _download_update(self):
        """Download the .zip asset with progress tracking."""
        with self._lock:
            release = self._latest_release
        if not release:
            with self._lock:
                self._status = "error"
                self._error_message = "No update available"
            return

        os.makedirs(UPDATE_DIR, exist_ok=True)
        zip_path = os.path.join(
            UPDATE_DIR, f"DashScribe-{release['version']}.zip"
        )

        # Clean up previous partial downloads
        if os.path.exists(zip_path):
            os.remove(zip_path)

        try:
            req = Request(
                release["zip_url"],
                headers={"User-Agent": "DashScribe-Updater"},
            )
            with urlopen(req, timeout=300) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with open(zip_path, "wb") as f:
                    while True:
                        if self._cancel_event.is_set():
                            break
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        with self._lock:
                            self._download_progress = (
                                downloaded / total if total > 0 else 0.0
                            )
        except Exception as e:
            if os.path.exists(zip_path):
                os.remove(zip_path)
            with self._lock:
                self._status = "error"
                self._error_message = f"Download failed: {e}"
            return

        # Handle cancellation
        if self._cancel_event.is_set():
            if os.path.exists(zip_path):
                os.remove(zip_path)
            with self._lock:
                self._status = "available"
                self._download_progress = 0.0
            self._cancel_event.clear()
            return

        # Verify SHA256
        if release.get("sha256"):
            with self._lock:
                self._status = "verifying"
            sha = hashlib.sha256()
            with open(zip_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    sha.update(chunk)
            if sha.hexdigest() != release["sha256"]:
                os.remove(zip_path)
                with self._lock:
                    self._status = "error"
                    self._error_message = "SHA256 verification failed"
                return

        with self._lock:
            self._status = "ready"
            self._zip_path = zip_path
            self._download_progress = 1.0

    # -- Install --

    @staticmethod
    def _find_app_bundle() -> str | None:
        """Find the .app bundle containing the running executable."""
        if getattr(sys, "frozen", None) != "macosx_app":
            return None  # Running in dev mode
        path = os.path.abspath(sys.executable)
        while path != "/":
            if path.endswith(".app"):
                return path
            path = os.path.dirname(path)
        return None

    def _install_update(self):
        """Extract, write helper script, launch it, quit app."""
        app_bundle = self._find_app_bundle()
        if not app_bundle:
            with self._lock:
                self._status = "error"
                self._error_message = (
                    "Cannot auto-update in development mode. "
                    "Download the new version manually from GitHub."
                )
            return

        with self._lock:
            zip_path = self._zip_path
        if not zip_path or not os.path.exists(zip_path):
            with self._lock:
                self._status = "error"
                self._error_message = "Update file not found"
            return

        # Extract to temp directory
        extract_dir = os.path.join(UPDATE_DIR, "extracted")
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
        except Exception as e:
            with self._lock:
                self._status = "error"
                self._error_message = f"Extract failed: {e}"
            return

        # Find the .app inside the extracted directory
        new_app = None
        for item in os.listdir(extract_dir):
            if item.endswith(".app"):
                new_app = os.path.join(extract_dir, item)
                break
        if not new_app:
            with self._lock:
                self._status = "error"
                self._error_message = "No .app found in update archive"
            return

        pid = os.getpid()
        app_parent = os.path.dirname(app_bundle)
        app_name = os.path.basename(app_bundle)
        dest_path = os.path.join(app_parent, app_name)

        # Write the helper script
        # NOTE: All paths are derived from the running process, not user input.
        # The script is written to a fixed temp location and self-deletes.
        script_path = "/tmp/dashscribe_update.sh"
        script_lines = [
            "#!/bin/bash",
            "# DashScribe Update Helper — auto-generated, self-deleting",
            f"while kill -0 {pid} 2>/dev/null; do sleep 0.5; done",
            f'rm -rf "{dest_path}"',
            f'mv "{new_app}" "{dest_path}"',
            f'xattr -r -d com.apple.quarantine "{dest_path}" 2>/dev/null || true',
            'CERT_NAME="DashScribe Developer"',
            'if security find-identity -v -p codesigning 2>/dev/null'
            ' | grep -q "\\"${CERT_NAME}\\""; then',
            f'    codesign --force --deep --sign "$CERT_NAME" "{dest_path}"'
            " 2>/dev/null || true",
            "fi",
            f'open "{dest_path}"',
            f'rm -rf "{extract_dir}"',
            f'rm -f "{zip_path}"',
            'rm -f "$0"',
        ]
        with open(script_path, "w") as f:
            f.write("\n".join(script_lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(script_path, 0o755)

        # Launch the helper detached from our process
        subprocess.Popen(
            ["/bin/bash", script_path],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        with self._lock:
            self._status = "installing"

        # Quit the app — os._exit bypasses all Python cleanup and is
        # guaranteed to work from any thread. The helper script waits for
        # our PID to die, then replaces the bundle and relaunches.
        def _quit():
            os._exit(0)

        timer = threading.Timer(0.5, _quit)
        timer.daemon = False  # Must outlive daemon threads
        timer.start()
