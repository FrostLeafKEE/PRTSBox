"""Build and verify a per-user Windows installer from a validated portable zip.

First run ``scripts/package.py`` to produce the zip.  The installer contains
the same executable and resources, with optional shortcuts and an uninstaller.
Requires Inno Setup 7 (ISCC.exe); set INNO_SETUP_ISCC for a custom location.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import winreg
import zipfile
from pathlib import Path, PurePosixPath


PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from prtsbox import __version__

DIST = PROJECT / "dist"
SCRIPT = PROJECT / "installer" / "PRTSBox.iss"
ARCHIVE_NAME = f"PRTSBox-v{__version__}-win64.zip"
APP_ID = "{237da98b-a1ec-4e05-b629-f302e6efda87}_is1"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"


def _compiler() -> Path:
    candidates = [
        os.environ.get("INNO_SETUP_ISCC", ""),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 7" / "ISCC.exe"),
        str(Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 7" / "ISCC.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit("Inno Setup 7 compiler (ISCC.exe) was not found")


def _archive(path: Path | None) -> tuple[Path, str]:
    if path is None:
        path = DIST / ARCHIVE_NAME
        if not path.is_file():
            raise SystemExit("No portable zip found; run scripts/package.py first")
    path = path.resolve(strict=True)
    if path.name != ARCHIVE_NAME:
        raise SystemExit(f"Unexpected portable zip name: {path.name}")
    return path, __version__


def _extract_payload(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        names = {info.filename for info in bundle.infolist() if not info.is_dir()}
        if not {"PRTSBox.exe", "README.md", "README.zh-CN.md", "使用说明.txt"}.issubset(names):
            raise SystemExit("Portable zip is missing its executable or instructions")
        if not any(name.startswith("_internal/") for name in names):
            raise SystemExit("Portable zip is missing its bundled runtime files")
        for info in bundle.infolist():
            member = PurePosixPath(info.filename)
            if (member.is_absolute() or ".." in member.parts
                    or "\\" in info.filename or ":" in member.parts[0]
                    or member.parts[0] == "data"):
                raise SystemExit(f"Unsafe or unexpected zip member: {info.filename}")
        bundle.extractall(destination)


def _run(command: list[str], *, timeout: int = 300, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        command, cwd=PROJECT, env=env, capture_output=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Exit {result.returncode}: {command[0]}\n"
            f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
        )


def _has_install_entry() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY):
            return True
    except FileNotFoundError:
        return False


def _verify(installer: Path, scratch: Path) -> None:
    # Do not let a verification install replace a user's real installed copy.
    if _has_install_entry():
        print("Skipped install/uninstall QA: PRTSBox is already installed for this user")
        return

    install_dir = (scratch / "qa-install").resolve()
    if not install_dir.is_relative_to(scratch.resolve()):
        raise RuntimeError("QA install path escaped its temporary directory")
    print("Checking silent installation without shortcuts...")
    _run([
        str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
        f"/DIR={install_dir}", "/MERGETASKS=!desktopicon,!startmenuicon",
    ], timeout=300)
    exe = install_dir / "PRTSBox.exe"
    uninstaller = install_dir / "unins000.exe"
    if not exe.is_file() or not uninstaller.is_file() or not _has_install_entry():
        raise RuntimeError("Installer did not create the application and uninstall entry")

    environment = {**os.environ, "PRTSBOX_SMOKE_TEST": "1", "QT_QPA_PLATFORM": "offscreen",
                   "PRTSBOX_DATA_DIR": str(scratch / "qa-data")}
    _run([str(exe)], timeout=90, env=environment)

    # Silent uninstall must preserve user-generated data.  Interactive removal
    # offers a separate, default-No choice for deleting it.
    sentinel = install_dir / "data" / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep", encoding="utf-8")

    print("Checking silent uninstallation...")
    _run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=180)
    for _ in range(40):
        if not exe.exists() and not _has_install_entry():
            break
        time.sleep(0.25)
    if exe.exists() or _has_install_entry():
        raise RuntimeError("Uninstaller left the application or uninstall entry behind")
    if not sentinel.is_file():
        raise RuntimeError("Silent uninstall unexpectedly deleted user data")
    print("Installation, GUI launch and uninstallation passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, help="validated portable zip to wrap")
    parser.add_argument("--skip-verify", action="store_true", help="compile without install QA")
    args = parser.parse_args()

    archive, version = _archive(args.archive)
    compiler = _compiler()
    DIST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prtsbox-installer-", dir=DIST) as temporary:
        scratch = Path(temporary).resolve()
        if not scratch.is_relative_to(DIST.resolve()):
            raise RuntimeError("Temporary directory escaped dist")
        payload = scratch / "payload"
        payload.mkdir()
        _extract_payload(archive, payload)
        print(f"Building installer from {archive.name}...")
        _run([
            str(compiler), f"/DSourceRoot={payload}", f"/DReleaseVersion={version}",
            f"/DOutputRoot={DIST.resolve()}", str(SCRIPT),
        ], timeout=300)
        installer = DIST / f"PRTSBox-Setup-v{version}-win64.exe"
        if not installer.is_file() or installer.stat().st_size < 1_000_000:
            raise RuntimeError("Installer output is missing or unexpectedly small")
        if not args.skip_verify:
            _verify(installer, scratch)
    print(f"Ready: {installer} ({installer.stat().st_size / (1024 ** 2):.1f} MiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
