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


if __name__ == "__main__":
    unittest.main()
