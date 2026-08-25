import unittest

from test_mapping.models import FunctionChunk, TestChunk
from test_mapping.source_function_gold import (
    is_business_function,
    evaluable_expected_function_ids,
    propose_source_function_gold,
    verification_reason,
)


def source_test(identifier: str, code: str, *, language: str = "C++", calls=None) -> TestChunk:
    return TestChunk(
        chunk_id=identifier,
        project="demo",
        language=language,
        file="tests/demo_test.cpp" if language == "C++" else "public_tests/test_demo.py",
        name="Case",
        qualified_name="Demo.Case" if language == "C++" else "test_case",
        code=code,
        chunk_text=code,
        start_line=1,
        end_line=max(1, code.count("\n") + 1),
        framework="TEST" if language == "C++" else "pytest",
        calls=calls or [],
    )


def function_chunk(identifier: str, name: str, *, language: str = "C++") -> FunctionChunk:
    return FunctionChunk(
        chunk_id=identifier,
        project="demo",
        language=language,
        file="src/demo.cpp" if language == "C++" else "src/demo.py",
        name=name,
        qualified_name=name,
        code="",
        start_line=1,
        end_line=5,
    )


class SourceFunctionGoldTest(unittest.TestCase):
    def test_direct_cpp_assertion_becomes_matched_gold(self):
        test = source_test("t1", "TEST(Demo, Case) { EXPECT_EQ(add(1, 2), 3); }", calls=["add"])
        add = function_chunk("f_add", "add")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[add])

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.expected_function_ids, ["f_add"])
        self.assertEqual(record.expected_functions[0].verification_reason, "direct_call_inside_assertion")

    def test_assignment_then_assertion_becomes_matched_gold(self):
        test = source_test(
            "t1",
            "TEST(Demo, Case) {\n  int result = add(1, 2);\n  EXPECT_EQ(result, 3);\n}",
            calls=["add"],
        )
        add = function_chunk("f_add", "add")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[add])

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.expected_functions[0].verification_reason, "call_result_assigned_then_asserted")

    def test_output_argument_then_assertion_becomes_matched_gold(self):
        test = source_test(
            "t1",
            'TEST(Demo, Case) {\n  char out[10];\n  base64_encode(out, "xy", 2);\n  EXPECT_STREQ(out, "eHk=");\n}',
            calls=["base64_encode"],
        )
        encode = function_chunk("f_encode", "base64_encode")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[encode])

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.expected_functions[0].verification_reason, "call_effect_or_output_asserted")

    def test_output_argument_derived_variable_then_assertion_becomes_matched_gold(self):
        test = source_test(
            "t1",
            "TEST(Demo, Case) {\n"
            "  char array[27];\n"
            "  MarshalTo(id, array);\n"
            "  std::string str(array);\n"
            "  EXPECT_EQ(str.length(), 26);\n"
            "}",
            calls=["MarshalTo"],
        )
        marshal = function_chunk("f_marshal", "MarshalTo")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[marshal])

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.expected_functions[0].verification_reason, "call_effect_or_output_asserted")

    def test_pointer_result_then_assertion_becomes_matched_gold(self):
        test = source_test(
            "t1",
            "TEST(Demo, Case) {\n"
            "  astTU *tu = parse(astTU::kGlobal);\n"
            "  ASSERT_NE(tu, nullptr);\n"
            "}",
            calls=["parse"],
        )
        parse = function_chunk("f_parse", "parse")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[parse])

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.expected_functions[0].verification_reason, "call_result_assigned_then_asserted")

    def test_callback_output_then_assertion_verifies_receiver_call(self):
        test = source_test(
            "t1",
            "TEST(Demo, Case) {\n"
            "  std::string output;\n"
            "  Process process(\"cat\", \"\", [&](const char* bytes, size_t n) {\n"
            "    output.append(bytes, n);\n"
            "  });\n"
            "  process.write(input);\n"
            "  ASSERT_EQ(output, expected_output);\n"
            "}",
            calls=["write"],
        )
        write = FunctionChunk(
            chunk_id="f_write",
            project="demo",
            language="C++",
            file="src/demo.cpp",
            name="write",
            qualified_name="Process.write",
            code="",
            start_line=1,
            end_line=5,
            parent="Process",
        )
        constructor = FunctionChunk(
            chunk_id="f_process",
            project="demo",
            language="C++",
            file="src/demo.cpp",
            name="Process",
            qualified_name="Process",
            code="",
            start_line=10,
            end_line=15,
        )

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[constructor, write])

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.expected_function_ids, ["f_write"])
        self.assertEqual(record.expected_functions[0].verification_reason, "call_effect_or_output_asserted")

    def test_exception_flag_then_assertion_verifies_throwing_call(self):
        test = source_test(
            "t1",
            "TEST(Demo, Case) {\n"
            "  bool did_throw = false;\n"
            "  try {\n"
            "    allocate(999999);\n"
            "  } catch (std::bad_alloc&) {\n"
            "    did_throw = true;\n"
            "  }\n"
            "  EXPECT_TRUE(did_throw);\n"
            "}",
            calls=["allocate"],
        )
        allocate = function_chunk("f_allocate", "allocate")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[allocate])

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.expected_functions[0].verification_reason, "call_effect_or_output_asserted")

    def test_constructed_object_state_then_assertion_becomes_matched_gold(self):
        test = source_test(
            "t1",
            "TEST(Demo, Case) {\n  Widget widget(42);\n  EXPECT_EQ(widget.value, 42);\n}",
            calls=["Widget"],
        )
        widget = function_chunk("f_widget", "Widget")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[widget])

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.expected_functions[0].verification_reason, "constructed_object_state_asserted")

    def test_receiver_state_then_assertion_becomes_matched_gold(self):
        test = source_test(
            "t1",
            "TEST(Demo, Case) {\n  Cache cache;\n  cache.put(42, 4242);\n  EXPECT_EQ(cache.get(42), 4242);\n}",
            calls=["put", "get"],
        )
        put = function_chunk("f_put", "put")
        get = function_chunk("f_get", "get")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[put, get])

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.expected_function_ids, ["f_put", "f_get"])
        self.assertEqual(record.expected_functions[0].verification_reason, "call_effect_or_output_asserted")
        self.assertEqual(record.expected_functions[1].verification_reason, "direct_call_inside_assertion")

    def test_called_helper_assertion_is_not_direct_gold_by_default(self):
        test = source_test(
            "t1",
            'TEST(Base64Public, DecodeSimple) { AssertDecodedPublic("eHk=", "xy"); }',
            calls=["AssertDecodedPublic"],
        )
        test = test.__class__(
            **{
                **test.to_dict(),
                "helpers": [
                    "void AssertDecodedPublic(const char* encoded, const char* expected) {\n"
                    "  char out[8];\n"
                    "  base64_decode(out, encoded, 4);\n"
                    "  ASSERT_EQ(std::string(out), std::string(expected));\n"
                    "}"
                ],
            }
        )
        decode = function_chunk("f_decode", "base64_decode")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[decode])

        self.assertEqual(record.status, "no_match")
        self.assertEqual(record.expected_function_ids, [])

    def test_multiple_unasserted_business_calls_are_uncertain(self):
        test = source_test(
            "t1",
            "TEST(Demo, Case) { setup_input(); add(1, 2); normalize(); EXPECT_TRUE(true); }",
            calls=["add", "normalize"],
        )
        add = function_chunk("f_add", "add")
        normalize = function_chunk("f_norm", "normalize")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[add, normalize])

        self.assertEqual(record.status, "uncertain")
        self.assertEqual(record.expected_function_ids, [])

    def test_python_assertion_is_detected(self):
        test = source_test("t1", "def test_case():\n    assert add(1, 2) == 3", language="Python", calls=["add"])
        add = function_chunk("f_add", "add", language="Python")

        record = propose_source_function_gold(pair="Python_to_C++", test=test, functions=[add])

        self.assertEqual(record.status, "matched")
        self.assertEqual(record.expected_function_ids, ["f_add"])

    def test_verification_reason_returns_none_without_assertion(self):
        test = source_test("t1", "TEST(Demo, Case) { add(1, 2); }", calls=["add"])

        self.assertIsNone(verification_reason(test, "add", business_link_count=1))

    def test_succeed_constructor_smoke_is_no_match(self):
        test = source_test(
            "t1",
            "TEST(Demo, Case) { Widget widget(1); SUCCEED(); }",
            calls=["Widget"],
        )
        widget = function_chunk("f_widget", "Widget")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[widget])

        self.assertEqual(record.status, "no_match")
        self.assertEqual(record.expected_function_ids, [])
        self.assertIsNone(verification_reason(test, "Widget", business_link_count=1))

    def test_no_assertion_no_crash_business_calls_are_no_match(self):
        test = source_test(
            "t1",
            "TEST(Demo, Case) { Canvas c(10, 20); c.begin_path(); c.stroke(); }",
            calls=["Canvas", "begin_path", "stroke"],
        )
        begin_path = function_chunk("f_begin", "begin_path")
        stroke = function_chunk("f_stroke", "stroke")

        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[begin_path, stroke])

        self.assertEqual(record.status, "no_match")
        self.assertEqual(record.evidence["reason"], "business_calls_without_verifying_assertion")

    def test_json_header_container_size_is_not_business_api(self):
        function = FunctionChunk(
            chunk_id="f_json_size",
            project="demo",
            language="C++",
            file="vendor/json.hpp",
            name="size",
            qualified_name="size",
            code="",
            start_line=1,
            end_line=1,
        )

        self.assertFalse(is_business_function(function))

    def test_only_business_api_relations_are_evaluable(self):
        test = source_test("t1", "TEST(Demo, Case) { EXPECT_EQ(add(1, 2), 3); }", calls=["add"])
        add = function_chunk("f_add", "add")
        record = propose_source_function_gold(pair="C++_to_Python", test=test, functions=[add])
        record.expected_functions[0] = record.expected_functions[0].__class__(
            **{
                **record.expected_functions[0].to_dict(),
                "relation": "inspection_api",
            }
        )

        self.assertEqual(evaluable_expected_function_ids(record), [])


if __name__ == "__main__":
    unittest.main()
