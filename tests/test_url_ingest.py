# tests/test_url_ingest.py
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import create_app


def test_from_url_invokes_yt_dlp_and_returns_path(tmp_path):
    fake_path = tmp_path / "downloaded.m4a"
    fake_path.write_bytes(b"")
    with patch("app._download_url") as mock_dl:
        mock_dl.return_value = str(fake_path)
        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/file-job/from-url", json={"url": "https://example.com/x"})
        assert resp.status_code == 200
        assert resp.json()["path"] == str(fake_path)


def test_from_url_rejects_empty_url():
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/file-job/from-url", json={"url": ""})
    assert resp.status_code == 400
