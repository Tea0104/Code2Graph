# Retriever

配合 `neo4j_import.py` 使用，从 Neo4j 代码图中检索节点及关系。

## 快速开始

```bash
cd D:\Code\Code2Graph
python retriever/retriever.py bolt://localhost:7687 neo4j your_password
```

## CLI 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `search <kw> [kind]` | 按名搜节点 | `search predict`, `search init Method` |
| `neigh <id>` | 查邻接 | `neigh run.py:predict:328` |
| `expand <id> [hops] [edge] [dir]` | N跳展开 | `expand run.py:predict:328 3 CALLS out` |
| `summary` | 图表统计 | `summary` |

## Python API

```python
from retriever import CodeGraphRetriever

cr = CodeGraphRetriever("bolt://localhost:7687", ("neo4j", "password"))

cr.search("predict")                          # 关键字搜索 → list[dict]
cr.search("__init__", kind="Method")           # 限定类型
cr.neighbors("run.py:predict:328")            # 出边+入边
cr.expand("run.py:predict:328", hops=3, edge_type="CALLS", direction="out")
cr.summary()
cr.close()
```
