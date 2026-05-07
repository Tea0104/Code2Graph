import csv
from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "Yuan812312"

CSV_FILE = r"D:\codeql_py\python_calls.csv"

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def import_call_graph(tx, caller, callee):
    tx.run("MERGE (a:Function {name: $caller})", caller=caller)
    tx.run("MERGE (b:Function {name: $callee})", callee=callee)
    tx.run("MATCH (a:Function {name: $caller}), (b:Function {name: $callee}) "
           "CREATE (a)-[:CALLS]->(b)", caller=caller, callee=callee)

def main():
    with driver.session() as session:
        with open(CSV_FILE, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)
            count = 0
            for row in reader:
                caller = row[2]
                callee = row[5]
                session.execute_write(import_call_graph, caller, callee)
                count += 1
                print(f"Imported: {caller} -> {callee}")
        print(f"✅ 导入完成，共 {count} 条调用关系")
    driver.close()

if __name__ == "__main__":
    main()
