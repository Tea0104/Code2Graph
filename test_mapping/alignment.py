from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher

from .models import Alignment, TestChunk


TEST_WORDS = {"test", "tests", "public", "case", "cases"}


def name_tokens(value: str) -> list[str]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", value) if token.lower() not in TEST_WORDS]


def normalized_name(value: str) -> str:
    return "".join(name_tokens(value))


def normalized_file(value: str) -> str:
    stem = value.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return normalized_name(stem)


def align_tests(source: list[TestChunk], target: list[TestChunk], *, expanded: bool = False) -> list[Alignment]:
    target_by_name: dict[str, list[TestChunk]] = defaultdict(list)
    for chunk in target:
        target_by_name[normalized_name(chunk.name)].append(chunk)

    alignments: list[Alignment] = []
    unresolved: list[TestChunk] = []
    used_targets: set[str] = set()
    for chunk in source:
        candidates = target_by_name.get(normalized_name(chunk.name), [])
        if len(candidates) == 1:
            target_chunk = candidates[0]
            used_targets.add(target_chunk.chunk_id)
            alignments.append(Alignment(
                chunk.chunk_id,
                (target_chunk.chunk_id,),
                "normalized_test_name",
                "high",
                evidence={"source_name": chunk.name, "target_name": target_chunk.name},
            ))
        elif len(candidates) > 1:
            alignments.append(Alignment(
                chunk.chunk_id,
                tuple(candidate.chunk_id for candidate in candidates),
                "normalized_test_name_collision",
                "ambiguous",
                evidence={"normalized_name": normalized_name(chunk.name)},
            ))
        else:
            unresolved.append(chunk)

    if not expanded:
        return alignments

    source_groups: dict[str, list[TestChunk]] = defaultdict(list)
    target_groups: dict[str, list[TestChunk]] = defaultdict(list)
    for chunk in unresolved:
        source_groups[normalized_file(chunk.file)].append(chunk)
    for chunk in target:
        if chunk.chunk_id not in used_targets:
            target_groups[normalized_file(chunk.file)].append(chunk)
    for file_key, source_group in source_groups.items():
        target_group = target_groups.get(file_key, [])
        if file_key and len(source_group) == len(target_group):
            for source_chunk, target_chunk in zip(source_group, target_group):
                alignments.append(Alignment(
                    source_chunk.chunk_id,
                    (target_chunk.chunk_id,),
                    "same_file_equal_count_order",
                    "medium",
                    score=0.7,
                    evidence={"file_key": file_key},
                ))
                used_targets.add(target_chunk.chunk_id)

    aligned_sources = {alignment.source_chunk_id for alignment in alignments}
    remaining_targets = [chunk for chunk in target if chunk.chunk_id not in used_targets]
    for source_chunk in source:
        if source_chunk.chunk_id in aligned_sources or not remaining_targets:
            continue
        scored = sorted(
            (
                (SequenceMatcher(None, normalized_name(source_chunk.name), normalized_name(target_chunk.name)).ratio(), target_chunk)
                for target_chunk in remaining_targets
            ),
            key=lambda item: (-item[0], item[1].chunk_id),
        )
        best_score, best_target = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score >= 0.72 and best_score - second_score >= 0.08:
            alignments.append(Alignment(
                source_chunk.chunk_id,
                (best_target.chunk_id,),
                "fuzzy_test_name_candidate",
                "low",
                score=best_score,
                evidence={"second_score": second_score},
            ))
    return alignments
