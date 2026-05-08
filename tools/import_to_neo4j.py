#!/usr/bin/env python3
"""
将 CodeQL 提取的各种关系 CSV 导入 Neo4j 图数据库。
用法:
    python import_to_neo4j.py --csv edges_calls.csv --edge_type CALLS --password xxx
    python import_to_neo4j.py --csv edges_inherits.csv --edge_type INHERITS --password xxx
"""

import argparse
import csv
import os
import sys
from neo4j import GraphDatabase

parser = argparse.ArgumentParser(description="Import CodeQL edges CSV into Neo4j")
parser.add_argument("--csv", required=True, help="Path to the CSV file")
parser.add_argument("--edge_type", required=True, help="Relationship type, e.g. CALLS, INHERITS, DEFINES, REFERENCES, IMPORTS")
parser.add_argument("--password", help="Neo4j password (or set NEO4J_PASSWORD env var)")
args = parser.parse_args()

password = args.password or os.environ.get("NEO4J_PASSWORD")
if not password:
    print("Error: Neo4j password not provided. Use --password or set NEO4J_PASSWORD.")
    sys.exit(1)

# 根据边类型推断节点标签
label_map = {
    "CALLS": "Function",
    "INHERITS": "Class",
    "DEFINES": "Module",
    "REFERENCES": "Variable",
    "IMPORTS": "Module",
}
node_label = label_map.get(args.edge_type, "Function")

print(f"Importing {args.edge_type} edges from {args.csv}, node label: {node_label}")

driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", password))

def import_edge(tx, from_name, to_name, edge_type, label):
    tx.run(f"MERGE (a:{label} {{name: $fn}})", fn=from_name)
    tx.run(f"MERGE (b:{label} {{name: $tn}})", tn=to_name)
    tx.run(
        f"MATCH (a:{label} {{name: $fn}}), (b:{label} {{name: $tn}}) "
        f"CREATE (a)-[:{edge_type}]->(b)",
        fn=from_name, tn=to_name,
    )

def main():
    csv_path = args.csv
    edge_type = args.edge_type
    count = 0
    with driver.session() as session:
        with open(csv_path, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            for row in reader:
                from_name = row[2].strip()
                to_name = row[5].strip()
                if not from_name or not to_name:
                    continue
                session.execute_write(import_edge, from_name, to_name, edge_type, node_label)
                count += 1
                print(f"  [{count}] {from_name} -[{edge_type}]-> {to_name}")
    driver.close()
    print(f"\n✅ Import finished. Total {count} {edge_type} relationships imported.")

if __name__ == "__main__":
    main()
