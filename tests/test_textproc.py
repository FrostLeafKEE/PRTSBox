"""Text cleanup and wrapped-line merging."""

from __future__ import annotations

import pytest

from prtsbox.models import OcrItem, is_chinese_language
from prtsbox.textproc import (
    has_unclosed_delimiter,
    is_already_chinese,
    is_continuation,
    looks_like_icon_noise,
    merge_wrapped_lines,
    prepare,
    should_translate,
)


def line(text: str, left: float, top: float, width: float = 200.0, height: float = 16.0):
    return OcrItem(
        box=((left, top), (left + width, top), (left + width, top + height), (left, top + height)),
        text=text,
        confidence=0.95,
    )


class TestShouldTranslate:
    @pytest.mark.parametrize(
        "text",
        ["Loading...", "Save", "Are you sure you want to delete this file?", "行7,列1", "UTF-8"],
    )
    def test_keeps_real_text(self, text: str) -> None:
        assert should_translate(text)

    @pytest.mark.parametrize(
        "text",
        ["", "   ", "1250", "1,250", "×", "•••", "+", "A", "W", "---"],
    )
    def test_rejects_noise(self, text: str) -> None:
        assert not should_translate(text)

    def test_rejects_toolbar_icon_noise(self) -> None:
        # Observed verbatim from a Notepad menu bar capture.
        assert not should_translate("H1 ν =ν B IS  ⊕V")

    def test_keeps_short_chinese(self) -> None:
        # The icon rule must not touch CJK, where short strings are normal.
        assert should_translate("文件")
        assert should_translate("编辑查看")

    def test_icon_noise_needs_several_words(self) -> None:
        # Two short words are not enough evidence of an icon strip.
        assert not looks_like_icon_noise("A B")
        assert looks_like_icon_noise("A B C D E")


class TestContinuation:
    def test_open_bracket_continues(self) -> None:
        assert is_continuation("This is (a test", "that continues)")

    def test_unbalanced_quote_continues(self) -> None:
        assert is_continuation('He said "hello', 'world"')

    def test_sentence_end_stops(self) -> None:
        assert not is_continuation("This is done.", "New sentence here")
        assert not is_continuation("完成了。", "下一句")

    def test_trailing_conjunction_continues(self) -> None:
        assert is_continuation("Press the button and", "then wait")

    def test_dangling_comma_continues(self) -> None:
        assert is_continuation("First, second,", "third")

    def test_lowercase_start_continues(self) -> None:
        assert is_continuation("The quick brown fox", "jumps over")

    def test_uppercase_start_does_not_continue(self) -> None:
        assert not is_continuation("Some heading", "Another heading")

    def test_cjk_without_terminator_may_continue(self) -> None:
        # Chinese has no case to read, so the text-level check can only say
        # "not obviously finished"; layout decides the rest.
        assert is_continuation("目标窗口不在前台", "时会暂停截图")
        assert not is_continuation("目标窗口不在前台。", "下一句")

    def test_apostrophe_is_not_a_quote(self) -> None:
        assert not has_unclosed_delimiter("don't stop")


class TestAlreadyChinese:
    @pytest.mark.parametrize(
        "text",
        [
            "目标窗口不在前台",
            "文件",
            "已连接 WiFi",
            "第 3 章 开始",
            "确定要删除吗？",
            "保存成功，共 12 项",
            "纯文本 UTF-8",
            "版本 2.0 Beta",
        ],
    )
    def test_detects_chinese(self, text: str) -> None:
        assert is_already_chinese(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Loading...",
            "Are you sure you want to delete this file?",
            "Damage: 1250  Critical Hit!",
            "Save",
            "1250",
            "API Key",
            # Mostly English with a stray Chinese word is an English line: it
            # is an instruction the user cannot read yet.
            "Click 确定 to continue",
            "Press 开始",
        ],
    )
    def test_leaves_other_languages_alone(self, text: str) -> None:
        assert not is_already_chinese(text)

    def test_japanese_is_not_chinese(self) -> None:
        # Japanese borrows Han characters; kana is what tells them apart, and
        # Japanese very much needs translating.
        assert not is_already_chinese("設定を開いてください")
        assert not is_already_chinese("ゲームを開始")
        # Kanji-only Japanese is indistinguishable from Chinese by script
        # alone; kana elsewhere on screen is what carries the distinction.
        assert is_already_chinese("東京大学")

    def test_empty_and_punctuation_only(self) -> None:
        assert not is_already_chinese("")
        assert not is_already_chinese("   ")
        assert not is_already_chinese("!!!")


class TestChineseLanguage:
    @pytest.mark.parametrize("code", ["zh-CN", "zh-TW", "ZH-cn"])
    def test_chinese_variants(self, code: str) -> None:
        assert is_chinese_language(code)

    @pytest.mark.parametrize("code", ["en", "ja", "ko", "auto", ""])
    def test_other_languages(self, code: str) -> None:
        assert not is_chinese_language(code)


class TestMergeWrappedLines:
    def test_single_item_untouched(self) -> None:
        items = [line("Loading...", 0, 0)]
        assert merge_wrapped_lines(items) == items

    def test_merges_wrapped_english(self) -> None:
        items = [line("The quick brown fox", 0, 0), line("jumps over the dog", 0, 18)]
        merged = merge_wrapped_lines(items)
        assert len(merged) == 1
        assert merged[0].text == "The quick brown fox jumps over the dog"

    def test_joins_cjk_without_spaces(self) -> None:
        items = [
            line("目标窗口不在前台时会暂停", 0, 0, width=240),
            line("截图以避免占用资源。", 0, 18, width=180),
        ]
        merged = merge_wrapped_lines(items)
        assert len(merged) == 1
        assert merged[0].text == "目标窗口不在前台时会暂停截图以避免占用资源。"

    def test_does_not_merge_stacked_cjk_menu_items(self) -> None:
        # A vertical menu is unpunctuated and left aligned just like wrapped
        # prose; only the short line length keeps the entries apart.
        items = [
            line("文件", 0, 0, width=40),
            line("编辑", 0, 18, width=40),
            line("查看", 0, 36, width=40),
        ]
        assert len(merge_wrapped_lines(items)) == 3

    def test_does_not_merge_short_cjk_dialog_buttons(self) -> None:
        items = [line("确定", 0, 0, width=56), line("取消", 0, 20, width=56)]
        assert len(merge_wrapped_lines(items)) == 2

    def test_does_not_merge_separate_sentences(self) -> None:
        items = [line("First sentence.", 0, 0), line("Second sentence.", 0, 18)]
        merged = merge_wrapped_lines(items)
        assert len(merged) == 2

    def test_does_not_merge_distant_lines(self) -> None:
        # A paragraph gap is bigger than a wrap.
        items = [line("The quick brown fox", 0, 0), line("jumps over the dog", 0, 60)]
        assert len(merge_wrapped_lines(items)) == 2

    def test_does_not_merge_list_items(self) -> None:
        items = [line("1. First option", 0, 0), line("2. Second option", 0, 18)]
        assert len(merge_wrapped_lines(items)) == 2

    def test_text_lowercase_starting_a_real_sentence_is_merged_only_when_wrapped(self) -> None:
        # Right-aligned columns are not one sentence even if they read alike.
        items = [line("Left column text", 0, 0), line("right column", 400, 18)]
        assert len(merge_wrapped_lines(items)) == 2

    def test_merged_box_covers_both_lines(self) -> None:
        items = [line("The quick brown fox", 10, 0, width=100), line("jumps over", 10, 18, width=60)]
        merged = merge_wrapped_lines(items)
        assert len(merged) == 1
        left, top, right, bottom = merged[0].bounds
        assert (left, top) == (10, 0)
        assert right == 110
        assert bottom == 34

    def test_lowest_confidence_is_kept(self) -> None:
        first = line("The quick brown fox", 0, 0)
        second = OcrItem(
            box=((0, 18), (200, 18), (200, 34), (0, 34)), text="jumps over", confidence=0.61
        )
        merged = merge_wrapped_lines([first, second])
        assert merged[0].confidence == pytest.approx(0.61)


class TestPrepare:
    def test_drops_noise_then_merges(self) -> None:
        items = [
            line("1250", 0, 0),
            line("The quick brown fox", 0, 20),
            line("jumps over the dog", 0, 38),
            line("×", 0, 60),
        ]
        prepared = prepare(items)
        assert len(prepared) == 1
        assert prepared[0].text == "The quick brown fox jumps over the dog"
