"""Tests for the image name resolution module."""

from __future__ import annotations

import pytest

from inspire.cli.utils.image_resolver import (
    ImageNotFoundError,
    ResolvedImage,
    _looks_like_full_url,
    _match_quality,
    resolve_image,
)
from inspire.platform.web.browser_api.images import CustomImageInfo


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _img(
    image_id: str = "img-1",
    name: str = "test-image",
    url: str = "registry.example.com/ns/test-image:v1",
    source: str = "SOURCE_PRIVATE",
    framework: str = "pytorch",
    version: str = "1.0",
    status: str = "READY",
    description: str = "",
    created_at: str = "",
) -> CustomImageInfo:
    return CustomImageInfo(
        image_id=image_id,
        url=url,
        name=name,
        framework=framework,
        version=version,
        source=source,
        status=status,
        description=description,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# _looks_like_full_url
# ---------------------------------------------------------------------------


def test_short_name_is_not_url() -> None:
    assert _looks_like_full_url("pytorch:25.06-py3") is False


def test_short_name_without_tag() -> None:
    assert _looks_like_full_url("dev-wjx") is False


def test_full_url_is_detected() -> None:
    assert _looks_like_full_url("docker.sii.shaipower.online/inspire-studio/dev-wjx:v1.1") is True


def test_registry_path_is_detected() -> None:
    assert _looks_like_full_url("registry/pytorch:latest") is True


# ---------------------------------------------------------------------------
# _match_quality
# ---------------------------------------------------------------------------


def test_exact_name_match() -> None:
    img = _img(name="dev-wjx:v-base")
    assert _match_quality("dev-wjx:v-base", img) == 2


def test_exact_url_match() -> None:
    img = _img(url="registry.example.com/dev-wjx:v-base")
    assert _match_quality("registry.example.com/dev-wjx:v-base", img) == 2


def test_exact_id_match() -> None:
    img = _img(image_id="img-123")
    assert _match_quality("img-123", img) == 2


def test_substring_name_match() -> None:
    img = _img(name="dev-wjx:v-base")
    assert _match_quality("dev-wjx", img) == 1


def test_no_match() -> None:
    img = _img(name="dev-wjx:v-base")
    assert _match_quality("something-else", img) == 0


def test_case_insensitive() -> None:
    img = _img(name="PyTorch:25.06")
    assert _match_quality("pytorch:25.06", img) == 2


# ---------------------------------------------------------------------------
# resolve_image — with monkeypatched API
# ---------------------------------------------------------------------------


def _setup_mock(monkeypatch, source_to_images: dict[str, list[CustomImageInfo]]):
    def fake_list(source, session=None):
        return source_to_images.get(source, [])
    monkeypatch.setattr(
        "inspire.cli.utils.image_resolver.browser_api_module.list_images_by_source",
        fake_list,
    )
    monkeypatch.setattr(
        "inspire.cli.utils.image_resolver.get_web_session",
        lambda: None,
    )


def test_resolve_short_name_personal(monkeypatch) -> None:
    images = {
        "personal-visible": [
            _img(name="dev-wjx:v-base", url="registry.example.com/dev-wjx:v-base",
                 source="SOURCE_PRIVATE"),
        ],
    }
    _setup_mock(monkeypatch, images)

    result = resolve_image("dev-wjx:v-base")
    assert result.url == "registry.example.com/dev-wjx:v-base"
    assert result.image_type == "SOURCE_PERSONAL_VISIBLE"
    assert result.name == "dev-wjx:v-base"


def test_resolve_falls_through_to_official(monkeypatch) -> None:
    images = {
        "personal-visible": [],
        "official": [
            _img(name="pytorch:25.06-py3", url="registry.example.com/official/pytorch:25.06-py3",
                 source="SOURCE_OFFICIAL"),
        ],
    }
    _setup_mock(monkeypatch, images)

    result = resolve_image("pytorch:25.06-py3")
    assert result.url == "registry.example.com/official/pytorch:25.06-py3"
    assert result.image_type == "SOURCE_OFFICIAL"


def test_resolve_personal_priority_over_official(monkeypatch) -> None:
    """When same short name exists in personal and official, personal wins."""
    images = {
        "personal-visible": [
            _img(name="pytorch:25.06", url="registry.example.com/custom/pytorch:25.06",
                 source="SOURCE_PRIVATE"),
        ],
        "official": [
            _img(name="pytorch:25.06", url="registry.example.com/official/pytorch:25.06",
                 source="SOURCE_OFFICIAL"),
        ],
    }
    _setup_mock(monkeypatch, images)

    result = resolve_image("pytorch:25.06")
    assert result.image_type == "SOURCE_PERSONAL_VISIBLE"
    assert "custom" in result.url


def test_resolve_full_url_passthrough(monkeypatch) -> None:
    """Full URL + explicit image_type => no API call."""
    _setup_mock(monkeypatch, {})  # empty, should never be called

    result = resolve_image(
        "docker.sii.shaipower.online/inspire-studio/dev-wjx:v1.1",
        image_type="SOURCE_PERSONAL_VISIBLE",
    )
    assert result.url == "docker.sii.shaipower.online/inspire-studio/dev-wjx:v1.1"
    assert result.image_type == "SOURCE_PERSONAL_VISIBLE"


def test_resolve_full_url_without_type_searches(monkeypatch) -> None:
    """Full URL without image_type => search to determine type."""
    images = {
        "personal-visible": [
            _img(name="dev-wjx:v1.1", url="docker.sii.shaipower.online/ns/dev-wjx:v1.1",
                 source="SOURCE_PRIVATE"),
        ],
    }
    _setup_mock(monkeypatch, images)

    result = resolve_image("docker.sii.shaipower.online/ns/dev-wjx:v1.1")
    assert result.image_type == "SOURCE_PERSONAL_VISIBLE"


def test_resolve_explicit_type_restricts_search(monkeypatch) -> None:
    """With explicit image_type, only search that source."""
    images = {
        "official": [
            _img(name="pytorch:25.06", url="registry.example.com/official/pytorch:25.06",
                 source="SOURCE_OFFICIAL"),
        ],
        "personal-visible": [
            _img(name="pytorch:25.06", url="registry.example.com/custom/pytorch:25.06",
                 source="SOURCE_PRIVATE"),
        ],
    }
    _setup_mock(monkeypatch, images)

    result = resolve_image("pytorch:25.06", image_type="SOURCE_OFFICIAL")
    assert result.image_type == "SOURCE_OFFICIAL"
    assert "official" in result.url


def test_resolve_not_found_raises(monkeypatch) -> None:
    _setup_mock(monkeypatch, {"personal-visible": [], "official": [], "public": []})

    with pytest.raises(ImageNotFoundError, match="not found"):
        resolve_image("nonexistent:image")


def test_resolve_substring_match_when_no_exact(monkeypatch) -> None:
    images = {
        "personal-visible": [
            _img(name="dev-wjx-v-base", url="registry.example.com/dev-wjx-v-base",
                 source="SOURCE_PRIVATE"),
        ],
    }
    _setup_mock(monkeypatch, images)

    result = resolve_image("dev-wjx")
    assert result.name == "dev-wjx-v-base"


def test_resolve_exact_match_preferred_over_substring(monkeypatch) -> None:
    images = {
        "personal-visible": [
            _img(name="dev-wjx", url="registry.example.com/dev-wjx",
                 source="SOURCE_PRIVATE"),
            _img(name="dev-wjx-experiment", url="registry.example.com/dev-wjx-experiment",
                 source="SOURCE_PRIVATE"),
        ],
    }
    _setup_mock(monkeypatch, images)

    result = resolve_image("dev-wjx")
    assert result.name == "dev-wjx"
    assert result.url == "registry.example.com/dev-wjx"
