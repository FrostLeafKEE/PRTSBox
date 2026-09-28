"""Settings dialog, including the local runtime and model manager."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import llama
from ..config import ConfigStore
from ..llama import DownloadProgress, LlamaManager
from ..translate.platform import PLATFORMS, PLATFORM_SECRETS, AZURE_ENDPOINT
from .layout import ResponsiveRow, expanding, scrollable, shrinkable_combo
from .theme import stylesheet, tokens_for


def _human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes:.0f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def _openvino_available() -> bool:
    """Whether the OpenVINO backend can actually be used.

    Checked by import rather than by a build flag, so the same code offers the
    right choices whether it is running from source or from a packaged build
    that deliberately omits OpenVINO.
    """
    import importlib.util

    return importlib.util.find_spec("openvino") is not None


class _Task(QObject):
    """Runs a blocking callable off the UI thread."""

    progressed = Signal(object)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, action) -> None:
        super().__init__()
        self._action = action
        self.cancel = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            self._action(self.cancel, self.progressed.emit)
            self.finished.emit()
        except llama.DownloadCancelled:
            self.failed.emit("已取消")
        except Exception as exc:  # surfaced verbatim in the dialog
            logging.getLogger("prtsbox.settings").exception("任务失败")
            self.failed.emit(str(exc))


class ModelCard(QGroupBox):
    """One downloadable model, with its own independent download/delete state."""

    download_requested = Signal(str)
    delete_requested = Signal(str)
    use_requested = Signal(str)

    def __init__(self, model: llama.LocalModel) -> None:
        super().__init__(model.name)
        self.model = model
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        detail = QLabel(
            f"{model.tagline}\n"
            f"体积 {_human_size(model.size_bytes)}　·　{model.vram_label}"
        )
        detail.setProperty("role", "hint")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        self._status = QLabel()
        self._status.setProperty("role", "hint")
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # Three buttons plus their margins need roughly 240px; below that they
        # are stacked so none of them is clipped.
        row = ResponsiveRow(threshold=280, spacing=8)
        self._download = QPushButton("下载")
        self._download.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._download.clicked.connect(lambda: self.download_requested.emit(self.model.id))
        self._use = QPushButton("使用")
        self._use.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._use.clicked.connect(lambda: self.use_requested.emit(self.model.id))
        self._delete = QPushButton("删除")
        self._delete.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._delete.setProperty("role", "danger")
        self._delete.clicked.connect(lambda: self.delete_requested.emit(self.model.id))
        row.add(self._download)
        row.add(self._use)
        row.add(self._delete)
        row.add_stretch(1)
        layout.addWidget(row)

        self.refresh(active_id="", busy=False)

    def refresh(self, *, active_id: str, busy: bool) -> None:
        installed = self.model.is_downloaded()
        is_active = installed and self.model.id == active_id

        self._progress.setVisible(False)
        self._download.setVisible(not installed)
        self._use.setVisible(installed)
        # The active model is the one being served; offering "use" again would
        # be a no-op, and deleting it while running is handled by the manager.
        self._use.setEnabled(installed and not is_active)
        self._delete.setVisible(installed)
        self._delete.setEnabled(installed and not busy)

        if is_active:
            self._status.setText("✓ 正在使用")
        elif installed:
            self._status.setText("✓ 已下载，可直接切换使用")
        else:
            self._status.setText("未下载")

    def set_downloading(self, fraction: float, text: str) -> None:
        self._download.setVisible(False)
        self._use.setVisible(False)
        self._delete.setVisible(False)
        self._progress.setVisible(True)
        self._progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        self._status.setText(text)


class SettingsDialog(QDialog):
    def __init__(self, config: ConfigStore, manager: LlamaManager, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._manager = manager
        self._logger = logging.getLogger("prtsbox.settings")
        self._task_thread: QThread | None = None
        self._task: _Task | None = None
        self._close_pending = False
        self._active_card: ModelCard | None = None
        # What the current task is doing, so the completion handler can put the
        # UI back without a closure being invoked on the worker thread.
        self._task_kind: str = ""
        self._task_target: object = None
        self._task_done: Callable[[], None] | None = None

        self.setWindowTitle("设置")
        self.setObjectName("settingsDialog")
        # Matches the main window: panels scroll, so the dialog does not need a
        # minimum tall enough for every tab's content at once.
        self.setMinimumSize(400, 400)
        self.resize(600, 620)
        self.setStyleSheet(stylesheet(tokens_for(self._config.get("theme"))))

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)
        tabs = QTabWidget()
        # Each page scrolls independently: the local-model page is by far the
        # tallest, and without this its model cards would be clipped on a short
        # window while the other pages still had room to spare.
        tabs.addTab(scrollable(self._build_general_tab()), "通用")
        tabs.addTab(scrollable(self._build_local_tab()), "本地模型")
        tabs.addTab(scrollable(self._build_platform_tab()), "翻译平台")
        tabs.addTab(scrollable(self._build_openai_tab()), "AI 大模型 API")
        root.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        # Qt localises its standard buttons from the system locale, which on a
        # Chinese Windows still leaves some of them in English; the text is set
        # explicitly so the dialog is consistent in either case.
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

        self._refresh_local_state()

    # -- tabs ------------------------------------------------------------

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        form = self._form()

        self._theme_combo = shrinkable_combo(QComboBox())
        self._theme_combo.addItem("深色", "dark")
        self._theme_combo.addItem("浅色", "light")
        self._theme_combo.addItem("普瑞赛斯 · 星芒档案", "priestess")
        index = self._theme_combo.findData(self._config.get("theme"))
        self._theme_combo.setCurrentIndex(max(0, index))
        self._theme_combo.currentIndexChanged.connect(self._save_theme)
        form.addRow("主题风格", self._theme_combo)

        self._ocr_combo = shrinkable_combo(QComboBox())
        self._ocr_combo.addItem("自动（推荐）", "auto")
        self._ocr_combo.addItem("ONNX Runtime（内存稳定）", "onnxruntime")
        # OpenVINO is only offered when it is actually installed.  The packaged
        # build leaves it out (242 MB, and it is not the default because of its
        # memory growth), and an option that silently fell back to something
        # else would be worse than no option at all.
        if _openvino_available():
            self._ocr_combo.addItem("OpenVINO（更快，占用会增长）", "openvino")
        index = self._ocr_combo.findData(self._config.get("ocr_backend"))
        self._ocr_combo.setCurrentIndex(max(0, index))
        self._ocr_combo.currentIndexChanged.connect(self._save_ocr_backend)
        form.addRow("文字识别后端", self._ocr_combo)

        ocr_hint = QLabel(
            "ONNX Runtime 内存占用稳定，长时间使用不会增长；OpenVINO 识别快约 2.6 倍，"
            "但遇到变化的文字宽度会持续占用内存（实测约 13 MiB/帧），因此默认不使用。"
        )
        ocr_hint.setProperty("role", "hint")
        ocr_hint.setWordWrap(True)
        form.addRow("", ocr_hint)

        hint = QLabel("主题可在此预览，关闭设置后应用到主界面。语言、布局与字号在主界面调整。")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        form.addRow("", hint)
        layout.addLayout(form)

        paths = QLabel(
            f"配置与模型目录：{self._config.path.parent}\n"
            "所有数据都保存在程序目录内，不会写入系统其他位置。"
        )
        paths.setProperty("role", "hint")
        paths.setWordWrap(True)
        layout.addWidget(paths)
        layout.addStretch(1)
        return page

    @staticmethod
    def _form() -> QFormLayout:
        layout = QFormLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(10)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        return layout

    def _build_local_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        source_group = QGroupBox("下载源")
        source_form = self._form()
        source_group.setLayout(source_form)

        self._source_combo = shrinkable_combo(QComboBox(), minimum_characters=12)
        for value in llama.DOWNLOAD_SOURCES:
            self._source_combo.addItem(llama.SOURCE_LABELS[value], value)
        index = self._source_combo.findData(
            llama.normalise_source(self._config.get("download_source"))
        )
        self._source_combo.setCurrentIndex(max(0, index))
        self._source_combo.currentIndexChanged.connect(self._save_source)
        source_form.addRow("下载源", self._source_combo)

        # The button and the result share a row: the result describes what the
        # button just did, and putting it in a separate label row above (as this
        # first did) reads as if it belonged to the combo box instead.
        source_row = ResponsiveRow(threshold=360, spacing=8)
        self._source_test_button = QPushButton("测试可达性")
        self._source_test_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._source_test_button.clicked.connect(self._test_sources)
        self._source_status = QLabel()
        self._source_status.setProperty("role", "hint")
        self._source_status.setWordWrap(True)
        source_row.add(self._source_test_button, 0)
        source_row.add(self._source_status, 1)
        source_form.addRow("", source_row)

        note = QLabel(
            "不同网络下能连上的源差别很大，连不上时换一个通常就好了。"
            "「测试可达性」会实际请求每个源并列出结果。"
        )
        note.setProperty("role", "hint")
        note.setWordWrap(True)
        source_form.addRow("", note)
        layout.addWidget(source_group)

        runtime_group = QGroupBox("推理运行时（llama.cpp）")
        runtime_layout = QVBoxLayout(runtime_group)
        runtime_layout.setSpacing(9)

        self._runtime_status = QLabel()
        self._runtime_status.setWordWrap(True)
        runtime_layout.addWidget(self._runtime_status)

        # Stacks when narrow so the button cannot squeeze the variant list.
        runtime_row = ResponsiveRow(threshold=340, spacing=9)
        self._variant_combo = shrinkable_combo(QComboBox(), minimum_characters=10)
        for variant in llama.RUNTIME_VARIANTS:
            self._variant_combo.addItem(
                f"{variant.name}　·　{_human_size(variant.size_bytes)}", variant.id
            )
        preferred = llama.recommend_variant()
        preferred_index = self._variant_combo.findData(preferred.id)
        self._variant_combo.setCurrentIndex(max(0, preferred_index))
        self._runtime_button = QPushButton("下载运行时")
        self._runtime_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._runtime_button.clicked.connect(self._download_runtime)
        runtime_row.add(self._variant_combo, 1)
        runtime_row.add(self._runtime_button, 0)
        runtime_layout.addWidget(runtime_row)

        self._runtime_progress = QProgressBar()
        self._runtime_progress.setRange(0, 1000)
        self._runtime_progress.setTextVisible(False)
        self._runtime_progress.setVisible(False)
        runtime_layout.addWidget(self._runtime_progress)

        note = QLabel(
            "推荐 Vulkan 版本：NVIDIA / AMD / Intel 通用且体积最小。"
            "llama-server 会被本程序作为子进程管理，退出时自动结束。"
        )
        note.setProperty("role", "hint")
        note.setWordWrap(True)
        runtime_layout.addWidget(note)
        layout.addWidget(runtime_group)

        models_group = QGroupBox("翻译模型")
        models_layout = QVBoxLayout(models_group)
        self._cards: dict[str, ModelCard] = {}
        for model in llama.MODELS:
            card = ModelCard(model)
            card.download_requested.connect(self._download_model)
            card.delete_requested.connect(self._delete_model)
            card.use_requested.connect(self._use_model)
            self._cards[model.id] = card
            models_layout.addWidget(card)
        layout.addWidget(models_group)

        license_note = QLabel(
            "模型为腾讯混元 Hy-MT2（Q4_K_M 量化），遵循腾讯混元社区许可。"
            "两个模型互相独立，可分别下载或删除。"
        )
        license_note.setProperty("role", "hint")
        license_note.setWordWrap(True)
        layout.addWidget(license_note)
        layout.addStretch(1)
        return page

    def _build_platform_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        self._platform_combo = shrinkable_combo(QComboBox())
        for key, name in PLATFORMS.items():
            self._platform_combo.addItem(name, key)
        layout.addWidget(self._platform_combo)
        self._platform_stack = QStackedWidget()
        self._platform_fields: dict[str, QLineEdit | QComboBox] = {}
        fields = {
            "azure": [("azure_api_key", "订阅密钥", "Azure Translator Key"),
                      ("azure_region", "资源区域", "例如 eastasia；全局资源可留空"),
                      ("azure_endpoint", "接口地址", AZURE_ENDPOINT)],
            "deepl": [("deepl_api_key", "API 密钥", "DeepL API Key")],
            "baidu": [("baidu_app_id", "APP ID", "百度翻译开放平台 APP ID"),
                      ("baidu_secret_key", "密钥", "通用文本翻译密钥")],
        }
        hints = {
            "azure": "区域须与 Azure 资源一致。默认使用全球接口，也可填写资源的自定义接口地址。",
            "deepl": "请使用 DeepL API 的密钥。Free 与 Pro 使用不同的接口地址，需与账户类型一致。",
            "baidu": "请先开通百度翻译开放平台的通用文本翻译服务。请求按每秒最多一次发送。",
        }
        for provider in PLATFORMS:
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(0, 8, 0, 0)
            form = self._form()
            for key, label, placeholder in fields[provider]:
                secret = key in PLATFORM_SECRETS
                value = self._config.get_secret(key) if secret else str(self._config.get(key) or "")
                edit = expanding(QLineEdit(value))
                edit.setPlaceholderText(placeholder)
                if secret:
                    edit.setEchoMode(QLineEdit.EchoMode.Password)
                self._platform_fields[key] = edit
                edit.editingFinished.connect(self._save_platform)
                form.addRow(label, edit)
            if provider == "deepl":
                plan = shrinkable_combo(QComboBox())
                plan.addItem("DeepL API Free", "free")
                plan.addItem("DeepL API Pro", "pro")
                plan.setCurrentIndex(max(0, plan.findData(self._config.get("deepl_plan"))))
                plan.currentIndexChanged.connect(self._save_platform)
                self._platform_fields["deepl_plan"] = plan
                form.addRow("账户类型", plan)
            panel_layout.addLayout(form)
            hint = QLabel(hints[provider])
            hint.setWordWrap(True)
            hint.setProperty("role", "hint")
            panel_layout.addWidget(hint)
            panel_layout.addStretch(1)
            self._platform_stack.addWidget(panel)
        index = max(0, self._platform_combo.findData(self._config.get("translation_platform")))
        self._platform_combo.setCurrentIndex(index)
        self._platform_stack.setCurrentIndex(index)
        self._platform_combo.currentIndexChanged.connect(self._platform_stack.setCurrentIndex)
        self._platform_combo.currentIndexChanged.connect(self._save_platform)
        layout.addWidget(self._platform_stack)
        hint = QLabel("配置自动保存，密钥使用 Windows DPAPI 加密。使用前请在主界面选择“翻译平台”。")
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)
        return page

    def _save_platform(self) -> None:
        self._config.set("translation_platform", self._platform_combo.currentData())
        for key, widget in self._platform_fields.items():
            value = widget.currentData() if isinstance(widget, QComboBox) else widget.text().strip()
            if key in PLATFORM_SECRETS:
                self._config.set_secret(key, value)
            else:
                self._config.set(key, value)
        self._config.save()

    def _build_openai_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        form = self._form()

        self._base_url = QLineEdit(str(self._config.get("openai_base_url") or ""))
        self._base_url.setPlaceholderText("https://api.openai.com/v1")
        self._base_url.editingFinished.connect(self._save_openai)
        expanding(self._base_url)
        form.addRow("接口地址", self._base_url)

        self._model_name = QLineEdit(str(self._config.get("openai_model") or ""))
        self._model_name.setPlaceholderText("gpt-4o-mini / deepseek-chat / ...")
        self._model_name.editingFinished.connect(self._save_openai)
        expanding(self._model_name)
        form.addRow("模型名称", self._model_name)

        self._api_key = QLineEdit(self._config.get_secret("openai_api_key"))
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("sk-...")
        self._api_key.editingFinished.connect(self._save_openai)
        expanding(self._api_key)
        form.addRow("API Key", self._api_key)
        layout.addLayout(form)

        self._key_hint = QLabel()
        self._key_hint.setProperty("role", "hint")
        self._key_hint.setWordWrap(True)
        layout.addWidget(self._key_hint)

        hint = QLabel(
            "API Key 使用 Windows DPAPI 加密后保存在本机配置文件中，"
            "换一台机器或换一个用户账户都无法解密。"
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        self._refresh_key_hint()
        return page

    def _refresh_key_hint(self) -> None:
        self._key_hint.setText(
            "✓ 已保存 API Key" if self._config.has_secret("openai_api_key") else "尚未填写 API Key"
        )

    # -- persistence -----------------------------------------------------

    @Slot()
    def _save_theme(self) -> None:
        self._config.set("theme", self._theme_combo.currentData())
        self._config.save()
        self.setStyleSheet(stylesheet(tokens_for(self._config.get("theme"))))

    @Slot()
    def _save_ocr_backend(self) -> None:
        self._config.set("ocr_backend", self._ocr_combo.currentData())
        self._config.save()

    @Slot()
    def _save_openai(self) -> None:
        self._config.update(
            openai_base_url=self._base_url.text().strip(),
            openai_model=self._model_name.text().strip(),
        )
        self._config.set_secret("openai_api_key", self._api_key.text().strip())
        self._config.save()
        self._refresh_key_hint()

    # -- local state -----------------------------------------------------

    def _refresh_local_state(self, *, busy: bool = False) -> None:
        busy = busy or self._task_thread is not None
        self._source_test_button.setEnabled(not busy)
        variant = llama.find_variant(self._variant_combo.currentData())
        installed = self._manager.is_runtime_installed()
        chosen_installed = variant is not None and variant.is_installed()

        if self._manager.is_server_running():
            running = self._manager.running_model_id
            self._runtime_status.setText(f"✓ 运行时运行中（模型：{running or '未知'}）")
        elif installed:
            names = "、".join(v.name for v in llama.installed_variants())
            self._runtime_status.setText(f"✓ 运行时已安装：{names}")
        else:
            self._runtime_status.setText("尚未安装运行时，本地翻译无法启动。")

        self._runtime_button.setVisible(not chosen_installed)
        self._runtime_button.setEnabled(not busy and not chosen_installed)
        self._variant_combo.setEnabled(not busy and not installed)

        active = str(self._config.get("local_model") or "")
        for card in self._cards.values():
            card.refresh(active_id=active, busy=busy)

    # -- tasks -----------------------------------------------------------

    def _start_task(self, action, *, kind: str, target, on_done) -> None:
        """Run one download at a time; the archives are large enough that
        overlapping them would only slow both down.

        Completion and progress are delivered to bound slots on this dialog
        rather than to plain callables.  A plain callable is invoked *directly*
        on whichever thread emits the signal, which is the worker thread here -
        and that caused two faults at once: the handlers touch Qt widgets, which
        is only legal on the GUI thread, and the completion handler released the
        last reference to the ``_Task`` object while its own signal was still
        being emitted, deleting the C++ object out from under the call.  That
        second one is a hard crash (0xC0000409) the moment a download finished.
        """
        if self._task_thread is not None:
            return
        task = _Task(action)
        thread = QThread()
        task.moveToThread(thread)
        thread.started.connect(task.run)
        task.finished.connect(self._on_task_finished)
        task.failed.connect(self._on_task_failed)
        task.progressed.connect(self._on_task_progressed)
        task.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        task.failed.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(task.deleteLater)
        thread.finished.connect(self._on_task_thread_finished)
        self._task = task
        self._task_thread = thread
        self._task_kind = kind
        self._task_target = target
        self._task_done = on_done
        self._refresh_local_state(busy=True)
        thread.start()

    @Slot()
    def _on_task_finished(self) -> None:
        callback, self._task_done = self._task_done, None
        self._finish_task()
        if callback is not None:
            callback()

    @Slot(str)
    def _on_task_failed(self, message: str) -> None:
        if not self._task_kind:
            # A late queued signal from a task that has already been reported;
            # acting on it would overwrite the current state with stale text.
            return
        self._logger.warning("下载任务结束：%s", message)
        self._task_done = None
        self._finish_task()
        self._refresh_local_state()
        self._runtime_status.setText(f"下载未完成：{message}")

    @Slot()
    def _save_source(self) -> None:
        preference = self._source_combo.currentData()
        self._config.set("download_source", preference)
        self._config.save()
        # The manager holds the live value used by the next download, so it has
        # to be told; waiting until the dialog closes would leave a download
        # started in this session on the previous source.
        self._manager.set_source_preference(str(preference))
        self._source_status.setText("")

    @Slot()
    def _test_sources(self) -> None:
        """Request every source for the selected model and report which answer.

        This is the only reliable way to choose: a source that is fast on one
        network is unreachable on another, and the failure otherwise only shows
        up at the end of a multi-gigabyte download.
        """
        if self._task_thread is not None:
            self._source_status.setText("正在下载，无法同时测试。")
            return

        model = llama.find_model(str(self._config.get("local_model") or "")) or llama.default_model()
        self._source_test_button.setEnabled(False)
        self._source_status.setText(f"正在测试 {model.filename} 的各下载源…")

        targets = model.sources_with_labels()
        results: list[str] = []

        def action(cancel, emit_progress):
            for label, url in targets:
                if cancel.is_set():
                    return
                started = time.perf_counter()
                ok, detail = llama.probe_source(url)
                elapsed = time.perf_counter() - started
                mark = "✓" if ok else "✗"
                suffix = f"{detail}　{elapsed:.1f}s" if ok else detail
                results.append(f"{mark} {label}　{suffix}")
                emit_progress(DownloadProgress(len(results), len(targets), 0.0))

        def done() -> None:
            self._source_status.setText("　·　".join(results) if results else "测试未完成")
            self._source_test_button.setEnabled(True)

        self._start_task(action, kind="probe", target=model, on_done=done)

    @Slot(object)
    def _on_task_progressed(self, progress: DownloadProgress) -> None:
        """Route a progress report to whichever widget the task is driving."""
        if self._task_kind == "runtime":
            variant = self._task_target
            name = getattr(variant, "name", "")
            self._runtime_progress.setValue(int(progress.fraction * 1000))
            self._runtime_status.setText(
                f"正在下载 {name}　{progress.fraction * 100:.0f}%　"
                f"{_human_size(progress.bytes_per_second)}/s"
            )
        elif self._task_kind == "model" and self._active_card is not None:
            eta = progress.eta_seconds
            suffix = f"　剩余 {eta:.0f}s" if eta > 1 else ""
            self._active_card.set_downloading(
                progress.fraction,
                f"下载中 {progress.fraction * 100:.0f}%　"
                f"{_human_size(progress.bytes_per_second)}/s{suffix}",
            )
        elif self._task_kind == "probe" and progress.total:
            self._source_status.setText(f"正在测试下载源…　{progress.downloaded}/{progress.total}")

    def _finish_task(self) -> None:
        """Tear down the worker thread without destroying the task mid-emission.

        ``_Task`` is the object whose signal led here, so dropping the reference
        now would delete it while ``emit`` is still unwinding.  It is kept until
        the thread has genuinely stopped, and the thread reference is held until
        then too, which also stops a new download from starting and replacing the
        task while the old thread is still running.
        """
        self._task_kind = ""
        self._task_target = None
        self._active_card = None
        self._runtime_progress.setVisible(False)

        thread = self._task_thread
        if thread is None:
            return
        thread.quit()

    @Slot()
    def _on_task_thread_finished(self) -> None:
        thread = self._task_thread
        if thread is None:
            return
        self._task_thread = None
        self._task = None
        thread.deleteLater()
        self._refresh_local_state()
        if self._close_pending:
            super().reject()

    @Slot()
    def _download_runtime(self) -> None:
        if self._task_thread is not None:
            return
        variant = llama.find_variant(self._variant_combo.currentData())
        if variant is None:
            return
        self._runtime_button.setEnabled(False)
        self._runtime_progress.setVisible(True)
        self._runtime_progress.setValue(0)
        self._runtime_status.setText(f"正在下载 {variant.name}…")

        def action(cancel, emit_progress):
            self._manager.install_runtime(variant, on_progress=emit_progress, cancel=cancel)

        def done() -> None:
            self._refresh_local_state()

        self._start_task(action, kind="runtime", target=variant, on_done=done)

    @Slot(str)
    def _download_model(self, model_id: str) -> None:
        if self._task_thread is not None:
            return
        model = llama.find_model(model_id)
        card = self._cards.get(model_id)
        if model is None or card is None:
            return
        self._active_card = card
        card.set_downloading(0.0, "准备下载…")

        def action(cancel, emit_progress):
            self._manager.download_model(model, on_progress=emit_progress, cancel=cancel)

        def done() -> None:
            self._refresh_local_state()

        self._start_task(action, kind="model", target=model, on_done=done)

    @Slot(str)
    def _delete_model(self, model_id: str) -> None:
        if self._task_thread is not None:
            return
        model = llama.find_model(model_id)
        if model is None:
            return
        self._manager.delete_model(model)
        # Deleting the model in use leaves the config pointing at something
        # that is no longer on disk; move to whatever is still available.
        if str(self._config.get("local_model") or "") == model_id:
            remaining = llama.downloaded_models()
            self._config.set("local_model", remaining[0].id if remaining else "")
            self._config.save()
        self._refresh_local_state()

    @Slot(str)
    def _use_model(self, model_id: str) -> None:
        self._config.set("local_model", model_id)
        self._config.save()
        self._refresh_local_state()

    def reject(self) -> None:
        self._save_platform()
        self._save_openai()
        if self._task_thread is not None:
            self._close_pending = True
            self._task_done = None
            self.cancel_task()
            self._runtime_status.setText("正在取消任务，完成后自动关闭…")
            self.setEnabled(False)
            return
        super().reject()

    def cancel_task(self) -> None:
        """Request cancellation without blocking the GUI event loop."""
        if self._task is not None:
            self._task.cancel.set()
        if self._task_thread is not None:
            self._task_thread.quit()

    def shutdown_task(self, timeout_ms: int) -> bool:
        self.cancel_task()
        thread = self._task_thread
        return thread is None or not thread.isRunning() or thread.wait(timeout_ms)
