# 组件文件清单

下面按“对外门面 -> 公共解析 -> 初始化 -> 翻译顺序 -> RAG -> 可选增强”列出
当前 Pipeline 直接相关的文件。生成的索引、缓存、测试输出和 `.code2graph/`
产物不属于源码组件，因此不在这里逐个列出。

## 1. 对外统一门面

| 文件 | 功能 |
| --- | --- |
| `code2graph/__init__.py` | 统一导出初始化、翻译顺序、Target test 定位接口。 |
| `code2graph/api.py` | 组装已有组件，提供可重复加载的 `Code2GraphPipeline`。 |
| `docs/pipeline_api.md` | 中文 API 说明、流程、示例、返回字段和边界。 |
| `docs/components.md` | 本文件，说明相关源码文件职责。 |

## 2. 公共解析层

| 文件 | 功能 |
| --- | --- |
| `repository_analysis/__init__.py` | 导出公共解析和图模型。 |
| `repository_analysis/languages.py` | 语言规范、别名、扩展名和解析器配置。 |
| `repository_analysis/parsing.py` | Tree-sitter parser 工厂、节点文本和通用语法辅助函数。 |
| `repository_analysis/repository.py` | 递归扫描仓库、检测语言、识别测试路径。 |
| `repository_analysis/dependencies.py` | 扫描后提取并解析 import/include，统一构建文件依赖图。 |
| `repository_analysis/graph.py` | 图节点、图边、作用域和图构建器。 |

## 3. 代码图提取

| 文件 | 功能 |
| --- | --- |
| `tree_sitter_graph/__init__.py` | 导出仓库级代码图提取入口。 |
| `tree_sitter_graph/extractor.py` | 统一调度 Python/C++ extractor，生成完整代码图。 |
| `tree_sitter_graph/python_extractor.py` | 提取 Python 定义、调用、继承和引用。 |
| `tree_sitter_graph/cpp_extractor.py` | 提取 C/C++ 定义、调用、include 和测试结构。 |

## 4. 仓库初始化

| 文件 | 功能 |
| --- | --- |
| `code2graph/__init__.py` | 原有 repository-level API 的导出层。 |
| `code2graph/initialization.py` | 生成 graph、Chunks、Source-test index、静态映射和翻译顺序。 |
| `code2graph/mapping.py` | 查询时把 RAG 结果和静态映射拼成 Source code 返回值。 |

## 5. 翻译顺序

| 文件 | 功能 |
| --- | --- |
| `file_topo_sort/__init__.py` | 导出文件级分析 API。 |
| `file_topo_sort/topo_sort_files.py` | 构建依赖图、处理循环依赖、拓扑排序并形成功能链。 |
| `file_topo_sort/README.md` | 翻译顺序模块的独立说明。 |
| `file_topo_sort/tests/` | 翻译顺序模块的测试。 |

翻译顺序模块还提供闭环规划函数 `plan_translation_batch`：输入 Agent 已翻译的文件
和最低新增文件数，返回按完整功能链扩展后的下一批文件、相关测试和阻塞依赖。

## 6. Chunk、索引和 RAG

| 文件 | 功能 |
| --- | --- |
| `test_mapping/models.py` | Function/Test Chunk、检索命中和查询结果数据模型。 |
| `test_mapping/repository.py` | 加载仓库项目和 Source/Target 数据。 |
| `test_mapping/parsing.py` | 解析测试函数、Source function、调用和测试元数据。 |
| `test_mapping/embedding.py` | UniXcoder 和 Hashing embedding。 |
| `test_mapping/index.py` | 向量索引构建、持久化和项目内检索。 |
| `test_mapping/test_to_test.py` | Source-test 检索策略和混合排序。 |
| `test_mapping/api.py` | Target test code 到 Source test code 的 API。 |
| `test_mapping/cli.py` | RAG 检索命令行入口。 |

## 7. Source test 到 Source function

| 文件 | 功能 |
| --- | --- |
| `test_mapping/source_function_mapping.py` | 生成、保存、读取和汇总 Source test -> Source function 映射。 |
| `test_mapping/static_resolution.py` | 根据调用名、作用域和语言规则解析函数候选。 |
| `test_mapping/source_function_gold.py` | 评估映射时生成和筛选 ground truth。 |
| `test_mapping/reverse.py` | 旧版/离线 Target test -> Source function 检索评估。 |

## 8. 可选动态组件

| 文件 | 功能 |
| --- | --- |
| `dynamic_test_mapping/__init__.py` | 动态测试映射包入口。 |
| `dynamic_test_mapping/build.py` | 构建动态映射所需的项目数据。 |
| `dynamic_test_mapping/coverage.py` | 读取 coverage 结果。 |
| `dynamic_test_mapping/recipes.py` | 不同语言/测试框架的运行配置。 |
| `dynamic_test_mapping/runner.py` | 执行测试并收集动态信息。 |
| `dynamic_test_mapping/test_discovery.py` | 发现测试文件和测试用例。 |
| `dynamic_test_mapping/models.py` | 动态映射的数据模型。 |
| `dynamic_test_mapping/cli.py` | 动态映射命令行入口。 |

动态组件目前保留，但默认的三接口初始化和查询流程不依赖它。
