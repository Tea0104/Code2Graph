from __future__ import annotations

import importlib
from typing import Any

from .languages import LANGUAGE_SPECS, normalize_language


def parser_for(language: str) -> Any:
    """Create a Tree-sitter parser from the shared language registry."""
    from tree_sitter import Language, Parser

    canonical = normalize_language(language)
    spec = LANGUAGE_SPECS[canonical]
    grammar = importlib.import_module(spec.tree_sitter_module)
    parser = Parser()
    parser.language = Language(grammar.language())
    return parser


def node_text(source: str, node) -> str:
    return source.encode("utf-8")[node.start_byte : node.end_byte].decode(
        "utf-8", errors="replace"
    )
