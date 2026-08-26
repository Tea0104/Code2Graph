from pathlib import Path
import json
import tempfile
import unittest

from code2graph import TargetToSourceCodeAPI, initialize_repository
from test_mapping.index import VectorIndex
from test_mapping.source_function_mapping import load_source_function_mapping


class RepositoryInitializationTest(unittest.TestCase):
    def test_source_only_repository_builds_all_reusable_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "calculator"
            root.mkdir()
            (root / "calculator.py").write_text(
                "def add(left, right):\n"
                "    return left + right\n\n"
                "def subtract(left, right):\n"
                "    return left - right\n",
                encoding="utf-8",
            )
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_calculator.py").write_text(
                "from calculator import add, subtract\n\n"
                "def test_add():\n"
                "    assert add(1, 2) == 3\n"
                "    assert subtract(3, 1) == 2\n",
                encoding="utf-8",
            )

            result = initialize_repository(
                root,
                embedder_kind="hashing",
            )

            self.assertEqual(result.repository_id, "calculator")
            self.assertEqual(result.source_language, "Python")
            self.assertEqual(result.source_function_count, 2)
            self.assertEqual(result.source_test_count, 1)
            self.assertNotIn("target", " ".join(result.artifacts).lower())

            artifact_dir = Path(result.artifact_dir)
            manifest = json.loads(
                (artifact_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(
                manifest["reports"]["source_test_index"]["status"], "built"
            )

            index = VectorIndex.load(artifact_dir / "indexes" / "source_tests")
            self.assertEqual(index.corpus_role, "source_test")
            self.assertEqual(len(index.chunks), 1)

            mappings = load_source_function_mapping(
                artifact_dir / "mappings" / "source_test_to_source_function.jsonl"
            )
            self.assertEqual(len(mappings), 1)
            self.assertEqual(mappings[0].source_functions[0].name, "add")

            translation = json.loads(
                (artifact_dir / "translation" / "translation_order.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(translation["translation_order"], ["calculator.py"])

            api = TargetToSourceCodeAPI.from_artifact_dir(
                artifact_dir, embedder_kind="hashing"
            )
            located = api.locate_source_code(
                target_language="C++",
                target_test_name="Calculator.Adds",
                target_test_code=(
                    "TEST(Calculator, Adds) { EXPECT_EQ(add(1, 2), 3); }"
                ),
            )
            self.assertIsInstance(located, str)
            self.assertIn("def add(left, right):", located)
            self.assertIn("return left + right", located)

            located_many = api.locate_source_code(
                target_language="C++",
                target_test_name="Calculator.AddsAndSubtracts",
                target_test_code=(
                    "TEST(Calculator, AddsAndSubtracts) { "
                    "EXPECT_EQ(add(1, 2), 3); "
                    "EXPECT_EQ(subtract(3, 1), 2); }"
                ),
            )
            self.assertIn("def add(left, right):", located_many)
            self.assertIn("def subtract(left, right):", located_many)
            self.assertIn("\n\n", located_many)


if __name__ == "__main__":
    unittest.main()
