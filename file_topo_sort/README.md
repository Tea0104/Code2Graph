# 文件翻译顺序工具

该工具分析项目内部的 Python `import` 或 C/C++ `#include` 关系，并生成依赖优先的文件翻译顺序。默认排除测试代码，支持文本和 JSON 两种输出。

## 环境

推荐 Python 3.10 及以上版本，并安装 tree-sitter 相关依赖：

```powershell
python -m pip install `
  tree-sitter==0.25.2 `
  tree-sitter-python==0.25.0 `
  tree-sitter-cpp==0.23.4
```

工具默认使用 tree-sitter 解析 Python 和 C/C++，不依赖目标项目所使用的 Python 运行时版本。tree-sitter 未安装或发生可捕获的解析异常时，脚本才会回退到正则提取 import/include。

不建议直接安装未固定版本的最新版组合。已验证 `tree-sitter 0.26.0` 与上述 grammar 版本在 Windows 上分析部分复杂项目时可能触发原生访问冲突；这里固定的版本组合已通过本地数据集回归测试。

## 文本输出

在仓库根目录运行：

```powershell
python .\file_topo_sort\topo_sort_files.py `
  --source ".\数据集\Python_to_C++\source_projects\djui_alias-tips" `
  --lang python
```

输出中的文件从上到下依次翻译。例如：

```text
utils.py
parser.py
main.py
```

## JSON 输出

```powershell
python .\file_topo_sort\topo_sort_files.py `
  --source ".\数据集\C++_to_Python\source_projects\a-e-k_canvas_ity" `
  --lang cpp `
  --format json `
  -o ".\file_order.json"
```

JSON 主要字段：

- `translation_order`：线性文件翻译顺序；
- `dependencies`：项目内部文件依赖及导入行号；
- `external_dependencies`：无法映射到项目文件的标准库或第三方依赖；
- `cycles`：检测到的循环依赖；
- `broken_edges`：为生成顺序而断开的循环依赖边。

示例：

```json
{
  "languages": ["cpp"],
  "translation_order": [
    "src/canvas_ity.hpp",
    "demos/tiger/tiger.cpp",
    "src/canvas_ity.cpp"
  ],
  "dependencies": [
    {
      "file": "demos/tiger/tiger.cpp",
      "depends_on": "src/canvas_ity.hpp",
      "line": 37
    }
  ],
  "cycles": [],
  "broken_edges": []
}
```

## 可选参数

```text
--source PATH       待分析项目路径，必填
--lang LANG         python、cpp，或逗号分隔的多个语言
--format FORMAT     text 或 json，默认 text
-o, --output PATH   将结果写入文件
--include-tests     将测试文件也纳入排序，默认不启用
```
