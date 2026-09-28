//! Native PP-OCRv5 ONNX detector and CTC recogniser.
//! The bundled models and dictionary are the same assets used by the Python build.

use anyhow::{Context, Result, bail};
use image::{
    Rgb, RgbImage, RgbaImage,
    imageops::{FilterType, crop_imm, resize},
};
use ort::{inputs, session::Session, value::Tensor};
use std::{collections::VecDeque, fs, path::Path};

#[derive(Clone, Debug)]
pub struct OcrLine {
    pub text: String,
    pub score: f32,
    pub bounds: [u32; 4],
}

struct Detection {
    bounds: [u32; 4],
    rotated: Option<RotatedCrop>,
}

struct RotatedCrop {
    center: [f32; 2],
    axis: [f32; 2],
    start: [f32; 2],
    size: [u32; 2],
}

impl Detection {
    fn crop(&self, image: &RgbImage) -> RgbImage {
        let Some(rotated) = &self.rotated else {
            let [left, top, right, bottom] = self.bounds;
            return crop_imm(image, left, top, right - left, bottom - top).to_image();
        };
        let mut crop = RgbImage::new(rotated.size[0], rotated.size[1]);
        let [cos, sin] = rotated.axis;
        for (x, y, pixel) in crop.enumerate_pixels_mut() {
            let u = rotated.start[0] + x as f32;
            let v = rotated.start[1] + y as f32;
            let sx = rotated.center[0] + u * cos - v * sin;
            let sy = rotated.center[1] + u * sin + v * cos;
            *pixel = bilinear(image, sx, sy);
        }
        crop
    }
}

fn bilinear(image: &RgbImage, x: f32, y: f32) -> Rgb<u8> {
    let x = x.clamp(0.0, image.width().saturating_sub(1) as f32);
    let y = y.clamp(0.0, image.height().saturating_sub(1) as f32);
    let x0 = x.floor() as u32;
    let y0 = y.floor() as u32;
    let x1 = (x0 + 1).min(image.width() - 1);
    let y1 = (y0 + 1).min(image.height() - 1);
    let (fx, fy) = (x - x0 as f32, y - y0 as f32);
    let a = image.get_pixel(x0, y0);
    let b = image.get_pixel(x1, y0);
    let c = image.get_pixel(x0, y1);
    let d = image.get_pixel(x1, y1);
    Rgb(std::array::from_fn(|channel| {
        let top = a[channel] as f32 * (1.0 - fx) + b[channel] as f32 * fx;
        let bottom = c[channel] as f32 * (1.0 - fx) + d[channel] as f32 * fx;
        (top * (1.0 - fy) + bottom * fy).round() as u8
    }))
}

pub struct Ocr {
    detector: Session,
    recognizer: Session,
    chars: Vec<String>,
}

impl Ocr {
    pub fn load(assets: &Path) -> Result<Self> {
        ort::init_from(assets.join("onnxruntime.dll"))?.commit();
        let model = assets.join("ppocrv5");
        let detector = Session::builder()?.commit_from_file(model.join("det.onnx"))?;
        let recognizer = Session::builder()?.commit_from_file(model.join("rec.onnx"))?;
        let mut chars = vec!["".to_owned()]; // CTC blank at index zero.
        chars.extend(
            fs::read_to_string(model.join("dict.txt"))?
                .lines()
                .map(str::to_owned),
        );
        chars.push(" ".to_owned());
        Ok(Self {
            detector,
            recognizer,
            chars,
        })
    }

    pub fn recognize(&mut self, frame: &RgbaImage) -> Result<Vec<OcrLine>> {
        if frame.width() == 0 || frame.height() == 0 {
            return Ok(Vec::new());
        }
        let scale = (1280.0 / frame.width().max(frame.height()) as f32).min(2.0);
        let aw = ((frame.width() as f32 * scale).round() as u32).max(1);
        let ah = ((frame.height() as f32 * scale).round() as u32).max(1);
        let image = resize(&rgba_to_bgr(frame), aw, ah, FilterType::CatmullRom);
        // Paddle's DetResizeForTest rounds each side to a multiple of 32.
        let dw = ((aw + 16) / 32).max(1) * 32;
        let dh = ((ah + 16) / 32).max(1) * 32;
        let detector_image = resize(&image, dw, dh, FilterType::Triangle);
        let area = (dw * dh) as usize;
        let mut input = vec![0.0f32; area * 3];
        for (x, y, pixel) in detector_image.enumerate_pixels() {
            let i = (y * dw + x) as usize;
            for channel in 0..3 {
                input[channel * area + i] = (pixel[channel] as f32 / 255.0
                    - [0.485, 0.456, 0.406][channel])
                    / [0.229, 0.224, 0.225][channel];
            }
        }
        let input = Tensor::<f32>::from_array(([1, 3, dh as usize, dw as usize], input))?;
        let boxes = {
            let output = self.detector.run(inputs![input])?;
            let (shape, map) = output[0].try_extract_tensor::<f32>()?;
            if shape.len() < 4 {
                bail!("OCR 检测模型输出维度异常：{shape:?}");
            }
            let (mh, mw) = (
                shape[shape.len() - 2] as usize,
                shape[shape.len() - 1] as usize,
            );
            connected_boxes(map, mw, mh, aw, ah)
        };
        let crops = boxes
            .iter()
            .map(|item| item.crop(&image))
            .collect::<Vec<_>>();
        let mut recognized = self.recognize_crops(&crops)?;
        let vertical = boxes
            .iter()
            .enumerate()
            .filter(|(_, item)| {
                item.rotated
                    .as_ref()
                    .is_some_and(|crop| crop.axis[0].abs() < 0.35)
            })
            .map(|(index, _)| index)
            .collect::<Vec<_>>();
        if !vertical.is_empty() {
            let opposite = vertical
                .iter()
                .map(|&index| image::imageops::rotate180(&crops[index]))
                .collect::<Vec<_>>();
            for (index, candidate) in vertical.into_iter().zip(self.recognize_crops(&opposite)?) {
                if candidate.1 > recognized[index].1 + 0.08 {
                    recognized[index] = candidate;
                }
            }
        }
        let mut lines = Vec::new();
        for (detected, (text, score)) in boxes.into_iter().zip(recognized) {
            let [left, top, right, bottom] = detected.bounds;
            if score < 0.50 || text.trim().is_empty() {
                continue;
            }
            lines.push(OcrLine {
                text: text.trim().to_owned(),
                score,
                bounds: [
                    ((left as f32) / scale).round() as u32,
                    ((top as f32) / scale).round() as u32,
                    ((right as f32) / scale).round() as u32,
                    ((bottom as f32) / scale).round() as u32,
                ],
            });
        }
        lines.sort_by_key(|line| (line.bounds[1] / 12, line.bounds[0]));
        Ok(lines)
    }

    fn recognize_crops(&mut self, crops: &[RgbImage]) -> Result<Vec<(String, f32)>> {
        let mut results = vec![(String::new(), 0.0); crops.len()];
        let mut indices = (0..crops.len()).collect::<Vec<_>>();
        indices.sort_by(|&a, &b| {
            (crops[a].width() as f32 / crops[a].height().max(1) as f32)
                .total_cmp(&(crops[b].width() as f32 / crops[b].height().max(1) as f32))
        });
        let height = 48u32;
        for batch in indices.chunks(2) {
            let widths = batch
                .iter()
                .map(|&index| {
                    ((crops[index].width() as f32 * height as f32
                        / crops[index].height().max(1) as f32)
                        .ceil() as u32)
                        .max(1)
                })
                .collect::<Vec<_>>();
            let padded = widths.iter().copied().max().unwrap_or(320).max(320);
            let area = (height * padded) as usize;
            let mut input = vec![0f32; batch.len() * 3 * area];
            for (batch_index, (&index, &width)) in batch.iter().zip(&widths).enumerate() {
                let resized = resize(&crops[index], width, height, FilterType::Triangle);
                for (x, y, pixel) in resized.enumerate_pixels() {
                    let i = batch_index * 3 * area + (y * padded + x) as usize;
                    for channel in 0..3 {
                        input[i + channel * area] = pixel[channel] as f32 / 127.5 - 1.0;
                    }
                }
            }
            let output = self.recognizer.run(inputs![Tensor::<f32>::from_array((
                [batch.len(), 3, height as usize, padded as usize],
                input
            ))?])?;
            let (shape, probabilities) = output[0].try_extract_tensor::<f32>()?;
            if shape.len() != 3 || shape[0] as usize != batch.len() {
                bail!("OCR 识别模型输出维度异常：{shape:?}");
            }
            let (steps, classes) = (shape[1] as usize, shape[2] as usize);
            for (batch_index, &index) in batch.iter().enumerate() {
                let start = batch_index * steps * classes;
                results[index] = Self::decode_ctc(
                    &self.chars,
                    &probabilities[start..start + steps * classes],
                    steps,
                    classes,
                )?;
            }
        }
        Ok(results)
    }

    fn decode_ctc(
        chars: &[String],
        probabilities: &[f32],
        steps: usize,
        classes: usize,
    ) -> Result<(String, f32)> {
        let mut text = String::new();
        let mut sum = 0.0;
        let mut count = 0usize;
        let mut previous = 0usize;
        for step in 0..steps {
            let row = &probabilities[step * classes..(step + 1) * classes];
            let (index, confidence) = row
                .iter()
                .copied()
                .enumerate()
                .max_by(|a, b| a.1.total_cmp(&b.1))
                .context("空的识别输出")?;
            if index != 0 && index != previous && index < chars.len() {
                text.push_str(&chars[index]);
                sum += confidence;
                count += 1;
            }
            previous = index;
        }
        Ok((text, if count == 0 { 0.0 } else { sum / count as f32 }))
    }
}

fn rgba_to_bgr(image: &RgbaImage) -> RgbImage {
    let mut output = RgbImage::new(image.width(), image.height());
    for (x, y, pixel) in image.enumerate_pixels() {
        output.put_pixel(x, y, Rgb([pixel[2], pixel[1], pixel[0]]));
    }
    output
}

/// Find text islands in the detector probability map. Coordinates are mapped
/// back to the analysis frame; thin islands are expanded to include glyph rims.
fn connected_boxes(map: &[f32], mw: usize, mh: usize, aw: u32, ah: u32) -> Vec<Detection> {
    if mw == 0 || mh == 0 || map.len() < mw * mh {
        return Vec::new();
    }
    let mut visited = vec![false; mw * mh];
    let mut result = Vec::new();
    for start in 0..mw * mh {
        if visited[start] || map[start] <= 0.20 {
            continue;
        }
        visited[start] = true;
        let mut queue = VecDeque::from([start]);
        let mut pixels = Vec::new();
        let (mut left, mut right, mut top, mut bottom) = (mw, 0usize, mh, 0usize);
        let (mut sum, mut count) = (0f32, 0usize);
        while let Some(i) = queue.pop_front() {
            let x = i % mw;
            let y = i / mw;
            left = left.min(x);
            right = right.max(x);
            top = top.min(y);
            bottom = bottom.max(y);
            sum += map[i];
            count += 1;
            pixels.push((
                x as f32 * aw as f32 / mw as f32,
                y as f32 * ah as f32 / mh as f32,
            ));
            for (nx, ny) in [
                (x.wrapping_sub(1), y),
                (x + 1, y),
                (x, y.wrapping_sub(1)),
                (x, y + 1),
            ] {
                if nx < mw && ny < mh {
                    let neighbor = ny * mw + nx;
                    if !visited[neighbor] && map[neighbor] > 0.20 {
                        visited[neighbor] = true;
                        queue.push_back(neighbor);
                    }
                }
            }
        }
        let (width, height) = (right - left + 1, bottom - top + 1);
        if count < 12 || width < 4 || height < 4 || sum / (count as f32) < 0.60 {
            continue;
        }
        let padx = (height as f32 * 0.20).round() as usize + 2;
        let pady = (height as f32 * 0.18).round() as usize + 2;
        let x0 = (left.saturating_sub(padx) as f32 * aw as f32 / mw as f32).floor() as u32;
        let y0 = (top.saturating_sub(pady) as f32 * ah as f32 / mh as f32).floor() as u32;
        let x1 =
            (((right + padx + 1).min(mw) as f32 * aw as f32 / mw as f32).ceil() as u32).min(aw);
        let y1 =
            (((bottom + pady + 1).min(mh) as f32 * ah as f32 / mh as f32).ceil() as u32).min(ah);
        if x1 > x0 + 3 && y1 > y0 + 3 {
            let rotated = oriented_crop(&pixels);
            result.push(Detection {
                bounds: [x0, y0, x1, y1],
                rotated,
            });
        }
    }
    result.sort_by_key(|item| (item.bounds[1], item.bounds[0]));
    result
}

fn oriented_crop(pixels: &[(f32, f32)]) -> Option<RotatedCrop> {
    let n = pixels.len() as f32;
    let center = [
        pixels.iter().map(|p| p.0).sum::<f32>() / n,
        pixels.iter().map(|p| p.1).sum::<f32>() / n,
    ];
    let (mut xx, mut yy, mut xy) = (0.0, 0.0, 0.0);
    for &(x, y) in pixels {
        let dx = x - center[0];
        let dy = y - center[1];
        xx += dx * dx;
        yy += dy * dy;
        xy += dx * dy;
    }
    let angle = 0.5 * (2.0 * xy).atan2(xx - yy);
    if angle.abs() < 8.0_f32.to_radians() {
        return None;
    }
    let (sin, cos) = angle.sin_cos();
    let (mut min_u, mut max_u, mut min_v, mut max_v) = (
        f32::INFINITY,
        f32::NEG_INFINITY,
        f32::INFINITY,
        f32::NEG_INFINITY,
    );
    for &(x, y) in pixels {
        let dx = x - center[0];
        let dy = y - center[1];
        let u = dx * cos + dy * sin;
        let v = -dx * sin + dy * cos;
        min_u = min_u.min(u);
        max_u = max_u.max(u);
        min_v = min_v.min(v);
        max_v = max_v.max(v);
    }
    let thickness = max_v - min_v;
    let pad_u = thickness * 0.20 + 3.0;
    let pad_v = thickness * 0.18 + 3.0;
    let start = [min_u - pad_u, min_v - pad_v];
    let size = [
        (max_u - min_u + 2.0 * pad_u).ceil() as u32,
        (thickness + 2.0 * pad_v).ceil() as u32,
    ];
    if size[0] < 4 || size[1] < 4 {
        return None;
    }
    Some(RotatedCrop {
        center,
        axis: [cos, sin],
        start,
        size,
    })
}
