//! Single-flight capture/OCR/translation worker with static-frame reuse.

use crate::{
    cancel::CancelToken,
    capture,
    config::Config,
    local::LocalServer,
    ocr::{Ocr, OcrLine},
    textproc, translate,
};
use image::{RgbaImage, imageops::crop_imm};
use std::{
    collections::{HashMap, VecDeque},
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
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
        );
        if token.check().is_err() {
            local.stop();
            previous_pixels.clear();
            previous_result = None;
            stable.clear();
            candidate.clear();
            candidate_count = 0;
            continue;
        }
        let result = match outcome {
            Ok(result) => result,
            Err(error) => FrameResult {
                generation: request.generation,
                hwnd: request.hwnd,
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
        let translations =
            translate::translate_cancellable(&request.config, &pending, local, token)?;
        token.check()?;
        for (source, result) in pending.into_iter().zip(translations) {
            cache_order.push_back(source.clone());
            cache.insert(source, result);
            if cache_order.len() > 2000
                && let Some(old) = cache_order.pop_front()
            {
                cache.remove(&old);
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
        size: [100, 100],
        lines: vec![],
        status: String::new(),
        timing_ms: [0; 3],
    };
    assert!(result.belongs_to(3, 100));
    assert!(!result.belongs_to(3, 200));
    assert!(!result.belongs_to(4, 100));
}
