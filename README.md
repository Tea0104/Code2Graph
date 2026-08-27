# Code2Graph

Code2Graph 是面向代码翻译主程序的仓库分析与测试定位组件。对外推荐只使用
仓库根目录的 `repoanalyze.py`，通过一个 `RepoAnalyze` 对象完成初始化、翻译顺序
获取和 Target test 定位。

## 主流程

```
Source repository
        |
        v
RepoAnalyze.initrepo()
        ├── Tree-sitter 代码图
        ├── Source function / Source test Chunk
        ├── Source-test Embedding 索引
        ├── Source test -> Source function 静态映射
        └── 文件级翻译顺序

Target test code
        |
        v
Target test -> Top-1 Source test -> 全部 Source functions
```

## 目录

```
repoanalyze.py        面向 Agent 的统一入口
code2graph/           初始化、RAG 查询和流程适配
repository_analysis/  公共语言、仓库扫描和文件依赖图
file_topo_sort/       文件拓扑排序和翻译顺序
test_mapping/         Chunk、Embedding 索引和静态函数映射
tree_sitter_graph/    Python/C++ 代码图提取
dynamic_test_mapping/ 可选的动态测试映射组件
docs/                 正式 API 和组件说明
```

## 安装

基础解析和测试：

```bash
python -m pip install -r requirements.txt
```

使用 UniXcoder 和 GPU 检索：

```bash
python -m pip install -r requirements-unixcoder.txt
```

## 使用

### 1. 创建统一入口

```python
from repoanalyze import RepoAnalyze

repo = RepoAnalyze(
    embedder_kind="unixcoder",
    model_path="/path/to/unixcoder-base-nine",
    device="auto",
)
```

也可以使用兼容导出：

```python
from code2graph import RepoAnalyze
```

### 2. 初始化 Source 仓库

```python
repo.initrepo(
    source_path="/path/to/source-repository",
    source_language="Python",  # 可选；不传时自动识别
)
```

只有 `source_path` 是必须参数。UniXcoder 模型路径、设备等可复用配置建议在
`RepoAnalyze(...)` 中设置；`initrepo(...)` 仍支持传入这些参数用于单次覆盖。
初始化方法不返回业务结果，生成的状态保存在
`source_path/.code2graph/` 中。初始化会完成代码图、Source function 和 Source test
Chunk、Embedding 索引、Source test 到 Source function 的静态映射以及翻译顺序表。

如果 Agent 没有主动调用 `initrepo`，后面的两个业务接口会自动检查
`.code2graph/manifest.json`；缺少该文件时会使用默认参数自动初始化。

### 3. 获取翻译顺序

```python
files = repo.get_translation_order(
    source_path="/path/to/source-repository",
    number=2,
    already=["config.py", "model.py"],
)
```

参数：

- `source_path`：Source 仓库路径；
- `number`：希望接下来翻译的文件数量；
- `already`：已经翻译完成的文件；
- `include_tests`：可选，是否把测试文件纳入顺序，默认 `False`。

返回值是 `list[str]`。接口根据静态顺序表排除 `already`，再返回接下来的文件：

```python
["service.py", "controller.py"]
```

### 4. Target test 定位 Source code

```python
source_code = repo.target_test_to_source_code(
    source_path="/path/to/source-repository",
    target_language="C++",
    target_test_code=target_test_code,
)
```

接口只要求三个参数：Source 仓库路径、Target test 语言和 Target test 代码。内部会：

1. 检索排名第一的 Source test；
2. 读取这个 Source test 的静态调用映射；
3. 获取它对应的全部 Source functions；
4. 按顺序拼接函数代码并返回。

返回值是 `str`。没有匹配结果时返回空字符串。模型和索引会在第一次查询时加载，
同一个 `RepoAnalyze` 对象后续查询会复用已经加载的 Pipeline。

## 生成产物

```
.code2graph/
├── manifest.json
├── graph/
│   ├── nodes.json
│   └── edges.json
├── chunks/
│   ├── source_functions.jsonl
│   └── source_tests.jsonl
├── indexes/source_tests/
│   ├── vectors.npy
│   ├── chunks.jsonl
│   └── manifest.json
├── mappings/
│   └── source_test_to_source_function.jsonl
├── reports/
└── translation/
    └── translation_order.json
```

## 测试

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s file_topo_sort/tests -v
```

更多组件职责和底层接口见：

- [`docs/components.md`](docs/components.md)
- [`docs/pipeline_api.md`](docs/pipeline_api.md)

## 当前边界

- Source function 和 Source test 的完整提取目前重点支持 Python 和 C++；
- 文件级翻译顺序支持 Python、C/C++、Java、JavaScript 和 C#；
- `dynamic_test_mapping` 保留为独立的可选组件，不属于默认流程。
