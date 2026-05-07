# Neo4j Call Graph Importer

将 CodeQL 提取的函数调用关系 CSV 文件自动导入 Neo4j 图数据库，生成 `Function` 节点和 `CALLS` 关系。

## 环境要求
- Python 3.8+
- Neo4j 数据库（本地或远程）
- 安装依赖：pip install neo4j

## 使用方法
python import_to_neo4j.py --csv path/to/calls.csv --password your_password
