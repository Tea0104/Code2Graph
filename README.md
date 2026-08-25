# Code2Graph

Code2Graph 是面向代码翻译主程序的仓库分析与测试定位组件。当前正式流程以
一个 Source repository 为输入，对外提供三个稳定接口：

1. 初始化 Source 仓库并生成代码图、Chunk、向量索引和静态映射；
2. 根据文件依赖关系生成翻译顺序；
3. 将 Target test code 定位到 Source test，再映射到 Source function code。

## Pipeline

```text
Source repository
        |
        v
initialize
  ├── Tree-sitter 代码图
  ├── Source function / Source test Chunk
  ├── Source-test UniXcoder 向量索引
  ├── Source test -> Source function 静态映射
  └── 文件依赖与翻译顺序

Target test code
        |
        v
Source-test RAG (dense / structure / fusion)
        |
        v
Source test -> Source function 静态映射
        |
        v
Source function 位置与代码
```

## 目录

```text
code2graph/           对外 API 与流程编排
repository_analysis/  公共语言、仓库扫描、Tree-sitter 与文件依赖图
file_topo_sort/       拓扑排序、循环处理与功能链
test_mapping/         Chunk、向量索引、RAG 与静态函数映射
tree_sitter_graph/    Python/C++ 完整代码图提取
tests/                正式主流程测试
docs/                 API、组件与归档说明
```

## 安装

基础解析和测试：

```bash
python -m pip install -r requirements.txt
```

使用 UniXcoder 和 GPU 检索时：

```bash
python -m pip install -r requirements-unixcoder.txt
```

UniXcoder 模型应提前下载到本地；初始化和查询使用同一个 `model_path`。

## 使用

### 1. 初始化 Source 仓库

```python
from code2graph import initialize

result = initialize(
    "/path/to/source-repository",
    source_language="Python",
    model_path="/path/to/unixcoder-base-nine",
    device="auto",
)
print(result.artifact_dir)
```

默认产物保存到 Source repository 的 `.code2graph/`。初始化不需要 Target
repository。

### 2. 获取翻译顺序

```python
from code2graph import get_translation_order

plan = get_translation_order(
    "/path/to/source-repository",
    ["Python"],
)
print(plan["translation_order"])
```

### 3. Target test 定位 Source code
如果 Agent 已经翻译了部分文件，可以让系统规划下一批完整功能链：

```python
from code2graph import get_translation_batch

plan = get_translation_batch(
    "/path/to/source-repository",
    ["Python"],
    translated_files=["config.py", "model.py"],
    requested_count=2,
)
print(plan["recommended_files"])
print(plan["verification_tests"])
```

请求数量是最低数量。如果只翻译指定数量不能形成完整功能链，系统会自动扩展并在
expanded 和 reasons 中说明原因。

```python
from code2graph import Code2GraphPipeline

pipeline = Code2GraphPipeline.from_artifact_dir(
    "/path/to/source-repository/.code2graph",
    model_path="/path/to/unixcoder-base-nine",
)

result = pipeline.locate_target_test_to_source_code(
    target_language="C++",
    target_test_name="Calculator.Adds",
    target_test_code=(
        "TEST(Calculator, Adds) { EXPECT_EQ(add(1, 2), 3); }"
    ),
)
print(result["source_functions"])
```

服务进程应复用一个 `Code2GraphPipeline` 实例，避免为每个测试重复加载模型。

## 测试

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s file_topo_sort/tests -v
```

详细接口见 [`docs/pipeline_api.md`](docs/pipeline_api.md)，组件文件职责见
[`docs/components.md`](docs/components.md)。历史实验和旧版
Neo4j/CodeQL 流程的恢复方式见 [`docs/archive.md`](docs/archive.md)。

RepoTransBench 接入所需的调用参数、返回格式和推荐调用位置见 [RepoTransBench 对接交接说明](docs/repo_transbench_handoff.md)。

## 当前边界

- Source function/test Chunk 的完整提取目前重点支持 Python 和 C++；
- 文件级翻译顺序支持 Python、C/C++、Java、JavaScript 和 C#；
- “请求 N 个文件并自动扩展为测试闭环批次”的接口已经实现，仍待主程序接入；
- coverage 动态映射保存在归档分支，不属于默认 Pipeline。
