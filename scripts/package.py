"""Build the distributable zip: PyInstaller run, docs, archive, verification.

Doing these as separate manual steps is how the user-facing README twice failed
to make it into the archive - the build cleans ``dist/`` and the file that was
placed there by hand disappears.  This does it in one pass and then checks the
archive by unpacking it somewhere else and running the packaged self-test, so a
broken build cannot be handed to anyone.  Build in a temporary directory so a
local ``dist/PRTSBox/data`` (settings, models and runtime) is never replaced.

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


def build(stage_parent: Path) -> Path:
    # Dependency discovery must not pick same-named DLLs from unrelated tools
    # on PATH (e.g. Poppler's ICU instead of the Windows ICU used by Qt).
    windows = Path(os.environ["SystemRoot"])
    environment = {**os.environ, "PATH": os.pathsep.join([
        str(Path(sys.executable).parent), sys.base_prefix,
        str(Path(sys.base_prefix) / "DLLs"), str(windows / "System32"), str(windows),
    ])}
    result = run(
        [sys.executable, "-m", "PyInstaller", "prtsbox.spec", "--noconfirm", "--clean",
         "--log-level=ERROR", "--distpath", str(stage_parent)],
        env=environment,
        capture_output=True,
        encoding='utf-8',
        errors='replace',
    )
    if result.returncode != 0:
        print(result.stdout[-4000:])
        print(result.stderr[-4000:])
        raise SystemExit("PyInstaller 构建失败")
    app_dir = stage_parent / "PRTSBox"
    if not (app_dir / "PRTSBox.exe").is_file():
        raise SystemExit(f"构建产物缺失：{app_dir / 'PRTSBox.exe'}")
    return app_dir


def stage_docs(app_dir: Path) -> None:
    for name in BUNDLED_DOCS:
        source = PROJECT / name
        if not source.is_file():
            raise SystemExit(f"缺少要打包的文件：{source}")
        shutil.copy2(source, app_dir / name)
        print(f"已放入 {name}")


def package_files(app_dir: Path) -> list[Path]:
    """List distributable files without touching local application data."""
    return [path for path in sorted(app_dir.rglob("*"))
            if path.is_file() and path.relative_to(app_dir).parts[0].casefold() != "data"]


def make_zip(app_dir: Path) -> Path:
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
        for path in package_files(app_dir):
            archive.write(path, path.relative_to(app_dir))
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


def package(app_dir: Path) -> int:
    stage_docs(app_dir)
    size_mb = sum(p.stat().st_size for p in package_files(app_dir)) / (1024 * 1024)
    print(f"待打包体积 {size_mb:.1f} MB")

    archive = make_zip(app_dir)
    size = archive.stat().st_size / (1024 * 1024)
    print(f"压缩包 {archive.name}　{size:.1f} MB")

    if not verify(archive):
        return 1
    print(f"\n完成：{archive}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true", help="reuse the existing dist folder")
    args = parser.parse_args()

    if args.skip_build:
        if not (APP_DIR / "PRTSBox.exe").is_file():
            raise SystemExit(f"缺少现有构建：{APP_DIR / 'PRTSBox.exe'}")
        return package(APP_DIR)

    DIST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prtsbox-package-", dir=DIST) as workspace:
        return package(build(Path(workspace)))


if __name__ == "__main__":
    sys.exit(main())
