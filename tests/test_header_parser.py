from translate_comments.header_parser import parse_header, parse_header_symbols


def _names(source: str) -> list[tuple[int, str, str]]:
    return [(f.line_start, f.name, f.class_context) for f in parse_header(source)]


def _symbols(source: str) -> list[tuple[int, str, str]]:
    return [(symbol.line_start, symbol.name, symbol.kind) for symbol in parse_header_symbols(source)]


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


def test_parses_namespace_wrapped_declarations() -> None:
    source = """
namespace demo {
class Foo {
public:
    Foo();
    ~Foo();
    void ok();
};

void free_func();
}
"""
    assert _names(source) == [
        (5, "Foo", "Foo"),
        (6, "~Foo", "Foo"),
        (7, "ok", "Foo"),
        (10, "free_func", ""),
    ]


def test_parses_variables_alongside_functions() -> None:
    source = """
namespace demo {
class Config {
public:
    static constexpr int kLimit = 4;
    std::string name_;
    void run();
};

extern int g_counter;
}
"""
    assert _symbols(source) == [
        (5, "kLimit", "variable"),
        (6, "name_", "variable"),
        (7, "run", "function"),
        (10, "g_counter", "variable"),
    ]
