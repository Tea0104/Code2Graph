# Code2Graph

This repository now provides a standalone CodeQL knowledge graph builder for the queries under codeql_queries/python/.

Build a graph from exported CodeQL results:

```bash
python codeql_knowledge_graph.py --results path/to/codeql-results --out output/codeql_kg.json
```

The script reads CSV, TSV, or JSON exports for the query files and writes a single JSON graph with nodes, edges, and metadata.