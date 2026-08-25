# Dynamic Test Mapping

Experimental dynamic coverage pipeline for C++ Source test -> Source function mapping.

This directory is intentionally isolated from the static `test_mapping` pipeline.
It is designed to answer one question:

```text
Can executing a specific C++ source test identify the project functions it covers?
```

## Pipeline

```text
mapping table rows
  -> select unresolved / low candidate tests
  -> configure/build project in sandbox
  -> discover test executables
  -> match source test to gtest/catch/doctest/boost/plain-function runnable
  -> run one test with coverage instrumentation
  -> llvm-cov export
  -> covered lines intersect FunctionChunk ranges
  -> dynamic candidate rows
  -> optionally merge dynamic candidates into a queryable mapping table
```

## Usage

On `pubsrv`, use the project-local venv created for this experiment:

```bash
cd /home/user/neo4j/Code2Graph
.venv-test-mapping/bin/python -m dynamic_test_mapping probe \
  --dataset-root /home/user/neo4j/datasets/team_subset \
  --pair C++_to_Python \
  --mapping outputs/source-function-map/cpp_to_python_recall_static_all_tests.jsonl \
  --selection unresolved \
  --max-projects 5 \
  --max-tests-per-project 5 \
  --sandbox-root /tmp/code2graph_dynamic \
  --output-dir outputs/source-function-map/dynamic_probe_poc_20260822 \
  --clean
```

Outputs:

```text
dynamic_probe.jsonl
dynamic_probe_report.json
```

Merge dynamic candidates into a table that can be used by the existing query
API:

```bash
.venv-test-mapping/bin/python -m dynamic_test_mapping merge \
  --mapping outputs/source-function-map/cpp_to_python_recall_static_all_tests_v6_parserfix.jsonl \
  --dynamic-probe outputs/source-function-map/dynamic_probe_v6_batch2_20260823/dynamic_probe.jsonl \
  --output outputs/source-function-map/cpp_to_python_recall_static_plus_dynamic_v6_batch2.jsonl \
  --report outputs/source-function-map/cpp_to_python_recall_static_plus_dynamic_v6_batch2_report.json
```

Query the merged table with the existing interface:

```bash
.venv-test-mapping/bin/python -m test_mapping query-source-function-map \
  --mapping outputs/source-function-map/cpp_to_python_recall_static_plus_dynamic_v6_batch2.jsonl \
  --project a-e-k_canvas_ity \
  --source-test PublicCanvasIty.FillDefaultColorNoCrash
```

## Design Notes

- Builds happen under `/tmp/code2graph_dynamic/<project>` or the supplied sandbox root.
- Source projects are not modified, except `make` projects may build in-tree if no out-of-tree build system exists; for this reason `make` should be used cautiously.
- Supported test runners now include gtest, Catch2, doctest, Boost.Test, binary smoke tests, and generated plain-function harnesses.
- gcc/gcov counters are deleted inside sandboxed build roots before each selected test so coverage is test-level rather than project-level.
- The implementation prioritizes clang/llvm coverage when available:
  - `-fprofile-instr-generate`
  - `-fcoverage-mapping`
  - `llvm-profdata`
  - `llvm-cov export`
- LLM use belongs outside the final resolver:
  - propose build recipes from README/CI logs
  - suggest safe configure flags after failures
- never directly decide the mapped function

## Current Status

This component is experimental and is intentionally excluded from the default
Code2Graph initialization and query pipeline. It is kept here so future work can
add dynamic evidence without recreating the runner and coverage machinery.

Dynamic coverage is useful when a project can be built and an individual test can
be isolated, but it is not yet a full-dataset fallback. Build-system differences,
external dependencies, generated tests, smoke tests, and incomplete function
extraction can all prevent a dynamic result.

When it is enabled later, the recommended precedence is:

```text
static high/medium
  -> dynamic coverage evidence
  -> static low
  -> unresolved
```

## Expected Role

Dynamic results should be merged after static high/medium:

```text
if static high/medium exists:
    keep static result
elif dynamic coverage hit exists:
    return confidence=dynamic
elif recall low exists:
    return confidence=low
else:
    return no_function
```

Dynamic is especially useful for:

- upgrading `low` candidates when coverage confirms them;
- finding constructor/operator/member functions missed by static call extraction;
- diagnosing whether remaining unresolved tests are runnable at all.
