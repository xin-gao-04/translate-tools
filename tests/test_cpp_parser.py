from translate_comments.parsers.cpp import CppParser


def test_groups_consecutive_line_comments_into_one_block() -> None:
    parser = CppParser()
    source = """
// First sentence.
// Second sentence.
int value = 1;
"""
    comments = parser.extract_comments(source)

    assert len(comments) == 1
    assert comments[0].line_start == 2
    assert comments[0].line_end == 3
    assert comments[0].text == "First sentence.\nSecond sentence."


def test_rewraps_grouped_line_comment_as_multiline_block() -> None:
    parser = CppParser()
    source = """
    // First sentence.
    // Second sentence.
    int value = 1;
"""
    translated = parser.replace_comments(source, {
        2: "第一句。\n第二句。",
    })

    assert "    // 第一句。\n    // 第二句。" in translated


def test_rewraps_grouped_line_comment_without_extra_blank_lines() -> None:
    parser = CppParser()
    source = """
    // Initialize the widget.
    // Keep state in sync.
    int value = 1;
""".strip("\n")

    translated = parser.replace_comments(source, {
        1: "初始化组件\n\n保持状态同步",
    })

    assert "    // 初始化组件\n    // 保持状态同步" in translated
    assert "\n    //\n" not in translated


def test_rewraps_single_line_translation_to_original_group_size() -> None:
    parser = CppParser()
    source = """
    // First sentence.
    // Second sentence.
    int value = 1;
""".strip("\n")

    translated = parser.replace_comments(source, {
        1: "初始化组件并保持状态同步",
    })

    assert translated.count("    // ") == 2
    assert "\n    //\n" not in translated
