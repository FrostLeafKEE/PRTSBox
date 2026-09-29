"""Version checks must be numeric and must never install anything."""

from __future__ import annotations

import pytest

from prtsbox import updates


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("v1.00", "v1.01"),
        ("v1.09", "v1.10"),
        ("v1.00", "v1.00.1"),
        ("v1.99", "v2.00"),
    ],
)
def test_release_versions_compare_numerically(older: str, newer: str) -> None:
    assert updates.version_tuple(newer) > updates.version_tuple(older)


def test_latest_release_uses_public_github_api(monkeypatch) -> None:
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"tag_name": "v1.01"}

    def get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        return Response()

    monkeypatch.setattr(updates.requests, "get", get)
    result = updates.check_latest_release("1.00")
    assert result == updates.UpdateResult("v1.01", True)
    assert calls[0][0] == updates.LATEST_RELEASE_API
    assert calls[0][1]["Accept"] == "application/vnd.github+json"
    assert calls[0][2] == 8


@pytest.mark.parametrize("tag", ["v1.00", "v0.99", "v2026.09.28-python", "v1.01-beta"])
def test_latest_release_rejects_unsupported_or_older_tags(monkeypatch, tag: str) -> None:
    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"tag_name": tag}

    monkeypatch.setattr(updates.requests, "get", lambda *_args, **_kwargs: Response())
    if "-" in tag:
        with pytest.raises(ValueError, match="Unsupported release version"):
            updates.check_latest_release("1.00")
    else:
        assert updates.check_latest_release("1.00").newer is False


def test_private_release_has_a_distinct_error(monkeypatch) -> None:
    class Response:
        status_code = 404

    monkeypatch.setattr(updates.requests, "get", lambda *_args, **_kwargs: Response())
    with pytest.raises(updates.ReleaseUnavailableError):
        updates.check_latest_release("1.00")
