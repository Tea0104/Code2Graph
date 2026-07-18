from pathlib import Path
import tempfile
import unittest

import numpy as np

from test_mapping.alignment import align_tests, normalized_name
from test_mapping.embedding import HashingEmbedder
from test_mapping.evaluation import build_function_gold
from test_mapping.index import VectorIndex
from test_mapping.models import Alignment, FunctionChunk, TestChunk
from test_mapping.pipeline import TestLocator
from test_mapping.query import build_query


def test_chunk(identifier: str, name: str, code: str, *, language: str = "Python") -> TestChunk:
    return TestChunk(
        chunk_id=identifier,
        project="demo",
        language=language,
        file=f"public_tests/test_demo.{ 'py' if language == 'Python' else 'cpp' }",
        name=name,
        qualified_name=name,
        code=code,
        chunk_text=f"Test: {name}\nCode:\n{code}",
        start_line=1,
        end_line=2,
        framework="pytest" if language == "Python" else "TEST",
        calls=["add"] if "add" in code else [],
    )


class CoreTest(unittest.TestCase):
    def test_normalized_cross_language_alignment(self):
        source = test_chunk("s1", "test_public_add_numbers", "assert add(1, 2) == 3")
        target = test_chunk("t1", "AddNumbers", "EXPECT_EQ(add(1, 2), 3)", language="C++")
        alignment = align_tests([source], [target])[0]
        self.assertEqual(normalized_name(source.name), normalized_name(target.name))
        self.assertEqual(alignment.confidence, "high")
        self.assertEqual(alignment.target_chunk_ids, ("t1",))

    def test_fuzzy_alignment_is_low_confidence_only(self):
        source = test_chunk("s1", "test_calculate_total_value", "assert total()")
        target = test_chunk("t1", "CalculateTotalValues", "EXPECT_TRUE(total())", language="C++")
        target.file = "public_tests/test_other.cpp"
        alignment = align_tests([source], [target], expanded=True)[0]
        self.assertEqual(alignment.method, "fuzzy_test_name_candidate")
        self.assertEqual(alignment.confidence, "low")

    def test_query_uses_called_source_function(self):
        source = test_chunk("s1", "test_add", "assert add(1, 2) == 3")
        function = FunctionChunk("f1", "demo", "Python", "add.py", "add", "add", "def add(a, b): return a + b", 1, 1)
        query = build_query(source, [function], "function_test")
        self.assertIsNotNone(query)
        self.assertIn("def add", query.text)
        self.assertEqual(query.source_function_ids, ("f1",))

    def test_function_gold_uses_only_unique_call_targets(self):
        source = test_chunk("s1", "test_add", "assert add(1, 2) == 3")
        target_alignment = Alignment("s1", ("t1",), "normalized_test_name", "high")
        unique = FunctionChunk("f1", "demo", "Python", "add.py", "add", "add", "def add(a, b): return a + b", 1, 1)
        self.assertEqual(build_function_gold([source], [unique], [target_alignment]), {"f1": {"t1"}})

        duplicate = FunctionChunk("f2", "demo", "Python", "other.py", "add", "add", "def add(a, b): return 0", 1, 1)
        self.assertEqual(build_function_gold([source], [unique, duplicate], [target_alignment]), {})

    def test_function_gold_ignores_non_strict_test_alignment(self):
        source = test_chunk("s1", "test_add", "assert add(1, 2) == 3")
        function = FunctionChunk("f1", "demo", "Python", "add.py", "add", "add", "def add(a, b): return a + b", 1, 1)
        medium = Alignment("s1", ("t1",), "same_file_equal_count_order", "medium")
        self.assertEqual(build_function_gold([source], [function], [medium]), {})

    def test_index_round_trip_and_project_filtered_search(self):
        embedder = HashingEmbedder(64)
        chunks = [
            test_chunk("t1", "add", "add numbers", language="C++"),
            test_chunk("t2", "remove", "remove value", language="C++"),
        ]
        index = VectorIndex.build(chunks, embedder)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            index.save(path)
            loaded = VectorIndex.load(path)
            hits = loaded.search("add numbers", embedder, k=1, project="demo")
            self.assertEqual(hits[0].chunk_id, "t1")
            np.testing.assert_allclose(index.vectors, loaded.vectors)

    def test_adaptive_falls_back_without_function_context(self):
        source = test_chunk("s1", "test_add", "assert add(1, 2) == 3")
        target = test_chunk("t1", "Add", "EXPECT_EQ(add(1, 2), 3)", language="C++")
        embedder = HashingEmbedder(64)
        locator = TestLocator(VectorIndex.build([target], embedder), embedder, confidence_threshold=2.0)
        result = locator.locate(source, [], strategy="adaptive", k=1)
        self.assertEqual(result.used_strategies, ["test"])
        self.assertEqual(result.hits[0].chunk_id, "t1")

    def test_fusion_returns_each_chunk_once(self):
        source = test_chunk("s1", "test_add", "assert add(1, 2) == 3")
        function = FunctionChunk("f1", "demo", "Python", "add.py", "add", "add", "def add(a, b): return a + b", 1, 1)
        target = test_chunk("t1", "Add", "EXPECT_EQ(add(1, 2), 3)", language="C++")
        embedder = HashingEmbedder(64)
        result = TestLocator(VectorIndex.build([target], embedder), embedder).locate(source, [function], strategy="fusion")
        self.assertEqual([hit.chunk_id for hit in result.hits], ["t1"])
        self.assertEqual(result.used_strategies, ["function_test", "test", "function"])

    def test_function_fusion_uses_matching_source_tests(self):
        source = test_chunk("s1", "test_add", "assert add(1, 2) == 3")
        function = FunctionChunk("f1", "demo", "Python", "add.py", "add", "add", "def add(a, b): return a + b", 1, 1)
        targets = [
            test_chunk("t1", "AddSmall", "EXPECT_EQ(add(1, 2), 3)", language="C++"),
            test_chunk("t2", "Other", "EXPECT_TRUE(other())", language="C++"),
        ]
        embedder = HashingEmbedder(64)
        locator = TestLocator(VectorIndex.build(targets, embedder), embedder)
        result = locator.locate_function_with_tests(function, [source], strategy="fusion", k=2)
        self.assertEqual(result.used_strategies, ["function_test", "test", "function"])
        self.assertEqual(result.diagnostics["source_test_count"], 1)
        self.assertEqual(result.hits[0].chunk_id, "t1")

    def test_function_adaptive_falls_back_without_source_test(self):
        function = FunctionChunk("f1", "demo", "Python", "add.py", "add", "add", "def add(a, b): return a + b", 1, 1)
        target = test_chunk("t1", "Add", "EXPECT_EQ(add(1, 2), 3)", language="C++")
        embedder = HashingEmbedder(64)
        locator = TestLocator(VectorIndex.build([target], embedder), embedder)
        result = locator.locate_function_with_tests(function, [], strategy="adaptive", k=1)
        self.assertEqual(result.used_strategies, ["function"])
        self.assertEqual(result.hits[0].chunk_id, "t1")


if __name__ == "__main__":
    unittest.main()
