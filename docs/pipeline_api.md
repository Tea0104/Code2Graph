# Code2Graph Pipeline API

论文主程序只需要依赖 `code2graph`。底层 Tree-sitter、拓扑排序、
RAG 和静态映射可以独立演进，不要求调用方了解内部目录。

## 1. 初始化接口

```python
from code2graph import initialize

result = initialize(
    source_repository="/path/to/source-repository",
    source_language="Python",       # 可选；默认自动检测
    repository_id="calculator",    # 可选；默认使用目录名
    artifact_dir="/path/to/artifacts",  # 可选；默认 .code2graph/
    embedder_kind="unixcoder",
    model_path="/path/to/unixcoder-base-nine",
    device="auto",
)
```

初始化仅需要 Source repository，依次生成：

```text
.code2graph/
├── manifest.json
├── graph/nodes.json
├── graph/edges.json
├── chunks/source_functions.jsonl
├── chunks/source_tests.jsonl
├── indexes/source_tests/
│   ├── vectors.npy
│   ├── chunks.jsonl
│   └── manifest.json
├── mappings/source_test_to_source_function.jsonl
├── reports/source_function_mapping.json
└── translation/translation_order.json
```

`InitializationResult` 返回仓库语言、Chunk 数量、产物路径、报告和警告。若没有
识别到 Source tests，向量索引会标记为 `skipped`，其余初始化仍然保留。

## 2. 翻译顺序接口

```python
from code2graph import get_translation_order

result = get_translation_order(
    source_repository="/path/to/source-repository",
    languages=["Python"],
    include_tests=False,
)
```

返回字段：

- `translation_order`：文件级建议顺序；
- `dependencies`：内部文件依赖；
- `external_dependencies`：无法解析到仓库文件的外部依赖；
- `chains`：按入口整理的功能链；
- `cycles`：发现的循环依赖；
- `broken_edges`：为生成顺序而断开的弱边。

仓库扫描、语言配置、import/include 提取和文件依赖图由 `repository_analysis` 公共层
提供；`file_topo_sort` 只负责排序、循环处理和功能链。

### 2.1 翻译闭环接口

如果 Agent 已经翻译了一部分文件，并希望继续翻译 N 个文件，使用：

```python
from code2graph import get_translation_batch

plan = get_translation_batch(
    source_repository="/path/to/source-repository",
    languages=["Python"],
    translated_files=["config.py", "model.py"],
    requested_count=2,
)
```

translated_files 是 Agent 已经完成的 Source 文件路径；路径可以是仓库相对路径，
也可以是仓库内的绝对路径。requested_count 是最低数量，不是硬上限。系统会优先
选择一个完整功能链；如果这条链还需要更多文件，返回的 recommended_files 会自动
扩展。

主要返回字段：

- recommended_files：本次建议继续翻译的文件，已经按依赖顺序排列；
- requested_count / recommended_count：请求数量和实际建议数量；
- expanded / expansion_count：是否为了补齐功能链而扩展；
- selected_chains：选择的功能链、入口文件和已经完成的部分；
- verification_tests：静态 import 关系上能够验证这条链的测试文件；
- untranslated_dependencies：仍然缺少的内部依赖；
- realtime_test_ready：是否已经具备静态意义上的实时测试前提。

realtime_test_ready=True 表示依赖链已经补齐，并且找到了相关测试文件；它不替代
目标语言编译和测试执行。实际测试仍由翻译主程序运行，失败后再进入 Target test
到 Source code 的定位接口。

## 3. Target test 到 Source code

### 连续查询（推荐）

```python
from code2graph import Code2GraphPipeline

pipeline = Code2GraphPipeline.from_artifact_dir(
    "/path/to/source-repository/.code2graph",
    model_path="/path/to/unixcoder-base-nine",
    device="auto",
)

result = pipeline.locate_target_test_to_source_code(
    target_language="C++",
    target_test_code=target_test_code,
    target_test_name=target_test_name,
    target_test_file=target_test_file,
    strategy="fusion",
    top_k_source_tests=5,
    top_k_source_functions=5,
)
```

同一个 `pipeline` 可以处理多个 Target tests。模型和 Source-test index 只加载一次，
每次查询只向量化新的 Target test。

### 单次查询

```python
from code2graph import locate_target_test_to_source_code

result = locate_target_test_to_source_code(
    artifact_dir="/path/to/source-repository/.code2graph",
    target_language="C++",
    target_test_code=target_test_code,
    model_path="/path/to/unixcoder-base-nine",
)
```

单次函数会在每次调用时加载模型和索引，适合脚本或 smoke test，不适合长期服务。

### 返回值

- `source_tests`：RAG 找到的 Source-test 候选、排名和分数；
- `source_functions`：静态映射得到的 Source function 位置与代码；
- `confidence`：第一名检索分数；
- `margin`：第一名与第二名的分数差；
- `has_source_function`：是否得到可返回的 Source function。

默认 `fusion` 同时使用 UniXcoder dense 检索和测试结构检索。结构检索比较测试
名称、文件名、调用集合和常量集合。
