# Code2Graph 重写记忆

## 当前决定

本次重写中，`initrepo` 只生成并保留以下 7 类核心产物：

1. `manifest.json`
2. `translation/translation_order.json`
3. `chunks/source_functions.jsonl`
4. `indexes/source_tests/vectors.npy`
5. `indexes/source_tests/chunks.jsonl`
6. `indexes/source_tests/manifest.json`
7. `mappings/source_test_to_source_function.jsonl`

## Source test 数据

不再单独保留 `chunks/source_tests.jsonl`。

Source test 的完整信息（包括源代码）已经保存在：

```text
indexes/source_tests/chunks.jsonl
```

该文件与 `vectors.npy` 按行对应：同一行号表示同一个 Source test。因此它同时承担：

- 向量索引的 chunk 数据；
- Source test 源代码的读取；
- 检索结果的反查；
- debug 时的人工检查。

## 重写原则

`initrepo` 负责一次性生成上述产物；后续接口只读取这些产物，不重复执行初始化阶段的扫描、解析、建表或向量化工作。

内部实现采用普通函数、`dict`、`list` 和 `Path`，不为路径、chunk、索引命中或仓库状态定义数据类。整个包只有根目录 `repoanalyze.py` 中对外必须保留的 `RepoAnalyze` 类；内部函数直接返回实际数据，方便阅读、调试和跳转。

## `get_translation_order` 重写决定

该接口的职责固定为：

1. 根据 `source_path` 找到 `.code2graph`；
2. 必要时通过统一检查逻辑触发初始化；
3. 读取 `translation/translation_order.json` 中已经保存的 `translation_order`；
4. 排除 `already` 中的文件；
5. 返回剩余顺序中的前 `number` 个文件。

该接口不重新扫描仓库、不重新解析依赖、不重新拓扑排序，也不重新识别语言。当前仓库中的旧目录结构和旧内部实现不限制这次重写，只作为接口兼容和行为参考。

实现时使用不可变默认值 `already=()`；路径统一为仓库相对 POSIX 路径，必要时支持将仓库内绝对路径转换为相对路径。读取 JSON 和截取列表的逻辑直接写在 `RepoAnalyze` 的两个接口中，不再经过额外的读取或列表包装函数。

重写版的目录职责如下：

- `repoanalyze.py`：唯一对外入口，直接定义 `RepoAnalyze` 类，不再经过转发文件。
- `initrepo/`：仓库扫描、Tree-sitter 解析、切片和初始化流程。
- `translation_order/`：生成并读取固定的文件翻译顺序。
- `target_test_to_source/`：Target test 查询、Source test 映射和 Source function 代码拼接。
- `common/`：数据结构、产物路径、向量化模型和 Source test 索引等共用能力。
- `tests/`：重写版测试。

外部仍使用 `from repoanalyze import RepoAnalyze`，内部模块则从上述功能目录直接导入，便于阅读和跳转。

## Worktree 清理决定

`code rewrite` worktree 只保留重写版入口、重写版内部实现、重写版测试、记忆文件、
依赖文件和 `.gitignore`。旧的 `code2graph/`、`test_mapping/`、`file_topo_sort/`、
`repository_analysis/`、`tree_sitter_graph/`、`dynamic_test_mapping/`、`docs/` 和旧测试
不再出现在这个 worktree；它们仍保留在原始 `Code2Graph-upstream/main` worktree 中。

重写版不再从旧的 `code2graph/`、`rewrite_interfaces/` 等目录导入代码。Tree-sitter
解析、仓库扫描和初始化位于 `initrepo/`，文件依赖排序位于 `translation_order/`，
静态 Source test 映射位于 `target_test_to_source/`，共用能力位于 `common/`。

## `get_all_translation_files` 重写决定

补充一个接口，用于直接返回仓库完整的文件翻译顺序：

```python
def get_all_translation_files(
    self,
    source_path: str | Path,
) -> list[str]:
    ...
```

它根据 `source_path` 找到 `.code2graph/translation/translation_order.json`，读取其中的 `translation_order` 并原样返回，不处理 `already`，也不截取数量。

测试文件的过滤在 `initrepo` 生成翻译顺序时完成（默认不把 test 纳入翻译顺序）。`get_all_translation_files` 本身不再重复过滤，保证读取逻辑简单且与 `get_translation_order` 共用同一份已保存顺序。
