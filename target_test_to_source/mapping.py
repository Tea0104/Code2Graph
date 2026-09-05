"""Source test 到 Source function 的简单映射和文件读写。"""

from __future__ import annotations

import json
from pathlib import Path


def build_source_test_mapping(
    source_tests: list[dict],
    source_functions: list[dict],
) -> dict[str, list[str]]:
    """按唯一函数名，把每个 Source test 映射到 Source function ID。"""
    by_name: dict[str, list[dict]] = {}
    for function in source_functions:
        by_name.setdefault(function["name"], []).append(function)
        by_name.setdefault(function["qualified_name"], []).append(function)

    mapping: dict[str, list[str]] = {}
    for test in source_tests:
        calls = list(test.get("calls", []))
        calls.extend(test.get("helper_calls", []))
        ids: list[str] = []
        for call in calls:
            candidates = by_name.get(call, [])
            unique = {item["chunk_id"] for item in candidates}
            if len(unique) == 1:
                ids.append(next(iter(unique)))
        mapping[test["chunk_id"]] = list(dict.fromkeys(ids))
    return mapping


def save_source_test_mapping(path: Path, mapping: dict[str, list[str]]) -> None:
    """保存 Source test 到 Source function 的 JSONL 映射表。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(
                {"source_test_id": test_id, "source_function_ids": ids},
                ensure_ascii=False,
            )
            + "\n"
            for test_id, ids in mapping.items()
        ),
        encoding="utf-8",
    )


def load_source_test_mapping(path: Path) -> dict[str, list[str]]:
    """读取 Source test 映射表。"""
    result: dict[str, list[str]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            test_id = value["source_test_id"]
            ids = value.get("source_function_ids", value.get("source_functions", []))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"映射表第 {line_number} 行格式错误: {path}") from exc
        if not isinstance(test_id, str) or not isinstance(ids, list):
            raise ValueError(f"映射表第 {line_number} 行字段类型错误: {path}")
        result[test_id] = list(dict.fromkeys(str(item) for item in ids))
    return result


def load_source_functions(path: Path) -> dict[str, dict]:
    """读取 Source function JSONL，并按 chunk ID 建立普通字典索引。"""
    result: dict[str, dict] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            function = json.loads(line)
            function_id = function["chunk_id"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"函数 chunk 第 {line_number} 行格式错误: {path}") from exc
        result[function_id] = function
    return result


def lookup_source_function_ids(source_test_id: str, mapping: dict[str, list[str]]) -> list[str]:
    """根据 Source test ID 读取对应的 Source function ID。"""
    return list(mapping.get(source_test_id, []))


def join_source_function_code(function_ids: list[str], functions: dict[str, dict]) -> str:
    """按映射顺序拼接 Source function 代码。"""
    code: list[str] = []
    for function_id in dict.fromkeys(function_ids):
        function = functions.get(function_id)
        if function and function.get("code", "").strip():
            code.append(function["code"].strip())
    return "\n\n".join(code)
