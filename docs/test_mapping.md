# 基于 RAG 的跨语言 Public Test 定位器

该模块把一个 Source public test（以及它调用的 Source function）作为查询，从同项目的 Target public test 函数块中检索最可能对应的测试。

## 完整流程

1. `inspect`：发现数据集布局，按测试函数切分 Source/Target public tests，并统计严格对齐覆盖率。
2. `build-index`：把每个 Target test case 连同 import/include、fixture 和直接 helper 上下文编码成向量，持久化为 `vectors.npy + chunks.jsonl + manifest.json`。文本先放测试主体、后放补充上下文，避免 512 token 截断时丢掉核心测试。
3. `locate`：构造 `test`、`function`、`function_test` 查询，支持阈值回退的 `adaptive` 和 RRF 多路融合的 `fusion`。
4. `evaluate`：只用独立的高置信名称对齐作为 gold。`--query-unit test` 评测 Source test→Target test；`--query-unit function` 用严格测试对齐与项目内唯一调用关系构造 Source function→Target tests 多标签 gold。

评测状态分开记录：

- `dataset_missing`：Source 或 Target 项目缺失。
- `chunk_failed`：缺少 public tests，或当前解析器未抽到测试块。
- `alignment_unknown`：测试块存在，但无法建立高置信 gold；这不算 RAG 检索失败。
- `retrieval_failed`：已有严格 gold，但正确目标未进入 Top-5。

## 本地安装与结构验证

```powershell
python -m venv .venv-test-mapping
.\.venv-test-mapping\Scripts\python.exe -m pip install -r requirements-test-mapping.txt
.\.venv-test-mapping\Scripts\python.exe -m unittest discover -s tests -v
```

本地无模型权重时使用确定性的 `hashing` 后端检查完整流程。它是工程测试基线，不代表 UniXcoder 的语义检索效果。

```powershell
$python = ".\.venv-test-mapping\Scripts\python.exe"
$dataset = "D:\Code\Code2Graph\数据集"

& $python -m test_mapping inspect `
  --dataset-root $dataset --pair "Python_to_C++" `
  --output test_mapping_outputs\inspect.json

& $python -m test_mapping build-index `
  --dataset-root $dataset --pair "Python_to_C++" `
  --embedder hashing --output-dir test_mapping_indexes\python-cpp-hashing

& $python -m test_mapping locate `
  --dataset-root $dataset --pair "Python_to_C++" `
  --index-dir test_mapping_indexes\python-cpp-hashing `
  --embedder hashing --project djui_alias-tips `
  --source-test test_suggest_alias_known_variants --strategy fusion

# 也可以直接从待翻译函数出发，返回它最相关的多个 Target tests：
& $python -m test_mapping locate `
  --dataset-root $dataset --pair "Python_to_C++" `
  --index-dir test_mapping_indexes\python-cpp-hashing `
  --embedder hashing --project djui_alias-tips `
  --source-function suggest_alias --top-k 5

& $python -m test_mapping evaluate `
  --dataset-root $dataset --pair "Python_to_C++" `
  --index-dir test_mapping_indexes\python-cpp-hashing `
  --embedder hashing --strategy fusion `
  --output-dir test_mapping_outputs\python-cpp-hashing

# 函数粒度评测：一个 Source function 可以对应多个 Target tests
& $python -m test_mapping evaluate `
  --dataset-root $dataset --pair "Python_to_C++" `
  --index-dir test_mapping_indexes\python-cpp-hashing `
  --embedder hashing --query-unit function `
  --output-dir test_mapping_outputs\python-cpp-function-hashing
```

## 服务器 UniXcoder 运行

服务器已有模型时不需要联网下载：

```bash
cd /home/user/neo4j/Code2Graph
source ~/neo4j/miniconda3/etc/profile.d/conda.sh
conda activate unixcoder

DATASET=/home/user/neo4j/datasets/team_subset
MODEL=/home/user/neo4j/models/unixcoder-base-nine
INDEX=/home/user/neo4j/indexes/python-cpp-unixcoder

python -m test_mapping build-index \
  --dataset-root "$DATASET" --pair 'Python_to_C++' \
  --embedder unixcoder --model-path "$MODEL" --device cuda \
  --batch-size 32 --output-dir "$INDEX"

python -m test_mapping evaluate \
  --dataset-root "$DATASET" --pair 'Python_to_C++' \
  --index-dir "$INDEX" --embedder unixcoder --model-path "$MODEL" \
  --device cuda --batch-size 32 --strategy fusion \
  --output-dir /home/user/neo4j/outputs/python-cpp-unixcoder

python -m test_mapping evaluate \
  --dataset-root "$DATASET" --pair 'Python_to_C++' \
  --index-dir "$INDEX" --embedder unixcoder --model-path "$MODEL" \
  --device cuda --batch-size 32 --query-unit function \
  --output-dir /home/user/neo4j/outputs/python-cpp-unixcoder-function
```

索引与结果目录已被 `.gitignore` 排除；模型和数据集也应继续放在仓库外。

## Gold 与结果解释

严格评测只接受去掉 `test/public` 等包装词后名称唯一一致的跨语言测试。相同文件且数量/顺序一致只能作为中置信候选，不进入严格指标。embedding 检索输出永远不会反过来充当自己的 gold。

测试粒度的 `alignment_coverage` 说明有多少 Source tests 能被客观评测；`Recall@K/MRR` 只衡量这些严格 gold；`end_to_end_recall_at_1` 以全部 Source tests 为分母。

函数粒度只接受“严格测试对齐 + 调用名在项目内唯一”的 gold。`macro_recall_at_k` 计算每个函数召回了多少对应测试后再宏平均，`hit_rate_at_k` 只要求至少命中一个，`function_coverage` 则反映全部 Source functions 中有多少能获得这种保守 gold。三者必须一起报告，不能只挑较高的 HitRate。

还应同时查看 `gold_coverage_of_referenced_functions`：它以被 Source public tests 唯一引用的函数为分母，避免大型仓库中大量未被 public tests 覆盖的函数把 `function_coverage` 压得难以解释。

### 评测限制

严格 gold 依赖跨语言测试名称唯一一致，因此评测集合天然保留较强的名称信号。`test` 查询表现较好，可能同时来自测试代码语义和名称重合；它不能单独证明模型理解了代码。中低置信的顺序/模糊候选只供人工抽查，不进入正式指标。后续论文实验应补充名称遮蔽消融或人工标注集。
