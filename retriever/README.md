# Retriever

配合 `tools/import_to_neo4j.py` 使用，从 Neo4j 代码图中检索节点及其关系。

## 快速开始

```bash
cd D:\Code\Code2Graph
python retriever/retriever.py bolt://localhost:7687 neo4j your_password
```

## CLI 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `search <kw> [kind]` | 按名搜节点 | `search predict`, `search __init__ Function` |
| `neigh <name>` | 查邻接（跨所有 label 分组展示） | `neigh predict` |
| `expand <name> [hops] [edge] [dir]` | N跳展开 | `expand predict 3 CALLS out` |
| `summary` | 图表统计 | `summary` |

## Python API

```python
from retriever import CodeGraphRetriever

cr = CodeGraphRetriever("bolt://localhost:7687", ("neo4j", "password"))

cr.search("predict")                          # 返回 list[dict]
cr.search("__init__", kind="Function")        # 限定类型
cr.neighbors("predict")                       # 出边 + 入边，跨 label 分组
cr.expand("predict", hops=2, edge_type="CALLS", direction="out")  # 依赖闭包
cr.summary()                                  # 节点/边统计
cr.close()
```
