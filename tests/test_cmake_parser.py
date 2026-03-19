from translate_comments.parsers import get_parser_for_path


def test_cmake_parser_extracts_grouped_hash_comments() -> None:
    parser = get_parser_for_path("CMakeLists.txt")
    source = """
# Configure project output.
# Keep install paths stable.
project(demo)
""".strip()

    comments = parser.extract_comments(source)

    assert len(comments) == 1
    assert comments[0].line_start == 1
    assert comments[0].line_end == 2
    assert comments[0].text == "Configure project output.\nKeep install paths stable."


def test_cmake_parser_rewrites_comments() -> None:
    parser = get_parser_for_path("cmake/modules/demo.cmake")
    source = """
  # Configure project output.
  # Keep install paths stable.
  project(demo)
""".strip("\n")

    translated = parser.replace_comments(source, {
        1: "配置项目输出\n保持安装路径稳定",
    })

    assert "  # 配置项目输出\n  # 保持安装路径稳定" in translated
