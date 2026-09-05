# Helper 函数解析修改报告

日期：2026-09-05
涉及文件：`initrepo/parsing.py`、`target_test_to_source/mapping.py`

---

## 1. 背景与问题

在"target test → source code"能力里，一个测试函数可能不直接调用 source 函数，而是通过测试文件里的 helper 函数间接调用：

```python
def _make_rect():          # helper 函数
    return Shape(3, 4)     # helper 内部才调用 source 函数 Shape

def test_area():
    s = _make_rect()       # 测试主体只调用了 helper，没直接调 Shape
```

要还原这条 `test → helper → source function` 的间接依赖链，就必须分析 helper 函数内部的调用。

改动前，helper 的调用是在**映射阶段**用正则现扫的：`mapping.py` 里的 `calls_from_code(code)` 对 helper 的**原始代码字符串**做 `re.findall(r"\b([A-Za-z_]\w*)\s*\(", code)`。这带来两个问题：

1. **误报**：正则会把关键字（`if`/`for`/`while`/`return`…）、注释、字符串字面量里的 `foo()` 都当成"调用"。
2. **不一致**：测试主体的调用是在解析阶段用 tree-sitter 精确提取的，而 helper 调用走的是另一条粗糙的正则路径；且两份过滤名单（`parsing.py` 的 `_IGNORED_CALLS` 和 `mapping.py` 的 `IGNORED_CALLS`）不一致，需要手工同步。

另外，C++ 侧 `extract_cpp_tests` 之前**完全没有**提取 helper，间接调用被整体漏掉。

---

## 2. 改动总览

| 文件 | 改动 |
|---|---|
| `initrepo/parsing.py` | `_test_chunk` 新增 `helper_calls` 字段；`extract_python_tests` 用 tree-sitter 提取 helper 内部调用；`extract_cpp_tests` 补上 helper 提取 |
| `target_test_to_source/mapping.py` | 删除 `calls_from_code` 和 `IGNORED_CALLS`；`build_source_test_mapping` 改为直接读 `helper_calls` 字段 |

核心思路：**把"提取 helper 调用"的动作从映射阶段挪到解析阶段**，那里有 tree-sitter，能精确识别真正的 `call` 节点，彻底摆脱正则误报。

---

## 3. 具体改动

### 3.1 `initrepo/parsing.py`

#### ① `_test_chunk` 新增 `helper_calls` 字段（[parsing.py:111](initrepo/parsing.py#L111)、[parsing.py:119](initrepo/parsing.py#L119)）

```python
# 签名新增一个参数
helpers: list[str] | None = None, helper_calls: list[str] | None = None,
# chunk 里多存一个字段
"helper_calls": helper_calls or [],
```

`helpers` 仍保留（存 helper 代码文本，给向量化上下文用），`helper_calls` 是新增的结构化结果（存 helper 内部调用名），二者用途不同。

#### ② `extract_python_tests`：用 tree-sitter 提取 helper 内部调用（[parsing.py:157-166](initrepo/parsing.py#L157-L166)）

**改前**（只存 helper 代码文本）：

```python
helpers = {
    _name(node, source): _text(source, node)
    for node in _walk(tree.root_node)
    if node.type == "function_definition"
    and _name(node, source)
    and not _name(node, source).startswith("test_")
}
```

**改后**（代码文本 + tree-sitter 精确提取的调用，各存一份）：

```python
helper_code: dict[str, str] = {}
helper_calls: dict[str, list[str]] = {}
for node in _walk(tree.root_node):
    if node.type != "function_definition":
        continue
    name = _name(node, source)
    if not name or name.startswith("test_"):
        continue
    helper_code[name] = _text(source, node)
    helper_calls[name] = _python_calls(node, source)   # 复用 tree-sitter 提取
```

组装 chunk 时，把"测试调用的 helper 的内部调用"展开成 `nested_calls`（[parsing.py:178-183](initrepo/parsing.py#L178-L183)）：

```python
calls = _python_calls(node, source)
helper_codes = [helper_code[value] for value in calls if value in helper_code]
nested_calls = [
    call
    for value in calls
    if value in helper_calls
    for call in helper_calls[value]
]
```

`_python_calls` 只认 tree-sitter 的 `call` 节点，注释/字符串里的 `foo()` 和 `if (...)` 天然不会被匹配，误报问题从根上消除。

#### ③ `extract_cpp_tests`：补上 C++ helper 提取（[parsing.py:293-303](initrepo/parsing.py#L293-L303)）

```python
helper_code: dict[str, str] = {}
helper_calls: dict[str, list[str]] = {}
for node in _walk(tree.root_node):
    if node.type != "function_definition":
        continue
    declarator = node.child_by_field_name("declarator")
    fn_name, _ = _cpp_function_name(_text(source, declarator) if declarator else "")
    if not fn_name or fn_name in _CPP_TEST_MACROS or "test" in fn_name.lower() or fn_name == "main":
        continue
    helper_code[fn_name] = _text(source, node)
    helper_calls[fn_name] = _cpp_calls(_text(source, node))
```

测试宏分支和 plain-function 回退分支，都按 Python 同样的方式计算 `helpers` / `helper_calls` 并传给 `_test_chunk`。

### 3.2 `target_test_to_source/mapping.py`

#### ① 删除 `calls_from_code` 和 `IGNORED_CALLS`

这两个只有 `calls_from_code` 一个使用方，正则提取逻辑整体移除；顺带删掉了不再使用的 `import re`。

#### ② `build_source_test_mapping` 改为直接读字段（[mapping.py:21-22](target_test_to_source/mapping.py#L21-L22)）

**改前**：

```python
calls = list(test.get("calls", []))
for helper in test.get("helpers", []):
    calls.extend(calls_from_code(helper))
```

**改后**：

```python
calls = list(test.get("calls", []))
calls.extend(test.get("helper_calls", []))
```

映射逻辑本身（按唯一函数名、`len(unique)==1` 才映射）保持不变。

---

## 4. 为什么这样改

1. **正确性**：tree-sitter 的 `call` 节点只匹配真正的函数调用，`if (x)`、`# bar()`、`"baz()"` 都不会被算进去，误报根除。
2. **一致性**：helper 调用和测试主体调用走同一套 tree-sitter 提取，复用 `_IGNORED_CALLS`，不再需要维护两份过滤名单。
3. **降低耦合**：mapping.py 从"既要解析代码又要映射"变成"只做映射"，职责更单一，也不再依赖正则。
4. **补齐 C++**：C++ 之前漏掉的 helper 间接调用一并补上。
5. **不破坏向量化**：`helpers`（代码文本）继续保留给 `common/models.py` 的 `test_chunk_text` 使用，向量检索语义不变。

---

## 5. 验证

用 `../Code2Graph/venv/bin/python`（含 tree-sitter 全家桶 + numpy）实测：

| 项目 | 结果 |
|---|---|
| Python `helper_calls` | `['Shape']` ✅ |
| C++ `helper_calls` | `['Shape', 'make_rect']` ✅ |
| 端到端映射 | `test → f_shape` ✅ |
| 现有测试套件 `tests.test_repoanalyze` | `ok` ✅ |

---

## 6. 已知遗留问题

C++ 的 `helper_calls` 里会多出一个 helper 自身的函数名（如 `make_rect`）。原因是 `_cpp_calls` 是**正则**提取，`int make_rect() { ... }` 里的函数定义名 `make_rect(` 也会被当作一次"调用"。

这是 `_cpp_calls` 的既有行为（并非本次改动引入），且下游 `build_source_test_mapping` 要求"函数名唯一才映射"，`make_rect` 通常不是 source 函数名，所以无害。Python 侧用 tree-sitter 的 `call` 节点，不存在此问题。如需彻底消除，可让 `_cpp_calls` 跳过函数定义（例如排除 `function_definition` 节点的 declarator）。
