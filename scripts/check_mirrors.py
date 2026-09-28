"""Verify every download source the application can use.

Downloads are pinned by size and SHA-256, so a mirror is only usable if it
serves byte-identical files.  A mirror that looks right in a browser but hands
back a re-packed archive would fail verification at the very end of a multi
gigabyte transfer, which is the worst possible place to find out.

    .venv\\Scripts\\python.exe scripts\\check_mirrors.py            # sizes only
    .venv\\Scripts\\python.exe scripts\\check_mirrors.py --hashes   # full verify
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prtsbox import llama

# ModelScope is the domestic source: a different host from HuggingFace and its
# mirror, which matters when neither is reachable.  Tencent publishes Hy-MT2
# there under its own organisation name.
MODELSCOPE_ORGS = ("Tencent-Hunyuan", "tencent")
MODELSCOPE_BASE = "https://modelscope.cn/models"
MODELSCOPE_REVISION = "master"


def modelscope_sources(repo: str, filename: str) -> list[str]:
    _, _, name = repo.partition("/")
    return [
        f"{MODELSCOPE_BASE}/{org}/{name}/resolve/{MODELSCOPE_REVISION}/{filename}"
        for org in MODELSCOPE_ORGS
    ]


def probe_size(url: str, timeout: float = 25.0) -> tuple[bool, int, str]:
    """Ask a URL for the file size without downloading it.

    A one-byte ranged request is used rather than HEAD: several of these hosts
    answer HEAD with no Content-Length, and some redirect to object storage
    that only reports the total in Content-Range.
    """
    try:
        response = requests.get(
            url, headers={"Range": "bytes=0-0"}, stream=True, timeout=timeout, allow_redirects=True
        )
        with response:
            if response.status_code not in (200, 206):
                return False, 0, f"HTTP {response.status_code}"
            content_range = response.headers.get("Content-Range", "")
            if "/" in content_range:
                total = content_range.rsplit("/", 1)[-1]
                if total.isdigit():
                    return True, int(total), f"HTTP {response.status_code}"
            length = response.headers.get("Content-Length", "")
            if length.isdigit():
                return True, int(length), f"HTTP {response.status_code}"
            # No size advertised; read the first chunk to confirm it is served.
            next(response.iter_content(1), None)
            return True, 0, f"HTTP {response.status_code}（未报告大小）"
    except requests.RequestException as exc:
        return False, 0, str(exc)[:80]


def hash_url(url: str, timeout: float = 60.0) -> tuple[bool, str, str]:
    digest = hashlib.sha256()
    try:
        response = requests.get(url, stream=True, timeout=timeout, allow_redirects=True)
        with response:
            response.raise_for_status()
            for chunk in response.iter_content(1024 * 512):
                digest.update(chunk)
        return True, digest.hexdigest(), ""
    except requests.RequestException as exc:
        return False, "", str(exc)[:80]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hashes", action="store_true", help="download fully and compare SHA-256")
    parser.add_argument("--model", default="", help="only this model id")
    parser.add_argument(
        "--only",
        default="",
        help="only sources whose label contains this text, e.g. ModelScope",
    )
    args = parser.parse_args()

    models = [m for m in llama.MODELS if not args.model or m.id == args.model]
    failures = 0

    for model in models:
        print(f"\n=== {model.name}  {model.filename}")
        print(f"    期望大小 {model.size_bytes:,} 字节")
        print(f"    期望 SHA256 {model.sha256[:16]}…")

        candidates = [("HF 官方", url) for url in model.sources()[:1]]
        candidates.append(("HF 镜像", model.sources()[1]))
        candidates += [
            (f"ModelScope ({org})", url)
            for org, url in zip(MODELSCOPE_ORGS, modelscope_sources(model.repo, model.filename))
        ]

        for label, url in candidates:
            if args.only and args.only.casefold() not in label.casefold():
                continue
            started = time.perf_counter()
            ok, size, detail = probe_size(url)
            elapsed = time.perf_counter() - started
            if not ok:
                failures += 1
                print(f"    {label:<24} 不可用：{detail}")
                continue

            matches = size == model.size_bytes
            note = "大小一致" if matches else f"大小不符（差 {size - model.size_bytes:+,}）"
            if size == 0:
                note = "无法确认大小"
            print(f"    {label:<24} 可用　{size:,} 字节　{note}　{elapsed:.1f}s")

            if args.hashes and matches:
                started = time.perf_counter()
                ok_h, digest, error = hash_url(url)
                took = time.perf_counter() - started
                if not ok_h:
                    print(f"        → 下载失败：{error}")
                    failures += 1
                elif digest == model.sha256:
                    print(f"        → SHA256 一致，{took:.0f}s")
                else:
                    print(f"        → SHA256 不一致：{digest[:16]}…，{took:.0f}s")
                    failures += 1

    print()
    print("=" * 56)
    print("全部源可用且一致" if not failures else f"发现 {failures} 处问题")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
