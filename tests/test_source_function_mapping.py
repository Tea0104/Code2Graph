from pathlib import Path
import tempfile
import unittest

from test_mapping.models import FunctionChunk, TestChunk
from test_mapping.source_function_gold import SourceFunctionGoldRecord, is_business_function
from test_mapping.source_function_mapping import (
    SourceFunctionMappingAPI,
    _looks_like_test_file,
    evaluate_source_function_mapping,
    load_source_function_mapping,
    lookup_best_mapped_source_function,
    lookup_mapped_source_functions,
    lookup_source_function_mapping_record,
    lookup_source_function_mapping_result,
    query_source_function_mapping,
    resolve_source_function_mapping,
    write_source_function_mapping,
)


def source_test(identifier: str, code: str, *, name: str = "Demo.Case") -> TestChunk:
    return TestChunk(
        chunk_id=identifier,
        project="demo",
        language="C++",
        file="public_tests/demo_public_test.cpp",
        name=name.rsplit(".", 1)[-1],
        qualified_name=name,
        code=code,
        chunk_text=code,
        start_line=1,
        end_line=max(1, code.count("\n") + 1),
        framework="TEST",
        parent=name.split(".", 1)[0],
        calls=[],
    )


def function_chunk(identifier: str, name: str) -> FunctionChunk:
    return FunctionChunk(
        chunk_id=identifier,
        project="demo",
        language="C++",
        file="src/demo.cpp",
        name=name,
        qualified_name=name,
        code=f"int {name}(int a, int b) {{ return a + b; }}",
        start_line=1,
        end_line=3,
    )


def gold_record(identifier: str, *, status: str, expected: list[str]) -> SourceFunctionGoldRecord:
    return SourceFunctionGoldRecord(
        schema_version=1,
        pair="C++_to_Python",
        project="demo",
        source_test_id=identifier,
        source_test_file="public_tests/demo_public_test.cpp",
        source_test_name="Demo.Case",
        status=status,
        expected_function_ids=expected,
        confidence="high" if expected else "none",
    )


class SourceFunctionMappingTest(unittest.TestCase):
    def test_all_scope_test_file_heuristic_covers_public_and_internal_tests(self):
        self.assertTrue(_looks_like_test_file(Path("test/demo_test.cpp")))
        self.assertTrue(_looks_like_test_file(Path("test_public.cc")))
        self.assertTrue(_looks_like_test_file(Path("src/foo_internal_test.cxx")))
        self.assertFalse(_looks_like_test_file(Path("src/demo.cpp")))
        self.assertFalse(_looks_like_test_file(Path("3rdparty/googletest/src/gtest.cc")))

    def test_business_function_filter_excludes_vendor_and_gtest_framework_code(self):
        vendor = FunctionChunk(
            chunk_id="f_vendor",
            project="demo",
            language="C++",
            file="3rdparty/googletest/include/gtest/gtest.h",
            name="GetParam",
            qualified_name="WithParamInterface.GetParam",
            code="int GetParam();",
            start_line=1,
            end_line=1,
        )

        self.assertFalse(is_business_function(vendor))

    def test_verified_static_mapping_keeps_directly_asserted_business_function(self):
        test = source_test("t_add", "TEST(Demo, Case) { EXPECT_EQ(add(1, 2), 3); }")
        add = function_chunk("f_add", "add")

        record = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[add],
            method="verified_static",
        )

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.resolver_method, "verified_static")
        self.assertEqual(record.source_functions[0].chunk_id, "f_add")
        self.assertEqual(
            record.source_functions[0].verification_reason,
            "direct_call_inside_assertion",
        )

    def test_verified_static_with_medium_expands_one_hop_test_helper(self):
        test = source_test("t_add", "TEST(Demo, Case) { expect_sum(); }")
        test.helpers = ["void expect_sum() { EXPECT_EQ(add(1, 2), 3); }"]
        add = function_chunk("f_add", "add")

        strict = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[add],
            method="verified_static",
        )
        expanded = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[add],
            method="verified_static_with_medium",
        )

        self.assertEqual(strict.status, "no_match")
        self.assertEqual(expanded.status, "matched")
        self.assertEqual(expanded.source_functions[0].chunk_id, "f_add")
        self.assertEqual(expanded.source_functions[0].confidence, "medium")
        self.assertEqual(
            expanded.source_functions[0].verification_reason,
            "direct_call_inside_assertion_via_helper",
        )

    def test_verified_static_with_medium_excludes_weak_low_confidence_candidates(self):
        test = source_test(
            "t_weak",
            "TEST(Demo, Case) { add(1, 2); subtract(3, 1); EXPECT_TRUE(true); }",
        )
        add = function_chunk("f_add", "add")
        subtract = function_chunk("f_subtract", "subtract")

        record = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[add, subtract],
            method="verified_static_with_medium",
        )

        self.assertEqual(record.status, "no_match")
        self.assertEqual(
            record.diagnostics["no_function_reason"],
            "business_calls_not_directly_verified",
        )
        self.assertEqual(
            record.diagnostics["no_function_type"],
            "business_call_without_strong_verification_signal",
        )

    def test_verified_static_with_low_keeps_weak_candidates_low_confidence(self):
        test = source_test(
            "t_weak",
            "TEST(Demo, Case) { add(1, 2); subtract(3, 1); EXPECT_TRUE(true); }",
        )
        add = function_chunk("f_add", "add")
        subtract = function_chunk("f_subtract", "subtract")

        record = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[add, subtract],
            method="verified_static_with_low",
        )

        self.assertEqual(record.status, "matched")
        self.assertEqual([hit.confidence for hit in record.source_functions], ["low", "low"])
        self.assertEqual(
            {hit.verification_reason for hit in record.source_functions},
            {"business_call_in_asserting_test_unverified"},
        )

    def test_recall_static_returns_low_candidate_instead_of_no_match(self):
        test = source_test("t_weak", "TEST(Demo, Case) { add(1, 2); }")
        add = function_chunk("f_add", "add")

        record = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[add],
            method="recall_static",
        )

        self.assertEqual(record.status, "candidate")
        self.assertEqual(record.source_functions[0].chunk_id, "f_add")
        self.assertEqual(record.source_functions[0].confidence, "low")

    def test_recall_static_fallback_matches_unresolved_call_by_name(self):
        test = source_test("t_display", "TEST(Demo, Case) { display(); }")
        display = function_chunk("f_display", "display")
        display.qualified_name = "DemoDisplay.display"
        duplicate = function_chunk("f_other_display", "display")
        duplicate.file = "examples/display.cpp"
        duplicate.qualified_name = "OtherDisplay.display"

        record = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[duplicate, display],
            method="recall_static",
        )

        self.assertEqual(record.status, "candidate")
        self.assertEqual(record.source_functions[0].chunk_id, "f_display")
        self.assertEqual(record.source_functions[0].resolution_reason, "recall_name_fallback")

    def test_recall_static_marks_empty_result_unresolved(self):
        test = source_test("t_unknown", "TEST(Demo, Case) { missing_api(); EXPECT_TRUE(true); }")

        record = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[],
            method="recall_static",
        )

        self.assertEqual(record.status, "unresolved")
        self.assertEqual(record.source_functions, [])
        self.assertEqual(
            record.diagnostics["unresolved_reason"],
            "no_source_business_functions_available",
        )
        self.assertEqual(
            record.diagnostics["no_function_type"],
            "source_function_parse_or_header_only_gap",
        )

    def test_recall_static_last_resort_returns_project_candidate(self):
        test = source_test("t_empty", "TEST(CachePublic, EvictionPolicy) { run_eviction_case(); EXPECT_TRUE(true); }")
        cache = function_chunk("f_cache", "evict")
        cache.file = "src/cache/eviction_policy.cpp"
        cache.qualified_name = "Cache.eviction_policy"

        record = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[cache],
            method="recall_static",
        )

        self.assertEqual(record.status, "candidate")
        self.assertEqual(record.source_functions[0].chunk_id, "f_cache")
        self.assertEqual(record.source_functions[0].resolution_reason, "recall_project_fallback")
        self.assertEqual(record.source_functions[0].confidence, "low")

    def test_recall_static_does_not_invent_function_for_assertion_only_test(self):
        test = source_test("t_assertion_only", "TEST(CachePublic, EvictionPolicy) { EXPECT_TRUE(true); }")
        cache = function_chunk("f_cache", "evict")
        cache.file = "src/cache/eviction_policy.cpp"
        cache.qualified_name = "Cache.eviction_policy"

        record = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[cache],
            method="recall_static",
        )

        self.assertEqual(record.status, "unresolved")
        self.assertEqual(record.source_functions, [])
        self.assertEqual(
            record.diagnostics["unresolved_reason"],
            "no_business_call_detected_after_filter",
        )
        self.assertEqual(record.diagnostics["test_kind"], "assertion_only_or_compile_time_test")

    def test_mapping_jsonl_can_be_queried_by_nodeid_and_api(self):
        test = source_test("t_add", "TEST(Demo, Case) { EXPECT_EQ(add(1, 2), 3); }")
        record = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[function_chunk("f_add", "add")],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.jsonl"
            write_source_function_mapping([record], path)
            loaded = load_source_function_mapping(path)

            matches = query_source_function_mapping(
                loaded,
                "public_tests/demo_public_test.cpp::Demo.Case",
            )
            api = SourceFunctionMappingAPI.from_jsonl(path)
            api_result = api.lookup("t_add")
            api_functions = api.lookup_functions("t_add")
            api_best = api.lookup_best_function("t_add")
            function_result = lookup_mapped_source_functions("t_add", mapping=path)
            best_function_result = lookup_best_mapped_source_function("t_add", mapping=path)
            record_result = lookup_source_function_mapping_record("t_add", mapping=path)
            result_payload = lookup_source_function_mapping_result("t_add", mapping=path)
            api_result_payload = api.lookup_result("t_add")

        self.assertEqual(len(matches), 1)
        self.assertEqual(api_result["source_functions"][0]["chunk_id"], "f_add")
        self.assertEqual(api_functions[0]["chunk_id"], "f_add")
        self.assertEqual(api_best["chunk_id"], "f_add")
        self.assertEqual(function_result[0]["chunk_id"], "f_add")
        self.assertEqual(best_function_result["chunk_id"], "f_add")
        self.assertEqual(record_result["source_test_id"], "t_add")
        self.assertTrue(result_payload["has_function"])
        self.assertIsNone(result_payload["no_function"])
        self.assertEqual(api_result_payload["source_functions"][0]["chunk_id"], "f_add")

    def test_lookup_result_explains_no_function_rows(self):
        test = source_test("t_assertion_only", "TEST(Demo, Case) { EXPECT_TRUE(true); }")
        record = resolve_source_function_mapping(
            pair="C++_to_Python",
            test=test,
            functions=[function_chunk("f_add", "add")],
            method="verified_static_with_medium",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.jsonl"
            write_source_function_mapping([record], path)
            result = lookup_source_function_mapping_result("t_assertion_only", mapping=path)

        self.assertFalse(result["has_function"])
        self.assertEqual(result["source_functions"], [])
        self.assertEqual(result["no_function"]["reason"], "no_business_call_detected_after_filter")
        self.assertEqual(result["no_function"]["type"], "assertion_only_or_compile_time_test")

    def test_evaluate_mapping_counts_matched_hit_and_no_match_empty(self):
        matched_test = source_test("t_add", "TEST(Demo, Case) { EXPECT_EQ(add(1, 2), 3); }")
        no_match_test = source_test("t_empty", "TEST(Demo, Empty) { EXPECT_TRUE(true); }", name="Demo.Empty")
        mapping_records = [
            resolve_source_function_mapping(
                pair="C++_to_Python",
                test=matched_test,
                functions=[function_chunk("f_add", "add")],
            ),
            resolve_source_function_mapping(
                pair="C++_to_Python",
                test=no_match_test,
                functions=[function_chunk("f_add", "add")],
            ),
        ]
        summary, rows = evaluate_source_function_mapping(
            mapping_records=mapping_records,
            gold_records=[
                gold_record("t_add", status="matched", expected=["f_add"]),
                gold_record("t_empty", status="no_match", expected=[]),
            ],
            mapping_path="mapping.jsonl",
            gold_path="gold.jsonl",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(summary.hit_rate_at_1, 1.0)
        self.assertEqual(summary.no_match_correct_count, 1)
        self.assertEqual(summary.overall_accuracy_at_1_including_no_match, 1.0)


if __name__ == "__main__":
    unittest.main()
