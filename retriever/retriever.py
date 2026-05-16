"""Neo4j 代码图检索器，配合 neo4j_import.py 使用。"""

from collections import defaultdict
from typing import Optional

try:
    from faiss_store import FaissQueryStore
except ImportError:
    from .faiss_store import FaissQueryStore


class CodeGraphRetriever:
    def __init__(self, uri="bolt://localhost:7687", auth=("neo4j", "neo4j")):
        from neo4j import GraphDatabase
        self._cache = FaissQueryStore()
        self._driver = None
        try:
            self._driver = GraphDatabase.driver(uri, auth=auth)
            self._driver.verify_connectivity()
        except Exception:
            self._driver = None

    def close(self):
        if self._driver:
            self._driver.close()
        self._cache.save()

    def _run(self, cypher, **params):
        with self._driver.session() as s:
            return list(s.run(cypher, **params))

    @staticmethod
    def _n(node):
        d = dict(node.items())
        d["labels"] = list(node.labels)
        return d

    def search(self, keyword, kind=None, limit=30):
        hit = self._cache.get(keyword, kind=kind, limit=limit)
        if hit is not None:
            return hit
        if not self._driver:
            raise RuntimeError("Neo4j 不可用，且向量库未命中")
        kind_clause = "AND n.kind = $kind" if kind else ""
        cypher = f"""
            MATCH (n:CodeQLNode)
            WHERE toLower(n.name) CONTAINS toLower($kw)
            {kind_clause}
            RETURN n ORDER BY n.name LIMIT {limit}
        """
        results = [self._n(r["n"]) for r in self._run(cypher, kw=keyword, kind=kind)]
        self._cache.put(keyword, results, kind=kind, limit=limit)
        return results

    def neighbors(self, node_id):
        recs = self._run("MATCH (n:CodeQLNode {id: $id}) RETURN n", id=node_id)
        if not recs:
            return {"node": None, "outgoing": [], "incoming": []}
        node = self._n(recs[0]["n"])

        out_recs = self._run(
            "MATCH (n:CodeQLNode {id: $id})-[r]->(m) RETURN m, type(r) AS edge", id=node_id
        )
        outgoing = [{"neighbor": self._n(r["m"]), "edge": r["edge"]} for r in out_recs]

        in_recs = self._run(
            "MATCH (m)-[r]->(n:CodeQLNode {id: $id}) RETURN m, type(r) AS edge", id=node_id
        )
        incoming = [{"neighbor": self._n(r["m"]), "edge": r["edge"]} for r in in_recs]

        return {"node": node, "outgoing": outgoing, "incoming": incoming}

    def expand(self, node_id, hops=2, edge_type=None, direction="both"):
        hops = max(1, min(hops, 10))
        start = self._run("MATCH (n:CodeQLNode {id: $id}) RETURN n", id=node_id)
        if not start:
            return {"start_node": None, "nodes": [], "edges": [], "layers": []}
        start_node = self._n(start[0]["n"])

        type_part = f":{edge_type}" if edge_type else ""
        dir_map = {
            "out":  f"-[{type_part}*1..{hops}]->",
            "in":   f"<-[{type_part}*1..{hops}]-",
            "both": f"-[{type_part}*1..{hops}]-",
        }
        arrow = dir_map[direction]

        cypher = f"MATCH (start:CodeQLNode {{id: $id}}){arrow}(reached) RETURN DISTINCT reached"
        reached_ids = {start_node["id"]}
        for r in self._run(cypher, id=node_id):
            reached_ids.add(r["reached"]["id"])

        all_nodes = [start_node]
        seen = {start_node["id"]}
        for rid in reached_ids:
            recs = self._run("MATCH (n:CodeQLNode {id: $id}) RETURN n", id=rid)
            if recs:
                n = self._n(recs[0]["n"])
                if n["id"] not in seen:
                    all_nodes.append(n); seen.add(n["id"])

        edges = self._edges_between(list(reached_ids))
        layers = self._build_layers(start_node["id"], all_nodes, edges, hops, direction, edge_type)
        return {"start_node": start_node, "nodes": all_nodes, "edges": edges, "layers": layers}

    def _edges_between(self, ids):
        if len(ids) < 2:
            return []
        cypher = """
            MATCH (a:CodeQLNode)-[r]->(b:CodeQLNode)
            WHERE a.id IN $ids AND b.id IN $ids
            RETURN a.id AS source, b.id AS target, type(r) AS type
        """
        return [
            {"source": r["source"], "target": r["target"], "type": r["type"]}
            for r in self._run(cypher, ids=list(ids))
        ]

    def _build_layers(self, start_id, nodes, edges, max_hops, direction, edge_type):
        out_adj, in_adj = defaultdict(list), defaultdict(list)
        for e in edges:
            out_adj[e["source"]].append((e["target"], e["type"]))
            in_adj[e["target"]].append((e["source"], e["type"]))

        id_to_node = {n["id"]: n for n in nodes}
        layers = []
        frontier = {start_id}
        visited = {start_id}

        for hop in range(1, max_hops + 1):
            new = set()
            layer_edges = []
            for nid in frontier:
                if direction in ("out", "both"):
                    for tgt, ety in out_adj.get(nid, []):
                        if (edge_type is None or ety == edge_type) and tgt in id_to_node:
                            layer_edges.append({"source": nid, "target": tgt, "type": ety})
                            if tgt not in visited:
                                new.add(tgt); visited.add(tgt)
                if direction in ("in", "both"):
                    for src, ety in in_adj.get(nid, []):
                        if (edge_type is None or ety == edge_type) and src in id_to_node:
                            layer_edges.append({"source": src, "target": nid, "type": ety})
                            if src not in visited:
                                new.add(src); visited.add(src)
            if not new:
                break
            layer_nodes = [id_to_node[nid] for nid in new]
            layers.append({"hop": hop, "nodes": layer_nodes, "edges": layer_edges})
            frontier = new
        return layers

    def summary(self):
        kind_recs = self._run(
            "MATCH (n:CodeQLNode) RETURN n.kind AS kind, count(n) AS cnt ORDER BY cnt DESC"
        )
        edge_recs = self._run(
            "MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS cnt ORDER BY cnt DESC"
        )
        return {
            "nodes": {r["kind"]: r["cnt"] for r in kind_recs},
            "edges": {r["rel"]: r["cnt"] for r in edge_recs},
        }

    def node_info(self, node_id):
        """Return full node details including code snippets for LLM consumption."""
        recs = self._run("MATCH (n:CodeQLNode {id: $id}) RETURN n", id=node_id)
        if not recs:
            return None
        node = self._n(recs[0]["n"])
        # Fetch relationships so the caller knows what this node calls / is called by
        out_recs = self._run(
            "MATCH (n:CodeQLNode {id: $id})-[r]->(m) RETURN m.id AS target, type(r) AS edge LIMIT 50",
            id=node_id,
        )
        in_recs = self._run(
            "MATCH (m)-[r]->(n:CodeQLNode {id: $id}) RETURN m.id AS source, type(r) AS edge LIMIT 50",
            id=node_id,
        )
        node["outgoing"] = [{"target": r["target"], "edge": r["edge"]} for r in out_recs]
        node["incoming"] = [{"source": r["source"], "edge": r["edge"]} for r in in_recs]
        return node


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
    print("\n  命令: search <kw> [kind] | detail <id> | neigh <id> | expand <id> [hops] [edge] [dir] | summary | quit\n")

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
                    print(f"    [{n['kind']}] {n['id']}")
                    print(f"      name={n['name']}, file={n.get('file','')}, line={n.get('startLine','')}-{n.get('endLine','')}")
                if not results:
                    print("    (无结果)")
            elif act == "neigh":
                nid = parts[1] if len(parts) > 1 else ""
                r = cr.neighbors(nid)
                if not r["node"]:
                    print(f"  [NOT FOUND] '{nid}'")
                    continue
                n = r["node"]
                print(f"  [{n['kind']}] {n['name']}")
                print(f"    id={n['id']}, file={n.get('file','')}, line={n.get('startLine','')}-{n.get('endLine','')}")
                print(f"  出边 ({len(r['outgoing'])}):")
                for x in r["outgoing"][:15]:
                    nb = x["neighbor"]
                    print(f"    --[{x['edge']}]--> [{nb['kind']}] {nb['name']}  ({nb.get('file','')}:{nb.get('startLine','')})")
                if len(r["outgoing"]) > 15:
                    print(f"    ... 还有 {len(r['outgoing']) - 15} 条")
                print(f"  入边 ({len(r['incoming'])}):")
                for x in r["incoming"][:15]:
                    nb = x["neighbor"]
                    print(f"    <--[{x['edge']}]-- [{nb['kind']}] {nb['name']}  ({nb.get('file','')}:{nb.get('startLine','')})")
                if len(r["incoming"]) > 15:
                    print(f"    ... 还有 {len(r['incoming']) - 15} 条")
            elif act == "expand":
                nid = parts[1] if len(parts) > 1 else ""
                hops = int(parts[2]) if len(parts) > 2 else 2
                etype = parts[3] if len(parts) > 3 else None
                direction = parts[4] if len(parts) > 4 else "both"
                r = cr.expand(nid, hops=hops, edge_type=etype, direction=direction)
                sn = r.get("start_node")
                if not sn:
                    print(f"  [NOT FOUND] '{nid}'")
                    continue
                print(f"  起点: [{sn['kind']}] {sn['name']} ({sn.get('file','')}:{sn.get('startLine','')})")
                print(f"  展开 {hops} 跳 ({direction}, {etype or '不限类型'})")
                print(f"  共 {len(r['nodes'])} 节点, {len(r['edges'])} 边")
                layers = r.get("layers", [])
                if not layers:
                    print("  -> 没有展开到新节点")
                else:
                    for layer in layers:
                        print(f"  -- 第 {layer['hop']} 跳 ({len(layer['nodes'])} 节点, {len(layer['edges'])} 边) --")
                        for n in layer["nodes"][:10]:
                            print(f"    [{n['kind']}] {n['name']}  ({n.get('file','')}:{n.get('startLine','')})")
                        if len(layer["nodes"]) > 10:
                            print(f"    ... 还有 {len(layer['nodes']) - 10} 个")
            elif act == "detail":
                nid = parts[1] if len(parts) > 1 else ""
                info = cr.node_info(nid)
                if not info:
                    print(f"  [NOT FOUND] '{nid}'")
                    continue
                print(f"  [{info['kind']}] {info['name']}")
                print(f"  id:    {info['id']}")
                print(f"  file:  {info.get('file','')}")
                print(f"  lines: {info.get('startLine','')}-{info.get('endLine','')}")
                ds = info.get('definitionSnippet')
                if ds:
                    print(f"  def:   {ds[:200]}")
                ims = info.get('implementationSnippet')
                if ims:
                    print(f"  --- implementationSnippet ({len(ims)} chars) ---")
                    print(ims[:800])
                    if len(ims) > 800:
                        print(f"  ... truncated ({len(ims) - 800} more chars)")
                print(f"  outgoing ({len(info['outgoing'])}):")
                for x in info["outgoing"][:10]:
                    print(f"    --[{x['edge']}]--> {x['target']}")
                if len(info["outgoing"]) > 10:
                    print(f"    ... {len(info['outgoing']) - 10} more")
                print(f"  incoming ({len(info['incoming'])}):")
                for x in info["incoming"][:10]:
                    print(f"    <--[{x['edge']}]-- {x['source']}")
                if len(info["incoming"]) > 10:
                    print(f"    ... {len(info['incoming']) - 10} more")
            elif act == "summary":
                s = cr.summary()
                print("  节点:"); [print(f"    {k}: {v}") for k, v in s["nodes"].items()]
                print("  边:");   [print(f"    {k}: {v}") for k, v in s["edges"].items()]
            else:
                print(f"  未知: {act}  |  可用: search / detail / neigh / expand / summary / quit")
        except Exception as e:
            print(f"  [ERROR] {e}")

    cr.close()


if __name__ == "__main__":
    _cli()
