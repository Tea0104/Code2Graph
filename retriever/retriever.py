"""Neo4j 代码图检索器，配合 import_to_neo4j.py 使用。"""

from collections import defaultdict
from typing import Optional

ALL_LABELS = ["Function", "Class", "Module", "Variable"]


class CodeGraphRetriever:
    def __init__(self, uri="bolt://localhost:7687", auth=("neo4j", "neo4j")):
        from neo4j import GraphDatabase
        self._driver = GraphDatabase.driver(uri, auth=auth)
        self._driver.verify_connectivity()

    def close(self):
        self._driver.close()

    def _run(self, cypher, **params):
        with self._driver.session() as s:
            return list(s.run(cypher, **params))

    @staticmethod
    def _n(node):
        d = dict(node.items())
        d["labels"] = list(node.labels)
        return d

    def search(self, keyword, kind=None, limit=30):
        labels = [kind] if kind else ALL_LABELS
        results = []
        for label in labels:
            cypher = f"""
                MATCH (n:{label})
                WHERE toLower(n.name) CONTAINS toLower($kw)
                RETURN n ORDER BY n.name LIMIT {limit}
            """
            for r in self._run(cypher, kw=keyword):
                results.append(self._n(r["n"]))
            if len(results) >= limit:
                break
        return results[:limit]

    def neighbors(self, name):
        matches = []
        for label in ALL_LABELS:
            recs = self._run(f"MATCH (n:{label} {{name: $name}}) RETURN n", name=name)
            if not recs:
                continue
            node = self._n(recs[0]["n"])

            out_recs = self._run(
                f"MATCH (n:{label} {{name: $name}})-[r]->(m) RETURN m, type(r) AS edge", name=name
            )
            outgoing = [{"neighbor": self._n(r["m"]), "edge": r["edge"]} for r in out_recs]

            in_recs = self._run(
                f"MATCH (m)-[r]->(n:{label} {{name: $name}}) RETURN m, type(r) AS edge", name=name
            )
            incoming = [{"neighbor": self._n(r["m"]), "edge": r["edge"]} for r in in_recs]

            matches.append({"node": node, "label": label, "outgoing": outgoing, "incoming": incoming})

        return {"name": name, "matches": matches}

    def expand(self, name, hops=2, edge_type=None, direction="both"):
        hops = max(1, min(hops, 10))
        start_nodes = []
        for label in ALL_LABELS:
            recs = self._run(f"MATCH (n:{label} {{name: $name}}) RETURN n", name=name)
            for r in recs:
                start_nodes.append(self._n(r["n"]))

        if not start_nodes:
            return {"start_nodes": [], "nodes": [], "edges": [], "layers": []}

        type_part = f":{edge_type}" if edge_type else ""
        dir_map = {
            "out":  f"-[{type_part}*1..{hops}]->",
            "in":   f"<-[{type_part}*1..{hops}]-",
            "both": f"-[{type_part}*1..{hops}]-",
        }
        arrow = dir_map[direction]

        reached_names = set()
        for sn in start_nodes:
            lbl = sn["labels"][0]
            cypher = f"MATCH (start:{lbl} {{name: $name}}){arrow}(reached) RETURN DISTINCT reached"
            for r in self._run(cypher, name=name):
                reached_names.add(r["reached"]["name"])

        all_names = {sn["name"] for sn in start_nodes} | reached_names
        all_nodes = list(start_nodes)
        seen = {sn["name"] for sn in start_nodes}
        for label in ALL_LABELS:
            for r in self._run(
                f"MATCH (n:{label}) WHERE n.name IN $names RETURN n", names=list(all_names)
            ):
                n = self._n(r["n"])
                if n["name"] not in seen:
                    all_nodes.append(n); seen.add(n["name"])

        edges = self._edges_between(list(all_names))
        layers = self._build_layers(
            {sn["name"] for sn in start_nodes}, all_nodes, edges, hops, direction, edge_type
        )
        return {"start_nodes": start_nodes, "nodes": all_nodes, "edges": edges, "layers": layers}

    def _edges_between(self, names):
        if len(names) < 2:
            return []
        cypher = """
            MATCH (a)-[r]->(b)
            WHERE a.name IN $names AND b.name IN $names
            RETURN a.name AS source, b.name AS target, type(r) AS type
        """
        return [
            {"source": r["source"], "target": r["target"], "type": r["type"]}
            for r in self._run(cypher, names=names)
        ]

    def _build_layers(self, start_names, nodes, edges, max_hops, direction, edge_type):
        out_adj = defaultdict(list)
        in_adj = defaultdict(list)
        for e in edges:
            out_adj[e["source"]].append((e["target"], e["type"]))
            in_adj[e["target"]].append((e["source"], e["type"]))

        name_set = {n["name"] for n in nodes}
        layers = []
        frontier = set(start_names)
        visited = set(start_names)

        for hop in range(1, max_hops + 1):
            new = set()
            layer_edges = []
            for nname in frontier:
                if direction in ("out", "both"):
                    for tgt, ety in out_adj.get(nname, []):
                        if (edge_type is None or ety == edge_type) and tgt in name_set:
                            layer_edges.append({"source": nname, "target": tgt, "type": ety})
                            if tgt not in visited:
                                new.add(tgt); visited.add(tgt)
                if direction in ("in", "both"):
                    for src, ety in in_adj.get(nname, []):
                        if (edge_type is None or ety == edge_type) and src in name_set:
                            layer_edges.append({"source": src, "target": nname, "type": ety})
                            if src not in visited:
                                new.add(src); visited.add(src)
            if not new:
                break
            layer_nodes = [n for n in nodes if n["name"] in new]
            layers.append({"hop": hop, "nodes": layer_nodes, "edges": layer_edges})
            frontier = new
        return layers

    def summary(self):
        node_counts = {}
        for label in ALL_LABELS:
            recs = self._run(f"MATCH (n:{label}) RETURN count(n) AS cnt")
            cnt = recs[0]["cnt"] if recs else 0
            if cnt:
                node_counts[label] = cnt
        edge_recs = self._run(
            "MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS cnt ORDER BY cnt DESC"
        )
        return {"nodes": node_counts, "edges": {r["rel"]: r["cnt"] for r in edge_recs}}


def _cli():
    import sys
    uri = sys.argv[1] if len(sys.argv) > 1 else "bolt://localhost:7687"
    user = sys.argv[2] if len(sys.argv) > 2 else "neo4j"
    pwd = sys.argv[3] if len(sys.argv) > 3 else "neo4j"

    print("=" * 55)
    print("  CodeGraph Retriever")
    print("=" * 55)

    try:
        cr = CodeGraphRetriever(uri, (user, pwd))
    except Exception as e:
        print(f"  [ERROR] {e}")
        return

    s = cr.summary()
    print(f"  节点: {s['nodes']}")
    print(f"  边:   {s['edges']}")
    print("\n  命令: search <kw> [kind] | neigh <name> | expand <name> [hops] [edge] [dir] | summary | quit\n")

    while True:
        try:
            cmd = input("graph> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not cmd:
            continue

        parts = cmd.split()
        act = parts[0].lower()

        try:
            if act == "quit":
                break
            elif act == "search":
                kw = parts[1] if len(parts) > 1 else ""
                kind = parts[2] if len(parts) > 2 else None
                results = cr.search(kw, kind)
                print(f"  {len(results)} 条结果:")
                for n in results:
                    print(f"    [{'|'.join(n['labels'])}] name={n['name']}")
            elif act == "neigh":
                name = parts[1] if len(parts) > 1 else ""
                r = cr.neighbors(name)
                matches = r.get("matches", [])
                if not matches:
                    print(f"  [NOT FOUND] '{name}'")
                    continue
                for i, m in enumerate(matches):
                    n = m["node"]
                    print(f"\n  --- [{m['label']}] {n['name']} ---")
                    print(f"  出边 ({len(m['outgoing'])}):")
                    for x in m["outgoing"][:15]:
                        nb = x["neighbor"]
                        print(f"    --[{x['edge']}]--> [{'|'.join(nb['labels'])}] {nb['name']}")
                    if len(m["outgoing"]) > 15:
                        print(f"    ... 还有 {len(m['outgoing']) - 15} 条")
                    print(f"  入边 ({len(m['incoming'])}):")
                    for x in m["incoming"][:15]:
                        nb = x["neighbor"]
                        print(f"    <--[{x['edge']}]-- [{'|'.join(nb['labels'])}] {nb['name']}")
                    if len(m["incoming"]) > 15:
                        print(f"    ... 还有 {len(m['incoming']) - 15} 条")
            elif act == "expand":
                name = parts[1] if len(parts) > 1 else ""
                hops = int(parts[2]) if len(parts) > 2 else 2
                etype = parts[3] if len(parts) > 3 else None
                direction = parts[4] if len(parts) > 4 else "both"
                r = cr.expand(name, hops=hops, edge_type=etype, direction=direction)
                sn = r.get("start_nodes", [])
                if not sn:
                    print(f"  [NOT FOUND] '{name}'")
                    continue
                sn_info = ", ".join(f"[{'|'.join(n['labels'])}]" for n in sn)
                print(f"  起点 ({len(sn)}): {sn_info}")
                print(f"  展开 {hops} 跳 ({direction}, {etype or '不限类型'})")
                print(f"  共 {len(r['nodes'])} 节点, {len(r['edges'])} 边")
                layers = r.get("layers", [])
                if not layers:
                    print(f"  -> 没有展开到新节点")
                else:
                    for layer in layers:
                        print(f"  -- 第 {layer['hop']} 跳 ({len(layer['nodes'])} 节点, {len(layer['edges'])} 边) --")
                        for n in layer["nodes"][:10]:
                            print(f"    [{'|'.join(n['labels'])}] {n['name']}")
                        if len(layer["nodes"]) > 10:
                            print(f"    ... 还有 {len(layer['nodes']) - 10} 个")
            elif act == "summary":
                s = cr.summary()
                print("  节点:"); [print(f"    {k}: {v}") for k, v in s["nodes"].items()]
                print("  边:");   [print(f"    {k}: {v}") for k, v in s["edges"].items()]
            else:
                print(f"  未知: {act}  |  可用: search / neigh / expand / summary / quit")
        except Exception as e:
            print(f"  [ERROR] {e}")

    cr.close()


if __name__ == "__main__":
    _cli()
