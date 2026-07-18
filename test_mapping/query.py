from __future__ import annotations

from .models import FunctionChunk, QueryVariant, TestChunk


def related_functions(test: TestChunk, functions: list[FunctionChunk]) -> list[FunctionChunk]:
    by_name: dict[str, list[FunctionChunk]] = {}
    for function in functions:
        by_name.setdefault(function.name, []).append(function)
    result: list[FunctionChunk] = []
    seen: set[str] = set()
    for call in test.calls:
        for function in by_name.get(call.split(".")[-1], []):
            if function.chunk_id not in seen:
                result.append(function)
                seen.add(function.chunk_id)
    return result


def build_query(test: TestChunk, functions: list[FunctionChunk], strategy: str) -> QueryVariant | None:
    related = related_functions(test, functions)
    function_text = "\n\n".join(
        f"Source function: {function.qualified_name}\nFile: {function.file}\n{function.code}" for function in related
    )
    test_text = f"Source public test: {test.qualified_name}\nFile: {test.file}\n{test.chunk_text}"
    if strategy == "test":
        text = test_text
    elif strategy == "function":
        if not function_text:
            return None
        text = function_text
    elif strategy == "function_test":
        if not function_text:
            return None
        text = f"{test_text}\n\n{function_text}"
    else:
        raise ValueError(f"Unsupported query strategy: {strategy}")
    return QueryVariant(strategy, text, test.chunk_id, tuple(function.chunk_id for function in related))
