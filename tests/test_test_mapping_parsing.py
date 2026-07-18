from pathlib import Path
import tempfile
import textwrap
import unittest

from test_mapping.parsing import extract_functions, extract_tests


class ParsingTest(unittest.TestCase):
    def _file(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_python_tests_include_methods_async_helpers_and_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._file(root, "public_tests/test_demo.py", """
                import pytest

                def helper(value):
                    return value + 1

                async def test_async_case():
                    assert helper(1) == 2

                class TestDemo:
                    def test_method(self):
                        assert helper(2) == 3
            """)
            chunks = extract_tests(path, root, "demo", "Python")
            self.assertEqual([chunk.qualified_name for chunk in chunks], ["test_async_case", "TestDemo.test_method"])
            self.assertIn("helper", chunks[0].calls)
            self.assertIn("def helper", chunks[0].chunk_text)
            self.assertEqual(chunks[1].fixture, "TestDemo")

    def test_python_pytest_fixture_is_selected_from_parameter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._file(root, "public_tests/test_fixture.py", """
                import pytest

                @pytest.fixture
                def prepared_value():
                    return 3

                def test_value(prepared_value):
                    assert prepared_value == 3
            """)
            chunk = extract_tests(path, root, "demo", "Python")[0]
            self.assertIn("def prepared_value", chunk.chunk_text)
            self.assertEqual(chunk.metadata["fixture_parameters"], ["prepared_value"])

    def test_cpp_google_test_catch2_fixture_and_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._file(root, "public_tests/test_demo.cpp", r'''
                #include "demo.hpp"
                int helper(int value) { return value + 1; }
                class DemoFixture : public testing::Test { protected: int value = 1; };
                TEST(Demo, Plain) { EXPECT_EQ(helper(1), 2); }
                TEST_F(DemoFixture, UsesFixture) { EXPECT_EQ(value, 1); }
                TEST_CASE("catch case") { REQUIRE(helper(2) == 3); }
            ''')
            chunks = extract_tests(path, root, "demo", "C++")
            self.assertEqual([chunk.qualified_name for chunk in chunks], ["Demo.Plain", "DemoFixture.UsesFixture", "catch case"])
            self.assertIn("int helper", chunks[0].chunk_text)
            self.assertIn("class DemoFixture", chunks[1].chunk_text)

    def test_cpp_functions_are_extracted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._file(root, "src/demo.cpp", """
                int add(int a, int b) { return a + b; }
                int Demo::twice(int value) { return add(value, value); }
            """)
            chunks = extract_functions([path], root, "demo", "C++")
            self.assertEqual([chunk.qualified_name for chunk in chunks], ["add", "Demo.twice"])
            self.assertIn("add", chunks[1].calls)

    def test_cpp_boost_and_plain_function_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boost = self._file(root, "boost_public.cpp", """
                BOOST_AUTO_TEST_CASE(public_add) { BOOST_CHECK(add(1, 2) == 3); }
            """)
            plain = self._file(root, "plain_public.cpp", """
                void test_add() { assert(add(1, 2) == 3); }
                int main() { test_add(); }
            """)
            self.assertEqual(extract_tests(boost, root, "demo", "C++")[0].name, "public_add")
            self.assertEqual(
                [chunk.name for chunk in extract_tests(plain, root, "demo", "C++")],
                ["test_add", "main"],
            )


if __name__ == "__main__":
    unittest.main()
