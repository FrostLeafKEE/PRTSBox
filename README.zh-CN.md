<p align="right">
  <a href="./README.md"><kbd>English</kbd></a>
  <a href="./README.zh-CN.md"><kbd>中文 ✓</kbd></a>
</p>

<p align="center"><img src="./prtsbox/ui/assets/app.png" width="96" alt="PRTSBox 图标"></p>

# PRTSBox v1.01

**Windows 窗口实时翻译工具。** PRTSBox 抓取指定窗口，识别其中的文字，翻译后将译文覆盖在原内容附近；译文层可被鼠标点穿。适用于游戏、视觉小说，以及文字无法直接选取的其他程序。

目前持续维护的是 **Python 版**。仓库中的 Rust 代码是实验性预览，不作为推荐下载版本。

## 下载

| Windows 10/11 · 64 位 | 文件 | 内容 |
|---|---|---|
| 安装版 | [PRTSBox-Setup-v1.01-win64.exe](https://github.com/FrostLeafKEE/PRTSBox/releases/download/v1.01/PRTSBox-Setup-v1.01-win64.exe) | 当前用户安装，可选桌面与开始菜单快捷方式，附 Windows 卸载程序 |
| 便携版 | [PRTSBox-v1.01-win64.zip](https://github.com/FrostLeafKEE/PRTSBox/releases/download/v1.01/PRTSBox-v1.01-win64.zip) | 解压后运行 `PRTSBox.exe`，无需安装 |

安装版无需管理员权限。两个版本都包含程序和 OCR 组件，但**不包含**体积较大的翻译模型与本地推理运行时。选择本地翻译时，首次使用需在设置中下载。

## 开始使用

1. 安装程序或解压便携包，然后启动 `PRTSBox.exe`。
2. 如果想离线翻译，打开**设置 → 本地模型**，下载推理运行时（推荐先选 Vulkan）和模型（标准版体积较小）。也可以改用在线翻译引擎。
3. 选择目标窗口、源语言、目标语言与翻译引擎。
4. 点击**开始实时翻译**。之后可按 **F8** 随时开关翻译。

设置旁边的 **中 / EN** 按钮用于切换软件界面语言，选择会自动保存。页面顶部的语言按钮用于切换两份完整 README；GitHub 默认显示英文版。

## 功能

| 功能 | 说明 |
|---|---|
| 翻译引擎 | 离线 Hy-MT2 本地模型、OpenAI 兼容模型接口，以及 Azure Translator、DeepL API、百度翻译 |
| OCR | 本地 PP-OCRv5 文字识别；打包版默认使用 ONNX Runtime |
| 窗口译文层 | 随目标窗口移动，鼠标可点穿 |
| 识别范围 | 识别整个窗口，或只识别底部 10–100% |
| 静止画面稳定性 | 相同画面复用结果；轻微 OCR 波动需要确认，译文位置保持稳定 |
| 桌宠 | 可显示、拖动；右键菜单可控制翻译、打开主窗口或关闭桌宠 |
| 显示 | 深色与普瑞赛斯风格主题，可调整译文位置和字号 |
| 截图隐私 | 默认不让截图或直播捕捉译文，也可在界面中切换 |
| 检查更新 | 在**设置 → 关于**中手动检查；发现较新的正式版本时可打开 GitHub 发布页 |

翻译运行时约每 **900 毫秒**尝试检查一帧；上一帧仍在处理时，下一次检查会延后。最小化窗口、受保护内容及部分独占全屏游戏能否抓取，取决于 Windows 和目标程序是否提供可读取的画面。

### 翻译方式

- **本地模型：**标准 Hy-MT2 1.8B 或增强 Hy-MT2 7B，通过下载的 llama.cpp 运行时执行。首次下载完成后可离线翻译，无需 API Key。两个模型的下载大小约为 1.06 GB 和 4.31 GB。
- **AI 大模型 API：**在**设置 → AI 大模型 API**中填写 OpenAI 兼容接口地址、模型名称和 API Key。
- **翻译平台：**在**设置 → 翻译平台**中配置**微软 Azure Translator**、**DeepL API**（Free / Pro）或**百度翻译**，然后在主界面选择该引擎。

API 密钥使用 Windows DPAPI 在本机加密保存。在线引擎会把识别出的文字发送给所配置的平台；本地模型则在电脑上运行推理。

## 数据、更新与卸载

PRTSBox 把设置、日志、已下载模型与运行时保存在 `PRTSBox.exe` 旁边的 `data/` 文件夹中。安装版通常位于 `%LOCALAPPDATA%\Programs\PRTSBox\data`；便携版位于解压目录。仓库与发行压缩包都不包含个人数据。

安装向导可选创建桌面和开始菜单快捷方式。可通过 **Windows 设置 → 已安装的应用**卸载；若创建了开始菜单快捷方式，也可从开始菜单卸载。交互式卸载会询问是否删除 `data/`，默认保留，以便重新安装后继续使用。

本地模型下载失败时，可到**设置 → 本地模型 → 测试可达性**检查下载源，再切换来源。程序无法启动时，可查看 `data/logs/crash.log`。抓到黑屏时，先确认目标窗口未最小化、未禁止抓取。

## 从源码运行与打包

在 Windows 上使用 Python 3.13：

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py
```

运行测试并制作发行文件：

```powershell
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe scripts\package.py
.venv\Scripts\python.exe scripts\build_installer.py
```

制作安装包需要 [Inno Setup 7](https://jrsoftware.org/isdl.php)。构建脚本会验证解压后的便携程序，并在临时目录完成一次安装、启动和卸载检查。更多手动验证脚本位于 `scripts/`。

实验性 Rust 版的范围见 [RUST-MIGRATION.md](./RUST-MIGRATION.md)。

## 开源协议

PRTSBox 项目代码采用 **LGPL-3.0-or-later**。详见 [LICENSE](./LICENSE) 和其引用的 [GNU GPL v3 正文](./COPYING.GPL)。下载的模型、运行时与第三方组件各自遵循原有许可和使用条款。
