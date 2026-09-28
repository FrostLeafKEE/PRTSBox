"""Main window: pick a window, pick an engine, translate."""

from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import (
    QMetaObject,
    QObject,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import llama
from ..config import REGION_PERCENT_MAX, REGION_PERCENT_MIN, ConfigStore
from ..llama import LlamaManager
from ..models import (
    LANGUAGES,
    TARGET_LANGUAGES,
    LayoutMode,
    TranslatorConfig,
    WindowInfo,
    is_chinese_language,
    language_name,
)
from ..overlay import TranslationOverlay
from ..pipeline import FrameResult, PipelineWorker
from ..translate import create_translator
from ..translate.platform import PLATFORMS, platform_credentials
from ..win32 import (
    enable_dpi_awareness,
    install_hotkey_hook,
    list_windows,
    uninstall_hotkey_hook,
)
from .layout import ResponsiveRow, scrollable, shrinkable_combo, scroll_page_on_wheel
from .desktop_pet import DesktopPet
from .settings_dialog import SettingsDialog
from .theme import stylesheet, tokens_for

# The measured round trip for a frame of unseen text is ~820 ms; a slightly
# longer period keeps the worker from being asked for frames it cannot start.
_FRAME_INTERVAL_MS = 900

# How long a single frame may be in flight before the UI assumes the reply is
# lost.  Generous enough for a cold 7B model to answer a large batch, short
# enough that a hang is reported rather than left silent.
_WORKER_STALL_SECONDS = 45.0


class PrepareWorker(QObject):
    """Loads the model and starts llama-server without blocking the UI.

    A cold 7B load takes ~4.2 s, which would freeze the window if it ran on the
    UI thread.
    """

    finished = Signal()
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, config: TranslatorConfig, manager: LlamaManager) -> None:
        super().__init__()
        self._config = config
        self._manager = manager
        self._logger = logging.getLogger("prtsbox.prepare")

    @Slot()
    def run(self) -> None:
        try:
            translator = create_translator(self._config, llama_manager=self._manager)
            translator.preflight()
            if self._config.engine == "local":
                model = llama.resolve_model(self._config.model_id)
                self.status.emit(f"正在加载模型：{model.name}")
                # Reuses the running server when the model has not changed.
                self._manager.ensure_server(model)
            self.finished.emit()
        except Exception as exc:
            self._logger.exception("启动翻译失败")
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    # Frame dispatch to the worker thread.
    #
    # This is a signal rather than QMetaObject.invokeMethod on purpose.
    # invokeMethod needs every argument described by a Q_ARG whose type name Qt
    # can resolve, and Q_ARG(object, ...) raises
    # "Unable to find a QMetaType for object" in PySide6 - it never reaches the
    # receiver.  Because the busy flag is set before the call and cleared only
    # when a frame comes back, that exception left the pipeline waiting forever
    # and the app stuck on its first status message.  A queued signal connection
    # crosses the thread boundary without any type-name marshalling.
    dispatch_frame = Signal(object, object)

    def __init__(self, config: ConfigStore, llama_manager: LlamaManager) -> None:
        super().__init__()
        self._config = config
        self._llama = llama_manager
        self._logger = logging.getLogger("prtsbox.ui")
        self._running = False
        self._worker_busy = False
        self._session_id = 0
        self._prepare_thread: QThread | None = None
        self._prepare_worker: PrepareWorker | None = None
        self._selected_window: WindowInfo | None = None
        self._last_result: FrameResult | None = None
        self._busy_since = 0.0
        self._shutting_down = False
        self._tokens = tokens_for(self._config.get("theme"))

        self.setWindowTitle("PRTSBox · 实时窗口翻译")
        # The minimum is deliberately smaller than the content's natural height.
        # Panels scroll when the window is short, so a small minimum is now safe
        # and keeps the window usable on a cramped display.  The width floor is
        # what the widest unbreakable row needs; below it the content would be
        # clipped rather than scrolled, since the horizontal bar is disabled.
        self.setMinimumSize(400, 420)
        self.resize(580, 720)

        self._overlay = TranslationOverlay()
        self._overlay.set_colors(self._tokens.overlay_colors())
        self._pet = DesktopPet()

        self._build_ui()
        self._pet.translation_requested.connect(self._start_button.click)
        self._pet.main_window_requested.connect(self._show_main_window)
        self._pet.menu_requested.connect(self._sync_pet_state)
        self._pet.visibility_changed.connect(
            lambda visible: self._pet_button.setText("关闭桌宠" if visible else "显示桌宠")
        )
        self._start_pipeline_thread()

        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

        # Installed only now: reporting a failure before the attempt is made
        # would show an error for a hotkey that works perfectly well.
        self._hotkey_ok = install_hotkey_hook(self._on_hotkey)
        self.refresh_windows()
        self._apply_theme()
        self._sync_engine_controls()
        if not self._hotkey_ok:
            self._set_status("F8 全局热键被系统拒绝，请使用界面按钮控制", error=True)

    # -- construction ----------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(14)

        # Title and the action area stay outside the scroll area: the settings
        # button and the start button are the two controls a user needs at any
        # window size, so they must not scroll out of reach.
        root.addWidget(self._build_header())
        root.addWidget(self._build_settings_area(), 1)
        root.addWidget(self._build_action_area())
        self.setCentralWidget(central)

    def _build_settings_area(self) -> QScrollArea:
        """The configurable panels, scrollable when the window is too short.

        Before this existed the window's explicit minimum height was smaller
        than the content needed (600px against 873px), so Qt compressed the
        layout and QFormLayout rows overlapped each other.  A scroll area lets
        the content keep its natural height instead, which removes the
        compression rather than hiding it behind a larger minimum size.
        """
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._build_target_group())
        layout.addWidget(self._build_engine_group())
        layout.addWidget(self._build_output_group())
        layout.addStretch(1)
        return scrollable(content)

    def _build_header(self) -> QWidget:
        row = ResponsiveRow(threshold=300, spacing=10)

        titles = QWidget()
        titles_layout = QVBoxLayout(titles)
        titles_layout.setContentsMargins(0, 0, 0, 0)
        titles_layout.setSpacing(3)
        title = QLabel("实时窗口翻译")
        title.setProperty("role", "title")
        subtitle = QLabel("本地模型离线翻译，译文覆盖在目标窗口上方")
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        titles_layout.addWidget(title)
        titles_layout.addWidget(subtitle)

        settings_button = QPushButton("设置")
        settings_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        settings_button.clicked.connect(self.open_settings)

        row.add(titles, 1)
        row.add(settings_button, 0)
        return row

    @staticmethod
    def _form() -> QFormLayout:
        """A form layout with the spacing the theme expects."""
        layout = QFormLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(8)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # AllNonFixedFieldsGrow gives every spare pixel to the field column, so
        # the label column keeps only the width its text needs and the inputs
        # stretch with the window.
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        return layout

    def _build_target_group(self) -> QGroupBox:
        group = QGroupBox("目标窗口")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)

        # Stacks on a narrow window so the button never squeezes the combo down
        # to an unreadable width.
        row = ResponsiveRow(threshold=320, spacing=9)
        self._window_combo = shrinkable_combo(QComboBox(), minimum_characters=14)
        self._window_combo.currentIndexChanged.connect(self._on_window_changed)
        refresh = QPushButton("刷新")
        refresh.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        refresh.clicked.connect(self.refresh_windows)
        row.add(self._window_combo, 1)
        row.add(refresh, 0)
        layout.addWidget(row)

        hint = QLabel("选择要翻译的窗口；译文显示在该窗口上方，鼠标可以点穿。")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Restricting the region matters for text-heavy games, which usually put
        # the dialogue in a box at the bottom: reading the whole window also
        # picks up lettering painted on artwork and clothing, and translating
        # that is both wrong and distracting.
        region_row = ResponsiveRow(threshold=340, spacing=8)
        self._region_check = QCheckBox("只识别窗口下方")
        self._region_check.toggled.connect(self._on_region_changed)
        self._region_spin = QSpinBox()
        scroll_page_on_wheel(self._region_spin)
        self._region_spin.setRange(REGION_PERCENT_MIN, REGION_PERCENT_MAX)
        self._region_spin.setSuffix(" %")
        self._region_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._region_spin.valueChanged.connect(self._on_region_changed)
        region_row.add(self._region_check, 1)
        region_row.add(self._region_spin, 0)
        layout.addWidget(region_row)
        return group

    def _build_engine_group(self) -> QGroupBox:
        group = QGroupBox("翻译引擎")
        layout = self._form()

        self._engine_combo = shrinkable_combo(QComboBox())
        self._engine_combo.addItem("本地模型（免费，无需联网）", "local")
        self._engine_combo.addItem("AI 大模型 API（OpenAI 兼容）", "openai")
        self._engine_combo.addItem("翻译平台", "platform")
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        layout.addRow("引擎", self._engine_combo)

        self._platform_combo = shrinkable_combo(QComboBox())
        for key, name in PLATFORMS.items():
            self._platform_combo.addItem(name, key)
        self._platform_combo.setCurrentIndex(max(0, self._platform_combo.findData(self._config.get("translation_platform"))))
        self._platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        self._platform_row_label = QLabel("翻译平台")
        layout.addRow(self._platform_row_label, self._platform_combo)

        self._model_combo = shrinkable_combo(QComboBox())
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._model_row_label = QLabel("本地模型")
        layout.addRow(self._model_row_label, self._model_combo)

        self._local_hint = QLabel()
        self._local_hint.setProperty("role", "hint")
        self._local_hint.setWordWrap(True)
        layout.addRow("", self._local_hint)

        self._source_combo = shrinkable_combo(QComboBox())
        for code, name in LANGUAGES.items():
            self._source_combo.addItem(name, code)
        self._target_combo = shrinkable_combo(QComboBox())
        for code in TARGET_LANGUAGES:
            self._target_combo.addItem(language_name(code), code)
        layout.addRow("源语言", self._source_combo)
        layout.addRow("目标语言", self._target_combo)

        self._source_combo.currentIndexChanged.connect(self._save_languages)
        self._target_combo.currentIndexChanged.connect(self._save_languages)

        group.setLayout(layout)
        return group

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox("译文显示")
        layout = self._form()

        self._layout_combo = shrinkable_combo(QComboBox())
        self._layout_combo.addItem("原文下方", LayoutMode.BELOW.value)
        self._layout_combo.addItem("原文右侧", LayoutMode.RIGHT.value)
        self._layout_combo.currentIndexChanged.connect(self._on_display_changed)
        layout.addRow("布局", self._layout_combo)

        self._font_spin = QSpinBox()
        scroll_page_on_wheel(self._font_spin)
        self._font_spin.setRange(9, 28)
        # A spin box has no reason to stretch; it holds two digits.
        self._font_spin.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._font_spin.valueChanged.connect(self._on_display_changed)
        layout.addRow("字号", self._font_spin)

        self._skip_chinese_check = QCheckBox("已是中文的文本不翻译")
        self._skip_chinese_check.setToolTip(
            "目标语言为中文时生效：识别结果本身是中文的行不会被送去翻译，"
            "避免把原文改写一遍。目标为其他语言时该选项不起作用。"
        )
        self._skip_chinese_check.toggled.connect(self._on_display_changed)
        layout.addRow("", self._skip_chinese_check)

        self._source_check = QCheckBox("同时显示原文")
        self._source_check.toggled.connect(self._on_display_changed)
        layout.addRow("", self._source_check)

        self._latency_check = QCheckBox("显示耗时")
        self._latency_check.toggled.connect(self._on_display_changed)
        layout.addRow("", self._latency_check)

        # Capture exclusion is a Windows display affinity rather than a Qt flag,
        # so this checkbox is the only place it can be turned off.
        self._capture_check = QCheckBox("译文可被截图 / 直播捕捉")
        self._capture_check.setToolTip(
            "默认关闭：译文层使用 Windows 捕获排除，不会出现在截图、录屏和直播画面里。"
            "开启后截图工具和 OBS 等采集软件就能看到译文。\n"
            "识别读的是目标窗口自身的内容，因此开启不会把译文当成原文重复翻译。"
        )
        self._capture_check.toggled.connect(self._on_display_changed)
        layout.addRow("", self._capture_check)

        group.setLayout(layout)
        return group

    def _build_action_area(self) -> QWidget:
        area = QWidget()
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._start_button = QPushButton("开始实时翻译　·　F8 开关")
        self._start_button.setProperty("role", "primary")
        self._start_button.clicked.connect(self.toggle_running)
        layout.addWidget(self._start_button)

        self._pet_button = QPushButton("显示桌宠")
        self._pet_button.clicked.connect(self._toggle_pet)
        layout.addWidget(self._pet_button)

        self._status_label = QLabel("就绪")
        self._status_label.setProperty("role", "status")
        self._status_label.setWordWrap(True)
        # No minimum height: the label sizes itself to its text, and a fixed
        # floor would waste vertical space in a short window.
        layout.addWidget(self._status_label)
        return area

    def _toggle_pet(self) -> None:
        if self._pet.isVisible():
            self._pet.close()
        else:
            self._sync_pet_state()
            self._pet.show()

    def _show_main_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _sync_pet_state(self) -> None:
        self._pet.set_translation_state(
            self._running, self._prepare_thread is not None,
            self._selected_window is not None and self._start_button.isEnabled(),
        )

    def _start_pipeline_thread(self) -> None:
        self._thread = QThread()
        backend = str(self._config.get("ocr_backend") or "auto")
        self._pipeline = PipelineWorker(self._llama, ocr_backend=backend)
        self._pipeline.moveToThread(self._thread)
        self._thread.started.connect(self._pipeline.initialize)
        self._pipeline.ready.connect(self._on_pipeline_ready)
        self._pipeline.failed.connect(self._on_pipeline_failed)
        self._pipeline.status.connect(self._set_status)
        self._pipeline.frame.connect(self._on_frame)
        # Queued because the worker lives on its own thread; this is the only
        # path that hands work to it.
        self.dispatch_frame.connect(
            self._pipeline.process, Qt.ConnectionType.QueuedConnection
        )
        self._thread.start()

    # -- settings --------------------------------------------------------

    def open_settings(self) -> None:
        previous_backend = str(self._config.get("ocr_backend") or "auto")
        dialog = SettingsDialog(self._config, self._llama, self)
        dialog.exec()
        self._platform_combo.setCurrentIndex(max(0, self._platform_combo.findData(self._config.get("translation_platform"))))
        self._tokens = tokens_for(self._config.get("theme"))
        self._overlay.set_colors(self._tokens.overlay_colors())
        self._apply_theme()
        self._reload_model_list()
        if str(self._config.get("ocr_backend") or "auto") != previous_backend:
            # Rebuilding the OCR service takes about a second, so it happens on
            # the next frame rather than freezing the UI while the dialog closes.
            self._pipeline.set_ocr_backend(str(self._config.get("ocr_backend")))
        if self._running:
            # Engine or credentials may have changed under a running session.
            self._pipeline.invalidate_cache()

    def _apply_theme(self) -> None:
        self.setStyleSheet(stylesheet(self._tokens))

    # -- state -----------------------------------------------------------

    def refresh_windows(self) -> None:
        previous = self._selected_window.hwnd if self._selected_window else 0
        saved = int(self._config.get("window_hwnd") or 0)
        exclude = {int(self._overlay.winId()), int(self._pet.winId())}
        windows = list_windows(exclude=exclude)

        self._window_combo.blockSignals(True)
        self._window_combo.clear()
        for window in windows:
            label = window.title.strip() or f"未命名窗口 ({window.hwnd})"
            self._window_combo.addItem(f"{label}　·　{window.width}×{window.height}", window.hwnd)
        self._window_combo.blockSignals(False)

        target = previous or saved
        if target:
            index = self._window_combo.findData(target)
            if index >= 0:
                self._window_combo.setCurrentIndex(index)
                self._on_window_changed(index)
                return
        if windows:
            self._window_combo.setCurrentIndex(0)
            self._on_window_changed(0)
        else:
            self._selected_window = None
            self._set_status("没有找到可翻译的窗口", error=True)

    @Slot(int)
    def _on_window_changed(self, index: int) -> None:
        hwnd = self._window_combo.itemData(index) if index >= 0 else None
        if not hwnd:
            self._selected_window = None
            return
        for window in list_windows(exclude={int(self._overlay.winId()), int(self._pet.winId())}):
            if window.hwnd == hwnd:
                if self._selected_window is None or self._selected_window.hwnd != hwnd:
                    self._session_id += 1
                    self._last_result = None
                    self._overlay.hide_overlay()
                self._selected_window = window
                self._config.update(window_hwnd=hwnd, window_title=window.title)
                self._config.save()
                return

    @Slot(int)
    def _on_engine_changed(self, _index: int) -> None:
        self._config.set("engine", self._engine_combo.currentData())
        self._config.save()
        self._sync_engine_controls()
        if self._running:
            self._pipeline.invalidate_cache()

    @Slot(int)
    def _on_model_changed(self, _index: int) -> None:
        model_id = self._model_combo.currentData()
        if model_id:
            self._config.set("local_model", model_id)
            self._config.save()
            if self._running:
                self._pipeline.invalidate_cache()

    def _sync_engine_controls(self) -> None:
        is_local = self._engine_combo.currentData() == "local"
        is_platform = self._engine_combo.currentData() == "platform"
        self._platform_combo.setVisible(is_platform)
        self._platform_row_label.setVisible(is_platform)
        self._model_combo.setVisible(is_local)
        self._model_row_label.setVisible(is_local)
        self._local_hint.setVisible(is_local)
        if is_local:
            self._reload_model_list()

    def _on_platform_changed(self) -> None:
        self._config.set("translation_platform", self._platform_combo.currentData())
        self._config.save()
        if self._running:
            self._pipeline.invalidate_cache()

    def _reload_model_list(self) -> None:
        ready = llama.downloaded_models()
        current = str(self._config.get("local_model") or "")

        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for model in ready:
            self._model_combo.addItem(f"{model.name}　·　{model.vram_label}", model.id)
        self._model_combo.blockSignals(False)

        if not ready:
            self._local_hint.setText("尚未下载本地模型，请点击「设置」→「本地模型」下载。")
            return

        index = self._model_combo.findData(current)
        self._model_combo.setCurrentIndex(max(index, 0))
        chosen = llama.find_model(self._model_combo.currentData())
        if chosen is not None and chosen.id != current:
            self._config.set("local_model", chosen.id)
            self._config.save()
        if not self._llama.is_runtime_installed():
            self._local_hint.setText("本地模型已就绪，但还缺少推理运行时，请在「设置」中下载。")
        else:
            self._local_hint.setText("本地翻译完全离线运行，不消耗 API 额度。")

    @Slot()
    def _on_region_changed(self) -> None:
        enabled = self._region_check.isChecked()
        self._region_spin.setEnabled(enabled)
        self._config.update(
            region_bottom_only=enabled,
            region_bottom_percent=self._region_spin.value(),
        )
        self._config.save()
        if self._running:
            # Crop only affects recognition, so nothing cached becomes wrong;
            # the next frame simply reads a different area.
            self._set_status(
                f"识别区域：窗口下方 {self._region_spin.value()}%" if enabled else "识别区域：整个窗口"
            )

    @Slot()
    def _save_languages(self) -> None:
        self._config.update(
            source_language=self._source_combo.currentData(),
            target_language=self._target_combo.currentData(),
        )
        self._config.save()
        self._sync_skip_chinese()
        if self._running:
            self._pipeline.invalidate_cache()

    def _sync_skip_chinese(self) -> None:
        """Enable the Chinese-skip option only where it can do anything.

        With an English target, Chinese source text is exactly what the user
        wants translated, so a checkbox that silently did nothing would be a
        lie.  Disabling it with a tooltip explains the rule instead.
        """
        applies = is_chinese_language(str(self._target_combo.currentData() or ""))
        self._skip_chinese_check.setEnabled(applies)
        self._skip_chinese_check.setToolTip(
            "目标语言是中文时，识别结果本身为中文的行不会被送去翻译。"
            if applies
            else "当前目标语言不是中文，中文原文需要翻译，因此该选项不生效。"
        )

    @Slot()
    def _on_display_changed(self) -> None:
        self._config.update(
            layout_mode=self._layout_combo.currentData(),
            overlay_font_size=self._font_spin.value(),
            show_source_text=self._source_check.isChecked(),
            show_latency=self._latency_check.isChecked(),
            skip_chinese=self._skip_chinese_check.isChecked(),
            overlay_capturable=self._capture_check.isChecked(),
        )
        self._config.save()
        if self._last_result is not None and self._selected_window is not None:
            self._render(self._last_result)

    def load_from_config(self) -> None:
        for key, combo in (
            ("engine", self._engine_combo),
            ("translation_platform", self._platform_combo),
            ("source_language", self._source_combo),
            ("target_language", self._target_combo),
            ("layout_mode", self._layout_combo),
        ):
            index = combo.findData(self._config.get(key))
            if index >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
        self._font_spin.blockSignals(True)
        self._font_spin.setValue(int(self._config.get("overlay_font_size")))
        self._font_spin.blockSignals(False)
        self._source_check.blockSignals(True)
        self._source_check.setChecked(bool(self._config.get("show_source_text")))
        self._source_check.blockSignals(False)
        self._skip_chinese_check.blockSignals(True)
        self._skip_chinese_check.setChecked(bool(self._config.get("skip_chinese")))
        self._skip_chinese_check.blockSignals(False)

        self._region_check.blockSignals(True)
        self._region_check.setChecked(bool(self._config.get("region_bottom_only")))
        self._region_check.blockSignals(False)
        self._region_spin.blockSignals(True)
        self._region_spin.setValue(int(self._config.get("region_bottom_percent")))
        self._region_spin.blockSignals(False)
        self._region_spin.setEnabled(self._region_check.isChecked())

        self._sync_skip_chinese()
        self._latency_check.blockSignals(True)
        self._latency_check.setChecked(bool(self._config.get("show_latency")))
        self._latency_check.blockSignals(False)
        self._capture_check.blockSignals(True)
        self._capture_check.setChecked(bool(self._config.get("overlay_capturable")))
        self._capture_check.blockSignals(False)
        self._sync_engine_controls()

    # -- running ---------------------------------------------------------

    def translator_config(self) -> TranslatorConfig:
        return TranslatorConfig(
            engine=str(self._engine_combo.currentData() or "local"),
            source_language=str(self._source_combo.currentData() or "auto"),
            target_language=str(self._target_combo.currentData() or "zh-CN"),
            model_id=str(self._model_combo.currentData() or self._config.get("local_model") or ""),
            skip_chinese=self._skip_chinese_check.isChecked(),
            credentials={
                **platform_credentials(self._config),
                "openai_base_url": str(self._config.get("openai_base_url") or ""),
                "openai_model": str(self._config.get("openai_model") or ""),
                "openai_api_key": self._config.get_secret("openai_api_key"),
            },
        )

    @Slot()
    def toggle_running(self) -> None:
        if self._running:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if self._shutting_down:
            return
        if self._selected_window is None:
            self._set_status("请先选择目标窗口", error=True)
            return
        if self._prepare_thread is not None:
            return

        self._start_button.setEnabled(False)
        self._set_status("准备中…")

        worker = PrepareWorker(self.translator_config(), self._llama)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.status.connect(self._set_status)
        worker.finished.connect(self._on_prepare_finished)
        worker.failed.connect(self._on_prepare_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._prepare_thread = thread
        self._prepare_worker = worker
        self._sync_pet_state()
        thread.start()

    @Slot()
    def _on_prepare_finished(self) -> None:
        if self._shutting_down:
            return
        self._teardown_prepare()
        self._running = True
        self._session_id += 1
        self._start_button.setEnabled(True)
        self._start_button.setText("停止实时翻译（F8 开关）")
        self._sync_pet_state()
        # Replace the "loading model" status immediately.  Waiting for the first
        # frame to do it leaves the loading text on screen for as long as it
        # takes to capture something, which reads as a stuck application.
        self._set_status("翻译运行中，正在等待画面…")
        self._pipeline.invalidate_cache()
        self._timer.start()
        self._tick()

    @Slot(str)
    def _on_prepare_failed(self, message: str) -> None:
        if self._shutting_down:
            return
        self._teardown_prepare()
        self._start_button.setEnabled(True)
        self._set_status(message, error=True)
        self._sync_pet_state()

    def _teardown_prepare(self, timeout_ms: int = 5000) -> bool:
        thread = self._prepare_thread
        if thread is not None:
            thread.quit()
            if not thread.wait(timeout_ms):
                # Never destroy the only reference to a still-running QThread.
                return False
        self._prepare_thread = None
        self._prepare_worker = None
        return True

    @Slot()
    def stop(self) -> None:
        self._running = False
        self._session_id += 1
        self._timer.stop()
        self._overlay.hide_overlay()
        self._last_result = None
        # An outstanding request still owns the busy flag until it replies.
        # Restarting must not queue a second frame behind that request.
        self._start_button.setText("开始实时翻译（F8 开关）")
        self._set_status("已停止")
        self._sync_pet_state()
        # The llama-server child is deliberately left running: stopping it would
        # cost a 1.2-4.2 s model reload, and users routinely start again right
        # away.  It is stopped when the model changes and when the app exits.

    def _tick(self) -> None:
        if not self._running or self._selected_window is None:
            return
        if self._worker_busy:
            self._check_worker_stall()
            return
        from ..win32 import get_window_info

        window = get_window_info(self._selected_window.hwnd)
        if window is None:
            self.stop()
            self.refresh_windows()
            self._set_status("目标窗口已关闭", error=True)
            return
        self._selected_window = window
        current = WindowInfo(
            hwnd=window.hwnd,
            title=window.title,
            left=window.left,
            top=window.top,
            width=window.width,
            height=window.height,
        )
        self._worker_busy = True
        self._busy_since = time.monotonic()
        self.dispatch_frame.emit(current, self._frame_config())

    def _frame_config(self) -> dict:
        """Everything one frame needs: translation settings plus capture region.

        Kept as one plain dict because it crosses to the worker thread, so it
        must not reference anything the UI can mutate underneath it.
        """
        config = self.translator_config().to_dict()
        config["session_id"] = self._session_id
        config["region_bottom_only"] = self._region_check.isChecked()
        config["region_bottom_percent"] = self._region_spin.value()
        return config

    def _check_worker_stall(self) -> None:
        """Give up on a frame that never reported back.

        The pipeline runs its work on one thread and delivers queued calls
        serially, so a genuinely stuck capture or inference would block every
        later frame behind it.  Merely clearing the busy flag would therefore
        queue one more doomed call per tick; the session is stopped instead, and
        the reason is shown so the user can retry rather than wonder why nothing
        is happening.
        """
        elapsed = time.monotonic() - self._busy_since
        if elapsed < _WORKER_STALL_SECONDS:
            return
        self._logger.error("单帧处理超过 %.0f 秒未返回，暂停本次会话", elapsed)
        self.stop()
        self._set_status(
            f"单帧处理超过 {_WORKER_STALL_SECONDS:.0f} 秒未返回，已停止。"
            "目标窗口可能无响应，请重新开始或更换目标窗口。",
            error=True,
        )

    @Slot(object)
    def _on_frame(self, result: FrameResult) -> None:
        self._worker_busy = False
        self._busy_since = 0.0
        if not self._running or self._shutting_down:
            return
        if result.request_config is not None and (
            self._selected_window is None
            or result.target_hwnd != self._selected_window.hwnd
            or result.request_config != self._frame_config()
        ):
            return
        self._last_result = result
        if result.note:
            # The frame is fine; there was simply nothing to translate.  Report
            # why instead of leaving the previous status on screen, which is
            # what made a failed capture look like a model that never finished
            # loading.
            self._overlay.clear()
            self._set_status(result.note, error=result.error or "失败" in result.note)
            return
        if not result.items:
            self._overlay.clear()
            return
        self._render(result)

    def _render(self, result: FrameResult) -> None:
        window = self._selected_window
        if window is None:
            return
        # Re-read the target each frame so the overlay follows moves and resizes.
        from ..win32 import get_window_info

        fresh = get_window_info(window.hwnd)
        if fresh is None:
            self._set_status("目标窗口已关闭", error=True)
            self.stop()
            self.refresh_windows()
            return
        self._selected_window = fresh
        self._overlay.attach(
            fresh, hide_from_capture=not bool(self._config.get("overlay_capturable"))
        )
        self._overlay.update_content(
            fresh,
            result.items,
            LayoutMode.parse(self._config.get("layout_mode")),
            result.frame_size,
            font_size=int(self._config.get("overlay_font_size")),
            show_source=bool(self._config.get("show_source_text")),
        )
        translated = sum(1 for item in result.items if item.translation.strip())
        detail = (
            f"翻译运行中　·　{translated} 条译文　·　"
            f"截图 {result.timing.capture_ms:.0f} ms　"
            f"OCR {result.timing.ocr_ms:.0f} ms　"
            f"翻译 {result.timing.translate_ms:.0f} ms"
        )
        if bool(self._config.get("show_latency")):
            detail += f"　·　合计 {result.timing.total_ms:.0f} ms　·　缓存命中 {result.from_cache}"
        self._set_status(detail)

    @Slot()
    def _on_pipeline_ready(self) -> None:
        self._logger.info("流水线就绪")

    @Slot(str)
    def _on_pipeline_failed(self, message: str) -> None:
        self._worker_busy = False
        self._set_status(message, error=True)

    @Slot()
    def _on_hotkey(self) -> None:
        # Called from the keyboard hook thread; marshal onto the UI thread.
        # No arguments to marshal, so invokeMethod is safe here.
        QMetaObject.invokeMethod(
            self._start_button, "click", Qt.ConnectionType.QueuedConnection
        )

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status_label.setProperty("role", "error" if error else "status")
        self._status_label.setText(message)
        # Re-evaluate the stylesheet so the role property takes effect.
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)

    def shutdown(self) -> None:
        """Release every resource, exactly once.

        Called from both ``closeEvent`` and ``QApplication.aboutToQuit``.
        Relying on ``closeEvent`` alone is not enough: ``app.quit()`` - which is
        how the smoke test exits, and what a session logoff triggers - destroys
        the widgets without ever delivering a close event, and the pipeline
        thread and llama-server child would still be running at interpreter
        shutdown.  That surfaced as a hard crash on exit (0xC0000409) rather than
        a clean termination.
        """
        if self._shutting_down:
            return
        self._shutting_down = True
        self._pet.close()
        self._running = False
        self._timer.stop()
        uninstall_hotkey_hook()
        self._overlay.hide_overlay()
        self._config.save()
        for worker_thread in (self._prepare_thread, self._thread):
            if worker_thread is not None:
                worker_thread.requestInterruption()
                worker_thread.quit()
        # Stop the child first: this unblocks its HTTP clients and model loader.
        # Unlike stop(), shutdown() never waits for the model-loading lock.
        self._llama.shutdown()
        deadline = time.monotonic() + 3.0
        prepare_stopped = self._teardown_prepare(3000)
        thread = self._thread
        pipeline_stopped = True
        if thread is not None and thread.isRunning():
            pipeline_stopped = thread.wait(max(0, int((deadline - time.monotonic()) * 1000)))
        if not prepare_stopped or not pipeline_stopped:
            # PrintWindow/native OCR and a remote HTTP request cannot safely be
            # interrupted by QThread.quit(). Settings and the owned child have
            # already been handled; exit the whole process instead of destroying
            # live QThreads or leaving invisible Python executor threads behind.
            self._logger.warning("后台任务未及时结束，已保存设置并停止模型，结束程序进程")
            os._exit(0)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()


def bootstrap() -> None:
    """Call before creating the QApplication."""
    enable_dpi_awareness()
