# C++ Source Test -> Source Function 查询接口

这个目录提供已经生成好的 C++ source test 到 C++ source function 映射表，以及使用的查询接口说明。

## 1. 可用映射表

默认推荐使用高置信静态表：

```text
outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests.jsonl
```

该表一行对应一个 C++ source test。返回结果只包含：

| confidence | 含义                                                                |
| ---------- | ------------------------------------------------------------------- |
| `high`   | test 直接调用并验证了 source business/public function               |
| `medium` | test 通过本地 helper 一跳间接验证了 source business/public function |

如果调用方希望尽量返回候选，即使置信度较低，可以使用召回优先表：

```text
outputs/source-function-map/cpp_to_python_recall_static_all_tests.jsonl
```

该表可能返回 `confidence=low` 的候选。调用方必须展示或继续处理 `confidence` 字段，不能把 `low` 当作确定匹配。

## 2. 查询输入

查询时至少需要传入 `source_test`。建议同时传入 `project`，避免不同项目中同名 test 造成歧义。

| 参数            |       必填 | 说明                                 |
| --------------- | ---------: | ------------------------------------ |
| `source_test` |         是 | 要查询的 source test 标识            |
| `project`     | 否，建议填 | 项目名，用于消除同名 test 歧义       |
| `mapping`     |         否 | 映射表路径；不传时使用代码里的默认表 |

`source_test` 支持以下格式：

| 输入格式                               | 示例                                                                                               |
| -------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `source_test_nodeid`                 | `src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat`                   |
| `source_test_id`                     | `suyash_ulid:C++:src/ulid_extra_public_test.cc:PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat:13` |
| `source_test_name`                   | `PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat`                                                  |
| `source_test_file`                   | `src/ulid_extra_public_test.cc`                                                                  |
| `source_test_file::source_test_name` | `src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat`                   |

推荐调用方使用：

```text
source_test_file::source_test_name
```

并同时传入 `project`。

## 3. 推荐函数接口

推荐使用 `lookup_source_function_mapping_result(...)`，因为它会同时返回匹配结果和无函数原因。

```python
from test_mapping import lookup_source_function_mapping_result

result = lookup_source_function_mapping_result(
    source_test="src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
    project="suyash_ulid",
    mapping="outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests.jsonl",
)
```

返回值是一个 `dict`：

| 字段                      | 类型           | 说明                                                                 |
| ------------------------- | -------------- | -------------------------------------------------------------------- |
| `project`               | `str`        | 项目名                                                               |
| `source_test_id`        | `str`        | source test 唯一 ID                                                  |
| `source_test_nodeid`    | `str`        | `file::qualified_test_name` 格式的 test 标识                       |
| `source_test_file`      | `str`        | source test 所在文件                                                 |
| `source_test_name`      | `str`        | source test 名称                                                     |
| `source_test_framework` | `str`        | 测试框架或测试宏，例如`TEST`、`TEST_F`                           |
| `resolver_method`       | `str`        | 生成该表时使用的静态解析方法                                         |
| `status`                | `str`        | 原始表状态：`matched`、`candidate`、`no_match`、`unresolved` |
| `has_function`          | `bool`       | 是否返回了映射函数                                                   |
| `source_functions`      | `list[dict]` | 映射到的 source function 列表，按`rank` 排序                       |
| `no_function`           | `dict          | None`                                                                |
| `diagnostics`           | `dict`       | 静态解析、过滤、验证过程中的诊断信息                                 |

## 4. 有函数时的返回格式

示例：

```json
{
  "project": "suyash_ulid",
  "source_test_id": "suyash_ulid:C++:src/ulid_extra_public_test.cc:PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat:13",
  "source_test_nodeid": "src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
  "source_test_file": "src/ulid_extra_public_test.cc",
  "source_test_name": "PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
  "source_test_framework": "TEST",
  "resolver_method": "verified_static_with_medium",
  "status": "matched",
  "has_function": true,
  "source_functions": [
    {
      "rank": 1,
      "chunk_id": "suyash_ulid:C++:src/ulid_struct.hh:MarshalTo:399",
      "file": "src/ulid_struct.hh",
      "name": "MarshalTo",
      "qualified_name": "MarshalTo",
      "start_line": 399,
      "end_line": 429,
      "matched_call": "ulid.MarshalTo",
      "resolution_reason": "cpp_qualified_suffix",
      "verification_reason": "call_effect_or_output_asserted",
      "confidence": "high",
      "parent": null
    }
  ],
  "no_function": null,
  "diagnostics": {
    "resolver_chain": [
      "static_direct_call_resolution",
      "business_function_filter",
      "test_verification_filter",
      "verified_function_ranking"
    ],
    "raw_resolved_link_count": 2,
    "raw_business_link_count": 2,
    "matched_call_count": 1
  }
}
```

`source_functions[*]` 字段说明：

| 字段                          | 说明                                |
| ----------------------------- | ----------------------------------- |
| `rank`                      | 排名，`1` 是最推荐的 function     |
| `chunk_id`                  | source function 唯一 ID             |
| `file`                      | source function 所在文件            |
| `name`                      | 函数短名                            |
| `qualified_name`            | 函数限定名                          |
| `start_line` / `end_line` | 函数在源文件中的行号范围            |
| `matched_call`              | test 中触发映射的调用表达式         |
| `resolution_reason`         | 静态解析命中原因                    |
| `verification_reason`       | 判断该函数被 test 验证的原因        |
| `confidence`                | `high`、`medium` 或 `low`     |
| `parent`                    | 所属 class/struct；没有则为`null` |

## 5. 无函数时的返回格式

如果 test 存在，但当前表判断没有可返回的 source function，`has_function=false`。

示例：

```json
{
  "project": "example_project",
  "source_test_id": "example_project:C++:tests/example_test.cc:CompileTime.Constants:10",
  "source_test_nodeid": "tests/example_test.cc::CompileTime.Constants",
  "source_test_file": "tests/example_test.cc",
  "source_test_name": "CompileTime.Constants",
  "source_test_framework": "TEST",
  "resolver_method": "verified_static_with_medium",
  "status": "no_match",
  "has_function": false,
  "source_functions": [],
  "no_function": {
    "type": "assertion_only_or_compile_time_test",
    "reason": "no_business_call_detected_after_filter",
    "description": "The test body does not contain a remaining project-business call after filtering assertion macros, framework calls, local helpers, declarations, and common library noise.",
    "has_assertion": true,
    "direct_calls": [],
    "raw_resolved_link_count": 0,
    "raw_business_link_count": 0,
    "expanded_resolved_link_count": 0,
    "source_business_function_count": 12
  },
  "diagnostics": {
    "no_function_type": "assertion_only_or_compile_time_test"
  }
}
```

常见 `no_function.type`：

| type                                                 | 含义                                                                       |
| ---------------------------------------------------- | -------------------------------------------------------------------------- |
| `assertion_only_or_compile_time_test`              | 只有断言、编译期检查或常量检查，没有可识别业务调用                         |
| `runner_or_smoke_test_without_assertion`           | runner/smoke 类测试，没有明确断言和业务函数验证                            |
| `static_resolution_gap`                            | 有调用表达式，但静态解析无法绑定到 source FunctionChunk                    |
| `framework_helper_or_dependency_test`              | 调用解析到了 helper、框架、vendor/dependency 等非业务函数                  |
| `business_call_without_strong_verification_signal` | 有业务调用，但不能证明该业务函数被 test 直接验证                           |
| `low_confidence_candidate_suppressed`              | 只有低置信候选，被默认高置信表压掉；可改用 recall 表                       |
| `source_function_parse_or_header_only_gap`         | 项目内没有可用 source business FunctionChunk，通常是解析、头文件或数据缺口 |

## 6. 简化函数接口

如果调用方只需要 function 列表，可以使用：

```python
from test_mapping import lookup_mapped_source_functions

functions = lookup_mapped_source_functions(
    source_test="src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
    project="suyash_ulid",
    mapping="outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests.jsonl",
)
```

返回：

| 情况                            | 返回               |
| ------------------------------- | ------------------ |
| 找到映射函数                    | `list[dict]`     |
| test 存在但无映射函数           | `[]`             |
| test 不存在                     | 抛出`KeyError`   |
| 不传`project` 且命中多条 test | 抛出`ValueError` |

如果只需要 top-1：

```python
from test_mapping import lookup_best_mapped_source_function

function = lookup_best_mapped_source_function(
    source_test="src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
    project="suyash_ulid",
    mapping="outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests.jsonl",
)
```

返回：

| 情况                            | 返回               |
| ------------------------------- | ------------------ |
| 找到映射函数                    | `dict`           |
| test 存在但无映射函数           | `None`           |
| test 不存在                     | 抛出`KeyError`   |
| 不传`project` 且命中多条 test | 抛出`ValueError` |

## 7. 批量查询接口

如果需要多次查询，建议先加载一次映射表：

```python
from test_mapping import SourceFunctionMappingAPI

api = SourceFunctionMappingAPI.from_jsonl(
    "outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests.jsonl"
)

result = api.lookup_result(
    source_test="src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
    project="suyash_ulid",
)

functions = api.lookup_functions(
    source_test="src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
    project="suyash_ulid",
)

best = api.lookup_best_function(
    source_test="src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
    project="suyash_ulid",
)
```

类接口方法：

| 方法                          | 返回                                  |
| ----------------------------- | ------------------------------------- |
| `lookup_result(...)`        | 推荐使用，返回完整结果 dict           |
| `lookup_functions(...)`     | 返回 source function 列表             |
| `lookup_best_function(...)` | 返回 top-1 source function 或`None` |
| `lookup(...)`               | 返回原始映射表记录 dict               |

## 8. 命令行查询

也可以通过 CLI 查询：

```bash
python -m test_mapping query-source-function-map \
  --mapping outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests.jsonl \
  --project suyash_ulid \
  --source-test 'src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat'
```

写入文件：

```bash
python -m test_mapping query-source-function-map \
  --mapping outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests.jsonl \
  --project suyash_ulid \
  --source-test 'src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat' \
  --output /tmp/query_result.json
```

如果不传 `project` 且允许返回多条匹配，可以加：

```bash
--allow-many
```

CLI 输出结构：

```json
{
  "mapping": "outputs/source-function-map/cpp_to_python_verified_static_with_medium_all_tests.jsonl",
  "source_test": "src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
  "match_count": 1,
  "results": [
    {
      "schema_version": 1,
      "pair": "C++_to_Python",
      "project": "suyash_ulid",
      "source_test_id": "suyash_ulid:C++:src/ulid_extra_public_test.cc:PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat:13",
      "source_test_nodeid": "src/ulid_extra_public_test.cc::PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
      "source_test_file": "src/ulid_extra_public_test.cc",
      "source_test_name": "PUBLIC_ULID_MarshalTo.MarshalsDifferentFormat",
      "source_test_language": "C++",
      "source_test_framework": "TEST",
      "resolver_method": "verified_static_with_medium",
      "status": "matched",
      "direct_calls": ["ulid.Create", "ulid.MarshalTo"],
      "source_functions": [
        {
          "rank": 1,
          "chunk_id": "suyash_ulid:C++:src/ulid_struct.hh:MarshalTo:399",
          "file": "src/ulid_struct.hh",
          "name": "MarshalTo",
          "qualified_name": "MarshalTo",
          "start_line": 399,
          "end_line": 429,
          "matched_call": "ulid.MarshalTo",
          "resolution_reason": "cpp_qualified_suffix",
          "verification_reason": "call_effect_or_output_asserted",
          "confidence": "high",
          "parent": null
        }
      ]
    }
  ]
}
```

## 9. 调用建议

调用时建议按下面逻辑处理：

1. 优先调用 `lookup_source_function_mapping_result(...)`。
2. 始终传入 `project`。
3. 如果 `has_function=true`，读取 `source_functions`，优先使用 `rank=1`。
4. 如果 `has_function=false`，读取 `no_function.type` 和 `no_function.description`，不要把空列表直接当作接口失败。
5. 如果使用 recall 表，必须读取 `confidence`；`low` 只能作为候选，不能当作确定映射。
