"""Parser registry — maps file extensions to parser classes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from translate_comments.parsers.base import BaseParser

_REGISTRY: dict[str, type["BaseParser"]] = {}
_PATH_REGISTRY: dict[str, type["BaseParser"]] = {}


def register(extensions: list[str]):
    """Class decorator: register a parser for the given file extensions."""
    def decorator(cls):
        for ext in extensions:
            _REGISTRY[ext.lower().lstrip(".")] = cls
        return cls
    return decorator


def register_paths(paths: list[str]):
    """Class decorator: register a parser for exact file names/paths."""
    def decorator(cls):
        for path in paths:
            _PATH_REGISTRY[path.lower().replace("\\", "/")] = cls
        return cls
    return decorator


def get_parser(extension: str) -> "BaseParser | None":
    """Return an instantiated parser for *extension*, or None if unsupported."""
    ext = extension.lower().lstrip(".")
    cls = _REGISTRY.get(ext)
    return cls() if cls else None


def get_parser_for_path(path: str) -> "BaseParser | None":
    """Return a parser for an exact path/name or its extension."""
    normalized = path.lower().replace("\\", "/")
    cls = _PATH_REGISTRY.get(normalized)
    if cls:
        return cls()

    basename = normalized.rsplit("/", 1)[-1]
    cls = _PATH_REGISTRY.get(basename)
    if cls:
        return cls()

    suffix = basename.rsplit(".", 1)
    ext = suffix[-1] if len(suffix) == 2 else ""
    cls = _REGISTRY.get(ext)
    return cls() if cls else None


def registered_extensions() -> list[str]:
    """Return all currently registered extensions (without leading dot)."""
    return list(_REGISTRY.keys())


def registered_path_patterns() -> list[str]:
    """Return exact file names/paths that have dedicated parsers."""
    return list(_PATH_REGISTRY.keys())


# Import parsers so their @register decorators fire.
from translate_comments.parsers import cpp  # noqa: E402, F401
from translate_comments.parsers import cmake  # noqa: E402, F401
