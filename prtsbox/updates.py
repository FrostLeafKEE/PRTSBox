"""Manual update checks against this project's published GitHub releases."""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from . import __version__

REPOSITORY_URL = "https://github.com/FrostLeafKEE/PRTSBox"
LATEST_RELEASE_URL = f"{REPOSITORY_URL}/releases/latest"
LATEST_RELEASE_API = "https://api.github.com/repos/FrostLeafKEE/PRTSBox/releases/latest"
_VERSION_PATTERN = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?\Z")


@dataclass(frozen=True)
class UpdateResult:
    latest_tag: str
    newer: bool


class ReleaseUnavailableError(RuntimeError):
    """GitHub did not expose a public release for this repository."""


def version_tuple(value: str) -> tuple[int, int, int]:
    """Compare release numbers, never dates or string ordering."""
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"Unsupported release version: {value!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def check_latest_release(current_version: str = __version__) -> UpdateResult:
    """Fetch the latest published, non-prerelease version without downloading it."""
    response = requests.get(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"PRTSBox/{current_version}",
        },
        timeout=8,
    )
    if response.status_code == 404:
        raise ReleaseUnavailableError("Release information is not publicly available")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected GitHub release response")
    tag = payload.get("tag_name")
    if not isinstance(tag, str):
        raise ValueError("GitHub release has no version tag")
    return UpdateResult(tag, version_tuple(tag) > version_tuple(current_version))
