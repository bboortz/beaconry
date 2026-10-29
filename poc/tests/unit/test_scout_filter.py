"""Unit tests for scout glob filter — BC-005.

Covers:
    - CAP-T018: Scout filters image tags by include/exclude glob patterns
"""

from __future__ import annotations

import pytest

from poc.scout import scout as scout_filter


def test_empty_tags_returns_empty_list() -> None:
    assert scout_filter.filter_image_tags([], include_globs=["*"], exclude_globs=[]) == []


def test_wildcard_include_passes_everything() -> None:
    tags = ["nginx:1.24", "redis:7", "busybox:latest"]
    assert scout_filter.filter_image_tags(tags, include_globs=["*"], exclude_globs=[]) == tags


def test_include_narrows_to_matching_tags() -> None:
    tags = ["nginx:1.24", "redis:7", "busybox:latest"]
    result = scout_filter.filter_image_tags(tags, include_globs=["nginx*"], exclude_globs=[])
    assert result == ["nginx:1.24"]


def test_multiple_include_globs_or_together() -> None:
    tags = ["nginx:1.24", "redis:7", "busybox:latest", "postgres:16"]
    result = scout_filter.filter_image_tags(
        tags, include_globs=["nginx*", "redis*", "postgres*"], exclude_globs=[]
    )
    assert set(result) == {"nginx:1.24", "redis:7", "postgres:16"}


def test_exclude_removes_matching_tags() -> None:
    tags = ["nginx:1.24", "nginx:1.24-test", "nginx-dev:latest"]
    result = scout_filter.filter_image_tags(
        tags, include_globs=["*"], exclude_globs=["*-test", "*-dev*"]
    )
    assert result == ["nginx:1.24"]


def test_exclude_overrides_include() -> None:
    """A tag matching both include and exclude is excluded."""
    tags = ["nginx:1.24", "nginx-dev:latest"]
    result = scout_filter.filter_image_tags(
        tags, include_globs=["nginx*"], exclude_globs=["nginx-dev*"]
    )
    assert result == ["nginx:1.24"]


def test_default_demo_safe_allowlist() -> None:
    """BC-005 defaults: nginx*, redis*, postgres* — only these pass."""
    tags = [
        "nginx:1.24",
        "redis:7",
        "postgres:16",
        "busybox:latest",
        "myapp:dev",
        "python:3.12-slim",
    ]
    result = scout_filter.filter_image_tags(
        tags,
        include_globs=["nginx*", "redis*", "postgres*"],
        exclude_globs=[],
    )
    assert set(result) == {"nginx:1.24", "redis:7", "postgres:16"}


def test_tag_without_colon_still_filtered() -> None:
    """Tags like 'nginx' (no :version) still match 'nginx*'."""
    tags = ["nginx", "busybox"]
    result = scout_filter.filter_image_tags(tags, include_globs=["nginx*"], exclude_globs=[])
    assert result == ["nginx"]


@pytest.mark.parametrize(
    ("globs_env", "expected"),
    [
        ("", []),
        ("nginx*", ["nginx*"]),
        ("nginx*,redis*,postgres*", ["nginx*", "redis*", "postgres*"]),
        ("  nginx*  ,  redis*  ", ["nginx*", "redis*"]),  # whitespace tolerated
    ],
)
def test_parse_globs_env(globs_env: str, expected: list[str]) -> None:
    assert scout_filter.parse_globs(globs_env) == expected
