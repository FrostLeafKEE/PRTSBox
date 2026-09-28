# PRTSBox

Windows 实时窗口 OCR 翻译：抓取目标窗口画面，识别文字，翻译后把译文覆盖回原位置，鼠标可以点穿。

当前使用和后续维护以 **Python 版**为主。Rust 实现保留作历史预览，暂停作为日常版本；其已知验证范围见 [RUST-MIGRATION.md](RUST-MIGRATION.md)。本地配置、API 密钥、下载的模型和打包产物不纳入仓库。

翻译默认走**本地模型**，完全离线、免费、无需 API Key。装好运行时和模型后不再需要联网。

## 功能

| 功能 | 说明 |
|---|---|
| 本地翻译 | 内置腾讯混元 Hy-MT2 翻译模型，经 llama.cpp 在本机推理，不消耗任何 API 额度 |
| 双模型可选 | 「标准」1.8B 与「增强」7B 各自独立下载、独立删除，随时切换 |
| OpenAI 兼容接口 | 也可接入任意 OpenAI 格式接口（OpenAI / DeepSeek / Kimi / GLM 等） |
| 窗口级抓帧 | `PrintWindow` 直接读取窗口自身内容，译文层不会被再次截取，杜绝回环翻译 |
| 本地 OCR | PP-OCRv5（`onnxocr`），优先 OpenVINO 加速，失败自动回退 ONNX Runtime |
| 鼠标穿透译文层 | 译文覆盖在目标窗口上方且不拦截点击，目标窗口保持焦点 |
| 译文不进截图 / 直播 | 默认对译文层启用 Windows 捕获排除；可在主界面关闭，让截图工具和 OBS 等采集到译文 |
| 只识别窗口下方 | 可只识别窗口下方 10–100% 的区域，避免把美术字、衣服上的字一起翻掉 |
| 静态画面复用 | 识别区域像素不变时复用结果；小幅位置漂移保留显示坐标，近似文本变化跨帧确认 |
| F8 全局热键 | 低级键盘钩子，游戏独占输入时依然生效 |
| 凭据加密 | API Key 经 Windows DPAPI 加密后保存，换机器或换用户都无法解密 |

## 快速开始

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py
```

首次使用：打开「设置」→「本地模型」，依次下载**推理运行时**（Vulkan 版约 33 MB）和**模型**（标准版约 1.06 GB）。两者都就绪后，在主界面选择目标窗口，点「开始实时翻译」，再按 **F8** 开关翻译。

## 翻译引擎与平台配置

主界面提供三类引擎：**翻译平台**、**AI 大模型 API（OpenAI 兼容）**、**本地模型**。

在「设置 → 翻译平台」选择并配置：

| 平台 | 配置 |
|---|---|
| 微软 Azure Translator | 订阅密钥、资源区域、接口地址；默认全球接口，支持资源自定义地址 |
| DeepL API | API 密钥、Free / Pro 账户类型 |
| 百度翻译 | 通用文本翻译 APP ID、密钥 |

配置自动保存，密钥使用 Windows DPAPI 加密。主界面选择「翻译平台」后可切换这三家服务，源语言和目标语言沿用主界面选项。平台请求会分批并检查返回条目数量；百度请求按最多约每秒一次发送。已有 OpenAI 兼容 API 与本地模型配置继续保留。

接口依据：[Azure Translator](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/reference/v3/translate)、[DeepL](https://developers.deepl.com/api-reference/translate/request-translation)、[百度通用文本翻译](https://api.fanyi.baidu.com/doc/23)。

## 模型选择

实测数据（RX 7900 XT / Vulkan / Q4_K_M，`scripts/models_compare.json` 有逐句对比）：

| | 标准（1.8B） | 增强（7B） |
|---|---|---|
| 下载体积 | 1.06 GB | 4.31 GB |
| 显存占用 | ≈ 2.0 GB | ≈ 6.0 GB |
| 加载耗时 | 1.2 s | 4.2 s |
| 生成速度 | 160–190 tok/s | 105–125 tok/s |
| 单条延迟 | 25–255 ms | 54–443 ms |

**默认用标准版。** 屏幕翻译几乎全是短 UI 字符串，1.8B 在这个场景下与 7B 持平甚至更好——7B 更容易把短标签过度翻译（`Damage: 1250 Critical Hit!` → 标准版「伤害：1250 暴击！」、增强版「损害值：1250，造成致命一击！」），中译英时也出现过误译（`foreground` → `front stage`）。7B 只在长段落措辞上更自然，所以定位为长文本增强档。

## 架构

```
prtsbox/
├── capture.py         # PrintWindow 窗口抓帧，裁到客户区
├── ocr.py             # PP-OCRv5 推理封装（OpenVINO / ONNX Runtime）
├── textproc.py        # 噪声过滤、折行合并
├── pipeline.py        # 单帧流水线 + LRU 译文缓存
├── overlay.py         # 鼠标穿透译文层
├── win32.py           # 窗口枚举、DPI、Z 序、F8 钩子
├── dpapi.py           # Windows 凭据加解密
├── config.py          # JSON 配置存储
├── llama/
│   ├── catalog.py     # 内置模型与运行时清单（体积 + SHA-256）
│   ├── download.py    # 断点续传 + 镜像回退 + 校验
│   ├── server.py      # llama-server 子进程托管（Job Object 绑定）
│   └── manager.py     # 运行时安装、模型增删、服务生命周期
├── translate/
│   ├── local.py       # 本地模型引擎（逐条并发 + 缓存）
│   └── openai.py      # OpenAI 兼容引擎
└── ui/
    ├── main_window.py
    ├── settings_dialog.py
    ├── layout.py      # 滚动区、可伸缩控件、窄窗口自动堆叠
    └── theme.py
```

数据全部放在程序目录下的 `data/`，不写入 `%LOCALAPPDATA%`：

```
data/
├── config.json        # 配置（密钥为 DPAPI 密文）
├── models/            # 下载的 GGUF 模型
├── runtime/           # 解压的 llama.cpp 运行时
├── logs/              # 日志与 llama-server 输出
└── cache/             # OCR 推理缓存
```

## 关键实现取舍

**为什么用逐条并发而不是单请求批量。** 单请求返回 JSON 数组看起来更省事，但实测中 1.8B 会**漏翻第一条**并原样返回（两次复现）；编号多行方案只快 1.0–1.4 倍却会拉低短文本质量。最终按每串一个请求、四个并发发出，与 llama-server 的 `-np 4` 对齐。`-np 8` 会把 8192 上下文切成 1024/槽，延迟反而从 854 ms 退化到 1936 ms。

**为什么抓帧不截屏幕。** 译文层就盖在目标窗口上，截屏幕区域会把自己的译文拍进去，OCR 后再翻译，形成无限回环。`PrintWindow(PW_RENDERFULLCONTENT)` 渲染窗口自身内容，结构上就不存在这个问题。译文层另外还会设置捕获排除，避免出现在直播和录屏里，也作为未来改用屏幕抓取时的兜底。这道排除可以在主界面「译文显示」里关掉（「译文可被截图 / 直播捕捉」），供需要把译文录进视频的场合使用；因为识别读的是目标窗口自身的渲染，关掉它不会让译文被重复翻译。

**为什么子进程要绑定 Job Object。** llama-server 会占用数 GB 显存。没有 `KILL_ON_JOB_CLOSE` 的话，主程序一旦崩溃就会留下孤儿进程，下次启动直接显存不足，且报错看起来像是本程序的 bug。

**译文层的 DPI。** `devicePixelRatioF()` 在窗口 show 之前返回 1.0，在 125% 缩放的屏幕上会导致译文层放大 25%、文字逐行漂移。改为按目标坐标查所在显示器取缩放比。

**译文缓存有上限。** 缓存键是整段 OCR 文本，不设上限的话长时间运行会持续增长。改为 LRU，上限 2000 条。

**面板滚动而不是压缩。** 主窗口内容自然高度约 873px，而最初窗口最小高度写的是 600px——Qt 在空间不足时会压缩布局，`QFormLayout` 压缩的方式就是让行互相重叠，这正是"字号输入框压住下面勾选框"的成因。修法不是把最小高度调大（那只是把问题藏起来），而是把配置面板放进 `QScrollArea`：内容保持自己的自然高度，放不下就滚动。标题和开始按钮留在滚动区之外，任何窗口尺寸下都可见。同理，横向的「下拉框 + 按钮」组合在宽度不足 280–340px 时自动改为纵向排列，避免控件被挤到不可读。

**跨线程派发用 Signal 而不是 `QMetaObject.invokeMethod`。** `invokeMethod` 要求每个参数都用 `Q_ARG` 描述，而 PySide6 无法解析 `Q_ARG(object, ...)` 的类型名，会直接抛 `Unable to find a QMetaType for "object"`。由于「忙碌」标志在调用前置位、只在收到帧时清除，这个异常会让流水线永远等不到回复——表现为界面卡在「正在加载模型」而后台其实早已停摆。改成排队的信号连接，跨线程不需要任何类型名编组。

**每帧必须恰好回报一次。** UI 同一时刻只允许一帧在处理中，收到回复才派发下一帧。因此 `process` 里任何提前 `return` 都必须先发出信号——截图失败曾静默返回，导致流水线永久冻结。`FrameResult.note` 用来区分"这一帧没东西可翻"和"这一帧失败了"，两者都会清除忙碌标志，只是状态栏文案不同。另有看门狗：单帧超过 45 秒未返回则停止会话并说明原因，而不是一直等下去。

**退出清理不能只放在 `closeEvent`。** `app.quit()`（冒烟测试、系统注销）不会派发关闭事件，此时流水线线程和 llama-server 子进程仍在运行，解释器退出时会以 `0xC0000409` 硬崩溃。现在 `closeEvent` 和 `QApplication.aboutToQuit` 都调用同一个幂等的 `shutdown()`。

**OCR 后端默认用 ONNX Runtime，而不是更快的 OpenVINO。** 实测 OpenVINO 的识别速度快约 2.6 倍（61ms vs 160ms），但它的识别模型输入宽度是逐批动态计算的（取该批最宽裁剪的比例），而**每一个新宽度都会占用约 4 MiB 且不再归还**。任何文字宽度会变化的屏幕——游戏 HUD 的数字、视频字幕——都会因此无限增长：端到端实测 13 MiB/帧，60 帧后进程达到 2.3 GB，几百帧就是 5 GB 以上。同样的负载在 ONNX Runtime 下是 0.05 MiB/帧。

定位过程是二分而非猜测：只跑检测器是干净的（+0.03 MiB/帧），识别器在输入形状固定时也干净（+0.06），只有"识别器 + 变化的裁剪宽度"这一组合会增长（+4.40）。设置里仍可选 OpenVINO 换速度，流水线另有一道兜底：内存增长超过 600 MiB 就重建识别服务。

**上下文大小不是内存的主要杠杆。** 实测 1.8B 模型下 `llama-server` 的常驻内存：`-c 8192` 2045 MiB、`-c 4096` 1787 MiB、`-c 2048` 1658 MiB，而一帧的耗时都在 383–405 ms。省得有限，因为其中约 1.5 GB 是模型权重（即便 `-c 1024` 也有 1592 MiB）。默认取 `-c 4096 -np 4`（每槽 1024 token，提示词约 100 token，足够）：白拿 258 MiB 且速度无损。

**稳态内存约 3.0 GB。** 120 帧连续翻译后：主进程 1128 MiB（第 60 帧起持平），llama-server 1897 MiB（第 15 帧后固定，是 KV cache 的一次性分配）。用 `scripts/soak_memory.py --translate` 可复现。

**只识别窗口下方。** 文字类游戏通常只在底部对话框放需要翻译的内容，而全窗口识别会把美术字、衣服和场景上的文字一起送进去。开启后截图在送 OCR 之前就裁掉上方，识别框再平移回整窗坐标——这样下游（折行合并、译文层、诊断）始终只用一套坐标，不必各自记住裁剪偏移。实测：一个顶部有美术字的窗口，全窗口识别命中美术字，只识别下方 30% 时命中 0，且 OCR 从 275 ms 降到 173 ms。

**静止对话保持稳定。** 识别区域像素完全相同时直接复用上一帧，不重复 OCR 或翻译。背景变化仍会触发 OCR，但相同文字的识别框在 6px 范围内漂移时保留已确认的坐标，并在折行合并之前处理，避免间距变化造成同一段话反复拆合。译文层也保留实际显示坐标，空白差异不会单独触发重绘。

近似文本变化及文字消失需要连续两次确认；候选内容持续变化时最多保留旧结果两帧，避免数字等真实变化一直不更新。明显不同的新句子直接处理。相似度仅用于短暂确认，不合并译文缓存键。停止、切换窗口或修改翻译配置后，旧请求的结果会被丢弃；确认文字消失后清除旧译文。

## 打包分发

```powershell
.venv\Scripts\python.exe scripts\package.py
```

产物为 `dist/PRTSBox-日期-win64.zip`，当前约 **135 MB**。脚本在临时目录构建、验证压缩包，不会清理已有的 `dist/PRTSBox/data/`。压缩包里只有程序本体，**不含模型和推理运行时**——它们在首次运行时由内置下载器按需获取（模型 1.06 GB / 4.31 GB 传不动，而且下载器会自动挑选适配本机显卡的运行时变体）。

打包时排除了：

| 排除项 | 原因 |
|---|---|
| `openvino` | 242 MB，且不是默认后端（识别路径在文字宽度变化时内存持续增长） |
| `data/` | 模型、运行时、日志、缓存，全部由程序自己生成 |
| Qt 的 Qml / Quick / Network / Sql 等模块 | 用不到，PySide6 因此从 202 MB 降到 71 MB |

设置页只在 OpenVINO 真正可导入时才显示该选项，因此打包版不会出现"选了就报错"的死选项。

**验证交付物**（发出去之前应该做）：

```powershell
# 解压后在该目录执行，检查 OCR、下载、子进程、翻译四件事
$env:PRTSBOX_SELFTEST="download"; .\PRTSBox.exe
```

结果写入 `data\logs\prtsbox.log`。`待下载` 表示尚未获取（首次运行的正常状态），只有 `失败` 才会返回非零退出码。加 `download` 会顺带验证下载与解压（拉取 33 MB 运行时）。

程序在没有控制台的模式下运行，启动阶段就失败时会把堆栈写进 `data\logs\crash.log` 并弹窗提示，不会出现"双击没反应"。

**下载源可选。** 模型和运行时各有多条下载路径，而不同网络下能连上的差别极大——国内镜像对某些人很快，对另一些人完全不通；第三方 GitHub 加速对能直连的人是多余的风险。所以做成用户选项而不是写死顺序：

| 选项 | 模型走 | 运行时走 |
|---|---|---|
| 自动（默认） | ModelScope → HF 镜像 → HuggingFace | gh-proxy → ghfast → GitHub |
| 仅国内源 | ModelScope → HF 镜像 | gh-proxy → ghfast |
| 仅官方源 | HuggingFace | GitHub |

设置页「本地模型」里有「测试可达性」，会实际请求每个源并列出结果（含耗时），比猜快得多。下载失败时日志里也会记录试过哪些源。

两个模型源都**验证过字节完全一致**（`scripts/check_mirrors.py --hashes` 完整下载后比对 SHA-256），所以换源不会下到不同的文件。运行时用的是 GitHub 的字节转发代理而非重新编译，保证 `llama-server.exe` 就是测试过的那个二进制。

## 开发

```powershell
# 单元测试（含 GUI 派发链路的回归测试）
.venv\Scripts\python.exe -m pytest tests -q

# 内存浸泡：真实 OCR + 真实模型，报告每帧增长与分配点
.venv\Scripts\python.exe scripts\soak_memory.py --frames 120 --translate
.venv\Scripts\python.exe scripts\soak_memory.py --frames 60 --backend openvino   # 复现 OpenVINO 增长

# 上下文/并发配置的内存与延迟对比
.venv\Scripts\python.exe scripts\bench_context.py

# 布局检查：多种窗口尺寸 + DPI 下检测挤压与重叠
.venv\Scripts\python.exe scripts\check_layout.py
$env:QT_SCALE_FACTOR="1.25"; .venv\Scripts\python.exe scripts\check_layout.py

# 渲染各尺寸截图供人工确认
.venv\Scripts\python.exe scripts\preview_ui.py

# 走完整 UI 会话循环（验证帧派发链路）
.venv\Scripts\python.exe scripts\e2e_session.py

# 截图持续失败时的恢复行为
.venv\Scripts\python.exe scripts\e2e_failed_capture.py

# 只识别下方区域 + 译文层不抖动
.venv\Scripts\python.exe scripts\e2e_region.py

# 下载源：可达性、设置项到管理器的传递、按源下载
.venv\Scripts\python.exe scripts\e2e_sources.py
.venv\Scripts\python.exe scripts\check_mirrors.py --hashes

# OCR 自检（对比 OpenVINO 与 ONNX Runtime）
.venv\Scripts\python.exe scripts\check_ocr.py

# 抓帧 + OCR（真实窗口）
.venv\Scripts\python.exe scripts\e2e_capture.py

# 下载运行时与模型并翻译（端到端）
.venv\Scripts\python.exe scripts\e2e_local.py

# 完整流水线 + 译文层叠加验证
.venv\Scripts\python.exe scripts\e2e_full.py

# 界面构造冒烟
.venv\Scripts\python.exe scripts\smoke_ui.py

# 启动后 2.5 秒自动退出
$env:PRTSBOX_SMOKE_TEST="1"; .venv\Scripts\python.exe run.py
```

模型推理基准（需先手动启动 `llama-server`）：

```powershell
.venv\Scripts\python.exe scripts\bench_llama.py --port 8123 --label 1.8B
```

## 许可证

本项目代码可自由使用。内置模型与运行时有各自的许可：

- **Hy-MT2**（腾讯混元）：遵循[腾讯混元社区许可](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF)，对商用有地区与月活限制，分发前请自行确认。
- **llama.cpp**：MIT。
- **PP-OCRv5 / onnxocr**：Apache-2.0。
