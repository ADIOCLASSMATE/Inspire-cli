"""v2 API layer for Inspire platform (/api/v2/*).

Uses Bearer token authentication (Keycloak) instead of the v1 browser
cookie session. All v2 endpoints follow the pattern:

    POST /api/v2/{service}?Action={action}

with JSON body and ``Authorization: Bearer <token>`` header.
"""

from inspire.platform.web.v2_api.auth import (
    V2TokenSet,
    clear_token,
    ensure_token,
    get_token,
)
from inspire.platform.web.v2_api.client import (
    V2ApiError,
    post_v2,
)
from inspire.platform.web.v2_api.models import (
    V2InferenceInfo,
    V2JobInfo,
    IdleWindow,
    MetricSummary,
    MetricTimeSeries,
    StatusFamily,
)

__all__ = [
    "IdleWindow",
    "MetricSummary",
    "MetricTimeSeries",
    "StatusFamily",
    "V2ApiError",
    "V2InferenceInfo",
    "V2JobInfo",
    "V2TokenSet",
    "clear_token",
    "ensure_token",
    "get_token",
    "post_v2",
]
