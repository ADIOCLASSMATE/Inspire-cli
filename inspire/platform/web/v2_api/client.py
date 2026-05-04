"""Generic v2 HTTP client for Inspire platform.

All v2 endpoints follow the pattern::

    POST /api/v2/{service}?Action={action}

with JSON body, Bearer token auth, and custom headers.
"""

from __future__ import annotations

from typing import Any

import requests

from inspire import __version__

V2_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "x-inspire-client-source": f"inspire-cli/{__version__}",
}


class V2ApiError(Exception):
    """Error returned by the v2 API."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int = 0,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status


def post_v2(
    service: str,
    action: str,
    body: dict[str, Any],
    token: str,
    base_url: str,
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    """Call a v2 API endpoint.

    POST ``{base_url}/api/v2/{service}?Action={action}`` with Bearer token.

    Returns the ``Result`` (or ``data``) field from the response envelope.

    Raises:
        V2ApiError: On any API-level or HTTP-level error.
        requests.RequestException: On network errors.
    """
    url = f"{base_url.rstrip('/')}/api/v2/{service}?Action={action}"

    headers = {**V2_HEADERS, "Authorization": f"Bearer {token}"}

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException:
        raise
    except Exception as e:
        raise V2ApiError(f"Request failed: {e}") from e

    # Handle auth failures
    if resp.status_code in (401, 302):
        raise V2ApiError(
            "Authentication expired or invalid. Run `inspire login` to re-authenticate.",
            http_status=resp.status_code,
        )

    # Handle forbidden
    if resp.status_code == 403:
        try:
            err_data = resp.json()
            msg = err_data.get("message", "Access forbidden")
        except Exception:
            msg = f"Access forbidden (HTTP 403)"
        raise V2ApiError(msg, http_status=403)

    # Parse response
    try:
        data = resp.json()
    except ValueError:
        raise V2ApiError(
            f"API returned non-JSON response (HTTP {resp.status_code})",
            http_status=resp.status_code,
        )

    # Check for error metadata
    metadata = data.get("ResponseMetadata")
    if metadata and metadata.get("Error"):
        err = metadata["Error"]
        raise V2ApiError(
            err.get("Message", err.get("Code", "Unknown API error")),
            code=err.get("Code"),
        )

    # Standard v2 envelope: Result or data
    if "Result" in data:
        return data["Result"]
    if "data" in data:
        return data["data"]

    return data
