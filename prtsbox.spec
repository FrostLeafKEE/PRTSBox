# PyInstaller spec for a distributable PRTSBox build.
#
#   .venv\Scripts\python.exe -m PyInstaller prtsbox.spec --noconfirm
#
# Output: dist/PRTSBox/ (a folder, not a single file - see below).
#
# What is deliberately NOT bundled:
#
# * ``data/`` - the downloaded GGUF models and the llama.cpp runtime are fetched
#   on demand by the application's own downloader.  The 1.8B model alone is
#   1.06 GB and the 7B is 4.31 GB, which would make the archive impossible to
#   pass around.  The app creates ``data/`` next to the executable on first run.
# * ``openvino`` - 242 MB, and not the default OCR backend because its
#   recognition path grows without bound on changing input widths.  The settings
#   dialog only offers it when it is importable, so nothing is lost.
# * Qt modules this application never touches (Qml, Quick, Network, Sql, ...),
#   which is most of PySide6's 200 MB.

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT = Path(SPECPATH).resolve()

# The OCR wheel ships its own detection and recognition ONNX models; without
# these the packaged build starts and then fails at the first frame.
datas = collect_data_files("onnxocr")

# Stylesheet icons.  Loaded through absolute paths built from __file__, so they
# have to land in the same relative location inside the bundle.
datas += [
    (str(PROJECT / "prtsbox" / "ui" / "assets"), "prtsbox/ui/assets"),
]

hiddenimports = [
    # onnxocr imports its predictor modules dynamically.
    *collect_submodules("onnxocr"),
    # Pulled in by onnxruntime/opencv at runtime rather than by an import
    # statement PyInstaller can see.
    "onnxruntime",
    "onnxruntime.capi._pybind_state",
    "cv2",
    "shapely",
    "pyclipper",
]

excludes = [
    # Large and unused; see the module docstring.
    "openvino",
    "openvino_telemetry",
    # Qt modules this UI does not use.  Svg is kept: the stylesheet loads SVG
    # icons through Qt's image plugins.
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDBus",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPrintSupport",
    "PySide6.QtSerialPort",
    "PySide6.QtWebSockets",
    "PySide6.QtXml",
    "PySide6.QtUiTools",
    "PySide6.QtStateMachine",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtPositioning",
    "PySide6.QtBluetooth",
    # Not used, and each drags in tens of megabytes.
    "tkinter",
    "matplotlib",
    "scipy",
    "pandas",
    "IPython",
    "pytest",
    "PIL.ImageQt",
]

a = Analysis(
    ["run.py"],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PRTSBox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # No console: this is a desktop application, and it writes its log to
    # data/logs.  A failure before the UI exists is reported by the handler in
    # run.py, which shows a dialog and writes data/logs/crash.log.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT / "prtsbox" / "ui" / "assets" / "app.ico")
    if (PROJECT / "prtsbox" / "ui" / "assets" / "app.ico").is_file()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    # A folder rather than one file: a one-file build unpacks ~500 MB to a temp
    # directory on every launch, which adds ten seconds of start-up and gives
    # native libraries like onnxruntime a path they sometimes mishandle.
    upx_exclude=[],
    name="PRTSBox",
)
