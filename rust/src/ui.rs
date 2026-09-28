use crate::{
    capture::{self, WindowInfo},
    config::Config,
    downloads,
    hotkey::Hotkey,
    pipeline::{self, FrameResult, Request, Worker},
};
use anyhow::Result;
use eframe::egui::{
    self, Color32, FontData, FontDefinitions, FontFamily, FontId, RichText, Stroke, TextureHandle,
    ViewportBuilder, ViewportCommand, ViewportId,
};
use image::imageops::crop_imm;
use serde_json::json;
use std::{
    sync::{
        Arc, Mutex,
        mpsc::{self, Receiver, Sender},
    },
    time::{Duration, Instant},
};
use windows_sys::Win32::UI::{
    HiDpi::GetDpiForSystem,
    WindowsAndMessaging::{
        FindWindowExW, GWL_STYLE, GetSystemMetrics, GetWindowLongW, GetWindowThreadProcessId,
        SM_CXSCREEN, SM_CYSCREEN, SWP_FRAMECHANGED, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
        SWP_NOZORDER, SetWindowLongW, SetWindowPos, WS_OVERLAPPEDWINDOW, WS_POPUP,
    },
};
use windows_sys::Win32::{
    Graphics::Dwm::{
        DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND,
        DwmSetWindowAttribute,
    },
    System::Threading::GetCurrentProcessId,
};

const LANGUAGES: &[(&str, &str)] = &[
    ("auto", "自动检测"),
    ("zh-CN", "简体中文"),
    ("zh-TW", "繁体中文"),
    ("en", "英语"),
    ("ja", "日语"),
    ("ko", "韩语"),
    ("fr", "法语"),
    ("de", "德语"),
    ("es", "西班牙语"),
    ("pt", "葡萄牙语"),
    ("ru", "俄语"),
    ("it", "意大利语"),
    ("ar", "阿拉伯语"),
    ("th", "泰语"),
    ("vi", "越南语"),
];

#[derive(Clone, Copy)]
struct Palette {
    background: Color32,
    card: Color32,
    border: Color32,
    text: Color32,
    muted: Color32,
    accent: Color32,
    accent_soft: Color32,
}

fn palette(theme: &str) -> Palette {
    match theme {
        "light" => Palette {
            background: Color32::from_rgb(240, 245, 250),
            card: Color32::WHITE,
            border: Color32::from_rgb(216, 226, 237),
            text: Color32::from_rgb(26, 40, 58),
            muted: Color32::from_rgb(98, 115, 134),
            accent: Color32::from_rgb(28, 107, 196),
            accent_soft: Color32::from_rgb(226, 239, 255),
        },
        "priestess" => Palette {
            background: Color32::from_rgb(23, 22, 34),
            card: Color32::from_rgb(35, 33, 52),
            border: Color32::from_rgb(91, 78, 120),
            text: Color32::from_rgb(244, 239, 255),
            muted: Color32::from_rgb(180, 169, 204),
            accent: Color32::from_rgb(165, 138, 244),
            accent_soft: Color32::from_rgb(58, 47, 83),
        },
        _ => Palette {
            background: Color32::from_rgb(11, 19, 31),
            card: Color32::from_rgb(23, 35, 51),
            border: Color32::from_rgb(48, 70, 91),
            text: Color32::from_rgb(235, 244, 250),
            muted: Color32::from_rgb(150, 174, 192),
            accent: Color32::from_rgb(58, 190, 218),
            accent_soft: Color32::from_rgb(25, 67, 83),
        },
    }
}

fn section_card(
    ui: &mut egui::Ui,
    colors: Palette,
    number: &str,
    title: &str,
    detail: &str,
    content: impl FnOnce(&mut egui::Ui),
) {
    egui::Frame::new()
        .fill(colors.card)
        .stroke(Stroke::new(1.0, colors.border))
        .corner_radius(14)
        .inner_margin(egui::Margin::symmetric(18, 16))
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.label(
                    RichText::new(number)
                        .size(12.0)
                        .strong()
                        .color(colors.accent),
                );
                ui.label(RichText::new(title).size(18.0).strong().color(colors.text));
            });
            if !detail.is_empty() {
                ui.label(RichText::new(detail).size(12.0).color(colors.muted));
            }
            ui.add_space(12.0);
            content(ui);
        });
    ui.add_space(12.0);
}

pub fn run() -> Result<()> {
    run_with_target(None)
}

pub fn run_with_target(initial_target: Option<isize>) -> Result<()> {
    let config = Config::load()?;
    let icon = pipeline::assets_dir().join("app.png");
    let mut viewport = ViewportBuilder::default()
        .with_title("PRTSBox · 实时窗口翻译")
        .with_inner_size(egui::vec2(760.0, 880.0))
        .with_min_inner_size(egui::vec2(560.0, 620.0))
        .with_max_inner_size(egui::vec2(820.0, 900.0))
        // OpenGL chooses its pixel format from the root viewport. Give child
        // viewports an alpha channel so the pet has no opaque window backing.
        .with_transparent(true);
    if let Ok(bytes) = std::fs::read(icon)
        && let Ok(decoded) = image::load_from_memory(&bytes)
    {
        let rgba = decoded.into_rgba8();
        viewport = viewport.with_icon(egui::IconData {
            width: rgba.width(),
            height: rgba.height(),
            rgba: rgba.into_raw(),
        });
    }
    eframe::run_native(
        "PRTSBox",
        eframe::NativeOptions {
            viewport,
            ..Default::default()
        },
        Box::new(move |cc| {
            configure_fonts(&cc.egui_ctx);
            let mut app = App::new(&cc.egui_ctx, config);
            if let Some(hwnd) = initial_target {
                app.selected = hwnd;
                app.toggle();
            }
            Ok(Box::new(app))
        }),
    )
    .map_err(|error| anyhow::anyhow!("界面启动失败：{error}"))
}

fn configure_fonts(ctx: &egui::Context) {
    let mut fonts = FontDefinitions::default();
    let candidate = [
        "C:\\Windows\\Fonts\\simhei.ttf",
        "C:\\Windows\\Fonts\\NotoSansSC-VF.ttf",
    ]
    .into_iter()
    .find(|path| std::path::Path::new(path).exists());
    if let Some(path) = candidate.and_then(|path| std::fs::read(path).ok()) {
        fonts
            .font_data
            .insert("cjk".into(), Arc::new(FontData::from_owned(path)));
        fonts
            .families
            .entry(FontFamily::Proportional)
            .or_default()
            .insert(0, "cjk".into());
        fonts
            .families
            .entry(FontFamily::Monospace)
            .or_default()
            .insert(0, "cjk".into());
        ctx.set_fonts(fonts);
    }
}

#[derive(Default)]
struct PetActions {
    toggle_translation: bool,
    close: bool,
    dragged_dx: f32,
    pending_native_drag: bool,
    drag_pose_direction: i8,
}

struct PetTextures {
    idle: Vec<TextureHandle>,
    right: Vec<TextureHandle>,
    left: Vec<TextureHandle>,
    thinking: Vec<TextureHandle>,
}

struct App {
    config: Config,
    windows: Vec<WindowInfo>,
    selected: isize,
    worker: Worker,
    busy: bool,
    running: bool,
    generation: u64,
    last_tick: Instant,
    result: Option<FrameResult>,
    status: String,
    settings: bool,
    pet_visible: bool,
    pet_actions: Arc<Mutex<PetActions>>,
    pet_position: Arc<Mutex<egui::Pos2>>,
    pet_textures: Option<PetTextures>,
    pet_start: Instant,
    pet_drag_until: Instant,
    pet_direction: i8,
    secret_inputs: [String; 4],
    download_sender: Sender<String>,
    download_results: Receiver<String>,
    downloading: bool,
    hotkey: Option<Hotkey>,
    theme_background: Option<TextureHandle>,
    config_changed_at: Option<Instant>,
}

impl App {
    fn new(ctx: &egui::Context, config: Config) -> Self {
        let selected = config.int("window_hwnd") as isize;
        let pet_visible = config.bool("pet_visible");
        let windows = capture::list_windows();
        let pet_textures = load_pet(ctx).ok();
        let theme_background = load_texture(ctx, "priestess-background.png", "priestess-bg").ok();
        // Win32 screen metrics are physical pixels in a per-monitor-DPI-aware process,
        // while egui viewport positions are points. Mixing them places the pet off-screen.
        // The pet starts on the primary monitor, which may use a different DPI
        // from the main window's monitor.
        let primary_scale = unsafe { GetDpiForSystem().max(96) as f32 / 96.0 };
        let initial_pet_position = unsafe {
            egui::pos2(
                (GetSystemMetrics(SM_CXSCREEN) as f32 / primary_scale - 144.0 - 20.0).max(0.0),
                (GetSystemMetrics(SM_CYSCREEN) as f32 / primary_scale - 156.0 - 20.0).max(0.0),
            )
        };
        let (download_sender, download_results) = mpsc::channel();
        Self {
            config,
            windows,
            selected,
            worker: pipeline::start(),
            busy: false,
            running: false,
            generation: 0,
            last_tick: Instant::now(),
            result: None,
            status: "就绪".into(),
            settings: false,
            pet_visible,
            pet_actions: Arc::new(Mutex::new(PetActions::default())),
            pet_position: Arc::new(Mutex::new(initial_pet_position)),
            pet_textures,
            pet_start: Instant::now(),
            pet_drag_until: Instant::now(),
            pet_direction: 0,
            secret_inputs: Default::default(),
            download_sender,
            download_results,
            downloading: false,
            hotkey: Hotkey::register(),
            theme_background,
            config_changed_at: None,
        }
    }

    fn refresh_windows(&mut self) {
        self.windows = capture::list_windows();
    }

    fn invalidate_translation(&mut self) {
        self.generation += 1;
        self.worker.cancel(self.generation);
        self.result = None;
        self.busy = false;
        self.last_tick = Instant::now() - Duration::from_secs(2);
    }

    fn toggle(&mut self) {
        if self.running {
            self.running = false;
            self.invalidate_translation();
            self.status = "已停止翻译".into();
        } else if self.selected == 0 || capture::window_info(self.selected).is_none() {
            self.status = "请先选择一个可用的目标窗口".into();
            self.refresh_windows();
        } else {
            self.running = true;
            self.last_tick = Instant::now() - Duration::from_secs(2);
            self.status = "正在准备翻译".into();
        }
    }

    fn poll_worker(&mut self) {
        while let Ok(message) = self.download_results.try_recv() {
            if let Some(path) = message.strip_prefix("DONE:") {
                self.downloading = false;
                self.status = format!("下载完成：{path}");
            } else if let Some(error) = message.strip_prefix("ERROR:") {
                self.downloading = false;
                self.status = format!("下载失败：{error}");
            } else {
                self.status = message;
            }
        }
        while let Ok(result) = self.worker.results.try_recv() {
            if !self.running || !result.belongs_to(self.generation, self.selected) {
                continue;
            }
            if result.complete {
                self.busy = false;
            }
            self.status = if result.complete
                && self.config.bool("show_latency")
                && result.size[0] > 0
            {
                format!(
                    "{} · 截图 {}ms · OCR {}ms · 翻译 {}ms",
                    result.status, result.timing_ms[0], result.timing_ms[1], result.timing_ms[2]
                )
            } else {
                result.status.clone()
            };
            if result.status == "目标窗口已关闭" {
                self.running = false;
                self.refresh_windows();
            }
            self.result = if result.size[0] > 0 {
                Some(result)
            } else {
                None
            };
        }
    }

    fn dispatch(&mut self) {
        if !self.running || self.busy || self.last_tick.elapsed() < Duration::from_millis(900) {
            return;
        }
        self.last_tick = Instant::now();
        if capture::window_info(self.selected).is_none() {
            self.running = false;
            self.status = "目标窗口已关闭".into();
            self.refresh_windows();
            return;
        }
        let request = Request {
            generation: self.generation,
            hwnd: self.selected,
            config: self.config.clone(),
        };
        if self.worker.sender.send(request).is_ok() {
            self.busy = true;
        } else {
            self.running = false;
            self.status = "翻译线程已停止".into();
        }
    }

    fn save(&mut self) {
        for (index, key) in [
            "openai_api_key",
            "azure_api_key",
            "deepl_api_key",
            "baidu_secret_key",
        ]
        .iter()
        .enumerate()
        {
            if !self.secret_inputs[index].is_empty() {
                if let Err(error) = self.config.set_secret(key, &self.secret_inputs[index]) {
                    self.status = error.to_string();
                    return;
                }
                self.secret_inputs[index].clear();
            }
        }
        match self.config.save() {
            Ok(()) => {
                self.config_changed_at = None;
                self.status = "设置已保存".into();
            }
            Err(error) => self.status = format!("保存失败：{error:#}"),
        }
    }

    fn begin_download(&mut self, model: bool) {
        if self.downloading {
            return;
        }
        self.downloading = true;
        self.status = "准备下载…".into();
        let config = self.config.clone();
        let sender = self.download_sender.clone();
        std::thread::spawn(move || {
            let result = if model {
                let id = config.get("local_model").to_owned();
                downloads::download_model(&config, &id, &sender)
            } else {
                let variant = match config.get("local_runtime_variant") {
                    "auto" | "" => "vulkan",
                    value => value,
                }
                .to_owned();
                downloads::download_runtime(&config, &variant, &sender)
            };
            match result {
                Ok(path) => {
                    let _ = sender.send(format!("DONE:{}", path.display()));
                }
                Err(error) => {
                    let _ = sender.send(format!("ERROR:{error:#}"));
                }
            }
        });
    }

    fn draw_main(&mut self, root: &mut egui::Ui) {
        let colors = palette(self.config.get("theme"));
        egui::Frame::new()
            .inner_margin(egui::Margin::symmetric(24, 18))
            .show(root, |ui| {
                ui.set_width(ui.available_width());
                ui.horizontal(|ui| {
                    ui.vertical(|ui| {
                        ui.label(
                            RichText::new("P R T S B O X   /   R U S T")
                                .size(11.0)
                                .strong()
                                .color(colors.accent),
                        );
                        ui.add_space(3.0);
                        ui.label(
                            RichText::new(if self.settings {
                                "偏好设置"
                            } else {
                                "实时窗口翻译"
                            })
                            .size(27.0)
                            .strong()
                            .color(colors.text),
                        );
                        ui.label(
                            RichText::new(if self.settings {
                                "配置翻译服务、识别方式与显示样式"
                            } else {
                                "让译文跟随窗口，不打断正在看的内容"
                            })
                            .size(12.0)
                            .color(colors.muted),
                        );
                    });
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::TOP), |ui| {
                        if ui
                            .add(
                                egui::Button::new(if self.settings {
                                    "← 返回主页"
                                } else {
                                    "⚙ 设置"
                                })
                                .fill(colors.card)
                                .stroke(Stroke::new(1.0, colors.border))
                                .corner_radius(9),
                            )
                            .clicked()
                        {
                            self.settings = !self.settings;
                        }
                    });
                });
                ui.add_space(15.0);
                let scroll_height = (ui.available_height() - 126.0).max(180.0);
                egui::ScrollArea::vertical()
                    .id_salt("main-scroll")
                    .max_height(scroll_height)
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        ui.set_width(ui.available_width() - 8.0);
                        if self.settings {
                            self.draw_settings(ui);
                        } else {
                            self.draw_controls(ui);
                        }
                    });
                ui.add_space(8.0);
                ui.separator();
                ui.add_space(8.0);
                ui.horizontal(|ui| {
                    let label = if self.settings {
                        "保存设置"
                    } else if self.running {
                        "■  停止实时翻译"
                    } else {
                        "▶  开始实时翻译"
                    };
                    let action_width = (ui.available_width() * 0.69).max(180.0);
                    if ui
                        .add_sized(
                            [action_width, 48.0],
                            egui::Button::new(
                                RichText::new(label)
                                    .size(16.0)
                                    .strong()
                                    .color(Color32::WHITE),
                            )
                            .fill(colors.accent)
                            .stroke(Stroke::NONE)
                            .corner_radius(10),
                        )
                        .clicked()
                    {
                        if self.settings {
                            self.save();
                        } else {
                            self.toggle();
                        }
                    }
                    if ui
                        .add_sized(
                            [ui.available_width(), 48.0],
                            egui::Button::new(if self.settings {
                                "返回主页"
                            } else if self.pet_visible {
                                "隐藏桌宠"
                            } else {
                                "显示桌宠"
                            })
                            .fill(colors.card)
                            .stroke(Stroke::new(1.0, colors.border))
                            .corner_radius(10),
                        )
                        .clicked()
                    {
                        if self.settings {
                            self.settings = false;
                        } else {
                            self.pet_visible = !self.pet_visible;
                            self.config.set("pet_visible", json!(self.pet_visible));
                            if !self.pet_visible {
                                ui.ctx().send_viewport_cmd_to(
                                    ViewportId::from_hash_of("prtsbox-pet"),
                                    ViewportCommand::Close,
                                );
                            }
                            self.save();
                        }
                    }
                });
                ui.add_space(6.0);
                ui.horizontal(|ui| {
                    ui.label(
                        RichText::new(if self.running {
                            "●  运行中"
                        } else {
                            "●  待机"
                        })
                        .size(12.0)
                        .color(colors.accent),
                    );
                    ui.label(
                        RichText::new(format!("{}   ·   F8 快捷开关", self.status))
                            .size(12.0)
                            .color(colors.muted),
                    );
                });
            });
    }

    fn draw_controls(&mut self, ui: &mut egui::Ui) {
        let colors = palette(self.config.get("theme"));
        section_card(
            ui,
            colors,
            "01",
            "目标窗口",
            "选择画面来源，译文会跟随窗口移动",
            |ui| {
                ui.horizontal(|ui| {
                    let selected_text = self
                        .windows
                        .iter()
                        .find(|w| w.hwnd == self.selected)
                        .map(|w| format!("{} · {}×{}", w.title, w.width, w.height))
                        .unwrap_or_else(|| "请选择窗口".into());
                    egui::ComboBox::from_id_salt("target")
                        .selected_text(selected_text)
                        .width((ui.available_width() - 86.0).max(180.0))
                        .show_ui(ui, |ui| {
                            for window in &self.windows {
                                if ui
                                    .selectable_value(
                                        &mut self.selected,
                                        window.hwnd,
                                        format!(
                                            "{} · {}×{}",
                                            window.title, window.width, window.height
                                        ),
                                    )
                                    .clicked()
                                {
                                    self.config.set("window_hwnd", json!(window.hwnd));
                                    self.config.set("window_title", json!(window.title));
                                }
                            }
                        });
                    if ui.button("刷新").clicked() {
                        self.refresh_windows();
                    }
                });
                ui.add_space(8.0);
                let mut bottom = self.config.bool("region_bottom_only");
                if ui.checkbox(&mut bottom, "只识别窗口下方").changed() {
                    self.config.set("region_bottom_only", json!(bottom));
                }
                if bottom {
                    let mut percent =
                        self.config.int("region_bottom_percent").clamp(10, 100) as i32;
                    if range_value(ui, &mut percent, 10..=100, "识别范围", "%", colors) {
                        self.config.set("region_bottom_percent", json!(percent));
                    }
                }
            },
        );
        section_card(
            ui,
            colors,
            "02",
            "翻译引擎",
            "按需选择本地模型、AI 接口或翻译平台",
            |ui| {
                combo_value(
                    ui,
                    &mut self.config,
                    "engine",
                    "引擎",
                    &[
                        ("local", "本地模型"),
                        ("openai", "AI 大模型 API"),
                        ("platform", "翻译平台"),
                    ],
                );
                match self.config.get("engine") {
                    "local" => {
                        combo_value(
                            ui,
                            &mut self.config,
                            "local_model",
                            "模型",
                            &[
                                ("hy-mt2-1.8b", "标准 · Hy-MT2 1.8B"),
                                ("hy-mt2-7b", "增强 · Hy-MT2 7B"),
                            ],
                        );
                        ui.label(
                            RichText::new("本地翻译需要已安装运行时和 GGUF 模型。")
                                .size(12.0)
                                .color(colors.muted),
                        );
                    }
                    "openai" => {
                        ui.label(
                            RichText::new(format!("当前模型：{}", self.config.get("openai_model")))
                                .size(12.0)
                                .color(colors.muted),
                        );
                    }
                    "platform" => {
                        ui.label(
                            RichText::new(format!(
                                "当前平台：{}",
                                self.config.get("translation_platform")
                            ))
                            .size(12.0)
                            .color(colors.muted),
                        );
                    }
                    _ => {}
                }
            },
        );
        section_card(
            ui,
            colors,
            "03",
            "语言与过滤",
            "自动识别源语言，过滤已是目标语言的内容",
            |ui| {
                combo_value(ui, &mut self.config, "source_language", "源语言", LANGUAGES);
                combo_value(
                    ui,
                    &mut self.config,
                    "target_language",
                    "目标语言",
                    &LANGUAGES[1..],
                );
                let mut skip = self.config.bool("skip_chinese");
                if ui
                    .checkbox(&mut skip, "中文目标时跳过已经是中文的文本")
                    .changed()
                {
                    self.config.set("skip_chinese", json!(skip));
                }
            },
        );
    }

    fn draw_settings(&mut self, ui: &mut egui::Ui) {
        let colors = palette(self.config.get("theme"));
        section_card(
            ui,
            colors,
            "01",
            "外观与识别",
            "选择界面风格和文字识别方式",
            |ui| {
                combo_value(
                    ui,
                    &mut self.config,
                    "theme",
                    "主题",
                    &[
                        ("dark", "深色"),
                        ("light", "浅色"),
                        ("priestess", "普瑞赛斯"),
                    ],
                );
                combo_value(
                    ui,
                    &mut self.config,
                    "ocr_backend",
                    "OCR 后端",
                    &[
                        ("auto", "自动 · ONNX Runtime"),
                        ("onnxruntime", "ONNX Runtime"),
                    ],
                );
                ui.label(
                    RichText::new("当前使用 ONNX Runtime CPU 推理。")
                        .size(12.0)
                        .color(colors.muted),
                );
            },
        );
        section_card(
            ui,
            colors,
            "02",
            "翻译平台",
            "只显示当前平台需要填写的凭据",
            |ui| {
                combo_value(
                    ui,
                    &mut self.config,
                    "translation_platform",
                    "平台",
                    &[
                        ("azure", "微软 Azure Translator"),
                        ("deepl", "DeepL API"),
                        ("baidu", "百度翻译"),
                    ],
                );
                match self.config.get("translation_platform") {
                    "azure" => {
                        text_value(ui, &mut self.config, "azure_endpoint", "接口地址");
                        text_value(ui, &mut self.config, "azure_region", "区域");
                        secret_value(
                            ui,
                            &mut self.secret_inputs[1],
                            "API Key",
                            self.config.secret("azure_api_key").is_empty(),
                        );
                    }
                    "deepl" => {
                        combo_value(
                            ui,
                            &mut self.config,
                            "deepl_plan",
                            "方案",
                            &[("free", "API Free"), ("pro", "API Pro")],
                        );
                        secret_value(
                            ui,
                            &mut self.secret_inputs[2],
                            "API Key",
                            self.config.secret("deepl_api_key").is_empty(),
                        );
                    }
                    "baidu" => {
                        text_value(ui, &mut self.config, "baidu_app_id", "APP ID");
                        secret_value(
                            ui,
                            &mut self.secret_inputs[3],
                            "密钥",
                            self.config.secret("baidu_secret_key").is_empty(),
                        );
                    }
                    _ => {}
                }
            },
        );
        section_card(
            ui,
            colors,
            "03",
            "AI 大模型 API",
            "支持 OpenAI 兼容接口",
            |ui| {
                text_value(ui, &mut self.config, "openai_base_url", "接口地址");
                text_value(ui, &mut self.config, "openai_model", "模型名称");
                secret_value(
                    ui,
                    &mut self.secret_inputs[0],
                    "API Key",
                    self.config.secret("openai_api_key").is_empty(),
                );
            },
        );
        section_card(
            ui,
            colors,
            "04",
            "译文显示",
            "调整覆盖层的位置和信息密度",
            |ui| {
                combo_value(
                    ui,
                    &mut self.config,
                    "layout_mode",
                    "译文位置",
                    &[("below", "原文下方"), ("right", "原文右侧")],
                );
                let mut font_size = self.config.int("overlay_font_size").clamp(9, 28) as i32;
                if range_value(ui, &mut font_size, 9..=28, "译文字号", "", colors) {
                    self.config.set("overlay_font_size", json!(font_size));
                }
                let mut show = self.config.bool("show_source_text");
                if ui.checkbox(&mut show, "同时显示原文").changed() {
                    self.config.set("show_source_text", json!(show));
                }
                let mut latency = self.config.bool("show_latency");
                if ui.checkbox(&mut latency, "显示耗时").changed() {
                    self.config.set("show_latency", json!(latency));
                }
                let mut capturable = self.config.bool("overlay_capturable");
                if ui.checkbox(&mut capturable, "允许录屏捕获译文").changed() {
                    self.config.set("overlay_capturable", json!(capturable));
                }
            },
        );
        section_card(
            ui,
            colors,
            "05",
            "本地模型",
            "选择运行时并管理下载",
            |ui| {
                combo_value(
                    ui,
                    &mut self.config,
                    "local_runtime_variant",
                    "运行时",
                    &[
                        ("auto", "自动"),
                        ("vulkan", "Vulkan"),
                        ("cpu", "CPU"),
                        ("hip", "AMD HIP"),
                    ],
                );
                combo_value(
                    ui,
                    &mut self.config,
                    "download_source",
                    "下载来源",
                    &[
                        ("auto", "自动"),
                        ("domestic", "仅国内源"),
                        ("official", "仅官方源"),
                    ],
                );
                ui.horizontal(|ui| {
                    if ui
                        .add_enabled(!self.downloading, egui::Button::new("下载运行时"))
                        .clicked()
                    {
                        self.begin_download(false);
                    }
                    if ui
                        .add_enabled(!self.downloading, egui::Button::new("下载所选模型"))
                        .clicked()
                    {
                        self.begin_download(true);
                    }
                });
            },
        );
    }

    fn draw_overlay(&self, ctx: &egui::Context) {
        if !self.running {
            ctx.send_viewport_cmd_to(
                ViewportId::from_hash_of("prtsbox-overlay"),
                ViewportCommand::Close,
            );
            return;
        }
        let Some(result) = self.result.as_ref() else {
            ctx.send_viewport_cmd_to(
                ViewportId::from_hash_of("prtsbox-overlay"),
                ViewportCommand::Close,
            );
            return;
        };
        if !result.belongs_to(self.generation, self.selected) || result.lines.is_empty() {
            ctx.send_viewport_cmd_to(
                ViewportId::from_hash_of("prtsbox-overlay"),
                ViewportCommand::Close,
            );
            return;
        }
        let Some(window) = capture::window_info(self.selected) else {
            return;
        };
        let result = result.clone();
        let mode = self.config.get("layout_mode").to_owned();
        let light = self.config.get("theme") == "light";
        let show_source = self.config.bool("show_source_text");
        let font_size = self.config.int("overlay_font_size").clamp(9, 28) as f32;
        let root_scale = ctx
            .input(|input| input.viewport().native_pixels_per_point)
            .unwrap_or(1.0);
        let overlay_scale = capture::viewport_scale("PRTSBox 译文覆盖层", root_scale);
        let builder = ViewportBuilder::default()
            .with_title("PRTSBox 译文覆盖层")
            .with_inner_size(egui::vec2(
                window.width as f32 / overlay_scale,
                window.height as f32 / overlay_scale,
            ))
            .with_position(egui::pos2(
                window.left as f32 / overlay_scale,
                window.top as f32 / overlay_scale,
            ))
            .with_decorations(false)
            .with_transparent(true)
            .with_mouse_passthrough(true)
            .with_taskbar(false);
        ctx.show_viewport_deferred(
            ViewportId::from_hash_of("prtsbox-overlay"),
            builder,
            move |ui, _| {
                let painter = ui.painter();
                let scale_x = ui.available_width() / result.size[0].max(1) as f32;
                let scale_y = ui.available_height() / result.size[1].max(1) as f32;
                let mut placed = Vec::<egui::Rect>::new();
                for item in &result.lines {
                    let bounds = item.line.bounds;
                    let x = if mode == "right" {
                        bounds[2] as f32 * scale_x + 6.0
                    } else {
                        bounds[0] as f32 * scale_x
                    };
                    let y = if mode == "right" {
                        bounds[1] as f32 * scale_y
                    } else {
                        bounds[3] as f32 * scale_y + 3.0
                    };
                    let text = if show_source {
                        format!("{}\n{}", item.line.text, item.translation)
                    } else {
                        item.translation.clone()
                    };
                    let max_width = (ui.available_width() - x - 8.0)
                        .max(80.0)
                        .min(((bounds[2] - bounds[0]) as f32 * scale_x * 1.6).max(240.0));
                    let fg = if light {
                        Color32::from_rgb(20, 24, 34)
                    } else {
                        Color32::from_rgb(232, 240, 255)
                    };
                    let bg = if light {
                        Color32::from_rgba_unmultiplied(255, 255, 255, 220)
                    } else {
                        Color32::from_rgba_unmultiplied(12, 16, 24, 215)
                    };
                    let galley =
                        painter.layout(text, FontId::proportional(font_size), fg, max_width);
                    let size = galley.size() + egui::vec2(10.0, 7.0);
                    let mut rect = egui::Rect::from_min_size(
                        egui::pos2(x.min((ui.available_width() - size.x).max(0.0)).max(0.0), y),
                        size,
                    );
                    if rect.bottom() > ui.available_height() {
                        rect = rect.translate(egui::vec2(
                            0.0,
                            -size.y - (bounds[3] - bounds[1]) as f32 * scale_y - 6.0,
                        ));
                    }
                    for _ in 0..placed.len() + 1 {
                        if let Some(collision) = placed.iter().find(|other| other.intersects(rect))
                        {
                            rect = rect
                                .translate(egui::vec2(0.0, collision.bottom() - rect.top() + 4.0));
                        } else {
                            break;
                        }
                    }
                    if rect.bottom() > ui.available_height() {
                        rect =
                            rect.translate(egui::vec2(0.0, ui.available_height() - rect.bottom()));
                    }
                    placed.push(rect);
                    painter.rect_filled(rect, 4.0, bg);
                    painter.galley(rect.min + egui::vec2(5.0, 3.5), galley, fg);
                }
            },
        );
        capture::set_capture_affinity("PRTSBox 译文覆盖层", self.config.bool("overlay_capturable"));
        capture::place_overlay_above("PRTSBox 译文覆盖层", &window);
    }

    fn draw_pet(&mut self, ctx: &egui::Context) {
        if !self.pet_visible {
            return;
        }
        let Some(textures) = self.pet_textures.as_ref() else {
            return;
        };
        let dragging = Instant::now() < self.pet_drag_until;
        let frames = if dragging && self.pet_direction > 0 {
            &textures.right
        } else if dragging && self.pet_direction < 0 {
            &textures.left
        } else if self.running {
            &textures.thinking
        } else {
            &textures.idle
        };
        let elapsed = self.pet_start.elapsed().as_millis() as usize;
        let frame = if dragging {
            (elapsed / 90) % frames.len()
        } else if self.running {
            (elapsed / 220) % frames.len()
        } else if elapsed % 6160 < 6000 {
            0
        } else {
            1.min(frames.len() - 1)
        };
        let texture = frames[frame].clone();
        let drag_right_texture = textures.right[3].id();
        let drag_left_texture = textures.left[3].id();
        let actions = self.pet_actions.clone();
        let pet_position = self.pet_position.clone();
        let running = self.running;
        let builder = ViewportBuilder::default()
            .with_title("PRTSBox · Frostbyte")
            .with_inner_size(egui::vec2(144.0, 156.0))
            .with_decorations(false)
            .with_transparent(true)
            .with_taskbar(false)
            .with_always_on_top()
            .with_active(false)
            .with_position(*self.pet_position.lock().unwrap());
        ctx.show_viewport_deferred(
            ViewportId::from_hash_of("prtsbox-pet"),
            builder,
            move |ui, _| {
                remove_pet_window_frame();
                if let Some(position) =
                    ui.input(|input| input.viewport().outer_rect.map(|rect| rect.min))
                {
                    *pet_position.lock().unwrap() = position;
                }
                let (rect, response) =
                    ui.allocate_exact_size(egui::vec2(144.0, 156.0), egui::Sense::click_and_drag());
                let drag_dx = response
                    .total_drag_delta()
                    .unwrap_or_else(|| response.drag_delta())
                    .x;
                let mut start_native_drag = false;
                let pose_direction;
                {
                    let mut state = actions.lock().unwrap();
                    if response.drag_started() {
                        state.drag_pose_direction = if drag_dx > 0.1 {
                            1
                        } else if drag_dx < -0.1 {
                            -1
                        } else {
                            0
                        };
                        state.dragged_dx = drag_dx;
                        state.pending_native_drag = true;
                        ui.ctx().request_repaint();
                        ui.ctx().request_repaint_of(ViewportId::ROOT);
                    } else if state.pending_native_drag {
                        state.pending_native_drag = false;
                        start_native_drag = ui
                            .input(|input| input.pointer.button_down(egui::PointerButton::Primary));
                    }
                    pose_direction = state.drag_pose_direction;
                }
                let display_texture =
                    if (response.dragged() || start_native_drag) && pose_direction > 0 {
                        drag_right_texture
                    } else if (response.dragged() || start_native_drag) && pose_direction < 0 {
                        drag_left_texture
                    } else {
                        texture.id()
                    };
                egui::Image::new((display_texture, egui::vec2(144.0, 156.0))).paint_at(ui, rect);
                if response.clicked() {
                    show_main_window(ui.ctx());
                }
                if start_native_drag {
                    ui.ctx().send_viewport_cmd(ViewportCommand::StartDrag);
                }
                if response.dragged() {
                    let delta = ui.input(|input| input.pointer.delta());
                    if delta.x.abs() > 0.1 {
                        actions.lock().unwrap().dragged_dx = delta.x;
                    }
                }
                egui::Popup::context_menu(&response)
                    .anchor(response.rect)
                    .align(egui::RectAlign::TOP_END)
                    .align_alternatives(&[])
                    .show(|ui| {
                        if ui.button("打开主窗口").clicked() {
                            show_main_window(ui.ctx());
                            ui.close();
                        }
                        if ui
                            .button(if running {
                                "停止翻译"
                            } else {
                                "开启翻译"
                            })
                            .clicked()
                        {
                            actions.lock().unwrap().toggle_translation = true;
                            ui.close();
                            ui.ctx().request_repaint_of(ViewportId::ROOT);
                        }
                        ui.separator();
                        if ui.button("关闭桌宠").clicked() {
                            actions.lock().unwrap().close = true;
                            ui.close();
                            ui.ctx().request_repaint_of(ViewportId::ROOT);
                        }
                    });
            },
        );
    }
}

fn show_main_window(ctx: &egui::Context) {
    ctx.send_viewport_cmd_to(ViewportId::ROOT, ViewportCommand::Minimized(false));
    ctx.send_viewport_cmd_to(ViewportId::ROOT, ViewportCommand::Focus);
}

fn remove_pet_window_frame() {
    let title = "PRTSBox · Frostbyte"
        .encode_utf16()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let mut previous = std::ptr::null_mut();
    // Winit keeps the normal overlapped style on its undecorated viewport.
    // Windows draws a rounded frame and shadow around that transparent pet.
    unsafe {
        loop {
            let hwnd = FindWindowExW(
                std::ptr::null_mut(),
                previous,
                std::ptr::null(),
                title.as_ptr(),
            );
            if hwnd.is_null() {
                break;
            }
            previous = hwnd;
            let mut owner = 0;
            GetWindowThreadProcessId(hwnd, &mut owner);
            if owner != GetCurrentProcessId() {
                continue;
            }
            let style = GetWindowLongW(hwnd, GWL_STYLE) as u32;
            if style & WS_OVERLAPPEDWINDOW != 0 {
                SetWindowLongW(
                    hwnd,
                    GWL_STYLE,
                    ((style & !WS_OVERLAPPEDWINDOW) | WS_POPUP) as i32,
                );
                SetWindowPos(
                    hwnd,
                    std::ptr::null_mut(),
                    0,
                    0,
                    0,
                    0,
                    SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
                );
                let border = DWMWA_COLOR_NONE;
                DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_BORDER_COLOR as u32,
                    (&border as *const u32).cast(),
                    std::mem::size_of_val(&border) as u32,
                );
                let corners = DWMWCP_DONOTROUND;
                DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_WINDOW_CORNER_PREFERENCE as u32,
                    (&corners as *const i32).cast(),
                    std::mem::size_of_val(&corners) as u32,
                );
            }
            break;
        }
    }
}

impl eframe::App for App {
    fn logic(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        if ctx.input(|input| input.viewport().close_requested()) {
            self.pet_visible = false;
            ctx.send_viewport_cmd_to(
                ViewportId::from_hash_of("prtsbox-pet"),
                ViewportCommand::Close,
            );
            ctx.send_viewport_cmd_to(
                ViewportId::from_hash_of("prtsbox-overlay"),
                ViewportCommand::Close,
            );
            return;
        }
        // WM_DPICHANGED can resize past the native max while a window crosses
        // monitors; clamp the logical client size after that transition too.
        if let Some(inner) = ctx.input(|input| input.viewport().inner_rect)
            && (inner.width() > 824.0 || inner.height() > 904.0)
        {
            ctx.send_viewport_cmd(ViewportCommand::InnerSize(egui::vec2(
                inner.width().min(820.0),
                inner.height().min(900.0),
            )));
        }
        let key_pressed = self
            .hotkey
            .as_ref()
            .map(Hotkey::pressed)
            .unwrap_or_else(|| ctx.input(|input| input.key_pressed(egui::Key::F8)));
        if key_pressed {
            self.toggle();
        }
        let mut toggle_from_pet = false;
        if let Ok(mut actions) = self.pet_actions.lock() {
            if actions.close {
                self.pet_visible = false;
                self.config.set("pet_visible", json!(false));
                let _ = self.config.save();
                ctx.send_viewport_cmd_to(
                    ViewportId::from_hash_of("prtsbox-pet"),
                    ViewportCommand::Close,
                );
                actions.close = false;
            }
            if actions.dragged_dx.abs() > 0.1 {
                self.pet_direction = if actions.dragged_dx > 0.0 { 1 } else { -1 };
                self.pet_drag_until = Instant::now() + Duration::from_millis(180);
                actions.dragged_dx = 0.0;
            }
            if actions.toggle_translation {
                actions.toggle_translation = false;
                toggle_from_pet = true;
            }
        }
        if toggle_from_pet {
            self.toggle();
        }
        self.poll_worker();
        self.dispatch();
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        if self.config_changed_at.is_some() {
            let _ = self.config.save();
        }
    }

    fn clear_color(&self, _visuals: &egui::Visuals) -> [f32; 4] {
        [0.0, 0.0, 0.0, 0.0]
    }
    fn ui(&mut self, root: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let config_before = self.config.data.clone();
        let processing_before = self.config.processing_key();
        let ctx = root.ctx().clone();
        let theme = self.config.get("theme");
        let colors = palette(theme);
        let mut visuals = if theme == "light" {
            egui::Visuals::light()
        } else {
            egui::Visuals::dark()
        };
        visuals.override_text_color = Some(colors.text);
        visuals.weak_text_color = Some(colors.muted);
        visuals.panel_fill = colors.background;
        visuals.window_fill = colors.card;
        visuals.extreme_bg_color = if theme == "light" {
            Color32::from_rgb(247, 250, 254)
        } else {
            Color32::from_rgb(16, 27, 41)
        };
        visuals.text_edit_bg_color = Some(visuals.extreme_bg_color);
        visuals.widgets.inactive.bg_fill = colors.card;
        visuals.widgets.inactive.weak_bg_fill = colors.card;
        visuals.widgets.inactive.bg_stroke = Stroke::new(1.0, colors.border);
        visuals.widgets.inactive.corner_radius = egui::CornerRadius::same(7);
        visuals.widgets.hovered.bg_fill = colors.accent_soft;
        visuals.widgets.hovered.weak_bg_fill = colors.accent_soft;
        visuals.widgets.hovered.bg_stroke = Stroke::new(1.0, colors.accent);
        visuals.widgets.hovered.corner_radius = egui::CornerRadius::same(7);
        visuals.widgets.active.bg_fill = colors.accent_soft;
        visuals.widgets.active.weak_bg_fill = colors.accent_soft;
        visuals.widgets.active.bg_stroke = Stroke::new(1.0, colors.accent);
        visuals.widgets.active.corner_radius = egui::CornerRadius::same(7);
        visuals.selection.bg_fill = colors.accent_soft;
        visuals.selection.stroke = Stroke::new(1.0, colors.accent);
        root.ctx().set_visuals(visuals);
        root.style_mut().spacing.item_spacing = egui::vec2(10.0, 9.0);
        root.style_mut().spacing.button_padding = egui::vec2(12.0, 8.0);
        root.painter()
            .rect_filled(root.max_rect(), 0.0, colors.background);
        root.painter().rect_filled(
            egui::Rect::from_min_size(
                root.max_rect().min,
                egui::vec2(root.max_rect().width(), 5.0),
            ),
            0.0,
            colors.accent,
        );
        if theme == "priestess"
            && let Some(background) = &self.theme_background
        {
            root.painter().image(
                background.id(),
                root.max_rect(),
                egui::Rect::from_min_max(egui::Pos2::ZERO, egui::pos2(1.0, 1.0)),
                Color32::from_rgba_unmultiplied(255, 255, 255, 105),
            );
        }
        self.draw_main(root);
        if self.config.processing_key() != processing_before {
            self.invalidate_translation();
        }
        self.draw_overlay(&ctx);
        self.draw_pet(&ctx);
        if self.config.data != config_before {
            self.config_changed_at = Some(Instant::now());
        }
        if self
            .config_changed_at
            .is_some_and(|changed| changed.elapsed() >= Duration::from_millis(600))
        {
            match self.config.save() {
                Ok(()) => self.config_changed_at = None,
                Err(error) => self.status = format!("自动保存失败：{error:#}"),
            }
        }
        ctx.request_repaint_after(Duration::from_millis(50));
    }
}

fn load_texture(ctx: &egui::Context, file: &str, name: &str) -> Result<TextureHandle> {
    let image = image::open(pipeline::assets_dir().join(file))?.into_rgba8();
    let color = egui::ColorImage::from_rgba_unmultiplied(
        [image.width() as usize, image.height() as usize],
        image.as_raw(),
    );
    Ok(ctx.load_texture(name, color, egui::TextureOptions::LINEAR))
}

fn combo_value(
    ui: &mut egui::Ui,
    config: &mut Config,
    key: &str,
    label: &str,
    options: &[(&str, &str)],
) {
    let mut value = config.get(key).to_owned();
    let selected = options
        .iter()
        .find(|(code, _)| *code == value)
        .map(|(_, name)| *name)
        .unwrap_or("请选择");
    ui.horizontal(|ui| {
        ui.add_sized([104.0, 28.0], egui::Label::new(label));
        egui::ComboBox::from_id_salt(key)
            .selected_text(selected)
            .width((ui.available_width() - 8.0).max(160.0))
            .show_ui(ui, |ui| {
                for (code, name) in options {
                    ui.selectable_value(&mut value, (*code).to_owned(), *name);
                }
            });
    });
    if value != config.get(key) {
        config.set(key, json!(value));
    }
}

fn range_value(
    ui: &mut egui::Ui,
    value: &mut i32,
    range: std::ops::RangeInclusive<i32>,
    label: &str,
    suffix: &str,
    colors: Palette,
) -> bool {
    ui.horizontal(|ui| {
        ui.add_sized([104.0, 28.0], egui::Label::new(label));
        let slider_changed = ui
            .scope(|ui| {
                ui.spacing_mut().slider_width = (ui.available_width() - 84.0).max(96.0);
                ui.visuals_mut().widgets.inactive.bg_fill = colors.border;
                ui.visuals_mut().selection.bg_fill = colors.accent;
                ui.add(
                    egui::Slider::new(value, range.clone())
                        .show_value(false)
                        .trailing_fill(true),
                )
                .changed()
            })
            .inner;
        let number_changed = ui
            .add_sized(
                [72.0, 28.0],
                egui::DragValue::new(value).range(range).suffix(suffix),
            )
            .changed();
        slider_changed || number_changed
    })
    .inner
}

fn text_value(ui: &mut egui::Ui, config: &mut Config, key: &str, label: &str) {
    let mut value = config.get(key).to_owned();
    ui.horizontal(|ui| {
        ui.add_sized([104.0, 28.0], egui::Label::new(label));
        if ui
            .add_sized(
                [ui.available_width(), 28.0],
                egui::TextEdit::singleline(&mut value),
            )
            .changed()
        {
            config.set(key, json!(value));
        }
    });
}

fn secret_value(ui: &mut egui::Ui, value: &mut String, label: &str, missing: bool) {
    ui.horizontal(|ui| {
        ui.add_sized([104.0, 28.0], egui::Label::new(label));
        ui.add_sized(
            [ui.available_width(), 28.0],
            egui::TextEdit::singleline(value)
                .password(true)
                .hint_text(if missing {
                    "未设置"
                } else {
                    "已保存；留空保持原值"
                }),
        );
    });
}

fn load_pet(ctx: &egui::Context) -> Result<PetTextures> {
    let path = pipeline::assets_dir().join("frostbyte/spritesheet.webp");
    let sheet = image::open(path)?.into_rgba8();
    let (width, height) = (sheet.width() / 8, sheet.height() / 11);
    let load = |name: &str, row: u32, columns: &[u32]| -> Vec<TextureHandle> {
        columns
            .iter()
            .map(|column| {
                let crop = crop_imm(&sheet, column * width, row * height, width, height).to_image();
                let pixels = egui::ColorImage::from_rgba_unmultiplied(
                    [width as usize, height as usize],
                    crop.as_raw(),
                );
                ctx.load_texture(
                    format!("pet-{name}-{column}"),
                    pixels,
                    egui::TextureOptions::LINEAR,
                )
            })
            .collect()
    };
    Ok(PetTextures {
        idle: load("idle", 0, &[0, 2]),
        right: load("right", 1, &[0, 1, 2, 3, 4, 5, 6, 7]),
        left: load("left", 2, &[0, 1, 2, 3, 4, 5, 6, 7]),
        thinking: load("think", 7, &[0, 1, 2, 3, 4, 5]),
    })
}
