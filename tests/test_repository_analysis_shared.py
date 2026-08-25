from pathlib import Path
import tempfile
import unittest

from repository_analysis import (
    GraphBuilder,
    ImportExtractor,
    build_file_dependency_graph,
    detect_languages,
    normalize_languages,
    scan_repository,
)
from file_topo_sort.topo_sort_files import DependencyExtractor
from tree_sitter_graph.extractor import parser_for


class SharedCodeGraphTest(unittest.TestCase):
    def test_file_ordering_uses_shared_import_extractor(self):
        self.assertIs(DependencyExtractor, ImportExtractor)

    def test_shared_language_registry_is_used_by_full_graph_parser(self):
        self.assertEqual(normalize_languages("py,c++"), ["python", "cpp"])
        self.assertIsNotNone(parser_for("python").language)
        self.assertIsNotNone(parser_for("cpp").language)

    def test_graph_container_is_language_neutral(self):
        graph = GraphBuilder()
        caller = graph.add_node("Function", "caller", "main.py", 1, 2)
        callee = graph.add_node("Function", "callee", "lib.py", 1, 2)
        graph.add_edge("CALLS", caller, callee)
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(next(iter(graph.edges.values())).kind, "CALLS")

    def test_repository_scan_marks_tests_and_detects_languages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "main.py").write_text(
                "def run(): return 1\n", encoding="utf-8"
            )
            (root / "tests" / "test_main.py").write_text(
                "def test_run(): assert True\n", encoding="utf-8"
            )

            files = scan_repository(root, "python")
            self.assertEqual(len(files), 2)
            self.assertEqual(sum(item.is_test for item in files), 1)
            self.assertEqual(detect_languages(root), [("python", 2)])

    def test_shared_file_dependency_graph_scans_extracts_and_resolves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package").mkdir()
            (root / "tests").mkdir()
            (root / "package" / "helper.py").write_text(
                "def helper(): return 1\n", encoding="utf-8"
            )
            (root / "main.py").write_text(
                "from package import helper\n", encoding="utf-8"
            )
            (root / "tests" / "test_main.py").write_text(
                "from main import main\n", encoding="utf-8"
            )

            graph = build_file_dependency_graph(root, "python")

            self.assertEqual(graph.languages, ["python"])
            self.assertNotIn("tests/test_main.py", graph.nodes)
            self.assertIn("package/helper.py", graph.adjacency["main.py"])
            self.assertGreater(
                graph.edge_lines[("main.py", "package/helper.py")], 0
            )

            graph_with_tests = build_file_dependency_graph(
                root, "python", include_tests=True
            )
            self.assertIn("tests/test_main.py", graph_with_tests.nodes)
            self.assertIn(
                "main.py", graph_with_tests.adjacency["tests/test_main.py"]
            )


if __name__ == "__main__":
    unittest.main()
