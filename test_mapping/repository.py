from __future__ import annotations

from dataclasses import dataclass

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
