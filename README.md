# Code2Graph

使用方法：
python main.py --source-root (写希望被建立数据库的仓库地址) --database ./build/my-db --results ./build/queries --query-dir ./codeql_queries

这样会在build/my-db下建立数据库并把查询结果存入build/queries

把 build/queries 里的 CSV 导入 Neo4j：


```bash
python neo4j_import.py --results build/queries --uri bolt://localhost:7687 --user neo4j --password your_password
```

这个导入脚本会先创建节点，再创建边。边会直接连接源节点和目标节点，如果边两端的节点在节点 CSV 里不存在，脚本会自动补一个占位节点，保证关系也能写进去。