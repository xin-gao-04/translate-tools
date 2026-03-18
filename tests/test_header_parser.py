from translate_comments.header_parser import parse_header


def _names(source: str) -> list[tuple[int, str, str]]:
    return [(f.line_start, f.name, f.class_context) for f in parse_header(source)]


def test_parses_constructors_and_destructors_without_return_types() -> None:
    source = """
class MyClass {
public:
    MyClass();
    ~MyClass();
    void run();
};
"""
    assert _names(source) == [
        (4, "MyClass", "MyClass"),
        (5, "~MyClass", "MyClass"),
        (6, "run", "MyClass"),
    ]


def test_keeps_tracking_braces_on_skipped_control_flow_lines() -> None:
    source = """
class A
{
public:
    if (ready) {
        doThing();
    }
    A();
    ~A();
    void ok();
};
"""
    assert _names(source) == [
        (8, "A", "A"),
        (9, "~A", "A"),
        (10, "ok", "A"),
    ]
