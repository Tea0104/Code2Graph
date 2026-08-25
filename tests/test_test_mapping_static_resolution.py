from pathlib import Path
import tempfile
import unittest

from test_mapping.models import FunctionChunk, TestChunk
from test_mapping.static_resolution import (
    direct_call_targets,
    related_functions,
    resolve_source_function_links,
)


def source_test(
    identifier: str,
    code: str,
    *,
    language: str = "Python",
    file: str | None = None,
    imports: list[str] | None = None,
    calls: list[str] | None = None,
    helpers: list[str] | None = None,
) -> TestChunk:
    suffix = "py" if language == "Python" else "cpp"
    return TestChunk(
        chunk_id=identifier,
        project="demo",
        language=language,
        file=file or f"public_tests/test_demo.{suffix}",
        name="test_case",
        qualified_name="TestDemo.test_case" if language == "Python" else "Demo.Case",
        code=code,
        chunk_text=f"Test: {identifier}\nCode:\n{code}",
        start_line=1,
        end_line=10,
        framework="pytest" if language == "Python" else "TEST",
        parent="TestDemo" if language == "Python" else "Demo",
        imports=imports or [],
        calls=calls or [],
        helpers=helpers or [],
    )


def function_chunk(
    identifier: str,
    name: str,
    file: str,
    *,
    qualified_name: str | None = None,
    language: str = "Python",
    parent: str | None = None,
) -> FunctionChunk:
    return FunctionChunk(
        chunk_id=identifier,
        project="demo",
        language=language,
        file=file,
        name=name,
        qualified_name=qualified_name or name,
        code=f"def {name}(): pass" if language == "Python" else f"void {name}() {{}}",
        start_line=1,
        end_line=1,
        parent=parent,
    )


class StaticResolutionTest(unittest.TestCase):
    def test_python_from_import_alias_disambiguates_duplicate_function_names(self):
        test = source_test(
            "s1",
            "def test_case():\n    assert renamed_add(1, 2) == 3",
            imports=["from src.demo import add as renamed_add"],
        )
        chosen = function_chunk("f1", "add", "src/demo.py")
        duplicate = function_chunk("f2", "add", "other/demo.py")

        links = resolve_source_function_links(test, [duplicate, chosen])

        self.assertEqual([link.function.chunk_id for link in links], ["f1"])
        self.assertEqual(links[0].call, "renamed_add")
        self.assertEqual(links[0].reason, "python_from_import")

    def test_python_filters_test_helpers_before_resolving_target_function(self):
        test = source_test(
            "s1",
            "def test_case():\n    helper()\n    return add(1, 2)",
            calls=["helper", "add"],
            helpers=["def helper():\n    return add(0, 0)"],
        )
        helper = function_chunk("f_helper", "helper", "public_tests/test_demo.py")
        add = function_chunk("f_add", "add", "src/demo.py")

        self.assertEqual(direct_call_targets(test), ["add"])
        self.assertEqual(related_functions(test, [helper, add]), [add])

    def test_cpp_include_disambiguates_duplicate_function_names_and_filters_macros(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { EXPECT_EQ(add(1, 2), 3); }",
            language="C++",
            imports=['#include "demo.hpp"'],
        )
        chosen = function_chunk("f1", "add", "src/demo.cpp", language="C++")
        duplicate = function_chunk("f2", "add", "other/add.cpp", language="C++")

        self.assertEqual(direct_call_targets(test), ["add"])
        links = resolve_source_function_links(test, [duplicate, chosen])
        self.assertEqual([link.function.chunk_id for link in links], ["f1"])
        self.assertEqual(links[0].reason, "cpp_include")

    def test_cpp_qualified_call_resolves_class_method(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { Demo::twice(2); }",
            language="C++",
        )
        method = function_chunk(
            "f1",
            "twice",
            "src/demo.cpp",
            qualified_name="Demo.twice",
            language="C++",
            parent="Demo",
        )

        links = resolve_source_function_links(test, [method])
        self.assertEqual([link.function.chunk_id for link in links], ["f1"])
        self.assertEqual(links[0].reason, "cpp_qualified_name")

    def test_cpp_qualified_suffix_resolves_namespaced_static_method(self):
        test = source_test(
            "s1",
            'TEST(MetaUtilsPublicTest, ToUpper) { EXPECT_EQ(MetaUtils::ToUpper("xyz"), "XYZ"); }',
            language="C++",
        )
        method = function_chunk(
            "f1",
            "ToUpper",
            "src/MetaUtils.cpp",
            qualified_name="Reflection.MetaUtils.ToUpper",
            language="C++",
            parent="MetaUtils",
        )

        self.assertEqual(direct_call_targets(test), ["MetaUtils.ToUpper"])
        links = resolve_source_function_links(test, [method])
        self.assertEqual([link.function.chunk_id for link in links], ["f1"])
        self.assertEqual(links[0].reason, "cpp_qualified_suffix")

    def test_cpp_qualified_suffix_prefers_included_duplicate_declaration(self):
        test = source_test(
            "s1",
            'TEST(Json11PublicTest, ParseValid) { std::string err; auto j = json11::Json::parse("{}", err); EXPECT_TRUE(err.empty()); }',
            language="C++",
            imports=['#include "json11.hpp"'],
        )
        implementation = function_chunk(
            "f_cpp",
            "parse",
            "Source/Common/Lib/json11.cpp",
            qualified_name="Json.parse",
            language="C++",
            parent="Json",
        )
        declaration = function_chunk(
            "f_hpp",
            "parse",
            "Source/Common/Lib/json11.hpp",
            qualified_name="Json.parse",
            language="C++",
            parent="Json",
        )

        self.assertEqual(direct_call_targets(test), ["json11.Json.parse"])
        links = resolve_source_function_links(test, [implementation, declaration])

        self.assertEqual([link.function.chunk_id for link in links], ["f_hpp"])
        self.assertEqual(links[0].reason, "cpp_qualified_suffix")

    def test_cpp_duplicate_platform_implementations_prefer_unix_on_linux_dataset(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { Process process; EXPECT_EQ(process.get_exit_status(), 0); }",
            language="C++",
            imports=['#include "process.hpp"'],
        )
        unix = function_chunk(
            "f_unix",
            "get_exit_status",
            "process_unix.cpp",
            qualified_name="Process.get_exit_status",
            language="C++",
            parent="Process",
        )
        win = function_chunk(
            "f_win",
            "get_exit_status",
            "process_win.cpp",
            qualified_name="Process.get_exit_status",
            language="C++",
            parent="Process",
        )

        links = resolve_source_function_links(test, [unix, win])
        self.assertEqual([link.function.chunk_id for link in links], ["f_unix"])
        self.assertEqual(links[0].reason, "cpp_qualified_name")

    def test_cpp_receiver_file_preference_avoids_benchmark_implementations(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { ulid::ULID ulid; EXPECT_TRUE(ulid.MarshalTo(buf)); }",
            language="C++",
        )
        header = function_chunk(
            "f_header",
            "MarshalTo",
            "src/ulid_struct.hh",
            qualified_name="MarshalTo",
            language="C++",
        )
        bench = function_chunk(
            "f_bench",
            "MarshalTo",
            "src/ulid_bench.cc",
            qualified_name="MarshalTo",
            language="C++",
        )

        links = resolve_source_function_links(test, [bench, header])

        self.assertEqual([link.function.chunk_id for link in links], ["f_header"])
        self.assertEqual(links[0].reason, "cpp_qualified_suffix")

    def test_cpp_constructor_variable_and_member_calls_resolve_to_type(self):
        test = source_test(
            "s1",
            """
            TEST(Demo, Case) {
                param p("user");
                p.name();
                param q = p;
                q.name();
                q = param("session");
            }
            """,
            language="C++",
        )
        constructor = function_chunk(
            "f_ctor",
            "param",
            "include/http/param.hxx",
            qualified_name="param",
            language="C++",
        )
        name = function_chunk(
            "f_name",
            "name",
            "include/http/param.hxx",
            qualified_name="param.name",
            language="C++",
            parent="param",
        )

        self.assertEqual(direct_call_targets(test), ["param", "param.name"])
        links = resolve_source_function_links(test, [constructor, name])
        self.assertEqual([link.function.chunk_id for link in links], ["f_ctor", "f_name"])

    def test_cpp_template_helper_definition_does_not_expand_to_return_type(self):
        helper = """
        template<typename T>
        T add(T a, T b) { return a + b; }
        """
        test = source_test(
            "s1",
            "TEST(Demo, Case) { EXPECT_EQ(add(1, 2), 3); }",
            language="C++",
            calls=["add"],
            helpers=[helper],
        )
        accidental = function_chunk("f_t", "T", "src/template.cpp", qualified_name="T", language="C++")

        self.assertEqual(direct_call_targets(test), [])
        self.assertEqual(resolve_source_function_links(test, [accidental]), [])

    def test_cpp_local_class_definition_is_treated_as_test_local(self):
        local_class = "class Widget { public: explicit Widget(int value) {} };"
        test = source_test(
            "s1",
            "TEST(Demo, Case) { Widget widget(1); EXPECT_TRUE(true); }",
            language="C++",
            calls=["Widget"],
            helpers=[local_class],
        )
        widget = function_chunk("f_widget", "Widget", "src/widget.cpp", qualified_name="Widget", language="C++")

        self.assertEqual(direct_call_targets(test), [])
        self.assertEqual(resolve_source_function_links(test, [widget]), [])

    def test_cpp_local_helper_method_name_does_not_filter_external_receiver(self):
        local_class = "class FakeDevice { public: void push() {} };"
        test = source_test(
            "s1",
            "TEST(Demo, Case) { UsbBus bus(1); bus.push(); EXPECT_TRUE(true); }",
            language="C++",
            helpers=[local_class],
        )
        push = function_chunk(
            "f_push",
            "push",
            "include/usbtop/usb_bus.h",
            qualified_name="UsbBus.push",
            language="C++",
            parent="UsbBus",
        )

        self.assertEqual(direct_call_targets(test), ["UsbBus", "UsbBus.push"])
        links = resolve_source_function_links(test, [push])
        self.assertEqual([link.function.chunk_id for link in links], ["f_push"])

    def test_cpp_ignores_new_and_access_specifier_noise(self):
        helper = "struct FakeDevice : public UsbDevice { void push() override { } };"
        test = source_test(
            "s1",
            "TEST(Demo, Case) { FakeDevice* dev = new FakeDevice(); EXPECT_TRUE(dev != nullptr); }",
            language="C++",
            helpers=[helper],
        )
        accidental_new = function_chunk("f_new", "new", "third_party/placement_new.hpp", qualified_name="new", language="C++")
        accidental_public = function_chunk("f_public", "public", "src/noise.cpp", qualified_name="public", language="C++")

        self.assertEqual(direct_call_targets(test), [])
        self.assertEqual(resolve_source_function_links(test, [accidental_new, accidental_public]), [])

    def test_cpp_callable_variable_is_not_resolved_by_unique_short_name(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { inplace_function<int(int), 32> f = [](int x){ return x + 9; }; EXPECT_EQ(f(6), 15); }",
            language="C++",
        )
        accidental = function_chunk(
            "f_accidental",
            "f",
            "src/other.cpp",
            qualified_name="A.f",
            language="C++",
            parent="A",
        )

        self.assertEqual(resolve_source_function_links(test, [accidental]), [])

    def test_cpp_standard_vector_member_call_is_not_typed_as_business_receiver(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { std::vector<int> values; EXPECT_EQ(values.size(), 0u); }",
            language="C++",
        )
        accidental_size = function_chunk(
            "f_size",
            "size",
            "src/project_container.cpp",
            qualified_name="ProjectContainer.size",
            language="C++",
            parent="ProjectContainer",
        )

        self.assertEqual(direct_call_targets(test), [])
        self.assertEqual(resolve_source_function_links(test, [accidental_size]), [])

    def test_cpp_untyped_nested_accessor_does_not_resolve_to_unrelated_size(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { Exporter exporter; ASSERT_EQ(exporter.exported_batches.size(), 1); }",
            language="C++",
        )
        accidental_size = function_chunk(
            "f_size",
            "size",
            "src/str_view.hpp",
            qualified_name="str_view.size",
            language="C++",
            parent="str_view",
        )

        self.assertEqual(direct_call_targets(test), [])
        self.assertEqual(resolve_source_function_links(test, [accidental_size]), [])

    def test_cpp_chained_member_accessor_does_not_become_global_call(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { Command cmd; ASSERT_EQ(cmd.args().size(), 2); EXPECT_TRUE(cmd.args().empty()); }",
            language="C++",
        )
        accidental_size = function_chunk("f_size", "size", "src/noise.cpp", qualified_name="size", language="C++")
        accidental_empty = function_chunk("f_empty", "empty", "src/noise.cpp", qualified_name="empty", language="C++")

        self.assertEqual(direct_call_targets(test), ["Command.args"])
        self.assertEqual(resolve_source_function_links(test, [accidental_size, accidental_empty]), [])

    def test_cpp_indexed_value_member_call_can_resolve_by_unique_name(self):
        test = source_test(
            "s1",
            'TEST(Demo, Case) { CsonObject obj; ASSERT_EQ(obj["key"].AsString(), "value"); }',
            language="C++",
        )
        as_string = function_chunk(
            "f_as_string",
            "AsString",
            "src/csonpp_impl.cc",
            qualified_name="Value.AsString",
            language="C++",
            parent="Value",
        )

        self.assertEqual(direct_call_targets(test), ["AsString"])
        links = resolve_source_function_links(test, [as_string])
        self.assertEqual([link.function.chunk_id for link in links], ["f_as_string"])

    def test_cpp_plain_object_declaration_and_arrow_member_calls_resolve_to_type(self):
        test = source_test(
            "s1",
            """
            TEST(Demo, Case) {
                SafeQueue queue;
                EXPECT_TRUE(queue.empty());
                SafeQueue* ptr = &queue;
                EXPECT_EQ(ptr->size(), 0);
            }
            """,
            language="C++",
        )
        empty = function_chunk(
            "f_empty",
            "empty",
            "include/SafeQueue.h",
            qualified_name="SafeQueue.empty",
            language="C++",
            parent="SafeQueue",
        )
        size = function_chunk(
            "f_size",
            "size",
            "include/SafeQueue.h",
            qualified_name="SafeQueue.size",
            language="C++",
            parent="SafeQueue",
        )

        self.assertEqual(direct_call_targets(test), ["SafeQueue.empty", "SafeQueue.size"])
        links = resolve_source_function_links(test, [empty, size])
        self.assertEqual([link.function.chunk_id for link in links], ["f_empty", "f_size"])

    def test_cpp_overload_resolves_using_argument_type_hint(self):
        test = source_test(
            "s1",
            """
            TEST(Demo, Case) {
                char arg[] = "--preset";
                EXPECT_TRUE(parseArgument(arg));
            }
            """,
            language="C++",
        )
        vector_overload = function_chunk(
            "f_vector",
            "parseArgument",
            "src/main.cpp",
            qualified_name="parseArgument",
            language="C++",
        )
        vector_overload = vector_overload.__class__(
            **{
                **vector_overload.to_dict(),
                "code": "bool parseArgument(const std::vector<std::string> &args) { return true; }",
            }
        )
        char_overload = function_chunk(
            "f_char",
            "parseArgument",
            "src/main.cpp",
            qualified_name="parseArgument",
            language="C++",
        )
        char_overload = char_overload.__class__(
            **{
                **char_overload.to_dict(),
                "code": "bool parseArgument(char *arg) { return true; }",
                "start_line": 68,
            }
        )

        links = resolve_source_function_links(test, [vector_overload, char_overload])

        self.assertEqual([link.function.chunk_id for link in links], ["f_char"])
        self.assertEqual(links[0].reason, "cpp_overload_argument_hint")

    def test_cpp_overload_resolves_pointer_declaration_without_space(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { char *arg = nullptr; EXPECT_FALSE(parseArgument(arg)); }",
            language="C++",
        )
        vector_overload = function_chunk(
            "f_vector",
            "parseArgument",
            "src/main.cpp",
            qualified_name="parseArgument",
            language="C++",
        )
        vector_overload = vector_overload.__class__(
            **{
                **vector_overload.to_dict(),
                "code": "bool parseArgument(const std::vector<std::string> &args) { return true; }",
            }
        )
        char_overload = function_chunk(
            "f_char",
            "parseArgument",
            "src/main.cpp",
            qualified_name="parseArgument",
            language="C++",
        )
        char_overload = char_overload.__class__(
            **{
                **char_overload.to_dict(),
                "code": "bool parseArgument(char *arg) { return true; }",
                "start_line": 68,
            }
        )

        links = resolve_source_function_links(test, [vector_overload, char_overload])

        self.assertEqual([link.function.chunk_id for link in links], ["f_char"])
        self.assertEqual(links[0].reason, "cpp_overload_argument_hint")

    def test_cpp_called_helper_does_not_expand_by_default(self):
        helper = """
        void AssertDecodedPublic(const char* encoded, const char* expected) {
            int dlen = base64_dec_len(encoded, strlen(encoded));
            std::vector<char> buf(dlen + 1, 0);
            base64_decode(buf.data(), encoded, strlen(encoded));
            ASSERT_EQ(std::string(buf.data(), dlen), std::string(expected));
        }
        """
        test = source_test(
            "s1",
            "TEST(Base64Public, DecodeSimple) { AssertDecodedPublic(\"eHk=\", \"xy\"); }",
            language="C++",
            calls=["AssertDecodedPublic"],
            helpers=[helper],
        )
        decode = function_chunk("f_decode", "base64_decode", "src/Base64.cpp", language="C++")
        dec_len = function_chunk("f_dec_len", "base64_dec_len", "src/Base64.cpp", language="C++")

        self.assertEqual(direct_call_targets(test), [])
        links = resolve_source_function_links(test, [decode, dec_len])

        self.assertEqual(links, [])

    def test_cpp_called_helper_can_expand_when_explicitly_requested(self):
        helper = """
        void AssertDecodedPublic(const char* encoded, const char* expected) {
            int dlen = base64_dec_len(encoded, strlen(encoded));
            std::vector<char> buf(dlen + 1, 0);
            base64_decode(buf.data(), encoded, strlen(encoded));
            ASSERT_EQ(std::string(buf.data(), dlen), std::string(expected));
        }
        """
        test = source_test(
            "s1",
            "TEST(Base64Public, DecodeSimple) { AssertDecodedPublic(\"eHk=\", \"xy\"); }",
            language="C++",
            calls=["AssertDecodedPublic"],
            helpers=[helper],
        )
        decode = function_chunk("f_decode", "base64_decode", "src/Base64.cpp", language="C++")
        dec_len = function_chunk("f_dec_len", "base64_dec_len", "src/Base64.cpp", language="C++")

        links = resolve_source_function_links(test, [decode, dec_len], include_helpers=True)

        self.assertEqual([link.function.chunk_id for link in links], ["f_dec_len", "f_decode"])
        self.assertTrue(all(link.reason.endswith("_via_helper") for link in links))

    def test_cpp_template_constructor_variable_resolves_to_type_constructor(self):
        test = source_test(
            "s1",
            "chain_router<PubDummySession> chainRouter(std::regex::icase);",
            language="C++",
        )
        constructor = function_chunk(
            "f_chain",
            "chain_router",
            "include/http/chain_router.hxx",
            qualified_name="chain_router",
            language="C++",
        )

        self.assertEqual(direct_call_targets(test), ["chain_router"])
        links = resolve_source_function_links(test, [constructor])
        self.assertEqual([link.function.chunk_id for link in links], ["f_chain"])

    def test_cpp_braced_constructor_initializer_resolves_to_type_constructor(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { APFloat f1{1.23}; EXPECT_NEAR(f1.convertToDouble(), 1.23, 1e-8); }",
            language="C++",
        )
        constructor = function_chunk(
            "f_ctor",
            "APFloat",
            "include/APFloat.hpp",
            qualified_name="APFloat",
            language="C++",
        )
        convert = function_chunk(
            "f_convert",
            "convertToDouble",
            "include/APFloat.hpp",
            qualified_name="APFloat.convertToDouble",
            language="C++",
            parent="APFloat",
        )

        self.assertEqual(direct_call_targets(test), ["APFloat", "APFloat.convertToDouble"])
        links = resolve_source_function_links(test, [constructor, convert])
        self.assertEqual([link.function.chunk_id for link in links], ["f_ctor", "f_convert"])

    def test_cpp_const_object_member_call_keeps_receiver_type(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { const UrlParser parser(\"/a\"); EXPECT_TRUE(parser.valid()); }",
            language="C++",
        )
        valid = function_chunk(
            "f_valid",
            "valid",
            "include/UrlParser.hpp",
            qualified_name="UrlParser.valid",
            language="C++",
            parent="UrlParser",
        )

        self.assertEqual(direct_call_targets(test), ["UrlParser", "UrlParser.valid"])
        links = resolve_source_function_links(test, [valid])
        self.assertEqual([link.function.chunk_id for link in links], ["f_valid"])

    def test_cpp_succeed_and_static_assert_are_filtered_as_test_macros(self):
        test = source_test(
            "s1",
            "TEST(Demo, Case) { SUCCEED(); static_assert(sizeof(int) > 0); }",
            language="C++",
        )
        accidental = function_chunk("f_succeed", "SUCCEED", "src/noise.cpp", qualified_name="SUCCEED", language="C++")

        self.assertEqual(direct_call_targets(test), [])
        self.assertEqual(resolve_source_function_links(test, [accidental]), [])


if __name__ == "__main__":
    unittest.main()
