"""Tests for v2 API authentication."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

from inspire.platform.web.v2_api.auth import (
    V2TokenSet,
    clear_token,
    ensure_token,
    get_token,
    load_cached_token,
    save_token,
    V2_TOKEN_CACHE_FILE,
)


class TestV2TokenSet:
    def test_create_token_set(self):
        ts = V2TokenSet(access_token="abc", access_expires_at=1234567890.0)
        assert ts.access_token == "abc"
        assert ts.access_expires_at == 1234567890.0


class TestTokenCache:
    def test_save_and_load_token(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "v2_token.json"
        monkeypatch.setattr(
            "inspire.platform.web.v2_api.auth.V2_TOKEN_CACHE_FILE", cache_file
        )

        ts = V2TokenSet(access_token="test-token", access_expires_at=time.time() + 3600)
        save_token(ts)

        loaded = load_cached_token()
        assert loaded is not None
        assert loaded.access_token == "test-token"
        assert abs(loaded.access_expires_at - ts.access_expires_at) < 1

    def test_load_nonexistent(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "nonexistent.json"
        monkeypatch.setattr(
            "inspire.platform.web.v2_api.auth.V2_TOKEN_CACHE_FILE", cache_file
        )
        assert load_cached_token() is None

    def test_load_corrupted(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "corrupted.json"
        cache_file.write_text("not valid json")
        monkeypatch.setattr(
            "inspire.platform.web.v2_api.auth.V2_TOKEN_CACHE_FILE", cache_file
        )
        assert load_cached_token() is None

    def test_clear_token(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "v2_token.json"
        monkeypatch.setattr(
            "inspire.platform.web.v2_api.auth.V2_TOKEN_CACHE_FILE", cache_file
        )
        ts = V2TokenSet(access_token="test-token", access_expires_at=time.time() + 3600)
        save_token(ts)
        assert load_cached_token() is not None

        clear_token()
        assert load_cached_token() is None

    def test_load_missing_fields(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "partial.json"
        cache_file.write_text(json.dumps({"access_token": "abc"}))
        monkeypatch.setattr(
            "inspire.platform.web.v2_api.auth.V2_TOKEN_CACHE_FILE", cache_file
        )
        assert load_cached_token() is None


class TestGetToken:
    def test_get_token_success(self):
        with mock.patch("inspire.platform.web.v2_api.auth.requests.post") as mock_post:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "code": 0,
                "data": {"access_token": "test-access-token"},
            }
            mock_post.return_value = mock_resp

            with mock.patch("inspire.platform.web.v2_api.auth.save_token"):
                token = get_token("https://example.com", "user", "pass")

            assert token == "test-access-token"
            mock_post.assert_called_once_with(
                "https://example.com/auth/token",
                json={"username": "user", "password": "pass"},
                timeout=30,
            )

    def test_get_token_auth_failure(self):
        with mock.patch("inspire.platform.web.v2_api.auth.requests.post") as mock_post:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "code": 1,
                "message": "Invalid credentials",
            }
            mock_post.return_value = mock_resp

            with pytest.raises(ValueError, match="Invalid credentials"):
                get_token("https://example.com", "user", "wrong-pass")

    def test_get_token_no_access_token(self):
        with mock.patch("inspire.platform.web.v2_api.auth.requests.post") as mock_post:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"code": 0, "data": {}}
            mock_post.return_value = mock_resp

            with pytest.raises(ValueError, match="no access_token"):
                get_token("https://example.com", "user", "pass")


class TestEnsureToken:
    def test_cache_hit(self, monkeypatch):
        """ensure_token returns cached token when valid."""
        valid_token = V2TokenSet(
            access_token="cached-token",
            access_expires_at=time.time() + 7200,  # 2 hours from now
        )
        monkeypatch.setattr(
            "inspire.platform.web.v2_api.auth.load_cached_token",
            lambda: valid_token,
        )

        from inspire.config.models import Config

        config = Config(username="user", password="pass")
        token = ensure_token(config)
        assert token == "cached-token"

    def test_cache_miss_fetches_fresh(self, monkeypatch):
        """ensure_token fetches fresh token when cache is empty."""
        monkeypatch.setattr(
            "inspire.platform.web.v2_api.auth.load_cached_token",
            lambda: None,
        )
        monkeypatch.setattr(
            "inspire.platform.web.v2_api.auth.get_token",
            lambda base_url, username, password: "fresh-token",
        )

        from inspire.config.models import Config

        config = Config(username="user", password="pass")
        token = ensure_token(config)
        assert token == "fresh-token"

    def test_expired_cache_fetches_fresh(self, monkeypatch):
        """ensure_token fetches fresh token when cached token is expired."""
        expired_token = V2TokenSet(
            access_token="expired-token",
            access_expires_at=time.time() - 3600,  # 1 hour ago
        )
        monkeypatch.setattr(
            "inspire.platform.web.v2_api.auth.load_cached_token",
            lambda: expired_token,
        )
        monkeypatch.setattr(
            "inspire.platform.web.v2_api.auth.get_token",
            lambda base_url, username, password: "fresh-token",
        )

        from inspire.config.models import Config

        config = Config(username="user", password="pass")
        token = ensure_token(config)
        assert token == "fresh-token"

    def test_missing_credentials_raises(self):
        """ensure_token raises ValueError when credentials are missing."""
        from inspire.config.models import Config

        config = Config(username="", password="")
        with pytest.raises(ValueError, match="Missing credentials"):
            ensure_token(config)
