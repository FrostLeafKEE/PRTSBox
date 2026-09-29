"""Presentation-only Chinese/English translations for the Qt interface.

The Chinese strings remain the source text.  Item data, model prompts, OCR
output and translated overlay text are never changed by the UI language.
"""

from __future__ import annotations

from .. import __version__

from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QTabWidget, QWidget,
)


ENGLISH = {
    f"PRTSBox v{__version__} · 实时窗口翻译": f"PRTSBox v{__version__} · Live Window Translation",
    "实时窗口翻译": "Live Window Translation",
    "本地模型离线翻译，译文覆盖在目标窗口上方": "Translate locally and display results over the target window",
    "设置": "Settings", "关闭": "Close", "刷新": "Refresh", "目标窗口": "Target window",
    "选择要翻译的窗口；译文显示在该窗口上方，鼠标可以点穿。": "Choose a window to translate. The overlay follows it and lets mouse clicks pass through.",
    "只识别窗口下方": "Scan only the bottom of the window",
    "翻译引擎": "Translation engine", "引擎": "Engine", "本地模型": "Local model",
    "本地模型（免费，无需联网）": "Local model (free, offline)",
    "AI 大模型 API（OpenAI 兼容）": "AI model API (OpenAI compatible)",
    "翻译平台": "Translation provider", "源语言": "Source language", "目标语言": "Target language",
    "译文显示": "Translation display", "原文下方": "Below source text",
    "原文右侧": "Right of source text", "布局": "Layout", "字号": "Font size",
    "已是中文的文本不翻译": "Skip text already in Chinese",
    "同时显示原文": "Show source text", "显示耗时": "Show timing",
    "译文可被截图 / 直播捕捉": "Capture translations",
    "目标语言是中文时，识别结果本身为中文的行不会被送去翻译。": "With a Chinese target, lines already in Chinese are skipped.",
    "当前目标语言不是中文，中文原文需要翻译，因此该选项不生效。": "This option applies only to Chinese targets; Chinese source text must be translated for other targets.",
    "目标语言为中文时生效：识别结果本身是中文的行不会被送去翻译，避免把原文改写一遍。目标为其他语言时该选项不起作用。": "With a Chinese target, lines already in Chinese are skipped. This option has no effect for other targets.",
    "默认关闭：译文层使用 Windows 捕获排除，不会出现在截图、录屏和直播画面里。开启后截图工具和 OBS 等采集软件就能看到译文。\n识别读的是目标窗口自身的内容，因此开启不会把译文当成原文重复翻译。": "Off by default: Windows excludes the overlay from screenshots, recordings and streams. Enable this to capture it in tools such as OBS. Recognition reads the target window directly, so the overlay will not be translated again.",
    "开始实时翻译　·　F8 开关": "Start translating · F8 toggle",
    "开始实时翻译（F8 开关）": "Start translating (F8 toggle)",
    "停止实时翻译（F8 开关）": "Stop translating (F8 toggle)",
    "显示桌宠": "Show pet", "关闭桌宠": "Hide pet", "就绪": "Ready",
    "打开主窗口": "Open main window", "开启翻译": "Start translation",
    "停止翻译": "Stop translation", "正在准备翻译…": "Preparing translation…",
    "通用": "General", "AI 大模型 API": "AI Model API", "关于": "About",
    "窗口文字实时识别与翻译": "Live window OCR translation",
    "打开 PRTSBox GitHub 仓库": "Open the PRTSBox GitHub repository",
    "检查更新": "Check for updates", "查看发布页": "Open release page",
    "请等待当前任务完成后再检查更新。": "Wait for the current task before checking for updates.",
    "正在检查 GitHub 正式版本…": "Checking the latest GitHub release…",
    "当前已是最新正式版。": "You have the latest stable version.",
    "检查更新失败，请稍后重试。": "Could not check for updates. Try again later.",
    "发布信息不可公开访问，请确认仓库已公开。": "Release information is not public. Check the repository visibility.",
    "正在结束检查，完成后自动关闭…": "Finishing the check; Settings will close shortly…",
    "本项目代码采用 LGPL-3.0-or-later": "Project code is licensed under LGPL-3.0-or-later",
    "模型、运行时和第三方组件遵循各自的许可": "Models, runtimes and third-party components have separate licenses",
    "深色": "Dark", "浅色": "Light", "普瑞赛斯 · 星芒档案": "Priestess · Star Archive",
    "主题风格": "Theme", "自动（推荐）": "Auto (recommended)",
    "ONNX Runtime（内存稳定）": "ONNX Runtime (stable memory use)",
    "OpenVINO（更快，占用会增长）": "OpenVINO (faster, growing memory use)",
    "文字识别后端": "OCR backend",
    "ONNX Runtime 内存占用稳定，长时间使用不会增长；OpenVINO 识别快约 2.6 倍，但遇到变化的文字宽度会持续占用内存（实测约 13 MiB/帧），因此默认不使用。": "ONNX Runtime keeps memory use stable. OpenVINO is about 2.6× faster but can use roughly 13 MiB more per frame as text widths change, so it is not the default.",
    "主题可在此预览，关闭设置后应用到主界面。语言、布局与字号在主界面调整。": "Preview the theme here; it applies to the main window when Settings closes. Set languages, layout and font size in the main window.",
    "所有数据都保存在程序目录内，不会写入系统其他位置。": "All data stays in the application directory.",
    "下载源": "Download source", "自动（国内优先，失败后转官方）": "Auto (regional first, then official)",
    "仅国内源（ModelScope / GitHub 加速）": "Regional only (ModelScope / GitHub mirror)",
    "仅官方源（HuggingFace / GitHub）": "Official only (HuggingFace / GitHub)",
    "测试可达性": "Test connections",
    "不同网络下能连上的源差别很大，连不上时换一个通常就好了。「测试可达性」会实际请求每个源并列出结果。": "Source availability varies by network. If one fails, choose another. Test connections checks each source and lists the results.",
    "推理运行时（llama.cpp）": "Inference runtime (llama.cpp)",
    "下载运行时": "Download runtime",
    "推荐 Vulkan 版本：NVIDIA / AMD / Intel 通用且体积最小。llama-server 会被本程序作为子进程管理，退出时自动结束。": "Vulkan is recommended: it supports NVIDIA, AMD and Intel and has the smallest download. The app manages llama-server and closes it on exit.",
    "翻译模型": "Translation models", "下载": "Download", "使用": "Use", "删除": "Delete",
    "✓ 正在使用": "✓ In use", "✓ 已下载，可直接切换使用": "✓ Downloaded; ready to use",
    "未下载": "Not downloaded",
    "模型为腾讯混元 Hy-MT2（Q4_K_M 量化），遵循腾讯混元社区许可。两个模型互相独立，可分别下载或删除。": "Models are Tencent Hunyuan Hy-MT2 (Q4_K_M), subject to the Tencent Hunyuan community license. Each model can be downloaded or deleted independently.",
    "标准（推荐）": "Standard (recommended)", "增强（长文本更佳）": "Enhanced (better for long text)",
    "响应快、占用低，屏幕短文本表现与增强档相当": "Fast and efficient; similar quality for short on-screen text",
    "长段落措辞更自然，速度较慢、显存占用较高": "More natural long passages; slower and uses more VRAM",
    "Vulkan（推荐）": "Vulkan (recommended)", "CPU（兜底）": "CPU (fallback)",
    "ROCm / HIP（AMD 专用）": "ROCm / HIP (AMD only)",
    "微软 Azure Translator": "Microsoft Azure Translator", "百度翻译": "Baidu Translate",
    "订阅密钥": "Subscription key", "资源区域": "Resource region",
    "接口地址": "Endpoint URL", "API 密钥": "API key", "密钥": "Secret key",
    "账户类型": "Account type", "模型名称": "Model name",
    "例如 eastasia；全局资源可留空": "e.g. eastasia; leave blank for a global resource",
    "百度翻译开放平台 APP ID": "Baidu Translate Open Platform APP ID",
    "通用文本翻译密钥": "General text translation key",
    "区域须与 Azure 资源一致。默认使用全球接口，也可填写资源的自定义接口地址。": "The region must match your Azure resource. The global endpoint is used by default; you may enter a custom resource endpoint.",
    "请使用 DeepL API 的密钥。Free 与 Pro 使用不同的接口地址，需与账户类型一致。": "Use a DeepL API key. Free and Pro use different endpoints; select the matching account type.",
    "请先开通百度翻译开放平台的通用文本翻译服务。请求按每秒最多一次发送。": "Enable General Text Translation in Baidu Translate Open Platform first. Requests are limited to one per second.",
    "配置自动保存，密钥使用 Windows DPAPI 加密。使用前请在主界面选择“翻译平台”。": "Settings save automatically; keys are encrypted with Windows DPAPI. Select Translation provider on the main window before use.",
    "API Key 使用 Windows DPAPI 加密后保存在本机配置文件中，换一台机器或换一个用户账户都无法解密。": "The API key is stored locally and encrypted with Windows DPAPI. Another device or Windows account cannot decrypt it.",
    "✓ 已保存 API Key": "✓ API key saved", "尚未填写 API Key": "API key not set",
    "自动检测": "Auto detect", "简体中文": "Simplified Chinese", "繁体中文": "Traditional Chinese",
    "英语": "English", "日语": "Japanese", "韩语": "Korean", "法语": "French",
    "德语": "German", "西班牙语": "Spanish", "葡萄牙语": "Portuguese",
    "俄语": "Russian", "意大利语": "Italian", "阿拉伯语": "Arabic",
    "泰语": "Thai", "越南语": "Vietnamese",
    "F8 全局热键被系统拒绝，请使用界面按钮控制": "Windows blocked the global F8 hotkey; use the on-screen button.",
    "没有找到可翻译的窗口": "No translatable window found",
    "目标窗口已关闭，请刷新窗口列表": "The target window closed; refresh the list",
    "目标窗口已关闭": "The target window closed",
    "尚未下载本地模型，请点击「设置」→「本地模型」下载。": "No local model is downloaded. Open Settings → Local model to download one.",
    "本地模型已就绪，但还缺少推理运行时，请在「设置」中下载。": "The local model is ready, but the inference runtime is missing. Download it in Settings.",
    "本地翻译完全离线运行，不消耗 API 额度。": "Local translation runs offline and uses no API quota.",
    "请先选择目标窗口": "Choose a target window first", "准备中…": "Preparing…",
    "翻译运行中，正在等待画面…": "Translation running; waiting for a frame…",
    "已停止": "Stopped", "程序正在退出": "The application is closing",
    "截图失败：目标窗口可能已最小化，或该窗口拒绝被渲染": "Capture failed: the target may be minimized or refuse rendering",
    "截图全黑：目标窗口可能受保护（如 DRM 视频、独占全屏游戏）": "Black frame: the target may be protected (for example DRM video or exclusive fullscreen)",
    "未识别到文字": "No text detected", "识别到的文字已是中文，无需翻译": "Detected text is already Chinese; no translation needed",
    "指定的识别区域太小，没有可识别的画面": "The selected OCR region is too small",
    "OCR 尚未就绪": "OCR is not ready", "所选 OCR 后端不可用，已回退 ONNX Runtime": "The selected OCR backend is unavailable; switched to ONNX Runtime",
    "OCR 内存占用过高，已重建识别服务": "OCR memory use was too high; recognition was restarted",
    "正在下载，无法同时测试。": "A download is in progress; connections cannot be tested now.",
    "测试未完成": "Connection test did not finish", "可访问": "Reachable",
    "尚未安装运行时，本地翻译无法启动。": "No runtime installed; local translation cannot start.",
    "准备下载…": "Preparing download…", "正在取消任务，完成后自动关闭…": "Cancelling; Settings will close when done…",
    "已取消": "Cancelled", "未知": "Unknown", "未知错误": "Unknown error",
    "尚未安装本地推理运行时，请先在设置中下载": "No local inference runtime installed; download it in Settings",
    "尚未安装本地推理运行时，请在设置中下载 llama.cpp 运行时": "No local inference runtime installed; download llama.cpp in Settings",
    "尚未下载本地模型，请在设置中下载「标准」或「增强」模型": "No local model downloaded; download Standard or Enhanced in Settings",
    "请在设置中填写接口 API Key": "Enter the API key in Settings",
    "请在设置中填写模型名称": "Enter a model name in Settings",
    "请选择有效的翻译平台": "Choose a valid translation provider",
    "请选择有效的源语言和目标语言": "Choose valid source and target languages",
    "请选择 DeepL API Free 或 Pro": "Choose DeepL API Free or Pro",
    "Azure 接口地址格式无效": "Invalid Azure endpoint URL",
    "Azure 接口地址须为不含密钥或查询参数的 HTTPS 地址": "The Azure endpoint must be an HTTPS URL without a key or query parameters",
    "翻译平台返回了无效的 JSON": "The provider returned invalid JSON",
    "翻译平台返回结构异常": "The provider returned an unexpected response",
    "本地模型译文被截断，请缩小识别区域或减少单段文字": "Local model output was truncated; reduce the OCR region or the length of a text segment",
    "本地模型返回了空译文或非文本内容": "The local model returned empty or non-text output",
    "模型返回了空译文或非文本内容，请检查模型及接口配置": "The model returned empty or non-text output; check the model and API settings",
    "单段文字超过平台请求限制，请缩小识别区域": "Text exceeds the provider request limit; reduce the OCR region",
    "平台返回的译文数量或内容异常，已停止本帧以避免译文错位": "The provider returned the wrong number of translations; this frame was stopped to avoid misaligned text",
    "凭据无效": "Invalid credentials", "凭据无效或无访问权限": "Invalid credentials or access denied",
    "请求过于频繁或额度不足": "Rate limit or quota exceeded", "翻译额度已用完": "Translation quota exhausted",
    "请求失败，请检查配置、语言及服务状态": "Request failed; check configuration, languages and service status",
}

# Fragments are used only for UI-generated progress and error messages.  They
# deliberately are not applied to OCR text, user-provided window titles or the
# translated overlay.
FRAGMENTS = {
    "当前版本：": "Installed version: ",
    "发现新版本：": "New version available: ",
    "配置与模型目录：": "Configuration and model directory: ",
    "所有数据都保存在程序目录内，不会写入系统其他位置。": "All data stays in the application directory.",
    "未知": "Unknown", "已取消": "Cancelled",
    "标准（推荐）": "Standard (recommended)",
    "增强（长文本更佳）": "Enhanced (better for long text)",
    "响应快、占用低，屏幕短文本表现与增强档相当": "Fast and efficient; similar quality for short on-screen text",
    "长段落措辞更自然，速度较慢、显存占用较高": "More natural long passages; slower and uses more VRAM",
    "Vulkan（推荐）": "Vulkan (recommended)",
    "CPU（兜底）": "CPU (fallback)",
    "ROCm / HIP（AMD 专用）": "ROCm / HIP (AMD only)",
    "体积 ": "Size ", "约 ": "Approx. ", " 显存": " VRAM",
    "未命名窗口 (": "Untitled window (",
    "识别区域：窗口下方 ": "OCR region: bottom ",
    "识别区域：整个窗口": "OCR region: whole window",
    "正在加载模型：": "Loading model: ",
    "单帧处理超过 ": "A frame took more than ",
    " 秒未返回，已停止。": " seconds; translation stopped. ",
    "目标窗口可能无响应，请重新开始或更换目标窗口。": "The target may be unresponsive; retry or choose another window.",
    "翻译运行中　·　": "Translating · ", " 条译文　·　": " translations · ",
    "截图 ": "Capture ", "翻译 ": "Translate ", "　·　合计 ": " · Total ",
    " ms　·　缓存命中 ": " ms · Cache hits ",
    "翻译中（": "Translating (", " 条）": " items)",
    "✓ 运行时运行中（模型：": "✓ Runtime running (model: ",
    "✓ 运行时已安装：": "✓ Runtime installed: ",
    "下载未完成：": "Download incomplete: ",
    "正在测试 ": "Testing ", " 的各下载源…": " download sources…",
    "正在下载 ": "Downloading ", "下载中 ": "Downloading ",
    "　剩余 ": " · remaining ", "正在测试下载源…": "Testing download sources…",
    "OCR 初始化失败：": "OCR initialization failed: ",
    "启动本地模型失败：": "Could not start local model: ",
    "本地翻译失败：": "Local translation failed: ",
    "翻译请求失败：": "Translation request failed: ",
    "接口返回错误：": "API error: ",
    "请在设置 → 翻译平台中填写 ": "Enter credentials for ",
    " 的凭据": " in Settings → Translation provider",
    " 连接失败或超时，请检查网络": " connection failed or timed out; check your network",
    "百度翻译：": "Baidu Translate: ",
    "可访问": "Reachable",
    "删除模型失败：": "Could not delete model: ",
    "模型尚未下载：": "Model not downloaded: ",
    "模型没有返回有效的译文数组：": "The model did not return a valid translation array: ",
    "接口返回的不是 JSON：": "The API did not return JSON: ",
    "接口返回结构异常：": "Unexpected API response: ",
    "签名错误，请检查密钥": "Invalid signature; check the secret key",
    "请求过于频繁": "Rate limit exceeded",
    "账户余额不足": "Insufficient account balance",
    "不支持该语言方向": "Unsupported language pair",
    "服务未开通或已关闭": "Service is not enabled or has been closed",
    "请求失败，请检查账户及接口配置": "Request failed; check account and API settings",
}


def localize(text: str, language: str) -> str:
    if language != "en" or not text:
        return text
    exact = ENGLISH.get(text)
    if exact is not None:
        return exact
    for source, target in FRAGMENTS.items():
        text = text.replace(source, target)
    return text.replace("　", " ").replace("（", "(").replace("）", ")").replace("、", ", ")


def set_localized_text(widget: QLabel | QAbstractButton | QGroupBox, source: str, language: str) -> None:
    widget.setProperty("_i18n_source_text", source)
    translated = localize(source, language)
    widget.setProperty("_i18n_display_text", translated)
    if isinstance(widget, QGroupBox):
        widget.setTitle(translated)
    else:
        widget.setText(translated)


def set_localized_tooltip(widget: QWidget, source: str, language: str) -> None:
    translated = localize(source, language)
    widget.setProperty("_i18n_source_tooltip", source)
    widget.setProperty("_i18n_display_tooltip", translated)
    widget.setToolTip(translated)


def _localized_value(widget: QWidget, key: str, current: str, language: str) -> str:
    source = widget.property(f"_i18n_source_{key}")
    displayed = widget.property(f"_i18n_display_{key}")
    if source is None or current != displayed:
        source = current
    translated = localize(str(source), language)
    widget.setProperty(f"_i18n_source_{key}", source)
    widget.setProperty(f"_i18n_display_{key}", translated)
    return translated


def translate_widget_tree(root: QWidget, language: str) -> None:
    """Relabel existing widgets without modifying values stored in itemData."""
    for widget in (root, *root.findChildren(QWidget)):
        if isinstance(widget, QGroupBox):
            widget.setTitle(_localized_value(widget, "title", widget.title(), language))
        elif isinstance(widget, (QLabel, QAbstractButton)):
            widget.setText(_localized_value(widget, "text", widget.text(), language))
        if widget.toolTip():
            widget.setToolTip(_localized_value(widget, "tooltip", widget.toolTip(), language))
        if isinstance(widget, QLineEdit) and widget.placeholderText():
            widget.setPlaceholderText(_localized_value(widget, "placeholder", widget.placeholderText(), language))
        if isinstance(widget, QComboBox):
            previous = widget.property("_i18n_combo_items") or []
            items = []
            for index in range(widget.count()):
                current = widget.itemText(index)
                old = previous[index] if index < len(previous) else None
                source = old[0] if old is not None and old[1] == current else current
                translated = localize(source, language)
                widget.setItemText(index, translated)
                items.append((source, translated))
            widget.setProperty("_i18n_combo_items", items)
        if isinstance(widget, QTabWidget):
            previous = widget.property("_i18n_tabs") or []
            tabs = []
            for index in range(widget.count()):
                current = widget.tabText(index)
                old = previous[index] if index < len(previous) else None
                source = old[0] if old is not None and old[1] == current else current
                translated = localize(source, language)
                widget.setTabText(index, translated)
                tabs.append((source, translated))
            widget.setProperty("_i18n_tabs", tabs)
    root.setWindowTitle(_localized_value(root, "window_title", root.windowTitle(), language))
