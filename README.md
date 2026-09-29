<p align="right">
  <a href="./README.md"><kbd>English ✓</kbd></a>
  <a href="./README.zh-CN.md"><kbd>中文</kbd></a>
</p>

<p align="center"><img src="./prtsbox/ui/assets/app.png" width="96" alt="PRTSBox icon"></p>

# PRTSBox v1.01

**Live translation for a window on Windows.** PRTSBox captures a window, recognizes its text, translates it, and places a click-through translation over the original content. It is designed for games, visual novels, and other apps whose text cannot be selected.

The maintained application is the **Python edition**. The Rust code in this repository is an experimental preview and is not the recommended download.

## Download

| Windows 10/11 · x64 | File | What you get |
|---|---|---|
| Installer | [PRTSBox-Setup-v1.01-win64.exe](https://github.com/FrostLeafKEE/PRTSBox/releases/download/v1.01/PRTSBox-Setup-v1.01-win64.exe) | Per-user installation, optional desktop and Start Menu shortcuts, Windows uninstaller |
| Portable | [PRTSBox-v1.01-win64.zip](https://github.com/FrostLeafKEE/PRTSBox/releases/download/v1.01/PRTSBox-v1.01-win64.zip) | Extract and run `PRTSBox.exe`; no installation |

The installer does not need administrator rights. Both packages contain the application and OCR components, but **not** the large translation models or local inference runtime. Download those from Settings on first use if you choose local translation.

## Get started

1. Install PRTSBox or extract the portable ZIP, then launch `PRTSBox.exe`.
2. If you want offline translation, open **Settings → Local model** and download a runtime (Vulkan is the recommended starting point) and a model (Standard is the smaller choice). You can instead configure an online translation engine.
3. Select the target window, source and target languages, and translation engine.
4. Select **Start live translation**. Press **F8** to toggle translation at any time.

Use the **中 / EN** button next to Settings to change the app language. This setting is saved. The documentation language buttons at the top of this page switch between two complete READMEs; GitHub opens this English file by default.

## What it can do

| Area | Behavior |
|---|---|
| Translation engines | Offline Hy-MT2 local models; OpenAI-compatible model APIs; Azure Translator, DeepL API, and Baidu Translate |
| OCR | Local PP-OCRv5 recognition; ONNX Runtime is the packaged default |
| Window overlay | Translations follow the target window and allow mouse clicks through |
| Region control | Scan the entire window or just its lower 10–100% |
| Stable text | Reuse results for identical frames; confirm small OCR changes and hold translation positions steady |
| Desktop pet | Optional draggable pet with a right-click menu for translation, the main window, and hiding the pet |
| Display | Dark and Preset-inspired themes, adjustable translation placement and text size |
| Capture privacy | Translations are excluded from screenshots and streams by default; this can be changed in the UI |
| Updates | Manual check in **Settings → About**; opens the GitHub release page when a newer stable version is available |

PRTSBox checks for another frame about every **900 ms** while translation is running. A frame still being processed can delay the next check. Capture of minimized, protected, or some exclusive-fullscreen windows depends on whether Windows and the target app make their contents available.

### Translation choices

- **Local model:** Standard Hy-MT2 1.8B or Enhanced Hy-MT2 7B, running through a downloaded llama.cpp runtime. After the initial downloads, translation works offline and needs no API key. The model downloads are approximately 1.06 GB and 4.31 GB respectively.
- **AI model API:** Enter an OpenAI-compatible endpoint, model name, and API key under **Settings → AI Model API**.
- **Translation provider:** Configure **Microsoft Azure Translator**, **DeepL API** (Free or Pro), or **Baidu Translate** under **Settings → Translation provider**, then choose that engine in the main window.

API keys are encrypted locally with Windows DPAPI. Online engines send recognized text to the configured provider; local translation keeps inference on your PC.

## Settings, updates, and removal

PRTSBox stores settings, logs, downloaded models, and the local runtime in a `data/` folder next to `PRTSBox.exe`. For the installer, that normally means `%LOCALAPPDATA%\Programs\PRTSBox\data`; for the portable version, it is inside the extracted folder. The repository and release archives do not include personal data.

The installer can create a desktop shortcut and/or Start Menu shortcuts. Uninstall through **Windows Settings → Installed apps**, or use the Start Menu uninstall shortcut if you selected it. Interactive removal asks whether to delete `data/`; the default is to keep it for a later installation.

If a local-model download fails, try **Settings → Local model → Test reachability**, then choose another download source. If the app does not start, inspect `data/logs/crash.log`. A blank capture can mean the chosen window is minimized or blocks capture.

## Build from source

Use Python 3.13 on Windows:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py
```

Run the regression suite and build the release artifacts:

```powershell
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe scripts\package.py
.venv\Scripts\python.exe scripts\build_installer.py
```

The installer step requires [Inno Setup 7](https://jrsoftware.org/isdl.php). The build scripts verify the extracted portable app and a temporary installation/uninstallation. Additional manual probes live in `scripts/`.

See [RUST-MIGRATION.md](./RUST-MIGRATION.md) for the experimental Rust preview.

## License

PRTSBox project code is licensed under **LGPL-3.0-or-later**. See [LICENSE](./LICENSE) and the incorporated [GNU GPL v3 text](./COPYING.GPL). Downloaded models, runtimes, and third-party components have their own licenses and use terms.
