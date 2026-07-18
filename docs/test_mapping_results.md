Exit code: 0
Wall time: 3.1 seconds
Output:
# RepoTransBench Public Test 定位：UniXcoder 正式结果

日期：2026-07-18

## 1. 实验设置

- 代码：`Code2Graph/test_mapping` 完整 CLI 实现。
- 数据：团队处理后的 RepoTransBench 子集。
- 方向：Python→C++、C++→Python。
- 模型：`microsoft/unixcoder-base-nine` 本地权重。
- GPU：NVIDIA GeForce RTX 5090 D。
- 检索单元：一个 Target public test case 一个 chunk。
- 严格 test gold：跨语言测试名称归一化后唯一一致。
- 严格 function gold：严格 test gold + Source public test 的调用名在项目内唯一对应一个 Source function。

## 2. 数据与覆盖率

| 方向 | 项目 | Source tests | Target tests | Strict test gold | 被测试唯一引用函数 | Strict function gold | Function gold links |
|---|---:|---:|---:|---:|---:|---:|---:|
| Python→C++ | 171 | 3066 | 1778 | 1392 | 741 | 337 | 701 |
| C++→Python | 181 | 973 | 1137 | 571 | 472 | 217 | 470 |

严格 function gold 在“被 public tests 唯一引用的函数”中的覆盖率分别为 `45.48%` 和 `45.97%`。以仓库全部函数为分母时覆盖率约为 `0.55%` 和 `0.44%`，主要因为大量源码函数没有 public test 覆盖，不能把该数字直接解释为解析失败。

## 3. Test→Test 结果

统一使用 `fusion`：

| 方向 | Recall@1 | Recall@3 | MRR | Alignment coverage |
|---|---:|---:|---:|---:|
| Python→C++ | 0.6430 | 0.8355 | 0.7439 | 0.4540 |
| C++→Python | 0.7863 | 0.9317 | 0.8580 | 0.5868 |

## 4. Function→Target Tests 策略比较

### Python→C++

| 策略 | Macro R@1 | Macro R@3 | HitRate@1 | MRR | Top-5 未命中 |
|---|---:|---:|---:|---:|---:|
| function | 0.4759 | 0.7645 | 0.6617 | 0.7599 | 31 |
| test | 0.6031 | 0.8553 | 0.8398 | 0.8866 | 18 |
| function_test | 0.5533 | 0.8077 | 0.7626 | 0.8370 | 16 |
| fusion | 0.5465 | 0.8367 | 0.7715 | 0.8509 | 10 |
| adaptive | **0.6155** | 0.8489 | **0.8457** | **0.8898** | 15 |

### C++→Python

| 策略 | Macro R@1 | Macro R@3 | HitRate@1 | MRR | Top-5 未命中 |
|---|---:|---:|---:|---:|---:|
| function | 0.5118 | 0.7824 | 0.7281 | 0.8190 | 6 |
| test | **0.6436** | **0.9092** | **0.9309** | **0.9633** | 0 |
| function_test | 0.6281 | 0.9010 | 0.9171 | 0.9533 | 0 |
| fusion | 0.6137 | 0.8933 | 0.9032 | 0.9452 | 0 |
| adaptive | 0.6405 | 0.9034 | **0.9309** | 0.9629 | 0 |

工程默认建议使用 `adaptive`：Python→C++ 上综合最优，C++→Python 上与 `test` 基本持平；若更关心 Python→C++ 的 Top-5 尾部覆盖，可额外使用 `fusion`。

## 5. 可展示案例

查询：`djui_alias-tips:Python:alias_tips.py:suggest_alias:1`

- 请求策略：`adaptive`
- 实际使用：`function_test`
- Source test context：2 个
- 未触发 fallback

| Rank | Score | Target test | Calls |
|---:|---:|---|---|
| 1 | 0.8096 | `TestPublicAliasTips.SuggestAliasKnownVariants` | `suggest_alias` |
| 2 | 0.7704 | `TestPublicAliasTips.SuggestAliasNoneVariants` | `suggest_alias` |
| 3 | 0.7585 | `TestPublicAliasTips.IsAliasRecommendedTrueAndFalse` | `is_alias_recommended` |

前两名是该函数对应的两个正确 Target public tests；第三名是同文件中的干扰测试。这说明系统已经能从待翻译 Source function 出发，利用 Source tests 和函数代码检索多个 Target tests。

## 6. 结论与限制

1. 完整流程已实现并在两个语言方向运行：结构扫描、chunk、embedding、持久化索引、单函数定位、批量评测和失败分类。
2. 单独使用 function code 效果明显较弱，Source public test context 对定位很重要。
3. 严格 gold 依赖测试名称一致性，`test` 策略可能利用名称信号；当前结果不能直接等价为纯代码语义理解能力。
4. 后续研究应增加测试名称遮蔽消融、人工标注样本，以及 filename/fuzzy/BM25 baseline。
