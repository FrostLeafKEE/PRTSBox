//! Conservative OCR noise filtering and wrapped-line assembly.

use crate::ocr::OcrLine;
use std::collections::HashMap;

pub fn prepare(lines: Vec<OcrLine>) -> Vec<OcrLine> {
    let mut ordered = lines
        .into_iter()
        .filter(|line| should_translate(&line.text))
        .collect::<Vec<_>>();
    if ordered.len() < 2 {
        return ordered;
    }
    ordered.sort_by_key(|line| (line.bounds[1], line.bounds[0]));
    let mut heights = ordered
        .iter()
        .map(|line| line.bounds[3].saturating_sub(line.bounds[1]).max(1) as f32)
        .collect::<Vec<_>>();
    heights.sort_by(f32::total_cmp);
    let median = heights[heights.len() / 2];
    let tolerance = (median * 0.8).max(8.0);
    let column = |line: &OcrLine| (line.bounds[0] as f32 / tolerance).round() as i32;
    let mut edges = HashMap::<i32, u32>::new();
    for line in &ordered {
        edges
            .entry(column(line))
            .and_modify(|x| *x = (*x).max(line.bounds[2]))
            .or_insert(line.bounds[2]);
    }
    let mut result = Vec::new();
    let mut group = Vec::new();
    for line in ordered {
        if let Some(previous) = group.last()
            && !can_merge(
                previous,
                &line,
                median,
                *edges.get(&column(previous)).unwrap_or(&previous.bounds[2]),
            )
        {
            result.push(merge_group(&group));
            group.clear();
        }
        group.push(line);
    }
    if !group.is_empty() {
        result.push(merge_group(&group));
    }
    result
}

fn should_translate(text: &str) -> bool {
    let semantic = text
        .chars()
        .filter(|ch| ch.is_alphanumeric())
        .collect::<Vec<_>>();
    if semantic.is_empty() || semantic.iter().all(|ch| ch.is_numeric()) {
        return false;
    }
    if semantic.len() == 1 && semantic[0].is_ascii_alphabetic() {
        return false;
    }
    true
}

fn contains_cjk(text: &str) -> bool {
    text.chars().any(|ch| {
        ('\u{2e80}'..='\u{9fff}').contains(&ch) || ('\u{ff00}'..='\u{ffef}').contains(&ch)
    })
}

fn can_merge(a: &OcrLine, b: &OcrLine, median: f32, column_edge: u32) -> bool {
    let [ax1, _ay1, ax2, ay2] = a.bounds;
    let [bx1, by1, bx2, _by2] = b.bounds;
    let gap = by1 as i32 - ay2 as i32;
    if gap as f32 > (median * 0.75).max(10.0) {
        return false;
    }
    if list_marker(&a.text) || list_marker(&b.text) {
        return false;
    }
    if !continuation(&a.text, &b.text) {
        return false;
    }
    if contains_cjk(&a.text) {
        let width = ax2.saturating_sub(ax1) as f32;
        if width < median * 8.0 || width < column_edge.saturating_sub(ax1) as f32 * 0.85 {
            return false;
        }
    }
    let width_a = ax2.saturating_sub(ax1).max(1) as f32;
    let width_b = bx2.saturating_sub(bx1).max(1) as f32;
    let centered = ((ax1 + ax2) as f32 / 2.0 - (bx1 + bx2) as f32 / 2.0).abs()
        <= (median * 1.5).max(width_a * 0.35);
    let left_aligned = (ax1 as i32 - bx1 as i32).abs() as f32 <= (median * 0.8).max(10.0);
    let overlap = ax2.min(bx2).saturating_sub(ax1.max(bx1)) as f32;
    centered || left_aligned || overlap >= width_a.min(width_b) * 0.35
}

fn continuation(previous: &str, following: &str) -> bool {
    let previous = previous.trim();
    let following = following.trim();
    if previous.is_empty() || following.is_empty() {
        return false;
    }
    for (open, close) in [('(', ')'), ('[', ']'), ('{', '}'), ('“', '”'), ('‘', '’')] {
        if previous.matches(open).count() > previous.matches(close).count() {
            return true;
        }
    }
    if previous.matches('"').count() % 2 == 1 {
        return true;
    }
    if previous.ends_with(['.', '!', '?', '。', '！', '？', '…']) {
        return false;
    }
    if contains_cjk(previous) {
        return true;
    }
    if previous.ends_with([
        ',', '，', '、', ':', '：', ';', '；', '-', '–', '—', '（', '(', '[', '"', '\'', '“', '‘',
    ]) {
        return true;
    }
    let last_word = previous
        .split(|ch: char| !ch.is_ascii_alphabetic())
        .rfind(|word| !word.is_empty())
        .unwrap_or("")
        .to_ascii_lowercase();
    if [
        "and", "or", "but", "to", "of", "for", "with", "from", "in", "on", "at", "by", "the", "a",
        "an", "that", "which", "as", "than", "because", "who", "where", "when", "what", "why",
        "how", "if", "while", "is", "are", "was", "were", "be", "been", "will", "would", "can",
        "could", "may", "might", "must", "have", "has", "had", "not", "into", "over", "under",
        "between", "during", "without", "within", "about", "after", "before", "getting", "making",
        "taking", "using", "trying", "going", "coming", "doing", "having", "keeping", "looking",
        "moving", "holding", "turning", "working", "waiting",
    ]
    .contains(&last_word.as_str())
    {
        return true;
    }
    following.chars().next().is_some_and(char::is_lowercase)
}

fn list_marker(text: &str) -> bool {
    let trimmed = text.trim_start();
    if trimmed.starts_with(['+', '×', '✚', '*', '•', '·', '▪', '◦', '-', '–', '—']) {
        return true;
    }
    let mut chars = trimmed.chars();
    if chars
        .next()
        .is_some_and(|ch| ch.is_ascii_digit() || ch.is_ascii_alphabetic())
    {
        let rest = chars.as_str();
        return rest.starts_with(['.', ')'])
            || rest.chars().next().is_some_and(|ch| ch.is_ascii_digit())
                && (rest.ends_with('.') || rest.ends_with(')'));
    }
    false
}

fn merge_group(group: &[OcrLine]) -> OcrLine {
    let mut result = group[0].clone();
    if group.len() == 1 {
        return result;
    }
    let separator = if group.iter().any(|line| contains_cjk(&line.text)) {
        ""
    } else {
        " "
    };
    result.text = group
        .iter()
        .map(|line| line.text.trim())
        .collect::<Vec<_>>()
        .join(separator);
    for line in &group[1..] {
        result.bounds[0] = result.bounds[0].min(line.bounds[0]);
        result.bounds[1] = result.bounds[1].min(line.bounds[1]);
        result.bounds[2] = result.bounds[2].max(line.bounds[2]);
        result.bounds[3] = result.bounds[3].max(line.bounds[3]);
        result.score = result.score.min(line.score);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    fn line(text: &str, x: u32, y: u32, w: u32) -> OcrLine {
        OcrLine {
            text: text.into(),
            score: 1.0,
            bounds: [x, y, x + w, y + 20],
        }
    }
    #[test]
    fn joins_a_wrapped_sentence_but_not_menu_rows() {
        let joined = prepare(vec![
            line("Please read the", 10, 10, 200),
            line("instructions carefully.", 10, 34, 210),
        ]);
        assert_eq!(joined.len(), 1);
        assert_eq!(joined[0].text, "Please read the instructions carefully.");
        let menu = prepare(vec![line("文件", 10, 10, 40), line("编辑", 10, 34, 40)]);
        assert_eq!(menu.len(), 2);
    }
}

#[cfg(test)]
#[test]
fn short_english_sentences_are_not_noise() {
    for text in ["It is up to me.", "Do it as I do.", "I am on my way."] {
        assert!(should_translate(text));
    }
    for text in ["12345", "---", ""] {
        assert!(!should_translate(text));
    }
}
