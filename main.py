import subprocess
from pathlib import Path
import argparse
import json
from csv2graph import load_csv,nodes,edges,create_nodes,create_edges

def create_database(database_dir:Path,source_root:Path):
    database_dir.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "codeql",
        "database",
        "create",
        str(database_dir),
        "--language=python",
        "--source-root",
        str(source_root),
    ]
    subprocess.run(command, check=True)

#运行codeql查询并返回结果csv文件路径
def export_query_results(database_dir: Path, query_dir: Path, results_dir: Path) -> list[Path]:
    query_files = sorted(query_dir.rglob("*.ql"))
    if not query_files:
        raise FileNotFoundError(f"No .ql files found under {query_dir}")

    exported_csv_files: list[Path] = []
    for query_file in query_files:
        query_output_dir = results_dir / query_file.stem
        query_output_dir.mkdir(parents=True, exist_ok=True)

        bqrs_path = query_output_dir / f"{query_file.stem}.bqrs"
        csv_path = query_output_dir / f"{query_file.stem}.csv"

        subprocess.run([
            "codeql",
            "query",
            "run",
            "--database",
            str(database_dir),
            "--output",
            str(bqrs_path),
            str(query_file),
        ], check=True)
        subprocess.run([
            "codeql",
            "bqrs",
            "decode",
            "--format=csv",
            "--output",
            str(csv_path),
            str(bqrs_path),
        ], check=True)
        exported_csv_files.append(csv_path)

    return exported_csv_files


def build_graph_from_csv_files(exported_csv_files: list[Path]) -> None:
    for csv_file in exported_csv_files:
        load_csv(csv_file)

    create_nodes()
    create_edges()

    json_dir = Path("./build/json")
    json_dir.mkdir(parents=True, exist_ok=True)

    with (json_dir / "nodes.json").open("w", encoding="utf-8") as f:
        json.dump(nodes, f, ensure_ascii=False, indent=2)
    with (json_dir / "edges.json").open("w", encoding="utf-8") as f:
        json.dump(edges, f, ensure_ascii=False, indent=2)



#创建一条命令
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build CodeQL database, export query CSVs, and generate a graph")
    parser.add_argument("command", nargs="?", choices=["build", "query"], default="build", help="build creates a database first; query reuses an existing database")
    parser.add_argument("--source-root", help="Path to the Python repository to analyze")
    parser.add_argument("--database", default="codeql_db", help="CodeQL database directory")
    parser.add_argument("--results", default="codeql_results", help="Directory for exported query results")
    parser.add_argument("--query-dir", default="codeql_queries/python", help="Directory containing .ql files")
    return parser


def main():
    args = build_parser().parse_args()

    database_dir = Path(args.database).resolve()
    results_dir = Path(args.results).resolve()
    query_dir = Path(args.query_dir).resolve()

    if args.command == "build":
        if not args.source_root:
            raise ValueError("--source-root is required in build mode")
        source_root = Path(args.source_root).resolve()
        create_database(database_dir, source_root)

    exported_csv_files = export_query_results(database_dir, query_dir, results_dir)
    build_graph_from_csv_files(exported_csv_files)


if __name__ =="__main__":
    main()
