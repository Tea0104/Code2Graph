"""三项功能共用的简单数据格式。

这里不定义数据类。函数和测试都用普通字典保存，字段名直接对应 JSON
中的字段，阅读和调试时可以直接看到实际数据。
"""

from __future__ import annotations

from typing import Any


Chunk = dict[str, Any]


def function_chunk_text(chunk: Chunk) -> str:
    """把一个 Source function 字典转换成向量化文本。"""
    parent = f"\nParent: {chunk['parent']}" if chunk.get("parent") else ""
    calls = f"\nCalls: {', '.join(chunk.get('calls', []))}" if chunk.get("calls") else ""
    return (
        f"Source function: {chunk['qualified_name']}\n"
        f"File: {chunk['file']}{parent}{calls}\n"
        f"Code:\n{chunk['code']}"
    )


def test_chunk_text(chunk: Chunk) -> str:
    """把一个 Source test 字典转换成向量化文本。"""
    calls = f"\nCalls: {', '.join(chunk.get('calls', []))}" if chunk.get("calls") else ""
    text = (
        f"Project: {chunk['project']}\n"
        f"File: {chunk['file']}\n"
        f"Test: {chunk['qualified_name']}{calls}\n"
        f"Code:\n{chunk['code']}"
    )
    context = chunk.get("imports", []) + chunk.get("helpers", [])
    if context:
        text += "\nContext:\n" + "\n".join(context)
    return text
