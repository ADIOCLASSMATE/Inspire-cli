"""Token acquisition, caching, and refresh for v2 API authentication.

Uses the platform's ``POST /auth/token`` endpoint with username/password
to obtain a Bearer access token. Token is cached to disk at
``~/.cache/inspire-cli/v2_token.json`` with 0o600 permissions.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from inspire.config.models import Config

logger = logging.getLogger(__name__)

V2_TOKEN_CACHE_DIR = Path.home() / ".cache" / "inspire-cli"
V2_TOKEN_CACHE_FILE = V2_TOKEN_CACHE_DIR / "v2_token.json"

# Refresh token this many seconds before actual expiry.
_REFRESH_SLIPPAGE_S = 300  # 5 minutes


@dataclass(frozen=True)
class V2TokenSet:
    """Cached v2 access token with expiry tracking."""

    access_token: str
    access_expires_at: float  # time.time() seconds


def get_token(base_url: str, username: str, password: str) -> str:
    """Obtain a v2 access token from the platform ``/auth/token`` endpoint.

    POST ``{base_url}/auth/token`` with ``{"username": ..., "password": ...}``.
    Returns the ``access_token`` string.

    Raises:
        ValueError: If the token endpoint returns an error or non-200 status.
        requests.RequestException: On network errors.
    """
    url = f"{base_url.rstrip('/')}/auth/token"
    try:
        resp = requests.post(
            url,
            json={"username": username, "password": password},
            timeout=30,
        )
        data = resp.json()
    except requests.RequestException:
        raise
    except ValueError as e:
        raise ValueError(f"Non-JSON response from {url}: {e}") from e

    if resp.status_code != 200:
        msg = data.get("message", f"HTTP {resp.status_code}")
        raise ValueError(f"Token endpoint returned {resp.status_code}: {msg}")

    if data.get("code") != 0:
        raise ValueError(
            f"Token endpoint error: {data.get('message', 'Unknown error')}"
        )

    access_token = (data.get("data") or {}).get("access_token")
    if not access_token:
        raise ValueError("Token endpoint returned no access_token")

    # Try to get expires_in from response; default to 1 hour.
    expires_in = int((data.get("data") or {}).get("expires_in", 3600))

    token_set = V2TokenSet(
        access_token=access_token,
        access_expires_at=time.time() + expires_in,
    )
    save_token(token_set)
    return access_token


def save_token(token_set: V2TokenSet) -> None:
    """Persist token set to disk cache with restricted permissions."""
    V2_TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": token_set.access_token,
        "access_expires_at": token_set.access_expires_at,
    }
    tmp_path = V2_TOKEN_CACHE_FILE.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(payload))
        tmp_path.chmod(0o600)
        tmp_path.rename(V2_TOKEN_CACHE_FILE)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def load_cached_token() -> Optional[V2TokenSet]:
    """Load a cached token set from disk. Returns None if not found or corrupted."""
    if not V2_TOKEN_CACHE_FILE.exists():
        return None
    try:
        raw = V2_TOKEN_CACHE_FILE.read_text()
        data = json.loads(raw)
        return V2TokenSet(
            access_token=data["access_token"],
            access_expires_at=float(data["access_expires_at"]),
        )
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def clear_token() -> None:
    """Remove the cached v2 token."""
    try:
        V2_TOKEN_CACHE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _is_token_valid(token_set: V2TokenSet) -> bool:
    """Check if a cached token is still valid (with slippage buffer)."""
    return token_set.access_expires_at > (time.time() + _REFRESH_SLIPPAGE_S)


def ensure_token(config: Config) -> str:
    """Get a valid v2 access token.

    Tries cached token first, then fetches a fresh one from the platform
    ``/auth/token`` endpoint.

    Raises:
        ValueError: If credentials are missing or token acquisition fails.
    """
    # Try cached token first
    cached = load_cached_token()
    if cached and _is_token_valid(cached):
        return cached.access_token

    # Need fresh token
    username = (config.username or "").strip()
    password = config.password or ""
    base_url = config.base_url or "https://api.example.com"

    if not username or not password:
        raise ValueError(
            "Missing credentials for v2 API authentication. "
            "Set INSPIRE_USERNAME and INSPIRE_PASSWORD."
        )

    return get_token(base_url, username, password)
