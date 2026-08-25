from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_mapping import SourceTestMappingAPI
from test_mapping.cli import main as cli_main
from test_mapping.embedding import HashingEmbedder
from test_mapping.index import VectorIndex
from test_mapping.models import FunctionChunk, TestChunk
from test_mapping.test_to_test import SourceTestLocator, structural_similarity


def test_chunk(
    identifier: str,
    name: str,
    code: str,
    *,
    language: str,
    project: str = "demo",
    calls: list[str] | None = None,
    helpers: list[str] | None = None,
) -> TestChunk:
    extension = "py" if language == "Python" else "cpp"
    return TestChunk(
        chunk_id=identifier,
        project=project,
        language=language,
        file=f"public_tests/test_public_math.{extension}",
        name=name,
        qualified_name=name,
        code=code,
        chunk_text=f"Test: {name}\nCode:\n{code}",
        start_line=1,
        end_line=2,
        framework="pytest" if language == "Python" else "TEST",
        calls=calls or [],
        helpers=helpers or [],
    )


def function_chunk(identifier: str, name: str, *, project: str = "demo") -> FunctionChunk:
    return FunctionChunk(
        chunk_id=identifier,
        project=project,
        language="Python",
        file=f"src/{name}.py",
        name=name,
        qualified_name=name,
        code=f"def {name}(): pass",
        start_line=1,
        end_line=1,
    )


class TestToTestMappingTest(unittest.TestCase):
    def test_raw_cpp_code_api_returns_python_source_test_code(self):
        embedder = HashingEmbedder(128)
        source = [
            test_chunk(
                "s1",
                "test_public_add_values",
                "def test_public_add_values():\n    assert add_values(7, 4) == 11",
                language="Python",
                calls=["add_values"],
            ),
            test_chunk(
                "s2",
                "test_render_page",
                "def test_render_page():\n    assert render_page('x')",
                language="Python",
                calls=["render_page"],
            ),
        ]
        api = SourceTestMappingAPI(
            VectorIndex.build(source, embedder, corpus_role="source_test"),
            embedder,
        )

        result = api.locate_source_tests(
            project="demo",
            target_language="cpp",
            target_test_code=(
                "TEST(MathOps, AddValues) {\n"
                "  EXPECT_EQ(add_values(7, 4), 11);\n"
                "}"
            ),
            top_k=2,
        )

        self.assertEqual(result["direction"], "target_test_code_to_source_test_code")
        self.assertEqual(result["target_test"]["name"], "MathOps.AddValues")
        self.assertEqual(result["target_test"]["calls"], ["add_values"])
        self.assertEqual(result["hits"][0]["source_test_id"], "s1")
        self.assertIn("def test_public_add_values", result["hits"][0]["source_test_code"])
        self.assertNotIn("source_functions", result["hits"][0])

    def test_raw_python_code_api_returns_cpp_source_test_code(self):
        embedder = HashingEmbedder(128)
        source = [
            test_chunk(
                "s1",
                "MultiplyValues",
                "TEST(MathOps, MultiplyValues) { EXPECT_EQ(multiply_values(3, 5), 15); }",
                language="C++",
                calls=["multiply_values"],
            ),
            test_chunk(
                "s2",
                "RenderPage",
                "TEST(View, RenderPage) { EXPECT_TRUE(render_page(\"x\")); }",
                language="C++",
                calls=["render_page"],
            ),
        ]
        api = SourceTestMappingAPI(
            VectorIndex.build(source, embedder, corpus_role="source_test"),
            embedder,
        )

        result = api.locate_source_tests(
            project="demo",
            target_language="python",
            target_test_code=(
                "def test_multiply_values():\n"
                "    assert multiply_values(3, 5) == 15\n"
            ),
            strategy="fusion",
            top_k=2,
        )

        self.assertEqual(result["target_test"]["name"], "test_multiply_values")
        self.assertEqual(result["hits"][0]["source_test_id"], "s1")
        self.assertIn("TEST(MathOps, MultiplyValues)", result["hits"][0]["source_test_code"])

    def test_raw_code_api_rejects_unknown_project_and_empty_code(self):
        embedder = HashingEmbedder(64)
        source = [test_chunk("s1", "sample", "assert True", language="Python")]
        api = SourceTestMappingAPI(
            VectorIndex.build(source, embedder, corpus_role="source_test"),
            embedder,
        )

        with self.assertRaisesRegex(ValueError, "Project is not present"):
            api.locate_source_tests(
                project="missing",
                target_language="C++",
                target_test_code="TEST(Demo, Sample) { SUCCEED(); }",
            )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            api.locate_source_tests(
                project="demo",
                target_language="Python",
                target_test_code="   ",
            )

    def test_raw_code_api_rejects_mismatched_embedder_at_startup(self):
        index_embedder = HashingEmbedder(64)
        query_embedder = HashingEmbedder(128)
        source = [test_chunk("s1", "sample", "assert True", language="Python")]
        index = VectorIndex.build(
            source,
            index_embedder,
            corpus_role="source_test",
        )

        with self.assertRaisesRegex(ValueError, "Index uses hashing-64"):
            SourceTestMappingAPI(index, query_embedder)

    def test_raw_code_api_result_is_json_serializable(self):
        embedder = HashingEmbedder(64)
        source = [
            test_chunk(
                "s1",
                "test_public_add_values",
                "def test_public_add_values():\n    assert add_values(1, 2) == 3",
                language="Python",
                calls=["add_values"],
            )
        ]
        api = SourceTestMappingAPI(
            VectorIndex.build(source, embedder, corpus_role="source_test"),
            embedder,
        )

        result = api.locate_source_tests(
            project="demo",
            target_language="C++",
            target_test_code=(
                "TEST(MathOps, AddValues) { EXPECT_EQ(add_values(1, 2), 3); }"
            ),
            top_k=1,
        )

        encoded = json.dumps(result)
        self.assertEqual(json.loads(encoded)["hits"][0]["source_test_id"], "s1")

    def test_raw_code_api_loads_persistent_index_once(self):
        with tempfile.TemporaryDirectory() as directory:
            embedder = HashingEmbedder()
            source = [
                test_chunk(
                    "s1",
                    "test_public_add_values",
                    "def test_public_add_values():\n    assert add_values(2, 3) == 5",
                    language="Python",
                    calls=["add_values"],
                )
            ]
            index_dir = Path(directory) / "source-test-index"
            VectorIndex.build(
                source,
                embedder,
                corpus_role="source_test",
            ).save(index_dir)

            api = SourceTestMappingAPI.from_index(
                index_dir,
                embedder_kind="hashing",
            )
            result = api.locate_source_tests(
                project="demo",
                target_language="C++",
                target_test_code=(
                    "TEST(MathOps, AddValues) { "
                    "EXPECT_EQ(add_values(2, 3), 5); }"
                ),
                top_k=1,
            )

            self.assertEqual(result["hits"][0]["source_test_id"], "s1")

    def test_structure_and_fusion_rank_corresponding_source_test(self):
        embedder = HashingEmbedder(128)
        source = [
            test_chunk(
                "s1",
                "test_public_add_values",
                "assert add_values(7, 4) == 11",
                language="Python",
                calls=["add_values"],
            ),
            test_chunk(
                "s2",
                "test_render_page",
                "assert render_page('x')",
                language="Python",
                calls=["render_page"],
            ),
        ]
        target = test_chunk(
            "t1",
            "AddValues",
            "EXPECT_EQ(add_values(7, 4), 11)",
            language="C++",
            calls=["add_values"],
        )
        index = VectorIndex.build(source, embedder, corpus_role="source_test")
        locator = SourceTestLocator(index, embedder)
        for strategy in ("structure", "fusion"):
            result = locator.locate(target, strategy=strategy, k=2)
            self.assertEqual(result.hits[0].source_test.chunk_id, "s1")
            self.assertEqual(len({hit.source_test.chunk_id for hit in result.hits}), 2)

    def test_name_masking_removes_name_and_file_scores(self):
        source = test_chunk(
            "s1", "test_public_add_values", "assert add_values(1, 2)",
            language="Python", calls=["add_values"],
        )
        target = test_chunk(
            "t1", "AddValues", "EXPECT_TRUE(add_values(1, 2))",
            language="C++", calls=["add_values"],
        )
        _, normal = structural_similarity(target, source)
        _, masked = structural_similarity(target, source, mask_names=True)
        self.assertGreater(normal["name"], 0.0)
        self.assertGreater(normal["file"], 0.0)
        self.assertEqual(masked["name"], 0.0)
        self.assertEqual(masked["file"], 0.0)
        self.assertEqual(masked["calls"], 1.0)

    def test_static_expansion_returns_unique_and_ambiguous_calls(self):
        source_test = test_chunk(
            "s1",
            "test_public_add_values",
            "assert add_values(1, 2)",
            language="Python",
            calls=["add_values", "helper", "missing"],
        )
        functions = [
            function_chunk("f1", "add_values"),
            function_chunk("f2", "helper"),
            function_chunk("f3", "helper"),
        ]
        expansion = SourceTestLocator.expand_functions(source_test, functions)
        self.assertEqual(
            {link.function.chunk_id for link in expansion.links},
            {"f1", "f2", "f3"},
        )
        self.assertEqual(expansion.unmatched_calls, ["missing"])
        self.assertEqual(expansion.ambiguous_calls["helper"], ["f2", "f3"])

    def test_static_expansion_links_python_constructor(self):
        source_test = test_chunk(
            "s1",
            "test_public_client",
            "client = Client()",
            language="Python",
            calls=["Client"],
        )
        constructor = FunctionChunk(
            chunk_id="f1",
            project="demo",
            language="Python",
            file="src/client.py",
            name="__init__",
            qualified_name="Client.__init__",
            code="def __init__(self): pass",
            start_line=2,
            end_line=2,
            parent="Client",
        )

        expansion = SourceTestLocator.expand_functions(source_test, [constructor])

        self.assertEqual([link.function.chunk_id for link in expansion.links], ["f1"])
        self.assertEqual(expansion.links[0].confidence, "unique_constructor")

    def test_static_expansion_follows_one_helper_layer(self):
        source_test = test_chunk(
            "s1",
            "test_public_client",
            "assert client_fixture.ready",
            language="Python",
            helpers=["def client_fixture():\n    return make_client()"],
        )
        function = function_chunk("f1", "make_client")

        expansion = SourceTestLocator.expand_functions(source_test, [function])

        self.assertEqual([link.function.chunk_id for link in expansion.links], ["f1"])

    def test_rejects_target_test_index_role(self):
        embedder = HashingEmbedder(64)
        chunk = test_chunk("s1", "sample", "assert True", language="Python")
        index = VectorIndex.build([chunk], embedder, corpus_role="target_test")
        with self.assertRaisesRegex(ValueError, "Source-test index"):
            SourceTestLocator(index, embedder)

    def test_cli_build_locate_and_evaluate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair = root / "Python_to_C++"
            source = pair / "source_projects" / "demo"
            target = pair / "target_projects" / "demo"
            (source / "public_tests").mkdir(parents=True)
            (target / "public_tests").mkdir(parents=True)
            (source / "math_ops.py").write_text(
                "def add_values(a, b):\n    return a + b\n",
                encoding="utf-8",
            )
            (source / "public_tests" / "test_public_math.py").write_text(
                "from math_ops import add_values\n\n"
                "def test_public_add_values():\n"
                "    assert add_values(1, 2) == 3\n",
                encoding="utf-8",
            )
            (target / "public_tests" / "test_public_math.cpp").write_text(
                "#include <gtest/gtest.h>\n"
                "TEST(MathOps, AddValues) {\n"
                "  EXPECT_EQ(add_values(1, 2), 3);\n"
                "}\n",
                encoding="utf-8",
            )
            source_id = (
                "demo:Python:public_tests/test_public_math.py:"
                "test_public_add_values:3"
            )
            target_id = (
                "demo:C++:public_tests/test_public_math.cpp:MathOps.AddValues:2"
            )
            ground_truth = root / "ground-truth.jsonl"
            ground_truth.write_text(json.dumps({
                "schema_version": 1,
                "pair": "Python_to_C++",
                "project": "demo",
                "target_test_id": target_id,
                "target_test_file": "public_tests/test_public_math.cpp",
                "target_test_name": "MathOps.AddValues",
                "source_test_ids": [source_id],
                "status": "matched",
                "annotation_method": "unit_test",
                "confidence": "verified",
                "evidence": {},
                "candidates": [],
                "reviewed": True,
                "review_notes": "",
            }) + "\n", encoding="utf-8")
            index_dir = root / "source-test-index"
            locate_output = root / "location.json"
            evaluation_dir = root / "evaluation"
            common = [
                "--dataset-root", str(root),
                "--pair", "Python_to_C++",
                "--embedder", "hashing",
            ]
            with patch("builtins.print"):
                self.assertEqual(cli_main([
                    "build-source-test-index", *common,
                    "--output-dir", str(index_dir),
                ]), 0)
                self.assertEqual(cli_main([
                    "locate-source-test", *common,
                    "--index-dir", str(index_dir),
                    "--project", "demo",
                    "--target-test", "AddValues",
                    "--strategy", "fusion",
                    "--output", str(locate_output),
                ]), 0)
                self.assertEqual(cli_main([
                    "evaluate-source-test", *common,
                    "--index-dir", str(index_dir),
                    "--ground-truth", str(ground_truth),
                    "--strategy", "fusion",
                    "--output-dir", str(evaluation_dir),
                ]), 0)

            location = json.loads(locate_output.read_text(encoding="utf-8"))
            self.assertEqual(
                location["direction"],
                "target_test_to_source_test_to_source_function",
            )
            self.assertEqual(location["hits"][0]["chunk_id"], source_id)
            linked = location["hits"][0]["source_functions"]["links"]
            self.assertEqual(linked[0]["function"]["name"], "add_values")
            metrics = json.loads(
                (evaluation_dir / "metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metrics["matched_query_count"], 1)
            self.assertEqual(metrics["hit_rate_at_1"], 1.0)


if __name__ == "__main__":
    unittest.main()
