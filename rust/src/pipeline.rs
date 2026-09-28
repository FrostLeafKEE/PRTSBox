//! Single-flight capture/OCR/translation worker with static-frame reuse.

use crate::{
    cancel::CancelToken,
    capture,
    config::Config,
    local::LocalServer,
    ocr::{Ocr, OcrLine},
    textproc, translate,
};
use image::{
    RgbaImage,
    imageops::{FilterType, crop_imm, resize},
};
use std::{
    collections::{HashMap, VecDeque},
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    thread,
};

#[derive(Clone, Debug)]
pub struct TranslatedLine {
    pub line: OcrLine,
    pub translation: String,
}

#[derive(Clone, Debug)]
pub struct FrameResult {
    pub generation: u64,
    pub hwnd: isize,
    pub complete: bool,
    pub size: [u32; 2],
    pub lines: Vec<TranslatedLine>,
    pub status: String,
    pub timing_ms: [u128; 3],
}

impl FrameResult {
    pub fn belongs_to(&self, generation: u64, hwnd: isize) -> bool {
        self.generation == generation && self.hwnd == hwnd
    }
}

pub struct Request {
    pub generation: u64,
    pub hwnd: isize,
    pub config: Config,
}
pub struct Worker {
    epoch: Arc<AtomicU64>,
    pub sender: Sender<Request>,
    pub results: Receiver<FrameResult>,
}

impl Worker {
    pub fn cancel(&self, generation: u64) {
        self.epoch.store(generation, Ordering::Release);
    }
}

pub fn start() -> Worker {
    let epoch = Arc::new(AtomicU64::new(0));
    let worker_epoch = epoch.clone();
    let (requests, request_rx) = mpsc::channel::<Request>();
    let (result_tx, results) = mpsc::channel::<FrameResult>();
    thread::Builder::new()
        .name("prtsbox-ocr".into())
        .spawn(move || run(request_rx, result_tx, worker_epoch))
        .expect("worker thread");
    Worker {
        epoch,
        sender: requests,
        results,
    }
}

pub fn assets_dir() -> PathBuf {
    let installed = std::env::current_exe()
        .unwrap_or_default()
        .parent()
        .unwrap()
        .join("assets");
    if installed.join("onnxruntime.dll").exists() {
        installed
    } else {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("assets")
    }
}

fn run(requests: Receiver<Request>, results: Sender<FrameResult>, epoch: Arc<AtomicU64>) {
    let mut ocr = match Ocr::load(&assets_dir()) {
        Ok(ocr) => ocr,
        Err(error) => {
            for request in requests {
                let _ = results.send(FrameResult {
                    generation: request.generation,
                    hwnd: request.hwnd,
                    complete: true,
                    size: [0, 0],
                    lines: vec![],
                    status: format!("OCR 初始化失败：{error:#}"),
                    timing_ms: [0, 0, 0],
                });
            }
            return;
        }
    };
    let mut previous_pixels = Vec::new();
    let mut previous_result: Option<FrameResult> = None;
    let mut previous_context = String::new();
    let mut translation_context = String::new();
    let mut stable: Vec<OcrLine> = Vec::new();
    let mut candidate = String::new();
    let mut candidate_count = 0u8;
    let mut cache: HashMap<String, String> = HashMap::new();
    let mut cache_order: VecDeque<String> = VecDeque::new();
    let mut local = LocalServer::new();
    while let Ok(mut request) = requests.recv() {
        while let Ok(newer) = requests.try_recv() {
            request = newer;
        }
        let token = CancelToken {
            epoch: epoch.clone(),
            expected: request.generation,
            scene_changed: Arc::new(AtomicBool::new(false)),
        };
        if token.check().is_err() {
            continue;
        }
        let translation_key = request.config.translation_key();
        if translation_context != translation_key {
            translation_context = translation_key;
            cache.clear();
            cache_order.clear();
        }
        let context = format!("{}:{}", request.hwnd, request.config.processing_key());
        if previous_context != context {
            previous_context = context;
            previous_pixels.clear();
            previous_result = None;
            stable.clear();
            candidate.clear();
            candidate_count = 0;
        }
        let outcome = process(
            &request,
            &mut ocr,
            &mut previous_pixels,
            &mut previous_result,
            &mut stable,
            &mut candidate,
            &mut candidate_count,
            &mut cache,
            &mut cache_order,
            &mut local,
            &token,
            &results,
        );
        if token.check().is_err() {
            local.stop();
            previous_pixels.clear();
            previous_result = None;
            stable.clear();
            candidate.clear();
            candidate_count = 0;
            if epoch.load(Ordering::Acquire) == request.generation {
                let _ = results.send(FrameResult {
                    generation: request.generation,
                    hwnd: request.hwnd,
                    complete: true,
                    size: [0, 0],
                    lines: vec![],
                    status: "画面再次切换，正在重新识别".into(),
                    timing_ms: [0, 0, 0],
                });
            }
            continue;
        }
        let result = match outcome {
            Ok(result) => result,
            Err(error) => FrameResult {
                generation: request.generation,
                hwnd: request.hwnd,
                complete: true,
                size: [0, 0],
                lines: vec![],
                status: format!("{error:#}"),
                timing_ms: [0, 0, 0],
            },
        };
        let _ = results.send(result);
    }
}

#[allow(clippy::too_many_arguments)]
fn process(
    request: &Request,
    ocr: &mut Ocr,
    previous_pixels: &mut Vec<u8>,
    previous_result: &mut Option<FrameResult>,
    stable: &mut Vec<OcrLine>,
    candidate: &mut String,
    candidate_count: &mut u8,
    cache: &mut HashMap<String, String>,
    cache_order: &mut VecDeque<String>,
    local: &mut LocalServer,
    token: &CancelToken,
    progress: &Sender<FrameResult>,
) -> anyhow::Result<FrameResult> {
    token.check()?;
    let capture_started = std::time::Instant::now();
    let window =
        capture::window_info(request.hwnd).ok_or_else(|| anyhow::anyhow!("目标窗口已关闭"))?;
    let frame = capture::capture_window(&window)?;
    token.check()?;
    let capture_ms = capture_started.elapsed().as_millis();
    let size = [frame.width(), frame.height()];
    if is_blank(&frame) {
        anyhow::bail!("截图全黑：目标窗口可能受保护或处于独占全屏模式");
    }
    let top = if request.config.bool("region_bottom_only") {
        let percent = request.config.int("region_bottom_percent").clamp(10, 100) as u32;
        frame.height() * (100 - percent) / 100
    } else {
        0
    };
    let crop = crop_imm(&frame, 0, top, frame.width(), frame.height() - top).to_image();
    if crop.as_raw() == previous_pixels
        && *candidate_count == 0
        && let Some(old) = previous_result
        && old.size == size
    {
        let mut reused = old.clone();
        reused.generation = request.generation;
        reused.timing_ms = [capture_ms, 0, 0];
        return Ok(reused);
    }
    let ocr_started = std::time::Instant::now();
    let mut lines = ocr.recognize(&crop)?;
    token.check()?;
    let ocr_ms = ocr_started.elapsed().as_millis();
    for line in &mut lines {
        line.bounds[1] += top;
        line.bounds[3] += top;
    }
    if !stable.is_empty() && stable.len() == lines.len() {
        for (current, old) in lines.iter_mut().zip(stable.iter()) {
            if normalize(&current.text) == normalize(&old.text)
                && current
                    .bounds
                    .iter()
                    .zip(old.bounds)
                    .all(|(a, b)| a.abs_diff(b) <= 6)
            {
                *current = old.clone();
            }
        }
    }
    let new_text = lines
        .iter()
        .map(|line| normalize(&line.text))
        .collect::<Vec<_>>()
        .join("|");
    let old_text = stable
        .iter()
        .map(|line| normalize(&line.text))
        .collect::<Vec<_>>()
        .join("|");
    if !old_text.is_empty() && new_text != old_text && similar(&new_text, &old_text) {
        if !confirm_candidate(candidate, candidate_count, &new_text) {
            lines.clone_from(stable);
        } else {
            *candidate_count = 0;
            candidate.clear();
            stable.clone_from(&lines);
        }
    } else {
        *candidate_count = 0;
        candidate.clear();
        stable.clone_from(&lines);
    }
    lines = textproc::prepare(lines);
    if request.config.bool("skip_chinese")
        && request.config.get("target_language").starts_with("zh")
    {
        lines.retain(|line| !already_chinese(&line.text));
    }
    let mut pending = Vec::new();
    for line in &lines {
        let key = normalize(&line.text);
        if !cache.contains_key(&key) && !pending.contains(&key) {
            pending.push(key);
        }
    }
    let translate_started = std::time::Instant::now();
    if !pending.is_empty() {
        let _watch = (request.config.get("engine") == "local")
            .then(|| SceneWatch::start(request.hwnd, top, &crop, token.scene_changed.clone()));
        if previous_result
            .as_ref()
            .is_none_or(|old| !same_source_lines(&old.lines, &lines))
        {
            // A new page must not keep painting the previous page while inference runs.
            let _ = progress.send(FrameResult {
                generation: request.generation,
                hwnd: request.hwnd,
                complete: false,
                size: [0, 0],
                lines: vec![],
                status: format!("画面已变化，正在翻译 {} 条新文字", pending.len()),
                timing_ms: [capture_ms, ocr_ms, 0],
            });
        }
        let chunk_size = if request.config.get("engine") == "local" {
            4
        } else {
            pending.len()
        };
        let mut finished = 0;
        let mut last_recheck = std::time::Instant::now();
        for part in pending.chunks(chunk_size) {
            let translations =
                translate::translate_cancellable(&request.config, part, local, token)?;
            token.check()?;
            for (source, result) in part.iter().zip(translations) {
                cache_order.push_back(source.clone());
                cache.insert(source.clone(), result);
                if cache_order.len() > 2000
                    && let Some(old) = cache_order.pop_front()
                {
                    cache.remove(&old);
                }
            }
            finished += part.len();
            if finished < pending.len() {
                let _ = progress.send(FrameResult {
                    generation: request.generation,
                    hwnd: request.hwnd,
                    complete: false,
                    size,
                    lines: cached_lines(&lines, cache),
                    status: format!("正在翻译新文字 {finished}/{}", pending.len()),
                    timing_ms: [capture_ms, ocr_ms, translate_started.elapsed().as_millis()],
                });
            }
            // Long game screens can take many local batches. Discard obsolete work
            // promptly when the player changes page again during translation.
            if finished < pending.len()
                && last_recheck.elapsed() >= std::time::Duration::from_secs(2)
            {
                if page_changed(request.hwnd, top, &new_text, ocr) {
                    anyhow::bail!("画面再次切换，正在重新识别");
                }
                last_recheck = std::time::Instant::now();
            }
        }
    }
    let translate_ms = translate_started.elapsed().as_millis();
    let translated = lines
        .into_iter()
        .map(|line| TranslatedLine {
            translation: cache
                .get(&normalize(&line.text))
                .cloned()
                .unwrap_or_default(),
            line,
        })
        .collect::<Vec<_>>();
    let status = if translated.is_empty() {
        "未识别到需要翻译的文字".into()
    } else {
        format!("已翻译 {} 条", translated.len())
    };
    let result = FrameResult {
        generation: request.generation,
        hwnd: request.hwnd,
        complete: true,
        size,
        lines: translated,
        status,
        timing_ms: [capture_ms, ocr_ms, translate_ms],
    };
    token.check()?;
    *previous_pixels = crop.into_raw();
    *previous_result = Some(result.clone());
    Ok(result)
}

fn cached_lines(lines: &[OcrLine], cache: &HashMap<String, String>) -> Vec<TranslatedLine> {
    lines
        .iter()
        .filter_map(|line| {
            cache
                .get(&normalize(&line.text))
                .map(|translation| TranslatedLine {
                    line: line.clone(),
                    translation: translation.clone(),
                })
        })
        .collect()
}

fn same_source_lines(old: &[TranslatedLine], current: &[OcrLine]) -> bool {
    old.len() == current.len()
        && old.iter().zip(current).all(|(a, b)| {
            normalize(&a.line.text) == normalize(&b.text)
                && a.line
                    .bounds
                    .iter()
                    .zip(b.bounds)
                    .all(|(x, y)| x.abs_diff(y) <= 6)
        })
}

fn page_changed(hwnd: isize, top: u32, original: &str, ocr: &mut Ocr) -> bool {
    let Some(window) = capture::window_info(hwnd) else {
        return false;
    };
    let Ok(frame) = capture::capture_window(&window) else {
        return false;
    };
    if top >= frame.height() {
        return true;
    }
    let crop = crop_imm(&frame, 0, top, frame.width(), frame.height() - top).to_image();
    let Ok(lines) = ocr.recognize(&crop) else {
        return false;
    };
    let observed = lines
        .iter()
        .map(|line| normalize(&line.text))
        .collect::<Vec<_>>()
        .join("|");
    materially_changed(original, &observed)
}

fn materially_changed(original: &str, observed: &str) -> bool {
    if original.is_empty() || observed.is_empty() {
        return original != observed;
    }
    !similar(original, observed)
}

struct SceneWatch {
    stop: Arc<AtomicBool>,
    handle: Option<thread::JoinHandle<()>>,
}

impl SceneWatch {
    fn start(hwnd: isize, top: u32, baseline: &RgbaImage, changed: Arc<AtomicBool>) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let stop_for_thread = stop.clone();
        let baseline = scene_signature(baseline);
        let handle = thread::spawn(move || {
            let mut consecutive = 0;
            while !stop_for_thread.load(Ordering::Acquire) {
                thread::sleep(std::time::Duration::from_millis(500));
                if stop_for_thread.load(Ordering::Acquire) {
                    break;
                }
                let Some(window) = capture::window_info(hwnd) else {
                    continue;
                };
                let Ok(frame) = capture::capture_window(&window) else {
                    continue;
                };
                if top >= frame.height() {
                    continue;
                }
                let crop = crop_imm(&frame, 0, top, frame.width(), frame.height() - top).to_image();
                if scene_looks_different(&baseline, &scene_signature(&crop)) {
                    consecutive += 1;
                    if consecutive >= 2 {
                        changed.store(true, Ordering::Release);
                        break;
                    }
                } else {
                    consecutive = 0;
                }
            }
        });
        Self {
            stop,
            handle: Some(handle),
        }
    }
}

impl Drop for SceneWatch {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
    }
}

fn scene_signature(frame: &RgbaImage) -> Vec<[u8; 3]> {
    resize(frame, 24, 14, FilterType::Triangle)
        .pixels()
        .map(|pixel| [pixel[0], pixel[1], pixel[2]])
        .collect()
}

fn scene_looks_different(old: &[[u8; 3]], new: &[[u8; 3]]) -> bool {
    old.len() != new.len()
        || old
            .iter()
            .zip(new)
            .filter(|(a, b)| a.iter().zip(b.iter()).any(|(x, y)| x.abs_diff(*y) > 35))
            .count()
            > old.len() * 15 / 100
}

fn is_blank(frame: &RgbaImage) -> bool {
    frame
        .pixels()
        .step_by(997)
        .all(|pixel| pixel[0] < 5 && pixel[1] < 5 && pixel[2] < 5)
}

fn normalize(text: &str) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .replace(" .", ".")
        .replace(" ,", ",")
        .replace(" !", "!")
}

fn already_chinese(text: &str) -> bool {
    if text.chars().any(|c| ('\u{3040}'..='\u{30ff}').contains(&c)) {
        return false;
    }
    let significant = text
        .chars()
        .filter(|c| c.is_alphabetic())
        .collect::<Vec<_>>();
    if significant.is_empty() {
        return false;
    }
    let han = |c: char| ('\u{4e00}'..='\u{9fff}').contains(&c);
    han(significant[0]) || significant.iter().filter(|c| han(**c)).count() * 2 >= significant.len()
}

fn similar(a: &str, b: &str) -> bool {
    if a.is_empty() || b.is_empty() {
        return true;
    }
    let ac = a.chars().collect::<Vec<_>>();
    let bc = b.chars().collect::<Vec<_>>();
    let limit = ac.len().max(bc.len());
    if ac.len().abs_diff(bc.len()) * 100 > limit * 15 {
        return false;
    }
    let mut previous = (0..=bc.len()).collect::<Vec<_>>();
    let mut current = vec![0usize; bc.len() + 1];
    for (i, left) in ac.iter().enumerate() {
        current[0] = i + 1;
        for (j, right) in bc.iter().enumerate() {
            current[j + 1] = (previous[j + 1] + 1)
                .min(current[j] + 1)
                .min(previous[j] + usize::from(left != right));
        }
        std::mem::swap(&mut previous, &mut current);
    }
    previous[bc.len()] * 100 <= limit * 15
}

fn confirm_candidate(candidate: &mut String, count: &mut u8, text: &str) -> bool {
    if candidate == text {
        *count = count.saturating_add(1);
    } else {
        *candidate = text.to_owned();
        *count = 1;
    }
    *count >= 2
}

#[cfg(test)]
#[test]
fn confirmation_requires_consecutive_identical_candidates() {
    let mut candidate = String::new();
    let mut count = 0;
    for text in ["todax", "todaq", "todaz"] {
        assert!(!confirm_candidate(&mut candidate, &mut count, text));
    }
    assert!(confirm_candidate(&mut candidate, &mut count, "todaz"));
    assert!(!confirm_candidate(&mut candidate, &mut count, ""));
    assert!(confirm_candidate(&mut candidate, &mut count, ""));
}

#[cfg(test)]
#[test]
fn results_belong_to_one_window_and_settings_generation() {
    let result = FrameResult {
        generation: 3,
        hwnd: 100,
        complete: true,
        size: [100, 100],
        lines: vec![],
        status: String::new(),
        timing_ms: [0; 3],
    };
    assert!(result.belongs_to(3, 100));
    assert!(!result.belongs_to(3, 200));
    assert!(!result.belongs_to(4, 100));
}

#[cfg(test)]
#[test]
fn page_switch_is_distinct_from_animation_and_small_ocr_noise() {
    let old = "TEAMS #2|The House of Spiders|Wuthering Heights|The Middle Little Sister";
    assert!(!materially_changed(old, old));
    assert!(!materially_changed(
        old,
        "TEAMS #2|The House of Spiders|Wuthering Heights|The Middle Little Sistef"
    ));
    assert!(materially_changed(old, "STORY|Chapter 1|Continue|Skip"));
    assert!(materially_changed(old, ""));
}

#[cfg(test)]
#[test]
fn partial_translation_only_paints_current_page_lines() {
    let current = vec![OcrLine {
        text: "new page".into(),
        bounds: [10, 20, 100, 40],
        score: 1.0,
    }];
    let mut cache = HashMap::new();
    cache.insert("old page".into(), "旧页".into());
    assert!(cached_lines(&current, &cache).is_empty());
    cache.insert("new page".into(), "新页".into());
    let partial = cached_lines(&current, &cache);
    assert_eq!(partial.len(), 1);
    assert_eq!(partial[0].translation, "新页");
}

#[cfg(test)]
#[test]
fn coarse_scene_signature_ignores_small_animation_but_detects_new_page() {
    let first = RgbaImage::from_pixel(240, 140, image::Rgba([25, 25, 25, 255]));
    let mut animated = first.clone();
    for y in 10..25 {
        for x in 10..25 {
            animated.put_pixel(x, y, image::Rgba([240, 240, 240, 255]));
        }
    }
    assert!(!scene_looks_different(
        &scene_signature(&first),
        &scene_signature(&animated)
    ));
    let mut next_page = first.clone();
    for y in 0..100 {
        for x in 0..150 {
            next_page.put_pixel(x, y, image::Rgba([220, 120, 80, 255]));
        }
    }
    assert!(scene_looks_different(
        &scene_signature(&first),
        &scene_signature(&next_page)
    ));
}
