# code_graph_demo

Minimal demo for representing a Python repository as a searchable code graph.

Default target: `RepoTransAgent/`.

## Supported Features

- AST nodes: `File`, `Class`, `Function`, `Method`, `Import`
- Call graph edges: `CALLS`
- CFG edges: `CFG_ENTRY`, `NEXT`, `TRUE_BRANCH`, `FALSE_BRANCH`, `LOOP_BODY`
- DFG nodes/edges: `VariableDef`, `VariableUse`, `DEFINES`, `USES`, `DATA_FLOW`
- Query demo: keyword search over nodes plus one-hop incoming/outgoing neighbors

## Run

Build the base graph:

```bash
python code_graph_demo/main.py --src RepoTransAgent --out code_graph_demo/output/code_graph.json
```

Build the graph with CFG and DFG:

```bash
python code_graph_demo/main.py --src RepoTransAgent --out code_graph_demo/output/code_graph_full.json --with-cfg --with-dfg
```

Query the graph:

```bash
python code_graph_demo/query.py --graph code_graph_demo/output/code_graph_full.json --q parse_action_from_text
```

## Output Format

The exported JSON has three top-level fields:

- `metadata`: source root, language, enabled features, unresolved call count
- `nodes`: code entities such as files, classes, functions, statements, variables
- `edges`: relationships such as containment, imports, calls, control flow, data flow

## Current Results

- Base graph: `nodes=179`, `edges=250`, `CALLS=79`
- Full graph: `nodes=1343`, `edges=1584`, `DFG nodes=726`, `DFG edges=876`

## Limitations

- Python only
- Static approximation only
- `CALLS`, `CFG`, and `DFG` are lightweight demos, not compiler-grade analysis
- Does not handle complex dynamic dispatch, aliases, reflection, closures, global/nonlocal scope, or polymorphism

## Next Extensions

- Use tree-sitter for multi-language parsing
- Store/query graphs with NetworkX or Neo4j
- Add embedding-based semantic retrieval
- Expand retrieved context with graph traversal
- Convert retrieved subgraphs into prompts for Graph RAG code generation
