# Source Test -> Source Function 静态映射表方案

日期：2026-08-21

## 1. 目标

对数据集中的每个 Source test 生成一张可查询的映射表：

```text
source test -> 被该 test 直接验证的 source public/business function
```

这里的 `function` 不是测试 helper、断言宏、标准库函数或实现细节 helper，而是当前 FunctionChunk 中可表达、并且被测试断言/异常/返回值/状态/输出副作用直接验证的业务 API。

## 2. 当前推荐链路

当前默认链路是 static-only：

```text
Source test 解析
  -> 提取 direct calls
  -> 静态解析到 Source FunctionChunk
  -> 过滤 public/business function
  -> 用测试验证证据过滤
  -> verified_static_with_medium 排序
  -> JSONL 映射表
```

默认方法名：

```text
verified_static_with_medium
```

如果目标是“尽量每个 test 都返回候选”，使用召回优先方法：

```text
recall_static
```

保留两个调试方法：

```text
static
verified_static_with_low
```

`static` 会保留静态解析到的直接调用；`verified_static_with_medium` 是推荐用于最终表和准确率评估的方法。
它保持原 high-confidence 规则不变，并额外加入 helper 一跳展开后仍有强验证证据的 `medium` 候选。
`verified_static_with_low` 只用于调试召回，会额外输出弱验证的 `low` 候选，不进入主查询表。
`recall_static` 是面向查询成功率的全量候选表：先返回 high/medium，若没有则返回 low 静态候选；仍无法候选时标记 `status=unresolved`。

## 3. 准确率是否能判断

可以，但只能在有人工 gold 的样本上判断。

已有的 555 条 C++ public gtest gold 文件：

```text
outputs/manual-gold/source_function_public_gtest_all555_manual_gold_v4.jsonl
```

gold 定义：

```text
source test 中被断言、异常检查、返回值检查、状态检查或输出副作用直接验证的 public/business API
```

历史 v4 文档里的 `verified_static` 结果是旧 rerun 口径；最新实现把映射表生成和
表级评估独立出来后，已经在远端 `pubsrv` 重新生成并评估。

最新 all-scope 映射表：

```text
/home/user/neo4j/Code2Graph/outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests.jsonl
/home/user/neo4j/Code2Graph/outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests_report.json
```

召回优先 all-scope 映射表：

```text
/home/user/neo4j/Code2Graph/outputs/source-function-map/cpp_to_python_recall_static_all_tests.jsonl
/home/user/neo4j/Code2Graph/outputs/source-function-map/cpp_to_python_recall_static_all_tests_report.json
```

表规模：

| 指标 | 数值 |
|---|---:|
| projects | 181 |
| mapping records | 5,052 |
| matched source tests | 2,328 |
| no_match source tests | 2,724 |
| resolved source-function links | 7,260 |
| top-1 high confidence | 2,004 |
| top-1 medium confidence | 324 |

说明：当前表已排除 `3rdparty/third_party/vendor/external/deps` 以及 gtest/gmock 框架路径，避免把依赖库自测和测试框架内部函数当作业务 test/function。

召回优先表规模：

| 指标 | 数值 |
|---|---:|
| projects | 181 |
| mapping records | 5,052 |
| matched source tests | 2,328 |
| candidate source tests | 1,100 |
| unresolved source tests | 1,624 |
| query success source tests | 3,428 |
| query success rate | 67.85% |
| top-1 high confidence | 2,004 |
| top-1 medium confidence | 324 |
| top-1 low confidence | 1,100 |

用 555 条 reviewed public gold 评估这张 all-scope 表：

```text
/home/user/neo4j/Code2Graph/outputs/source-function-map/eval_cpp_to_python_verified_static_with_medium_all555_v4/metrics.json
/home/user/neo4j/Code2Graph/outputs/source-function-map/eval_cpp_to_python_verified_static_with_medium_all555_v4/results.jsonl
```

| 指标 | 数值 |
|---|---:|
| Overall Accuracy@1（含 no_match，表级严格口径） | 99.64% |
| Hit@1（matched only） | 99.64% |
| Hit@3 | 99.64% |
| Hit@5 | 99.64% |
| MacroRecall@1 | 84.05% |
| MacroRecall@3 | 96.61% |
| MacroRecall@5 | 97.65% |
| MRR | 99.64% |
| missing mapping records | 1 |

召回优先表在同一 gold 上：

| 指标 | 数值 |
|---|---:|
| Overall Accuracy@1（含 no_match，严格口径） | 85.59% |
| Hit@1（matched only） | 99.64% |
| Hit@3 | 100.00% |
| Hit@5 | 100.00% |
| MRR | 99.76% |

解释：`recall_static` 会给一部分 gold no_match 返回 low candidate，因此严格 accuracy 会下降；它适合“宁可给低置信候选，也不要空输出”的查询任务。

这里的 `99.64%` 是“给定 source test 必须能从生成表中查到”的严格口径。

剩余错误/缺口：

| 类型 | 数量 | 说明 |
|---|---:|---|
| matched miss | 1 | `suyash_ulid` 的 `PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat` 命中 `ulid_struct.hh::MarshalTo`，gold 是 `ulid_uint128.hh::Create/MarshalTo` |
| missing no_match row | 1 | `kazuho_picojson:test_public.cc:expr.expected` 是自定义函数式宏定义形成的历史 gold pseudo-test；当前解析表中对应实际记录是 `test_public.cc::main` |

全量数据集如果包含 public/internal/original tests，且没有人工 gold，则不能直接给“真实准确率”。能做的是：

- 先生成全量映射表，得到覆盖率、空输出率、每个 method 的使用量。
- 从全量结果中按项目/框架/输出状态分层抽样做 gold。
- 用 `evaluate-source-function-map` 在新增 gold 上评估准确率。

## 4. 映射表生成

新增入口：

```bash
python -m test_mapping build-source-function-map \
  --dataset-root /home/user/neo4j/datasets/team_subset \
  --pair C++_to_Python \
  --method verified_static_with_medium \
  --test-scope all \
  --output outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests.jsonl \
  --report outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests_report.json
```

生成召回优先表：

```bash
python -m test_mapping build-source-function-map \
  --dataset-root /home/user/neo4j/datasets/team_subset \
  --pair C++_to_Python \
  --method recall_static \
  --test-scope all \
  --output outputs/source-function-map/cpp_to_python_recall_static_all_tests.jsonl \
  --report outputs/source-function-map/cpp_to_python_recall_static_all_tests_report.json
```

只跑某个项目：

```bash
python -m test_mapping build-source-function-map \
  --dataset-root /home/user/neo4j/datasets/team_subset \
  --pair C++_to_Python \
  --project beasthttp \
  --method verified_static_with_medium \
  --test-scope all \
  --output outputs/source-function-map/beasthttp_verified_static.jsonl \
  --report outputs/source-function-map/beasthttp_verified_static_report.json
```

## 5. 表格式

JSONL：一行一个 Source test。

示例结构：

```json
{
  "schema_version": 1,
  "pair": "C++_to_Python",
  "project": "demo",
  "source_test_id": "demo:C++:public_tests/demo_public_test.cpp:1:1:Demo.Case",
  "source_test_nodeid": "public_tests/demo_public_test.cpp::Demo.Case",
  "source_test_file": "public_tests/demo_public_test.cpp",
  "source_test_name": "Demo.Case",
  "source_test_language": "C++",
  "source_test_framework": "TEST",
  "resolver_method": "verified_static_with_medium",
  "status": "matched",
  "direct_calls": ["add"],
  "source_functions": [
    {
      "rank": 1,
      "chunk_id": "demo:C++:src/demo.cpp:1:3:add",
      "file": "src/demo.cpp",
      "name": "add",
      "qualified_name": "add",
      "start_line": 1,
      "end_line": 3,
      "matched_call": "add",
      "resolution_reason": "cpp_include",
      "verification_reason": "direct_call_inside_assertion",
      "confidence": "high",
      "parent": null
    }
  ],
  "diagnostics": {
    "resolver_chain": [
      "static_direct_call_resolution",
      "business_function_filter",
      "test_verification_filter",
      "verified_function_ranking"
    ],
    "raw_resolved_link_count": 1,
    "raw_business_link_count": 1,
    "matched_call_count": 1
  }
}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `resolver_method` | 生成该条映射使用的方法，默认 `verified_static` |
| `status` | `matched` 表示找到业务函数；`no_match` 表示该 test 没有可验证的业务函数输出 |
| `direct_calls` | 从 test body 里提取的直接调用名 |
| `source_functions` | 排序后的映射函数列表，`rank=1` 是默认查询答案 |
| `matched_call` | test 中触发该函数映射的调用 |
| `resolution_reason` | 静态解析为什么能连到该 FunctionChunk |
| `verification_reason` | 为什么认为该函数被 test 直接验证 |
| `confidence` | `high` 表示 test 直接验证；`medium` 表示 helper 一跳展开后有强验证证据；`low` 仅调试方法输出 |
| `diagnostics.resolver_chain` | 该行实际经过的链路节点 |

在 `recall_static` 表里，`low` 不再只是调试输出，而是主查询候选；调用方必须展示或使用 `confidence` 字段。

## 6. 查询接口

命令行查询：

```bash
python -m test_mapping query-source-function-map \
  --mapping outputs/source-function-map/cpp_to_python_verified_static_all_tests.jsonl \
  --project beasthttp \
  --source-test "public_tests/http_public_test.cpp::HttpPublic.ParseRequest"
```

`--source-test` 支持：

- `source_test_id`
- `source_test_nodeid`
- `source_test_name`
- `source_test_file`
- `source_test_file::source_test_name`

Python 函数式接口（推荐用于直接查询 function 列表）：

```python
from test_mapping import lookup_mapped_source_functions

functions = lookup_mapped_source_functions(
    "public_tests/http_public_test.cpp::HttpPublic.ParseRequest",
    mapping="outputs/source-function-map/cpp_to_python_verified_static_all_tests.jsonl",
    project="beasthttp",
)

for function in functions:
    print(function["rank"], function["qualified_name"], function["file"])
```

如果 test 在表中存在但没有映射到业务函数，返回空列表 `[]`。

Python 类接口：

```python
from test_mapping import SourceFunctionMappingAPI

api = SourceFunctionMappingAPI.from_jsonl(
    "outputs/source-function-map/cpp_to_python_verified_static_all_tests.jsonl"
)
functions = api.lookup_functions(
    "public_tests/http_public_test.cpp::HttpPublic.ParseRequest",
    project="beasthttp",
)
print(functions[0]["qualified_name"])
```

如果一个 selector 命中多条记录，默认会报 ambiguous；可以传：

```python
api.lookup("ParseRequest", project="beasthttp", allow_many=True)
```

## 7. 映射表准确率评估

新增入口：

```bash
python -m test_mapping evaluate-source-function-map \
  --mapping outputs/source-function-map/cpp_to_python_verified_static_all_tests.jsonl \
  --gold outputs/manual-gold/source_function_public_gtest_all555_manual_gold_v4.jsonl \
  --output-dir outputs/source-function-map/eval_cpp_to_python_verified_static_all_tests_all555_v4
```

输出：

```text
outputs/source-function-map/eval_cpp_to_python_verified_static_all_tests_all555_v4/metrics.json
outputs/source-function-map/eval_cpp_to_python_verified_static_all_tests_all555_v4/results.jsonl
```

核心指标：

| 指标 | 解释 |
|---|---|
| `overall_accuracy_at_1_including_no_match` | matched gold 的 Top-1 命中 + no_match gold 的空输出正确，占全部 reviewed evaluable gold |
| `hit_rate_at_1` | 只看 matched gold，只要 Top-1 命中任一 gold function 就算对 |
| `hit_rate_at_3/5` | 只看 matched gold，只要 Top-3/5 命中任一 gold function 就算对 |
| `macro_recall_at_1/3/5` | 每条 test 的 gold function 可能有多个，先算该 test 召回比例，再对 test 平均 |
| `mrr` | matched gold 中第一个正确函数排名的 reciprocal rank 平均 |
| `no_match_correct_count` | gold 是 no_match 且映射表输出空函数列表的数量 |
| `missing_mapping_record_count` | gold 中存在、但映射表缺失的 source test 数量 |

## 8. 当前实现位置

代码：

```text
test_mapping/source_function_mapping.py
test_mapping/cli.py
test_mapping/__init__.py
```

测试：

```text
tests/test_source_function_mapping.py
```

已验证：

```text
python -m unittest tests.test_source_function_mapping tests.test_source_function_gold tests.test_test_mapping_static_resolution
```

结果：

```text
Ran 43 tests
OK
```

本地全量 unittest 还需要安装解析依赖：

```text
tree-sitter
tree-sitter-python
tree-sitter-cpp
```

这些依赖记录在：

```text
requirements-test-mapping.txt
```
