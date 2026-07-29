# 文件翻译顺序工具

根据项目内部的 Python `import` 或 C/C++ `#include` 关系生成依赖优先的翻译顺序。
提供两种模式：

- **静态输出**：直接生成排序后的文件列表（text / JSON）
- **交互模式**：逐步追踪翻译进度，自动计算当前可翻译的文件

默认排除测试代码。依赖关系优先用 tree-sitter AST 精确解析，不可用时回退到正则。

## 环境

推荐 Python 3.10 及以上版本，并安装 tree-sitter 相关依赖：

```bash
pip install tree-sitter==0.25.2 tree-sitter-python==0.25.0 tree-sitter-cpp==0.23.4
```

tree-sitter 未安装或发生解析异常时，脚本自动回退到正则提取 import/include。

不建议直接安装未固定版本的最新版组合。已验证 `tree-sitter 0.26.0` 与上述 grammar 版本在 Windows 上分析部分复杂项目时可能触发原生访问冲突；这里固定的版本组合已通过本地数据集回归测试。

## 排序算法

不采用全局扁平拓扑排序，而是**按功能依赖链**组织：

1. 找到**入口文件**（不被项目内任何文件依赖的，如 `main.py`、`app.py`）
2. 从每个入口 DFS 追溯完整传递依赖，得到该功能的全部文件
3. 被其他链依赖的链优先输出，链内按依赖关系拓扑排序
4. **共享文件只出现在第一条用到它的链中**，后续链自动跳过

这样做的实际效果：翻译完链 1 后，对应的功能就可以跑起来验证，
而不需要等所有底层库全部翻译完。

## 静态输出（生成翻译顺序）

```bash
# Python 项目，输出纯文本文件列表
python file_topo_sort/topo_sort_files.py --source ./myproject

# C/C++ 项目
python file_topo_sort/topo_sort_files.py --source ./myproject --lang cpp

# JSON 格式输出（含依赖详情和链结构）
python file_topo_sort/topo_sort_files.py --source ./myproject --format json

# 写入文件
python file_topo_sort/topo_sort_files.py --source ./myproject -o order.txt

# 包含测试文件
python file_topo_sort/topo_sort_files.py --source ./myproject --include-tests
```

输出是一行一个文件路径，按依赖排序。例如：

```text
utils.py
models.py
app.py
main.py
```

## JSON 输出

```bash
python file_topo_sort/topo_sort_files.py --source ./myproject --format json
```

JSON 字段说明：

| 字段                      | 说明                                                   |
| ------------------------- | ------------------------------------------------------ |
| `translation_order`     | 完整的线性翻译顺序（去重后）                           |
| `chains`                | 按功能入口拆分的依赖链，每条链含`entry` 和 `files` |
| `dependencies`          | 项目内部文件依赖及导入行号                             |
| `external_dependencies` | 无法映射到项目文件的标准库或第三方依赖                 |

示例：

```json
{
  "languages": ["python"],
  "translation_order": [
    "utils.py",
    "models.py",
    "app.py",
    "main.py"
  ],
  "chains": [
    {"entry": "main.py", "files": ["utils.py", "models.py", "app.py", "main.py"]}
  ],
  "dependencies": [
    {
      "file": "app.py",
      "depends_on": "utils.py",
      "line": 3
    }
  ],
  "external_dependencies": [
    {"file": "utils.py", "dependency": "os"}
  ]
}
```

## 交互模式（翻译进度追踪）

```bash
# 启动
python file_topo_sort/topo_sort_files.py --source ./myproject -i

# 重新开始（丢弃已有进度）
python file_topo_sort/topo_sort_files.py --source ./myproject -i --reset

# 指定状态文件路径
python file_topo_sort/topo_sort_files.py --source ./myproject -i --state my_state.json
```

进入后，工具自动显示**当前可翻译的文件**（其所有项目内依赖已翻译完成）。
状态保存到 `<source>/.translate_state.json`，下次启动自动恢复。

### 交互命令

| 命令                    | 简写   | 作用                                            |
| ----------------------- | ------ | ----------------------------------------------- |
| `done <文件>`         | `d`  | 标记文件为已翻译（支持文件名片段模糊匹配）      |
| `undo <文件>`         | —     | 撤销翻译标记                                    |
| `ready [N]`           | `ls` | 显示当前可翻译的文件及其依赖提示                |
| `next [N]`            | `n`  | 按链顺序显示建议翻译的下几个文件                |
| `remaining [关键词]`  | `r`  | 列出未翻译文件及阻塞原因（等待哪些依赖）        |
| `translated [关键词]` | `t`  | 列出已翻译文件                                  |
| `status`              | `s`  | 进度概览（百分比 + 进度条 + 可翻译/等待中数量） |
| `search <关键词>`     | —     | 搜索文件，显示每个文件的翻译状态                |
| `quit`                | `q`  | 退出并保存状态                                  |

示例操作流程：

```
> ls                          # 查看可翻译的
  utils.py
  models.py

> d utils.py models.py        # 翻译完成两个基础文件
✔ 标记完成 (2): utils.py, models.py
📊 进度: 2/10 (20%)

> n                           # 查看建议的下一个
  app.py  ← 依赖: utils.py, models.py

> d app.py                    # 继续翻译
```

## 可选参数总览

| 参数                  | 说明                                                           |
| --------------------- | -------------------------------------------------------------- |
| `--source PATH`     | 待分析项目路径（必填）                                         |
| `--lang LANG`       | `python`、`cpp`，逗号分隔。默认 `python`                 |
| `-o, --output PATH` | 静态模式将结果写入文件                                         |
| `--format FORMAT`   | 静态模式输出格式：`text`（默认）/ `json`                   |
| `-i, --interactive` | 启动交互式翻译进度追踪模式                                     |
| `--state PATH`      | 交互模式状态文件路径（默认`<source>/.translate_state.json`） |
| `--reset`           | 交互模式丢弃已有进度重新开始                                   |
| `--include-tests`   | 将测试文件也纳入排序                                           |
