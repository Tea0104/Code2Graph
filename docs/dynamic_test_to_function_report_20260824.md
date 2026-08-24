# C++ Source Test -> Source Function 动态方案阶段汇报

## 1. 目标

当前动态方案的目标是补充静态方案在 C++ 测试映射中的召回不足：

```text
给定一个 source test
  -> 尽量单独运行该 test
  -> 收集该 test 的代码覆盖
  -> 将覆盖行与 FunctionChunk 区间相交
  -> 返回该 test 实际覆盖到的 source function 候选
```

该方案主要用于静态方案无法高置信解析的测试，例如：

- 静态表中 `unresolved` 的 source test；
- 静态表中只有 `low` 置信候选的 source test；
- 宏、helper、间接调用、成员函数、构造函数等静态解析容易漏掉的情况。

动态方案不是替代静态方案，而是作为静态高/中置信结果之后的补充链路。

## 2. 当前采取的方法

动态方案采用“构建 + 单测过滤 + 覆盖率 + 函数区间相交”的方式。

### 2.1 输入

输入来自已经生成的 source test -> source function 静态映射表，例如：

```text
outputs/source-function-map/cpp_to_python_gtest_recall_static_improved.jsonl
```

动态 probe 会从表中选择需要补救的行：

```text
--selection unresolved_or_low
```

含义是只跑：

- 没有返回函数的 test；
- 或只返回 low-confidence 静态候选的 test。

### 2.2 项目构建

动态工具会在 sandbox 中构建项目，不直接污染源项目：

```text
/tmp/code2graph_dynamic_<task>/<project>/
```

当前支持的构建路径包括：

- CMake 项目自动 configure/build；
- Makefile 项目 fallback；
- 已存在可执行文件发现；
- 单文件 ad-hoc 编译 fallback；
- Docker + Conan + 项目级 recipe 的可复现构建实验路径。

### 2.3 覆盖率采集

当前实现支持两类覆盖率工具：

- LLVM coverage：
  - `-fprofile-instr-generate`
  - `-fcoverage-mapping`
  - `llvm-profdata`
  - `llvm-cov export`
- GCC/gcov fallback：
  - `--coverage`
  - `.gcda/.gcno`
  - `gcov`

每次运行单个 test 前，会清理 sandbox 内已有 `.gcda`，避免多个测试之间覆盖率串扰。

### 2.5 函数候选生成

覆盖率产物包含：

```text
covered file + covered lines
```

 `FunctionChunk` 对原代码分块，得到函数起止行

 将coverage产物和FunctionChunk做区间相交，命中的业务函数会按覆盖行数排序，输出 top candidates。

过滤规则会排除：

- gtest/gmock 框架函数；
- third_party/vendor/external 依赖函数；
- 测试文件中的 test/helper；
- C++ 伪函数名，如 `if`、`for`、`return` 等。

## 3. Docker + Conan / recipe 实验

为了解决 C++ 项目依赖和环境不可复现的问题，动态方案还实现了 Docker + Conan + 项目级 recipe 的实验路径。

### 3.1 Docker 基础环境

新增：

```text
dynamic_test_mapping/docker/Dockerfile
```

目标是构建统一动态分析环境：

```text
GCC / G++
CMake
Ninja
LLVM / gcov
Python parser dependencies
Conan 2
```

### 3.2 项目级 recipe

新增 recipe 支持：

```text
dynamic_test_mapping/recipes/
```

recipe 可以声明：

- CMake 参数；
- Conan 依赖；
- GTest provider；
- 额外 include path；
- 可执行文件 glob；
- 项目特殊说明。

这使得复杂项目可以逐步沉淀为：

```text
通用动态框架 + 项目级 recipe
```

而不是每次人工重新排查。

## 4. 已完成的工作

### 4.1 建立动态方案代码目录

新增独立目录：

```text
dynamic_test_mapping/
```

它与静态方案 `test_mapping/` 分离，避免影响当前已可提交的静态查询接口。

主要模块包括：

| 文件                                       | 作用                                               |
| ------------------------------------------ | -------------------------------------------------- |
| `dynamic_test_mapping/cli.py`            | probe/merge 命令入口                               |
| `dynamic_test_mapping/build.py`          | 项目配置、构建、可执行文件发现                     |
| `dynamic_test_mapping/test_discovery.py` | gtest/Catch/doctest/Boost/自定义 runner 发现与匹配 |
| `dynamic_test_mapping/coverage.py`       | 运行单个 test 并采集覆盖率                         |
| `dynamic_test_mapping/models.py`         | 动态结果数据结构                                   |
| `dynamic_test_mapping/recipes.py`        | 项目级 dynamic recipe 支持                         |
| `dynamic_test_mapping/docker/Dockerfile` | Docker + Conan 可复现环境实验                      |

### 4.2 实现 probe 命令

命令形式：

```bash
.venv-test-mapping/bin/python -m dynamic_test_mapping probe \
  --dataset-root /home/doge316/C++_to_Python \
  --pair C++_to_Python \
  --mapping outputs/source-function-map/cpp_to_python_gtest_recall_static_improved.jsonl \
  --project <project_name> \
  --selection unresolved_or_low \
  --max-tests-per-project 5 \
  --sandbox-root /tmp/code2graph_dynamic \
  --output-dir outputs/source-function-map/<dynamic_probe_output> \
  --clean
```

输出：

```text
dynamic_probe.jsonl
dynamic_probe_report.json
```

### 4.3 实现 merge 命令

动态结果可以合并回可查询映射表：

```bash
.venv-test-mapping/bin/python -m dynamic_test_mapping merge \
  --mapping <base_mapping.jsonl> \
  --dynamic-probe <dynamic_probe.jsonl> \
  --output <merged_mapping.jsonl> \
  --report <merged_report.json>
```

合并策略：

- 静态 high/medium 保留；
- dynamic 替换 low；
- dynamic 填补 unresolved；
- 没有 dynamic 命中时保留原结果。

## 5. gtest 项目验证

选取项目：

```text
a-e-k_canvas_ity
```

该项目满足：

- 测试文件明确 `#include <gtest/gtest.h>`；
- CMake 中存在 gtest 测试目标；
- gtest binary 支持 `--gtest_list_tests`；
- 可以用 `--gtest_filter` 精确运行单个 test。

运行命令：

```bash
time .venv-test-mapping/bin/python -m dynamic_test_mapping probe \
  --dataset-root /home/doge316/C++_to_Python \
  --pair C++_to_Python \
  --mapping outputs/source-function-map/cpp_to_python_gtest_recall_static_improved.jsonl \
  --output-dir outputs/source-function-map/dynamic_probe_gtest_canvas_ity_20260824 \
  --sandbox-root /tmp/code2graph_dynamic_canvas_gtest \
  --project a-e-k_canvas_ity \
  --selection unresolved_or_low \
  --max-tests-per-project 5 \
  --recipe-dir dynamic_test_mapping/recipes \
  --configure-timeout 120 \
  --build-timeout 300 \
  --list-timeout 20 \
  --test-timeout 120 \
  --clean
```

结果：

```text
耗时：16.9s
selected tests：5
dynamic candidates：5
unresolved：0
coverage_mapped：5
```

输出文件：

```text
outputs/source-function-map/dynamic_probe_gtest_canvas_ity_20260824/dynamic_probe.jsonl
outputs/source-function-map/dynamic_probe_gtest_canvas_ity_20260824/dynamic_probe_report.json
```

部分映射结果：

| source test                                        | 实际 gtest filter                            | Top 映射函数                                                         |
| -------------------------------------------------- | -------------------------------------------- | -------------------------------------------------------------------- |
| `PublicCanvasIty.Construction`                   | `CanvasIty.Construction`                   | `src/canvas_ity.cpp::canvas`                                       |
| `PublicCanvasIty.BasicBeginEndPathMoveLineClose` | `CanvasIty.BasicBeginEndPathMoveLineClose` | `canvas`, `begin_path`, `close_path`, `move_to`, `line_to` |
| `PublicCanvasIty.FillDefaultColorNoCrash`        | `CanvasIty.FillDefaultColorNoCrash`        | `canvas`, `fill`, `begin_path`, `close_path`, `move_to`    |
| `PublicCanvasIty.StrokeDefaultColorNoCrash`      | `CanvasIty.StrokeDefaultColorNoCrash`      | `canvas`, `stroke`, `begin_path`, `move_to`, `line_to`     |
| `PublicCanvasIty.SetColorFillAndStroke`          | `CanvasIty.SetColorFillAndStroke`          | `canvas`, `fill`, `stroke`, `set_color`, `begin_path`      |

注意：该项目中 source test 名为 `PublicCanvasIty.*`，而运行时 gtest 名为 `CanvasIty.*`。当前工具通过后缀/归一化匹配完成了 source test 到 runnable gtest 的对齐。

结论：

```text
标准 gtest + CMake 项目上，动态链路可以较快完成，并成功生成 test->function 映射。
```
