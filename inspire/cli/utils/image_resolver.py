"""Image name resolution for job/run commands.

Resolves short image names (like "dev-wjx:v-base" or "pytorch:25.06-py3")
to the full URL and image_type required by the job creation API.

Resolution priority (when image_type is not specified):
  1. personal-visible (your own custom images)
  2. official (platform images)
  3. public (community images)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web.session import WebSession, get_web_session


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedImage:
    """Result of image name resolution."""

    url: str        # Full image URL for the API
    image_type: str  # API image_type (e.g. "SOURCE_PERSONAL_VISIBLE")
    name: str       # Display name
    source: str     # Raw source from API (e.g. "SOURCE_PRIVATE")


class ImageNotFoundError(Exception):
    """Raised when an image cannot be resolved."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Priority order for multi-source search.
_DEFAULT_SOURCE_ORDER = ("personal-visible", "official", "public")

# Map from CLI source key to the image_type value for the job API.
_SOURCE_TO_IMAGE_TYPE = {
    "official": "SOURCE_OFFICIAL",
    "public": "SOURCE_PUBLIC",
    "personal-visible": "SOURCE_PERSONAL_VISIBLE",
}

# Map from API-returned source field to image_type.
_API_SOURCE_TO_IMAGE_TYPE = {
    "SOURCE_OFFICIAL": "SOURCE_OFFICIAL",
    "SOURCE_PUBLIC": "SOURCE_PUBLIC",
    "SOURCE_PRIVATE": "SOURCE_PERSONAL_VISIBLE",
    "SOURCE_PERSONAL_VISIBLE": "SOURCE_PERSONAL_VISIBLE",
}

# Reverse map: image_type → source key for list_images_by_source.
_IMAGE_TYPE_TO_SOURCE_KEY = {
    "SOURCE_OFFICIAL": "official",
    "SOURCE_PUBLIC": "public",
    "SOURCE_PERSONAL_VISIBLE": "personal-visible",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_full_url(image: str) -> bool:
    """Heuristic: if the image string contains '/', treat it as a full URL."""
    return "/" in image


def _match_quality(query: str, img: browser_api_module.CustomImageInfo) -> int:
    """Return match quality: 2=exact, 1=substring, 0=no match."""
    q = query.lower()
    if q == img.name.lower() or q == img.url.lower() or img.image_id == query:
        return 2
    if q in img.name.lower() or q in img.url.lower():
        return 1
    return 0


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def resolve_image(
    image: str,
    *,
    image_type: Optional[str] = None,
    session: Optional[WebSession] = None,
) -> ResolvedImage:
    """Resolve an image identifier to a full URL and image_type.

    Resolution logic:
      1. If image_type is explicitly provided AND the image looks like a full URL,
         pass through (backward compatibility, no API call).
      2. If image_type is provided, search only in that source.
      3. If image_type is not provided, search sources in priority order.

    Args:
        image: Image name, short name, or full URL.
        image_type: Optional image_type override (e.g. "SOURCE_OFFICIAL").
        session: Existing web session.

    Returns:
        ResolvedImage with url, image_type, name, source.

    Raises:
        ImageNotFoundError: No matching image found.
    """
    if session is None:
        session = get_web_session()

    # Backward compat: explicit image_type + full URL => pass through
    if image_type and _looks_like_full_url(image):
        short = image.rsplit("/", 1)[-1]
        return ResolvedImage(url=image, image_type=image_type, name=short, source=image_type)

    # Determine which sources to search
    if image_type:
        source_keys = [_IMAGE_TYPE_TO_SOURCE_KEY.get(image_type, image_type.lower())]
    else:
        source_keys = list(_DEFAULT_SOURCE_ORDER)

    # Search each source, collecting best matches
    best: Optional[tuple[int, str, browser_api_module.CustomImageInfo]] = None  # (quality, src_key, img)

    for src_key in source_keys:
        try:
            images = browser_api_module.list_images_by_source(source=src_key, session=session)
        except Exception:
            continue

        for img in images:
            quality = _match_quality(image, img)
            if quality == 0:
                continue
            if best is None or quality > best[0]:
                best = (quality, src_key, img)
            if quality == 2:
                break  # exact match found in this source, no need to check more

        if best is not None and best[0] == 2:
            break  # exact match found, stop searching other sources

    if best is None:
        raise ImageNotFoundError(
            f"Image '{image}' not found. "
            f"Run 'inspire image list' to see available images."
        )

    _, src_key, img = best
    api_image_type = _API_SOURCE_TO_IMAGE_TYPE.get(img.source, _SOURCE_TO_IMAGE_TYPE.get(src_key, src_key))

    return ResolvedImage(
        url=img.url,
        image_type=api_image_type,
        name=img.name,
        source=img.source,
    )


__all__ = ["ImageNotFoundError", "ResolvedImage", "resolve_image"]
