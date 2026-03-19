"""File scanner — discovers source files by extension."""

from __future__ import annotations

import os
from pathlib import Path


class FileScanner:
    """Walk a path and yield files whose extension matches *extensions*.

    Parameters
    ----------
    extensions:
        Collection of extensions to include, with or without a leading dot
        (e.g. ``[".cpp", "h"]``).  Matching is case-insensitive.
    recursive:
        If True, descend into subdirectories.
    follow_symlinks:
        Whether to follow symbolic links during directory traversal.
    exclude_dirs:
        Directory names (not paths) to skip entirely (e.g. ``[".git", "build"]``).
    """

    DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
        ".git", ".svn", ".hg",
        "build", "cmake-build-debug", "cmake-build-release",
        "out", "dist", ".cache", "node_modules",
    })

    def __init__(
        self,
        extensions: list[str],
        recursive: bool = True,
        follow_symlinks: bool = False,
        exclude_dirs: set[str] | None = None,
    ) -> None:
        self._extensions: frozenset[str] = frozenset(
            ext.lower().lstrip(".") for ext in extensions
        )
        self.recursive = recursive
        self.follow_symlinks = follow_symlinks
        self.exclude_dirs = (
            self.DEFAULT_EXCLUDE_DIRS
            if exclude_dirs is None
            else frozenset(exclude_dirs)
        )

    # ------------------------------------------------------------------ #

    def scan(self, path: str | Path) -> list[Path]:
        """Return a sorted list of matching files under *path*.

        *path* may be a single file or a directory.
        """
        root = Path(path).expanduser().resolve()

        if root.is_file():
            return [root] if self._matches(root) else []

        if not root.is_dir():
            raise FileNotFoundError(f"Path not found: {root}")

        results: list[Path] = []
        self._walk(root, results)
        results.sort()
        return results

    # ------------------------------------------------------------------ #

    def _walk(self, directory: Path, results: list[Path]) -> None:
        try:
            entries = list(directory.iterdir())
        except PermissionError:
            return

        for entry in entries:
            if entry.is_symlink() and not self.follow_symlinks:
                continue
            if entry.is_dir():
                if entry.name not in self.exclude_dirs and self.recursive:
                    self._walk(entry, results)
            elif entry.is_file() and self._matches(entry):
                results.append(entry)

    def _matches(self, path: Path) -> bool:
        normalized = path.name.lower()
        if normalized in self._extensions:
            return True
        return path.suffix.lower().lstrip(".") in self._extensions
