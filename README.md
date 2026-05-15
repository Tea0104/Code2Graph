# Code2Graph

main.py使用方法：

python main.py --source-root (写希望被建立数据库的仓库地址) --database ./build/my-db --results ./build/queries --query-dir ./codeql_queries

这样会在build/my-db下建立数据库并把查询结果存入build/queries



neo4j_import.py使用方法：

```bash
python neo4j_import.py --nodes build/json/nodes.json --edges build/json/edges.json --source-root /path/to/source --uri bolt://localhost:7687 --user yourname --password your_password
```

这个导入脚本会先创建节点，再创建边。节点会额外写入 `snippet` 属性，内容来自 `--source-root` 指向的源码仓库，并按 `file + startline + endline` 截取。

边会直接连接源节点和目标节点，因为 build/json 里已经写好了 id，所以导入时会按 id 进行 MERGE，不需要再重新推导唯一键。

如果只想先删除 Neo4j 里的所有数据，不导入任何内容，可以用：

```bash
python neo4j_import.py --uri bolt://localhost:7687 --user yourname --password your_password --clear-only
```

retriever使用方法：

```bash
python retriever/retriever.py bolt://localhost:7687 neo4j your_password
```
