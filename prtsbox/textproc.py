"""Turning raw OCR output into sensible translation units.

Two jobs, both of which have an outsized effect on how the overlay looks:

*Discarding noise.*  Screens are full of text that must not be sent to a
translator: bare numbers, hotkey hints, punctuation-only artefacts from
decorative rules, single letters that are really bullet glyphs.

*Rejoining wrapped lines.*  An OCR engine sees visual lines, but a wrapped
sentence is one sentence.  Translating each fragment separately produces the
garbled "translations" that make screen translators feel broken, so adjacent
lines that clearly belong together are merged back into one unit before
translation.

The merging is deliberately conservative.  A false merge destroys two good
translations, while a missed merge merely leaves one sentence in pieces, so
every rule below demands positive evidence of continuation (an unfinished
phrase, matching alignment) rather than assuming it.
"""

from __future__ import annotations

import re
import statistics
import unicodedata

from .models import OcrItem

# Words that cannot end an English sentence: if a line stops on one of these,
# the sentence continues on the next line.
_CONTINUATION_WORDS = frozenset(
    ["and", "or", "but", "so", "nor", "yet", "to", "of", "for", "with", "from", "in", "on", "at", "by", "the", "a", "an", "that", "which", "as", "than", "because", "who", "whose", "where", "when", "what", "why", "how", "if", "while", "is", "are", "was", "were", "be", "been", "being", "will", "would", "shall", "should", "can", "could", "may", "might", "must", "have", "has", "had", "not", "no", "into", "over", "under", "between", "during", "without", "within", "about", "after", "before", "getting", "making", "taking", "using", "trying", "going", "coming", "doing", "having", "keeping", "looking", "moving", "holding", "turning", "working", "waiting"]
)

# Bullets, numbering and list markers: these start a new item, never a wrap.
_LIST_MARKER = re.compile(r"^\s*(?:[+×✚*•·▪◦]|\d+[.)]|[A-Za-z]\)|[-–—])\s*[:：]?")
_SENTENCE_END = re.compile(r"[.!?。！？…]$")
# A line that trails off on one of these is mid-sentence.
_DANGLING_TAIL = ",，、:：;；-–—…（([\"'“‘"
_CJK = re.compile(r"[\u2e80-\u9fff\uff00-\uffef]")

# Han ideographs, including the extension A and compatibility blocks that OCR
# occasionally emits for rarer characters.
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# Japanese kana.  Their presence means the text is Japanese, not Chinese, even
# though it also contains Han characters.
_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
# Fallback share of Han characters for a line that does not open in Chinese.
# The primary signal is the opening character, because that is what separates a
# Chinese line that embeds a Latin product name from an English line that
# embeds a Chinese word - a ratio alone cannot tell "版本 2.0 Beta" (0.33) from
# "Press 开始" (0.29), but the opening character separates them cleanly.
_CHINESE_RATIO = 0.5


def _bounds(item: OcrItem) -> tuple[float, float, float, float]:
    return item.bounds


def has_unclosed_delimiter(text: str) -> bool:
    """Whether a line opens a bracket or quote it never closes."""
    for opener, closer in (("(", ")"), ("[", "]"), ("{", "}")):
        if text.count(opener) > text.count(closer):
            return True
    # An odd count of straight quotes means the quoted phrase runs on.  An
    # apostrophe inside a word (don't) is not a delimiter, hence the pairing
    # check on curly quotes only.
    if text.count('"') % 2:
        return True
    return text.count("“") > text.count("”") or text.count("‘") > text.count("’")


def is_already_chinese(text: str) -> bool:
    """Whether a line is already written in Chinese.

    Translating Chinese into Chinese is at best a no-op and at worst an
    invitation for the model to silently reword the user's own text, so these
    lines are dropped before translation.

    Japanese is explicitly excluded.  It borrows Han characters, so a line of
    kanji alone would otherwise look Chinese; kana is what tells them apart, and
    Japanese very much needs translating.

    The opening character decides, with a ratio fallback.  Chinese interfaces
    routinely mix in Latin names, so "版本 2.0 Beta" and "已连接 WiFi" are
    Chinese lines that happen to contain Latin; meanwhile "Press 开始" is an
    English line that happens to contain Chinese.  Their Han ratios are close
    enough to overlap, but their openings are unambiguous.  When the two rules
    disagree the line is translated, because a missed translation is a silent
    gap on screen whereas a needless one only risks a reworded line.
    """
    if _KANA.search(text):
        return False

    # Digits and punctuation say nothing about language, so "第 3 章" is judged
    # on 第 and 章 rather than being diluted below the threshold.
    meaningful = [
        character
        for character in text
        if not character.isspace()
        and unicodedata.category(character)[0] not in {"P", "S", "N"}
    ]
    if not meaningful:
        return False

    han = sum(1 for character in meaningful if _HAN.match(character))
    if not han:
        return False
    if _HAN.match(meaningful[0]):
        return True
    return han / len(meaningful) >= _CHINESE_RATIO


def is_continuation(previous: str, following: str) -> bool:
    """Whether ``following`` looks like the rest of ``previous``'s sentence.

    This reads the text only.  Layout evidence - whether a line actually ran out
    of horizontal room - is applied separately by :func:`merge_wrapped_lines`,
    because a column of short CJK labels passes every textual test here.
    """
    previous = previous.strip()
    following = following.strip()
    if not previous or not following:
        return False
    if has_unclosed_delimiter(previous):
        return True
    if _SENTENCE_END.search(previous):
        return False

    if _CJK.search(previous):
        # Chinese and Japanese have no case and frequently no trailing
        # punctuation, so the absence of a terminator is the only signal.
        return True

    if previous[-1] in _DANGLING_TAIL:
        return True
    words = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", previous.casefold())
    if words and words[-1] in _CONTINUATION_WORDS:
        return True
    return bool(following[:1].islower())


def _cjk_actually_wrapped(
    previous: OcrItem,
    column_edge_of: dict[int, float],
    median_height: float,
) -> bool:
    """Decide whether a CJK line wrapped, using layout rather than wording.

    Chinese and Japanese give no case or punctuation hints, so an unpunctuated
    line is ambiguous: it could be the first half of a paragraph or a complete
    menu entry.  Two layout facts separate them.  A wrapped line ran out of
    horizontal room, so it reaches the right edge of its column; and it is long
    enough that running out of room was plausible in the first place, which is
    what stops short stacked menu items ("文件", "编辑") being glued together.
    Long entries in a vertical list remain the known failure mode.
    """
    left, _top, right, _bottom = _bounds(previous)
    width = right - left

    # A CJK glyph is roughly square, so the line height is a good proxy for one
    # character's width regardless of the font size in use.
    if width < median_height * _MIN_CJK_WRAP_CHARS:
        return False
    edge = column_edge_of.get(id(previous), right)
    return width >= (edge - left) * 0.85


def _column_right_edges(ordered: list[OcrItem], tolerance: float) -> list[float]:
    """Right edge of the widest line in each item's column.

    A wrapped line is one that ran out of horizontal room, so it reaches the
    right edge of its text block; a short label in the same column does not.
    Comparing against the widest sibling is what tells a line of prose that
    merely lacks a full stop apart from a menu entry - without having to find
    the text block itself.
    """
    columns: dict[int, float] = {}
    for item in ordered:
        key = round(_bounds(item)[0] / tolerance)
        columns[key] = max(columns.get(key, 0.0), _bounds(item)[2])
    return [columns[round(_bounds(item)[0] / tolerance)] for item in ordered]


def should_translate(text: str) -> bool:
    """Whether a string carries meaning worth sending to a translator."""
    compact = [character for character in text.strip() if not character.isspace()]
    if not compact:
        return False

    # Punctuation and symbols alone carry no meaning.
    semantic = [
        character
        for character in compact
        if unicodedata.category(character)[0] not in {"P", "S"}
    ]
    if not semantic:
        return False
    # "1250" or "1,250" is data, not prose; translating it only risks changing
    # the value.
    if all(character.isdigit() for character in semantic):
        return False
    # A lone Latin letter is a key hint ("W", "E") rather than a word.
    if len(semantic) == 1 and semantic[0].isalpha():
        try:
            if "LATIN" in unicodedata.name(semantic[0], ""):
                return False
        except ValueError:
            pass
    return not looks_like_icon_noise(text)


# Toolbars and icon strips are a rich source of false positives: OCR reads a
# row of glyphs as text and produces things like "H1 ν =ν B IS  ⊕V".  What
# separates those from prose is that they shatter into many one- and two-letter
# fragments.  Real sentences average four to five characters per word, so the
# threshold has a wide margin and does not touch short real strings such as
# "Loading..." or "Save".
_MIN_WORDS_FOR_NOISE_CHECK = 4
_MIN_AVERAGE_WORD_LENGTH = 2.5
# A CJK line must be at least this many characters wide before "it ran out of
# room" is believable.  Eight characters is longer than a typical menu entry
# and shorter than a line of prose that filled a column.
_MIN_CJK_WRAP_CHARS = 8


def looks_like_icon_noise(text: str) -> bool:
    if _CJK.search(text):
        # Case and word length say nothing about Chinese or Japanese, where a
        # short string is perfectly normal.
        return False
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    if len(words) < _MIN_WORDS_FOR_NOISE_CHECK:
        return False
    average = sum(len(word) for word in words) / len(words)
    return average < _MIN_AVERAGE_WORD_LENGTH


def merge_wrapped_lines(items: list[OcrItem]) -> list[OcrItem]:
    """Join OCR lines that are one wrapped sentence, leaving the rest alone."""
    if len(items) < 2:
        return list(items)

    ordered = sorted(items, key=lambda item: (_bounds(item)[1], _bounds(item)[0]))
    heights = [max(1.0, _bounds(item)[3] - _bounds(item)[1]) for item in ordered]
    median_height = statistics.median(heights)
    column_edges = _column_right_edges(ordered, max(8.0, median_height * 0.8))
    column_edge_of = dict(zip((id(item) for item in ordered), column_edges, strict=True))

    merged: list[OcrItem] = []
    group: list[OcrItem] = []

    def flush() -> None:
        if not group:
            return
        if len(group) == 1:
            merged.append(group[0])
            return
        boxes = [point for item in group for point in item.box]
        texts = [item.text.strip() for item in group]
        # Chinese and Japanese are written without spaces between words, so
        # joining them with one would insert breaks that were never there.
        separator = "" if any(_CJK.search(text) for text in texts) else " "
        merged.append(
            OcrItem(
                box=(
                    (min(p[0] for p in boxes), min(p[1] for p in boxes)),
                    (max(p[0] for p in boxes), min(p[1] for p in boxes)),
                    (max(p[0] for p in boxes), max(p[1] for p in boxes)),
                    (min(p[0] for p in boxes), max(p[1] for p in boxes)),
                ),
                text=separator.join(texts),
                confidence=min(item.confidence for item in group),
            )
        )

    for item in ordered:
        if not group:
            group = [item]
            continue

        previous = group[-1]
        px1, _py1, px2, py2 = _bounds(previous)
        x1, y1, x2, _y2 = _bounds(item)

        # Wrapped lines sit close together - closer than separate paragraphs,
        # but not so close that they overlap.
        vertical_gap = y1 - py2
        close_enough = vertical_gap <= max(10.0, median_height * 0.75)

        previous_width = max(1.0, px2 - px1)
        current_width = max(1.0, x2 - x1)
        previous_center = (px1 + px2) / 2.0
        current_center = (x1 + x2) / 2.0
        centered = abs(current_center - previous_center) <= max(
            median_height * 1.5, previous_width * 0.35
        )
        left_aligned = abs(px1 - x1) <= max(10.0, median_height * 0.8)
        overlap = max(0.0, min(px2, x2) - max(px1, x1))
        overlapping = overlap >= min(previous_width, current_width) * 0.35

        continuation = is_continuation(previous.text, item.text)
        is_list = bool(_LIST_MARKER.match(previous.text) or _LIST_MARKER.match(item.text))

        if continuation and _CJK.search(previous.text):
            continuation = _cjk_actually_wrapped(previous, column_edge_of, median_height)

        # Centred text is a strong signal on its own (dialogs, subtitles);
        # left-aligned text only merges when the wording proves the sentence
        # is unfinished, which is what keeps separate list rows apart.
        alignment_ok = centered or (continuation and (left_aligned or overlapping))

        if close_enough and alignment_ok and continuation and not is_list:
            group.append(item)
        else:
            flush()
            group = [item]

    flush()
    return merged


def prepare(items: list[OcrItem]) -> list[OcrItem]:
    """Full cleanup pass: drop noise, then rejoin wrapped lines."""
    meaningful = [item for item in items if should_translate(item.text)]
    return merge_wrapped_lines(meaningful)
