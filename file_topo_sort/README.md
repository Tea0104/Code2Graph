# 文件翻译顺序与进度工具

该工具分析项目内部的 Python、C/C++、Java、JavaScript 和 C# 显式依赖关系，生成依赖优先的文件翻译顺序，并支持交互式记录翻译进度。默认排除测试代码。

## 环境

推荐 Python 3.10 及以上版本：

```powershell
python -m pip install `
  tree-sitter==0.25.2 `
  tree-sitter-python==0.25.0 `
  tree-sitter-cpp==0.23.4 `
  tree-sitter-c `
  tree-sitter-java `
  tree-sitter-javascript `
  tree-sitter-c-sharp
```

工具优先使用 tree-sitter，不依赖目标项目的 Python 运行时版本。tree-sitter 不可用或解析失败时会回退到正则提取 import/include。

也可以作为稳定的 Python 接口复用：

```python
from file_topo_sort import analyze_project

result = analyze_project("./my-project", "java")
print(result["translation_order"])
```

该接口返回带 `schema_version` 的 JSON 兼容字典。CLI 的 `--format json` 使用完全相同的数据结构。

## 静态排序

```powershell
# Python 项目
python .\file_topo_sort\topo_sort_files.py `
  --source ".\my-python-project" `
  --lang python

# C/C++ 项目，并输出 JSON
python .\file_topo_sort\topo_sort_files.py `
  --source ".\my-cpp-project" `
  --lang cpp `
  --format json `
  -o ".\file_order.json"
```

文本输出是一行一个文件，依赖文件位于使用它的文件之前。遇到循环依赖时，工具优先断开位于文件末尾的延迟导入边，并在输出末尾报告循环和断边。

JSON 保留原有字段，并增加功能依赖链：

- `translation_order`：完整线性翻译顺序；
- `chains`：按入口文件组织的功能依赖链，共享文件只出现在第一条使用它的链中；
- `dependencies`：项目内部文件依赖及导入行号；
- `external_dependencies`：标准库或第三方依赖；
- `cycles`：检测到的循环依赖；
- `broken_edges`：为生成可执行顺序而断开的循环依赖边。

## Python 接口

如果要在服务层或其他模块里暴露排序能力，可以直接导入两个接口：

```python
from file_topo_sort import get_order_information, get_translation_order

info = get_order_information("./my-python-project")
# {"number": 3, "files": ["base.py", "service.py", "app.py"]}

next_files = get_translation_order(
    "./my-python-project",
    number=2,
    already=["base.py"],
)
# ["service.py", "app.py"]
```

| 接口 | 返回 |
|---|---|
| `get_order_information(source_path: str, include_tests: bool = False)` | `{"number": int, "files": list[str]}` |
| `get_translation_order(source_path: str, number: int, already: list[str], include_tests: bool = False)` | `list[str]` |

返回的文件路径是相对 `source_path` 的 POSIX 路径。`already` 应使用
`get_order_information()` 返回的路径，或位于 `source_path` 内部的绝对路径。

## 交互模式

```powershell
# 启动并把进度保存到项目目录下的 .translate_state.json
python .\file_topo_sort\topo_sort_files.py `
  --source ".\my-python-project" `
  --lang python `
  --interactive

# 清空已有进度并重新扫描
python .\file_topo_sort\topo_sort_files.py `
  --source ".\my-python-project" `
  --interactive `
  --reset

# 指定状态文件
python .\file_topo_sort\topo_sort_files.py `
  --source ".\my-python-project" `
  --interactive `
  --state ".\translation-progress.json"
```

交互模式会显示当前依赖已经完成、可以开始翻译的文件。状态会在每次修改后自动保存，下次启动继续使用。

| 命令 | 简写 | 作用 |
|---|---|---|
| `ready [N]` | `ls` | 显示当前可翻译文件 |
| `done <文件...>` | `d` | 标记为已翻译，支持唯一的路径片段匹配 |
| `undo <文件...>` | | 撤销已翻译标记 |
| `next [N]` | `n` | 按整体翻译顺序显示后续文件及状态 |
| `remaining [关键词]` | `r` | 显示未翻译文件及阻塞依赖 |
| `translated [关键词]` | `t` | 显示已翻译文件 |
| `search <关键词>` | | 搜索文件并显示状态 |
| `status` | `s` | 显示总体进度 |
| `quit` | `q` | 保存并退出 |

## 参数

| 参数 | 说明 |
|---|---|
| `--source PATH` | 待分析项目路径，必填 |
| `--lang LANG` | `python`、`c`、`cpp`、`java`、`javascript`、`csharp`，支持常见别名和逗号分隔 |
| `--format text/json` | 静态输出格式 |
| `-o, --output PATH` | 静态结果输出文件 |
| `--include-tests` | 将测试文件纳入分析 |
| `-i, --interactive` | 启动交互模式 |
| `--state PATH` | 指定交互状态文件 |
| `--reset` | 丢弃已有状态并重新扫描 |
