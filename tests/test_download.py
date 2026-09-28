import hashlib
from types import SimpleNamespace

import pytest
import requests

from prtsbox.llama import download


def test_resume_counts_existing_bytes_only_once(tmp_path, monkeypatch):
    target = tmp_path / "model.gguf"
    partial = target.with_suffix(".gguf.part")
    partial.write_bytes(b"abc")

    class Response:
        headers = {"Content-Length": "3"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield b"def"

    monkeypatch.setattr(download, "_open_stream", lambda *args: (Response(), True))
    progress = []
    download.download_file(["https://example.invalid/model"], target,
                           expected_size=6, expected_sha256=hashlib.sha256(b"abcdef").hexdigest(),
                           on_progress=progress.append)
    assert target.read_bytes() == b"abcdef"
    assert all(p.downloaded <= 6 for p in progress)


def test_completed_partial_is_verified_without_redownloading(tmp_path, monkeypatch):
    target = tmp_path / "model.gguf"
    target.with_suffix(".gguf.part").write_bytes(b"complete")

    def unexpected_request(*args, **kwargs):
        raise AssertionError("A complete verified partial must not request an EOF range")

    monkeypatch.setattr(download, "_open_stream", unexpected_request)
    download.download_file(["https://example.invalid/model"], target,
                           expected_size=8, expected_sha256=hashlib.sha256(b"complete").hexdigest())
    assert target.read_bytes() == b"complete"


def test_incorrect_resume_offset_is_rejected_and_response_closed(monkeypatch):
    closed = []
    response = SimpleNamespace(status_code=206, headers={"Content-Range": "bytes 0-5/6"},
                               raise_for_status=lambda: None, close=lambda: closed.append(True))
    monkeypatch.setattr(download.requests, "get", lambda *args, **kwargs: response)
    with pytest.raises(download.DownloadError):
        download._open_stream("https://example.invalid/model", 3, 1)
    assert closed


def test_eof_range_restarts_unknown_size_download(monkeypatch):
    calls, closed = [], []

    def failed():
        raise requests.HTTPError("416")

    first = SimpleNamespace(status_code=416, raise_for_status=failed, close=lambda: closed.append(True))
    second = SimpleNamespace(status_code=200, headers={}, raise_for_status=lambda: None)

    def get(*args, **kwargs):
        calls.append(kwargs["headers"])
        return first if len(calls) == 1 else second

    monkeypatch.setattr(download.requests, "get", get)
    result, resumed = download._open_stream("https://example.invalid/runtime", 6, 1)
    assert result is second and not resumed
    assert "Range" not in calls[1]
    assert closed
