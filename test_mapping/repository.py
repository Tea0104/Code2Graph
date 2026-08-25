from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repository_analysis.languages import display_language, normalize_language
from repository_analysis.repository import detect_languages, scan_repository

from .dataset import PairLayout, iter_language_files, public_test_files
from .models import FunctionChunk, ProjectPaths, TestChunk
from .parsing import extract_functions, extract_tests


@dataclass
class ProjectData:
    paths: ProjectPaths
    source_tests: list[TestChunk]
    target_tests: list[TestChunk]
    source_functions: list[FunctionChunk]
    errors: list[str]


@dataclass
class SourceRepositoryData:
    root: Path
    project: str
    language: str
    source_files: list[Path]
    test_files: list[Path]
    source_functions: list[FunctionChunk]
    source_tests: list[TestChunk]
    language_counts: dict[str, int]
    errors: list[str]


def load_source_repository(
    source_root: str | Path,
    *,
    source_language: str | None = None,
    project: str | None = None,
) -> SourceRepositoryData:
    """Extract Source function/test chunks from an ordinary repository."""
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Source repository does not exist: {root}")

    detected = detect_languages(root)
    language_counts = {language: count for language, count in detected}
    if source_language is None:
        supported = [item for item in detected if item[0] in {"python", "cpp"}]
        if not supported:
            raise ValueError(
                "Cannot detect a supported Source language; currently expected Python or C++"
            )
        canonical = supported[0][0]
    else:
        canonical = normalize_language(source_language)
        if canonical not in {"python", "cpp"}:
            raise ValueError(
                f"Source chunk extraction currently supports Python and C++, got {source_language}"
            )

    language = display_language(canonical)
    files = scan_repository(root, [canonical])
    source_files = [item.path for item in files if not item.is_test]
    test_files = [item.path for item in files if item.is_test]
    project_name = (project or root.name).strip()
    if not project_name:
        raise ValueError("project must not be empty")

    errors: list[str] = []
    try:
        source_functions = extract_functions(
            source_files, root, project_name, language
        )
    except Exception as exc:
        source_functions = []
        errors.append(f"source_function_parse_error:{type(exc).__name__}:{exc}")

    source_tests: list[TestChunk] = []
    for path in test_files:
        try:
            source_tests.extend(extract_tests(path, root, project_name, language))
        except Exception as exc:
            relative = path.relative_to(root).as_posix()
            errors.append(
                f"source_test_parse_error:{relative}:{type(exc).__name__}:{exc}"
            )

    return SourceRepositoryData(
        root=root,
        project=project_name,
        language=language,
        source_files=source_files,
        test_files=test_files,
        source_functions=source_functions,
        source_tests=source_tests,
        language_counts=language_counts,
        errors=errors,
    )


def load_project(layout: PairLayout, project: str) -> ProjectData:
    paths = layout.project(project)
    errors: list[str] = []
    source_tests: list[TestChunk] = []
    target_tests: list[TestChunk] = []
    source_functions: list[FunctionChunk] = []
    if paths.source_dir is None:
        errors.append("missing_source_project")
    else:
        source_files = list(iter_language_files(paths.source_dir, layout.pair.source))
        try:
            source_functions = extract_functions(source_files, paths.source_dir, project, layout.pair.source)
        except Exception as exc:  # keep dataset-wide scans moving while preserving the error
            errors.append(f"source_function_parse_error:{type(exc).__name__}:{exc}")
        for path in public_test_files(paths.source_dir, layout.pair.source):
            try:
                source_tests.extend(extract_tests(path, paths.source_dir, project, layout.pair.source))
            except Exception as exc:
                errors.append(f"source_test_parse_error:{path.name}:{type(exc).__name__}:{exc}")
    if paths.target_dir is None:
        errors.append("missing_target_project")
    else:
        for path in public_test_files(paths.target_dir, layout.pair.target):
            try:
                target_tests.extend(extract_tests(path, paths.target_dir, project, layout.pair.target))
            except Exception as exc:
                errors.append(f"target_test_parse_error:{path.name}:{type(exc).__name__}:{exc}")
    return ProjectData(paths, source_tests, target_tests, source_functions, errors)


def load_target_corpus(layout: PairLayout, projects: list[str] | None = None) -> tuple[list[TestChunk], list[dict]]:
    selected = projects or [item.project for item in layout.projects()]
    chunks: list[TestChunk] = []
    reports: list[dict] = []
    for project in selected:
        paths = layout.project(project)
        project_chunks: list[TestChunk] = []
        errors: list[str] = []
        if paths.target_dir is None:
            errors.append("missing_target_project")
        else:
            for path in public_test_files(paths.target_dir, layout.pair.target):
                try:
                    project_chunks.extend(extract_tests(path, paths.target_dir, project, layout.pair.target))
                except Exception as exc:
                    errors.append(f"target_test_parse_error:{path.name}:{type(exc).__name__}:{exc}")
        chunks.extend(project_chunks)
        reports.append({"project": project, "target_test_chunks": len(project_chunks), "errors": errors})
    return chunks, reports


def load_source_test_corpus(
    layout: PairLayout, projects: list[str] | None = None
) -> tuple[list[TestChunk], list[dict]]:
    selected = projects or [item.project for item in layout.projects()]
    chunks: list[TestChunk] = []
    reports: list[dict] = []
    for project in selected:
        paths = layout.project(project)
        project_chunks: list[TestChunk] = []
        errors: list[str] = []
        if paths.source_dir is None:
            errors.append("missing_source_project")
        else:
            for path in public_test_files(paths.source_dir, layout.pair.source):
                try:
                    project_chunks.extend(
                        extract_tests(path, paths.source_dir, project, layout.pair.source)
                    )
                except Exception as exc:
                    errors.append(
                        f"source_test_parse_error:{path.name}:{type(exc).__name__}:{exc}"
                    )
        chunks.extend(project_chunks)
        reports.append({
            "project": project,
            "source_test_chunks": len(project_chunks),
            "errors": errors,
        })
    return chunks, reports


def load_source_function_corpus(
    layout: PairLayout,
    projects: list[str] | None = None,
) -> tuple[list[FunctionChunk], list[dict]]:
    selected = projects or [item.project for item in layout.projects()]
    chunks: list[FunctionChunk] = []
    reports: list[dict] = []
    for project in selected:
        paths = layout.project(project)
        project_chunks: list[FunctionChunk] = []
        errors: list[str] = []
        if paths.source_dir is None:
            errors.append("missing_source_project")
        else:
            source_files = list(
                iter_language_files(paths.source_dir, layout.pair.source)
            )
            try:
                project_chunks = extract_functions(
                    source_files,
                    paths.source_dir,
                    project,
                    layout.pair.source,
                )
            except Exception as exc:
                errors.append(
                    f"source_function_parse_error:{type(exc).__name__}:{exc}"
                )
        chunks.extend(project_chunks)
        reports.append({
            "project": project,
            "source_function_chunks": len(project_chunks),
            "errors": errors,
        })
    return chunks, reports
