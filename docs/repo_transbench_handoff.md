# RepoTransBench 对接交接说明

本文面向 RepoTransBench 主程序的接入方。主程序只需要依赖 `code2graph` 的公开接口，不需要直接导入 `repository_analysis`、`file_topo_sort` 或 `test_mapping` 的内部模块。

推荐的主流程是：

```text
准备 Source repository
        |
        v
Code2Graph 初始化一次
        |
        +--> 翻译批次规划
        |       |
        |       +--> Agent 翻译一批
        |       +--> 记录已翻译文件
        |       +--> 再规划下一批
        |
        +--> Agent 执行 Target tests
                |
                +--> 测试失败
                        |
                        +--> Target test -> Source code 查询
```

## 1. 初始化接口

### 调用位置

放在 RepoTransBench 已经准备好 Source repository 路径之后、第一次生成翻译批次之前。每个 Source repository 的一次任务只调用一次。

### 调用格式

```python
from code2graph import initialize

initialization = initialize(
    source_repository=source_repository,
    source_language="Python",
    repository_id=project_name,
    artifact_dir=work_dir / ".code2graph",
    embedder_kind="unixcoder",
    model_path=model_path,
    device="auto",
    batch_size=16,
)
```

参数说明：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `source_repository` | `str | Path` | Source 仓库路径，必填。 |
| `source_language` | `str | None` | Source 语言，例如 `Python`、`C++`。可以省略以自动检测。 |
| `repository_id` | `str | None` | 项目唯一名称。省略时使用仓库目录名。 |
| `artifact_dir` | `str | Path | None` | 产物目录。省略时使用 Source 仓库下的 `.code2graph/`。 |
| `embedder_kind` | `str` | 当前使用 `unixcoder`。 |
| `model_path` | `str | Path | None` | UniXcoder 模型目录。使用向量索引时应提供。 |
| `device` | `str` | `auto`、`cpu` 或 `cuda`。 |
| `batch_size` | `int` | 初始化时的向量化批大小。 |

### 返回格式

返回 `InitializationResult` 对象，不是字符串。主要字段如下：

```python
{
    "schema_version": 1,
    "repository_id": "demo-project",
    "source_root": "/work/source",
    "source_language": "Python",
    "artifact_dir": "/work/.code2graph",
    "source_file_count": 12,
    "source_test_file_count": 3,
    "source_function_count": 48,
    "source_test_count": 9,
    "artifacts": {
        "graph_nodes": ".../graph/nodes.json",
        "graph_edges": ".../graph/edges.json",
        "translation_order": ".../translation/translation_order.json",
        "source_functions": ".../chunks/source_functions.jsonl",
        "source_tests": ".../chunks/source_tests.jsonl",
        "source_test_index": ".../indexes/source_tests",
        "source_test_to_source_function": ".../mappings/source_test_to_source_function.jsonl",
        "source_function_mapping_report": ".../reports/source_function_mapping.json",
    },
    "reports": {...},
    "warnings": [],
}
```

对象可以直接使用属性访问：

```python
artifact_dir = initialization.artifact_dir
warnings = initialization.warnings
```

RepoTransBench 应保存 `artifact_dir`，后续翻译和测试失败查询都使用同一份产物。不要在每次测试失败时重新初始化。

## 2. 完整翻译顺序接口

### 调用位置

如果主程序需要一次性获取完整的文件级顺序，放在初始化之后、翻译开始之前。

### 调用格式

```python
from code2graph import get_translation_order

order_result = get_translation_order(
    source_repository=source_repository,
    languages=["Python"],
    include_tests=False,
)
```

参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `source_repository` | `str | Path` | Source 仓库路径。 |
| `languages` | `str | list[str]` | 需要分析的语言。 |
| `include_tests` | `bool` | 是否将测试文件也纳入文件顺序，默认 `False`。 |

### 返回格式

返回 JSON 兼容字典：

```python
{
    "schema_version": 2,
    "source_root": "/work/source",
    "languages": ["python"],
    "translation_order": [
        "src/config.py",
        "src/service.py",
        "src/main.py",
    ],
    "dependencies": [...],
    "external_dependencies": [...],
    "chains": [...],
    "cycles": [...],
    "broken_edges": [...],
}
```

RepoTransBench 通常只需要读取：

```python
files = order_result["translation_order"]
chains = order_result["chains"]
```

如果主程序需要“继续翻译 N 个文件，并保证功能链尽可能完整”，应使用下面的 `get_translation_batch`，而不是自行截取 `translation_order` 的前 N 项。

## 3. 翻译闭环接口

### 调用位置

放在 RepoTransBench 的翻译调度循环中。每轮翻译完成后，主程序更新 `translated_files`，然后再次调用该接口规划下一轮。

### 调用格式

```python
from code2graph import get_translation_batch

plan = get_translation_batch(
    source_repository=source_repository,
    languages=["Python"],
    translated_files=[
        "src/config.py",
        "src/model.py",
    ],
    requested_count=2,
    include_tests=False,
)
```

参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `source_repository` | `str | Path` | Source 仓库路径。 |
| `languages` | `str | list[str]` | Source 语言。 |
| `translated_files` | `list[str] | tuple[str, ...]` | 已完成翻译的 Source 文件，可传仓库相对路径或仓库内绝对路径。 |
| `requested_count` | `int` | 希望新增的最低文件数，不是硬上限。 |
| `include_tests` | `bool` | 是否把测试文件纳入规划，默认 `False`。 |

### 返回格式

```python
{
    "schema_version": 2,
    "source_root": "/work/source",
    "translated_files": ["src/config.py", "src/model.py"],
    "requested_count": 2,
    "recommended_files": [
        "src/parser.py",
        "src/service.py",
        "src/entry.py",
    ],
    "recommended_count": 3,
    "expanded": True,
    "expansion_count": 1,
    "reasons": ["expanded_to_complete_feature_chain"],
    "status": "ready_for_realtime_test",
    "selected_chains": [...],
    "verification_tests": [...],
    "realtime_test_ready": True,
    "untranslated_dependencies": [],
    "translation_order": [...],
}
```

主程序主要读取：

```python
next_files = plan["recommended_files"]
can_test = plan["realtime_test_ready"]
tests = plan["verification_tests"]
status = plan["status"]
```

`recommended_count` 可能大于 `requested_count`。这表示如果只翻译用户要求的数量，功能链或静态验证条件还不完整，调用方应接受扩展后的文件列表。

`realtime_test_ready` 只表示静态依赖和验证测试条件满足，不代表 Target 代码已经编译通过；实际测试仍由 RepoTransBench 执行。

## 4. Target test 到 Source code 接口

### 调用位置

放在 RepoTransBench 的测试失败处理阶段：

```text
Target code 翻译
  -> 执行 Target tests
  -> 发现具体失败的 Target test
  -> 读取该测试函数源码
  -> 调用本接口
  -> 将返回的 Source code 候选加入修复反馈
```

不要把完整 pytest/GTest 日志直接作为 `target_test_code`。应先定位具体失败的测试函数，再传入该函数源码。

### 连续查询格式，推荐

```python
from code2graph import Code2GraphPipeline

query_pipeline = Code2GraphPipeline.from_artifact_dir(
    artifact_dir=initialization.artifact_dir,
    embedder_kind="unixcoder",
    model_path=model_path,
    device="auto",
    batch_size=16,
)

result = query_pipeline.locate_target_test_to_source_code(
    target_language="C++",
    target_test_code=failed_test_code,
    target_test_name=failed_test_name,
    target_test_file=failed_test_file,
    strategy="fusion",
    top_k_source_tests=5,
    top_k_source_functions=5,
    mask_names=False,
)
```

`query_pipeline` 应在项目任务开始时创建一次，在同一项目的多个测试失败查询之间复用。

参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `artifact_dir` | `str | Path` | 初始化接口生成的产物目录。 |
| `target_language` | `str` | Target 语言，例如 `C++` 或 `Python`。 |
| `target_test_code` | `str` | 一个具体 Target test 函数的完整源码。 |
| `target_test_name` | `str | None` | 测试函数名或测试用例名。 |
| `target_test_file` | `str | None` | Target 测试文件路径。 |
| `strategy` | `str` | `dense`、`call_name`、`fusion` 等已支持策略，默认推荐 `fusion`。 |
| `top_k_source_tests` | `int` | Source test 候选数量。 |
| `top_k_source_functions` | `int` | Source function 候选数量。 |
| `mask_names` | `bool` | 是否屏蔽名称和文件名特征，默认 `False`。 |

### 返回格式

```python
{
    "schema_version": 1,
    "direction": "target_test_code_to_source_test_to_source_function_code",
    "repository_id": "demo-project",
    "source_language": "Python",
    "target_language": "C++",
    "strategy": "fusion",
    "used_strategies": ["fusion"],
    "confidence": 0.91,
    "margin": 0.18,
    "target_test": {
        "name": "Calculator.Adds",
        "file": "public_tests/test_calculator.cpp",
        "code": "...",
        "calls": ["add"],
    },
    "source_tests": [
        {
            "rank": 1,
            "score": 0.91,
            "source_test_id": "...",
            "source_test_name": "test_add",
            "source_test_file": "tests/test_calculator.py",
            "source_test_code": "...",
            "mapping_status": "matched",
            "source_functions": [...],
        },
    ],
    "source_functions": [
        {
            "chunk_id": "...",
            "name": "add",
            "file": "calculator.py",
            "code": "def add(a, b): ...",
            "source_test_rank": 1,
            "source_test_score": 0.91,
        },
    ],
    "has_source_function": True,
}
```

主程序通常读取：

```python
source_functions = result["source_functions"]
source_tests = result["source_tests"]
confidence = result["confidence"]
has_source_function = result["has_source_function"]
```

`source_functions` 是候选，不应在没有检查置信度和映射状态的情况下直接当作唯一 Ground Truth。

### 单次查询格式

只适合 smoke test 或一次性脚本：

```python
from code2graph import locate_target_test_to_source_code

result = locate_target_test_to_source_code(
    artifact_dir=initialization.artifact_dir,
    target_language="C++",
    target_test_code=failed_test_code,
    target_test_name=failed_test_name,
    target_test_file=failed_test_file,
    strategy="fusion",
    model_path=model_path,
    device="auto",
)
```

长期运行不要在每次测试失败时调用这个单次接口，因为它会重新加载索引和 embedding 模型。

## 5. 动态测试映射组件

### 定位

`dynamic_test_mapping/` 是可选实验组件，不属于默认初始化、翻译顺序或 Target test 查询流程。RepoTransBench 第一版不需要调用它。

如果后续要补充 coverage 证据，推荐放在静态 Source test -> Source function 映射之后，作为低置信度或 unresolved 测试的补充验证。

### 调用格式

当前使用 CLI：

```bash
python -m dynamic_test_mapping probe \
  --dataset-root /path/to/dataset \
  --pair C++_to_Python \
  --mapping /path/to/source-function-mapping.jsonl \
  --selection unresolved_or_low \
  --max-projects 5 \
  --max-tests-per-project 5 \
  --sandbox-root /tmp/code2graph_dynamic \
  --output-dir /path/to/dynamic-output \
  --clean
```

输入是数据集根目录、语言对、已有静态映射表和筛选范围；输出目录包含 `dynamic_probe.jsonl` 和 `dynamic_probe_report.json`。该组件目前主要面向 C++ 测试，不能作为主流程的必选依赖。

## 6. RepoTransBench 推荐放置位置

| RepoTransBench 阶段 | 接入内容 | 推荐调用 |
| --- | --- | --- |
| 项目路径准备完成后 | 建立一次性 Code2Graph 产物 | `initialize(...)` |
| 首次翻译前，需要完整顺序时 | 获取完整文件顺序 | `get_translation_order(...)` |
| 每轮翻译调度前 | 根据已翻译文件规划下一批 | `get_translation_batch(...)` |
| Agent 翻译完成并运行 Target tests 后 | 失败测试反查 Source code | `Code2GraphPipeline.locate_target_test_to_source_code(...)` |
| 静态映射无法确认时，后续增强 | coverage 补充证据 | `dynamic_test_mapping` CLI |

推荐的最小接入代码结构：

```python
def run_project(source_repository, work_dir, config):
    initialization = initialize(
        source_repository=source_repository,
        source_language=config.source_language,
        artifact_dir=work_dir / ".code2graph",
        model_path=config.model_path,
        device=config.device,
    )
    query_pipeline = Code2GraphPipeline.from_artifact_dir(
        initialization.artifact_dir,
        model_path=config.model_path,
        device=config.device,
    )

    translated_files = []
    while True:
        plan = get_translation_batch(
            source_repository=source_repository,
            languages=[config.source_language],
            translated_files=translated_files,
            requested_count=config.files_per_round,
        )
        next_files = plan["recommended_files"]
        if not next_files:
            break

        run_translation_agent(next_files)
        translated_files.extend(next_files)
        run_target_tests(plan["verification_tests"])

        for failure in failed_target_tests():
            mapping = query_pipeline.locate_target_test_to_source_code(
                target_language=config.target_language,
                target_test_code=failure.code,
                target_test_name=failure.name,
                target_test_file=failure.file,
                strategy="fusion",
            )
            add_source_candidates_to_feedback(mapping)
```

上面的 `run_translation_agent`、`run_target_tests` 和 `add_source_candidates_to_feedback` 属于 RepoTransBench 自己的流程，不需要由 Code2Graph 提供。

## 7. 接入边界

RepoTransBench 只需要依赖以下公开名称：

```python
from code2graph import (
    Code2GraphPipeline,
    get_translation_batch,
    get_translation_order,
    initialize,
    locate_target_test_to_source_code,
)
```

不建议主程序直接依赖以下内部路径：

```text
repository_analysis/
file_topo_sort/
test_mapping/
tree_sitter_graph/
```

这样后续 Code2Graph 内部调整文件组织时，RepoTransBench 只需要继续遵守公开接口，不需要同步修改内部调用。
