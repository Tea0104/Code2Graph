from pathlib import Path
import tempfile
import unittest

from file_topo_sort import analyze_project, normalize_languages


class LanguageAdapterTest(unittest.TestCase):
    def analyze(self, language: str, files: dict[str, str]) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            return analyze_project(root, language)

    def assert_dependency_before_consumer(
        self, result: dict[str, object], dependency: str, consumer: str,
    ) -> None:
        order = result["translation_order"]
        self.assertLess(order.index(dependency), order.index(consumer))

    def test_language_aliases_are_normalized(self):
        self.assertEqual(
            normalize_languages("py,c++,js,cs,c#"),
            ["python", "cpp", "javascript", "csharp"],
        )

    def test_result_schema_is_versioned(self):
        result = self.analyze("javascript", {"main.js": "export default 1;"})
        self.assertEqual(result["schema_version"], 1)

    def test_c_local_include_is_ordered(self):
        result = self.analyze("c", {
            "include/math.h": "int add(int a, int b);",
            "src/main.c": '#include "../include/math.h"\nint main(void) { return 0; }',
        })
        self.assert_dependency_before_consumer(
            result, "include/math.h", "src/main.c",
        )

    def test_java_import_uses_package_index(self):
        result = self.analyze("java", {
            "src/demo/core/Helper.java": "package demo.core; public class Helper {}",
            "src/demo/app/Main.java": (
                "package demo.app;\nimport demo.core.Helper;\npublic class Main {}"
            ),
        })
        self.assert_dependency_before_consumer(
            result, "src/demo/core/Helper.java", "src/demo/app/Main.java",
        )

    def test_javascript_relative_imports_and_require_are_ordered(self):
        result = self.analyze("js", {
            "lib/helper.js": "export function helper() {}",
            "lib/config.js": "module.exports = {};",
            "main.js": (
                'import { helper } from "./lib/helper.js";\n'
                'const config = require("./lib/config");\n'
            ),
        })
        self.assert_dependency_before_consumer(result, "lib/helper.js", "main.js")
        self.assert_dependency_before_consumer(result, "lib/config.js", "main.js")

    def test_csharp_using_namespace_is_ordered(self):
        result = self.analyze("c#", {
            "Core/Helper.cs": "namespace Demo.Core; public class Helper {}",
            "Core/Utility.cs": "namespace Demo.Core; public class Utility {}",
            "App/Program.cs": (
                "using Demo.Core;\nnamespace Demo.App; "
                "public class Program { Helper helper; Utility utility; }"
            ),
        })
        self.assert_dependency_before_consumer(
            result, "Core/Helper.cs", "App/Program.cs",
        )
        self.assert_dependency_before_consumer(
            result, "Core/Utility.cs", "App/Program.cs",
        )

    def test_existing_python_and_cpp_interfaces_remain_supported(self):
        python_result = self.analyze("python", {
            "package/__init__.py": "",
            "package/helper.py": "def helper(): pass",
            "main.py": "from package import helper",
        })
        self.assert_dependency_before_consumer(
            python_result, "package/__init__.py", "main.py",
        )
        cpp_result = self.analyze("cpp", {
            "include/value.hpp": "int value();",
            "src/main.cpp": '#include "../include/value.hpp"',
        })
        self.assert_dependency_before_consumer(
            cpp_result, "include/value.hpp", "src/main.cpp",
        )


if __name__ == "__main__":
    unittest.main()
