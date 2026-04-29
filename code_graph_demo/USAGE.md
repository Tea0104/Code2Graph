# code_graph_demo 使用说明

这个 demo 用来把一个 Python 代码仓库转换成“代码图”。普通代码是文本文件，代码图会把文件、类、函数、方法、import、调用关系、控制流、数据流表示成节点和边，方便后续做检索或 Graph RAG。

默认分析目录是主仓库里的 `RepoTransAgent/`。

## 目录结构

- `main.py`：命令行入口，负责串起扫描、AST 提取、建图、导出。
- `repo_scanner.py`：扫描 `.py` 文件。
- `ast_extractor.py`：用 Python 标准库 `ast` 提取类、函数、调用等信息。
- `graph_builder.py`：把 AST 信息组装成统一代码图。
- `cfg_builder.py`：生成轻量控制流图。
- `dfg_builder.py`：生成轻量数据流图。
- `query.py`：对生成的 JSON 图做关键词查询。
- `output/`：保存生成的图文件。

## 生成基础代码图

基础图包含 AST 节点和调用边。

```bash
python code_graph_demo/main.py --src RepoTransAgent --out code_graph_demo/output/code_graph.json
```

## 生成完整代码图

完整图额外包含 CFG 和 DFG。

```bash
python code_graph_demo/main.py --src RepoTransAgent --out code_graph_demo/output/code_graph_full.json --with-cfg --with-dfg
```

## 查询代码图

下面命令会搜索包含 `parse_action_from_text` 的节点，并显示它的一跳邻居边。

```bash
python code_graph_demo/query.py --graph code_graph_demo/output/code_graph_full.json --q parse_action_from_text
```

## JSON 输出怎么看

生成的 `code_graph.json` 主要有三部分：

- `metadata`：图的说明信息，例如分析目录、语言、开启了哪些功能。
- `nodes`：代码实体，例如文件、类、函数、变量定义。
- `edges`：实体之间的关系，例如文件包含函数、函数调用函数、变量定义流向变量使用。

## 常见 Node Type

- `File`：一个 Python 文件。
- `Class`：一个类。
- `Function`：顶层函数。
- `Method`：类中的方法。
- `Import`：import 语句。
- `Statement` / `ReturnStatement`：函数中的语句节点，用于 CFG。
- `VariableDef`：变量定义，例如 `x = ...`。
- `VariableUse`：变量使用，例如 `return x`。

## 常见 Edge Type

- `CONTAINS`：包含关系，例如 File -> Function。
- `IMPORTS`：文件导入了某个模块或符号。
- `CALLS`：函数或方法调用另一个函数或方法。
- `CFG_ENTRY`：函数进入第一条语句。
- `NEXT`：顺序执行下一条语句。
- `TRUE_BRANCH`：if 条件为真进入的分支。
- `FALSE_BRANCH`：if 条件为假进入的分支。
- `LOOP_BODY`：for/while 进入循环体。
- `DEFINES`：函数定义了变量。
- `USES`：函数使用了变量。
- `DATA_FLOW`：变量定义流向后续变量使用。

## 推荐阅读源码顺序

1. `main.py`
2. `repo_scanner.py`
3. `ast_extractor.py`
4. `graph_builder.py`
5. `query.py`
6. `cfg_builder.py`
7. `dfg_builder.py`

## 当前局限

- 只支持 Python。
- `CALLS`、`CFG`、`DFG` 都是静态近似，不是工业级分析。
- 不处理复杂动态调用、别名、多态、跨文件精确解析。
- DFG 只做同一个函数内部的简单变量名匹配。



这个 demo 把 Python 仓库从纯文本代码转换成包含 AST、调用关系、控制流和数据流的结构化代码图，并支持关键词检索和一跳邻居扩展，可作为 Graph RAG for Code Generation 的最小原型。
