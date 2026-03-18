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
