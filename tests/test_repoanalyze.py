from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from repoanalyze import RepoAnalyze


class RewriteRepoAnalyzeTest(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        (root / "src").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "public_test_summary.json").write_text(
            json.dumps({
                "public_tests": {
                    "test_files": ["tests/test_math_utils.py"],
                },
            }),
            encoding="utf-8",
        )
        (root / "src" / "__init__.py").write_text("", encoding="utf-8")
        (root / "src" / "math_utils.py").write_text(
            "def add(left, right):\n"
            "    return left + right\n\n"
            "def multiply(left, right):\n"
            "    return left * right\n",
            encoding="utf-8",
        )
        (root / "tests" / "test_math_utils.py").write_text(
            "from src.math_utils import add\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n",
            encoding="utf-8",
        )

    def test_full_flow_and_seven_artifacts(self) -> None:
        with TemporaryDirectory() as value:
            root = Path(value) / "demo_repo"
            self.make_repository(root)
            api = RepoAnalyze(embedder_kind="hashing", source_language="Python")

            all_files = api.get_all_translation_files(root)
            self.assertEqual(all_files, ["src/__init__.py", "src/math_utils.py"])

            next_files = api.get_translation_order(
                root,
                number=1,
                already=[root / "src" / "__init__.py"],
            )
            self.assertEqual(next_files, ["src/math_utils.py"])

            source_code = api.target_test_to_source_code(
                root,
                "C++",
                "TEST(Math, Add) { ASSERT_EQ(add(2, 3), 5); }",
            )
            self.assertIn("def add(left, right):", source_code)

            expected = {
                ".code2graph/manifest.json",
                ".code2graph/translation/translation_order.json",
                ".code2graph/chunks/source_functions.jsonl",
                ".code2graph/indexes/source_tests/vectors.npy",
                ".code2graph/indexes/source_tests/chunks.jsonl",
                ".code2graph/indexes/source_tests/manifest.json",
                ".code2graph/mappings/source_test_to_source_function.jsonl",
            }
            actual = {
                path.relative_to(root).as_posix()
                for path in (root / ".code2graph").rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, expected)

            manifest = json.loads(
                (root / ".code2graph" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["source_language"], "Python")

    def test_summary_is_authoritative_and_dirty_tests_are_ignored(self) -> None:
        with TemporaryDirectory() as value:
            root = Path(value) / "demo_repo"
            (root / "src").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "bench").mkdir()
            (root / "public_test_summary.json").write_text(
                json.dumps({
                    "public_tests": {
                        "test_files": ["tests/real_public_test.py"],
                    },
                }),
                encoding="utf-8",
            )
            (root / "src" / "module.py").write_text(
                "def value():\n    return 1\n", encoding="utf-8"
            )
            (root / "tests" / "real_public_test.py").write_text(
                "def test_value():\n    assert True\n", encoding="utf-8"
            )
            (root / "tests" / "test_performance.py").write_text(
                "def test_benchmark():\n    assert True\n", encoding="utf-8"
            )
            (root / "bench" / "run_test.py").write_text(
                "def run_test():\n    return 1\n", encoding="utf-8"
            )

            from initrepo.repository import scan_repository, scan_source_files

            scanned = {
                item["path"].relative_to(root).as_posix(): item
                for item in scan_repository(root, "Python")
            }
            self.assertTrue(scanned["tests/real_public_test.py"]["is_test"])
            self.assertFalse(scanned["tests/real_public_test.py"]["dirty_test"])
            self.assertFalse(scanned["tests/test_performance.py"]["is_test"])
            self.assertTrue(scanned["tests/test_performance.py"]["dirty_test"])

            source_files, test_files = scan_source_files(root, "Python")
            self.assertEqual(
                {path.relative_to(root).as_posix() for path in source_files},
                {"src/module.py"},
            )
            self.assertEqual(
                {path.relative_to(root).as_posix() for path in test_files},
                {"tests/real_public_test.py"},
            )


if __name__ == "__main__":
    unittest.main()
