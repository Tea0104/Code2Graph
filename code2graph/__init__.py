"""Public API for the repository-level Code2Graph pipeline.

The package keeps imports lightweight so callers can use static repository
analysis helpers without installing the optional embedding/full-graph stack.
"""

__all__ = [
    "InitializationResult",
    "TargetToSourceCodeAPI",
    "Code2GraphPipeline",
    "initialize_repository",
    "locate_source_code",
    "initialize",
    "get_translation_order",
    "get_translation_batch",
    "locate_target_test_to_source_code",
    "RepoAnalyze",
]


def __getattr__(name: str):
    if name == "InitializationResult":
        from .results import InitializationResult

        return InitializationResult
    if name == "initialize_repository":
        from .initialization import initialize_repository

        return initialize_repository
    if name in {"TargetToSourceCodeAPI", "locate_source_code"}:
        from . import mapping

        return getattr(mapping, name)
    if name in {
        "Code2GraphPipeline",
        "initialize",
        "get_translation_order",
        "get_translation_batch",
        "locate_target_test_to_source_code",
    }:
        from . import api

        return getattr(api, name)
    if name == "RepoAnalyze":
        from repoanalyze import RepoAnalyze

        return RepoAnalyze
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
