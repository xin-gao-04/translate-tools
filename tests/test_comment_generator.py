from translate_comments.comment_generator import HeaderCommentOptions, _normalize_comment
from translate_comments.header_parser import HeaderSymbolInfo


def _symbol() -> HeaderSymbolInfo:
    return HeaderSymbolInfo(
        kind="function",
        name="PlotBoth",
        full_signature="bool PlotBoth(XsfEM_Rcvr* rcvrPtr);",
        line_start=1,
        line_end=1,
        class_context="AntennaPlotFunction",
        namespace_context="",
        has_comment=False,
        existing_comment="",
        comment_line_start=0,
        comment_line_end=0,
    )


def test_normalize_comment_keeps_author_and_date_inside_block() -> None:
    raw = "/** @brief Draw both antenna gain patterns. */\n * @author model\n * @date 2026-03-23"
    normalized = _normalize_comment(
        raw,
        _symbol(),
        HeaderCommentOptions(author="fzone", include_date=True, date_format="%Y-%m-%d"),
    )

    assert normalized == (
        "/**\n"
        " * @brief Draw both antenna gain patterns.\n"
        " * @author fzone\n"
        " * @date 2026-03-23\n"
        " */"
    )
