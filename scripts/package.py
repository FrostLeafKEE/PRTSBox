"""Build the distributable zip: PyInstaller run, docs, archive, verification.

Doing these as separate manual steps is how the user-facing README twice failed
to make it into the archive - the build cleans ``dist/`` and the file that was
placed there by hand disappears.  This does it in one pass and then checks the
archive by unpacking it somewhere else and running the packaged self-test, so a
broken build cannot be handed to anyone.

    .venv\\Scripts\\python.exe scripts\\package.py
    .venv\\Scripts\\python.exe scripts\\package.py --skip-build
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist"
APP_DIR = DIST / "PRTSBox"

# Files copied next to the executable, from the repository.
BUNDLED_DOCS = ("使用说明.txt",)


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(command)}")
    # PyInstaller writes its log in the console's encoding (GBK on a Chinese
    # Windows), not UTF-8, so decoding must not assume UTF-8.
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return subprocess.run(command, cwd=PROJECT, check=False, **kwargs)


def build() -> None:
    # Dependency discovery must not pick same-named DLLs from unrelated tools
    # on PATH (e.g. Poppler's ICU instead of the Windows ICU used by Qt).
    windows = Path(os.environ["SystemRoot"])
    environment = {**os.environ, "PATH": os.pathsep.join([
        str(Path(sys.executable).parent), sys.base_prefix,
        str(Path(sys.base_prefix) / "DLLs"), str(windows / "System32"), str(windows),
    ])}
    result = run(
        [sys.executable, "-m", "PyInstaller", "prtsbox.spec", "--noconfirm", "--clean", "--log-level=ERROR"],
        env=environment,
        capture_output=True,
        encoding='utf-8',
        errors='replace',
    )
    if result.returncode != 0:
        print(result.stdout[-4000:])
        print(result.stderr[-4000:])
        raise SystemExit("PyInstaller 构建失败")
    if not (APP_DIR / "PRTSBox.exe").is_file():
        raise SystemExit(f"构建产物缺失：{APP_DIR / 'PRTSBox.exe'}")


def stage_docs() -> None:
    for name in BUNDLED_DOCS:
        source = PROJECT / name
        if not source.is_file():
            raise SystemExit(f"缺少要打包的文件：{source}")
        shutil.copy2(source, APP_DIR / name)
        print(f"已放入 {name}")


def prune_data() -> None:
    """Drop anything the running build created.

    ``data/`` holds the downloaded models and runtime; shipping it would make
    the archive gigabytes and defeat the point of the in-app downloader.
    """
    data = APP_DIR / "data"
    if data.exists():
        shutil.rmtree(data, ignore_errors=True)
        print("已移除 data/（模型与运行时由使用者自行下载）")


def make_zip() -> Path:
    stamp = subprocess.run(
        [sys.executable, "-c", "import datetime;print(datetime.date.today().strftime('%Y%m%d'))"],
        capture_output=True,
        encoding='utf-8',
        errors='replace',
        check=True,
    ).stdout.strip()
    target = DIST / f"PRTSBox-{stamp}-win64.zip"
    target.unlink(missing_ok=True)

    print(f"正在压缩 → {target.name}")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(APP_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(APP_DIR))
    return target


def verify(archive: Path) -> bool:
    """Unpack the archive elsewhere and run the packaged self-test.

    Verifying the archive rather than the build directory is deliberate: it
    catches a file that was staged after archiving, and it proves the build runs
    from a path with no source tree behind it.
    """
    print(f"\n正在验证 {archive.name}")
    with tempfile.TemporaryDirectory(prefix="prtsbox-verify-") as workspace:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(workspace)

        root = Path(workspace)
        names = sorted(p.name for p in root.iterdir())
        print(f"  解压内容：{', '.join(names)}")
        for name in BUNDLED_DOCS:
            if name not in names:
                print(f"  <-- 缺少 {name}")
                return False

        environment = {**os.environ, "PRTSBOX_SELFTEST": "1",
                       "PRTSBOX_DATA_DIR": str(root / "data")}
        result = subprocess.run(
            [str(root / "PRTSBox.exe")],
            cwd=root,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )

        log = root / "data" / "logs" / "prtsbox.log"
        if log.is_file():
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-8:]:
                print(f"  {line}")
        if result.returncode != 0:
            print(f"  自检退出码 {result.returncode}")
            crash = root / "data" / "logs" / "crash.log"
            if crash.is_file():
                print(crash.read_text(encoding="utf-8", errors="replace")[-2000:])
            return False
        # OCR self-test does not import the UI. Check the extracted GUI too,
        # otherwise missing Qt DLLs can slip through a successful self-test.
        environment.pop("PRTSBOX_SELFTEST", None)
        environment.update(PRTSBOX_SMOKE_TEST="1", QT_QPA_PLATFORM="offscreen")
        result = subprocess.run(
            [str(root / "PRTSBox.exe")], cwd=root, env=environment,
            capture_output=True, timeout=60, check=False,
        )
        if result.returncode != 0:
            print(f"  界面启动检查失败：{result.returncode}")
            crash = root / "data" / "logs" / "crash.log"
            if crash.is_file():
                print(crash.read_text(encoding="utf-8", errors="replace")[-2000:])
            return False
        print("  界面启动与退出检查通过")
    print("  自检通过")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true", help="reuse the existing dist folder")
    args = parser.parse_args()

    if not args.skip_build:
        build()
    stage_docs()
    prune_data()

    size_mb = sum(p.stat().st_size for p in APP_DIR.rglob("*") if p.is_file()) / (1024 * 1024)
    print(f"待打包体积 {size_mb:.1f} MB")

    archive = make_zip()
    size = archive.stat().st_size / (1024 * 1024)
    print(f"压缩包 {archive.name}　{size:.1f} MB")

    if not verify(archive):
        return 1
    print(f"\n完成：{archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
