# Code2Graph

tree-sitter 版本的图抽取入口：

```bash
python tree_sitter_main.py --source-root /path/to/source --languages python,cpp --output-dir build/json
```

这个命令直接扫描源码并输出和 Neo4j 导入脚本兼容的 `nodes.json` / `edges.json`。

需要的 Python 包包括 `tree-sitter`、`tree-sitter-python` 和 `tree-sitter-cpp`。



neo4j_import.py使用方法：

```bash
python neo4j_import.py --nodes build/json/nodes.json --edges build/json/edges.json --source-root /path/to/source --uri bolt://localhost:7687 --user yourname --password your_password
```

这个导入脚本会先创建节点，再创建边。对于 `Class`、`Function`、`Method` 节点，会额外写入两个属性：`definitionSnippet` 表示定义头部，`implementationSnippet` 表示从 `startLine` 到 `endLine` 的完整代码块。

边会直接连接源节点和目标节点，因为 build/json 里已经写好了 id，所以导入时会按 id 进行 MERGE，不需要再重新推导唯一键。


如果只想先删除 Neo4j 里的所有数据，不导入任何内容，可以用：

```bash
python neo4j_import.py --uri bolt://localhost:7687 --user yourname --password your_password --clear-only
```

retriever使用方法：

```bash
python retriever/retriever.py bolt://localhost:7687 neo4j your_password
```
