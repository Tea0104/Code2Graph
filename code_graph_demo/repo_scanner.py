"""Repository scanning skeleton for Python source files."""

from __future__ import annotations

import os
from pathlib import Path

IGNORED_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "dist",
    "build",
    "target",
    ".idea",
    ".vscode",
}


def scan_python_files(src: str | Path) -> list[Path]:
    """Recursively scan a directory and return sorted Python files."""
    source_path = Path(src)
    if not source_path.exists():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")

    if source_path.is_file():
        return [source_path] if source_path.suffix == ".py" else []

    python_files: list[Path] = []
    for root, dirs, files in os.walk(source_path):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        root_path = Path(root)
        for name in files:
            if name.endswith(".py"):
                python_files.append(root_path / name)

    return sorted(python_files, key=lambda path: path.as_posix())
