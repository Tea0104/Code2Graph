from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .runner import run_command


@dataclass(frozen=True)
class RunnableTest:
    executable: Path
    framework: str
    test_filter: str | None
    display_name: str


def _parse_gtest_list(output: str) -> list[str]:
    tests: list[str] = []
    current_suite = ""
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if not line.startswith(" "):
            current_suite = line.strip().split("#", 1)[0].strip()
            if current_suite.endswith("."):
                current_suite = current_suite[:-1]
            continue
        case = line.strip().split("#", 1)[0].strip()
        if current_suite and case:
            tests.append(f"{current_suite}.{case}")
    return tests


def _parse_doctest_list(output: str) -> list[str]:
    tests: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("[") or set(line) == {"="}:
            continue
        if "test cases passing" in line or "listing all" in line:
            continue
        tests.append(line)
    return tests


def _parse_boost_list(output: str) -> list[str]:
    tests: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Boost.Test") or line.startswith("Usage"):
            continue
        # --list_content commonly prints either plain names or tree markers.
        line = re.sub(r"^[-+|`\\ ]+", "", line)
        line = re.sub(r"\*+$", "", line).strip()
        if not line or any(token in line.lower() for token in ("test case", "test suite", "module")):
            continue
        name = line.split()[0]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_./:-]*$", name):
            tests.append(name)
    return tests


def list_runnable_tests(executable: Path, *, timeout: int = 20) -> list[RunnableTest]:
    gtest = run_command(
        [str(executable), "--gtest_list_tests"],
        cwd=executable.parent,
        timeout=timeout,
    )
    if gtest.ok and gtest.stdout.strip():
        tests = _parse_gtest_list(gtest.stdout)
        if tests:
            return [
                RunnableTest(executable, "gtest", test_name, test_name)
                for test_name in tests
            ]

    catch = run_command(
        [str(executable), "--list-tests"],
        cwd=executable.parent,
        timeout=timeout,
    )
    catch_output = "\n".join(part for part in [catch.stdout, catch.stderr] if part)
    if catch_output.strip() and "All available test cases" in catch_output:
        tests = []
        for line in catch_output.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("[") and not stripped.startswith("All available") and not stripped.endswith("test cases") and not stripped[0].isdigit():
                tests.append(stripped)
        if tests:
            return [
                RunnableTest(executable, "catch", test_name, test_name)
                for test_name in tests[:500]
            ]

    doctest = run_command(
        [str(executable), "--list-test-cases"],
        cwd=executable.parent,
        timeout=timeout,
    )
    doctest_output = "\n".join(part for part in [doctest.stdout, doctest.stderr] if part)
    if doctest_output.strip() and "[doctest]" in doctest_output:
        tests = _parse_doctest_list(doctest_output)
        if tests:
            return [
                RunnableTest(executable, "doctest", test_name, test_name)
                for test_name in tests[:500]
            ]

    boost = run_command(
        [str(executable), "--list_content"],
        cwd=executable.parent,
        timeout=timeout,
    )
    boost_output = "\n".join(part for part in [boost.stdout, boost.stderr] if part)
    if boost_output.strip() and ("Boost.Test" in boost_output or "test case" in boost_output.lower() or boost.ok):
        tests = _parse_boost_list(boost_output)
        if tests:
            return [
                RunnableTest(executable, "boost", test_name, test_name)
                for test_name in tests[:500]
            ]

    return [RunnableTest(executable, "binary", None, executable.name)]


def normalize_test_name(value: str) -> str:
    value = value.replace("::", ".")
    value = re.sub(r"/[0-9]+(?=\.)", "", value)
    value = re.sub(r"\s+", "", value)
    return value.strip(".").lower()


def match_source_test_to_runnable(
    source_test_name: str,
    candidates: list[RunnableTest],
) -> RunnableTest | None:
    wanted = normalize_test_name(source_test_name)
    if not wanted:
        return None
    exact = [
        candidate for candidate in candidates
        if candidate.test_filter and normalize_test_name(candidate.test_filter) == wanted
    ]
    if exact:
        return exact[0]
    suffix = [
        candidate for candidate in candidates
        if candidate.test_filter
        and (
            normalize_test_name(candidate.test_filter).endswith(wanted)
            or wanted.endswith(normalize_test_name(candidate.test_filter))
        )
    ]
    if suffix:
        return sorted(suffix, key=lambda item: len(item.test_filter or ""))[0]
    return None


def command_for_runnable(test: RunnableTest) -> list[str]:
    if test.framework == "gtest" and test.test_filter:
        return [str(test.executable), f"--gtest_filter={test.test_filter}"]
    if test.framework == "catch" and test.test_filter:
        return [str(test.executable), test.test_filter]
    if test.framework == "doctest" and test.test_filter:
        return [str(test.executable), f"--test-case={test.test_filter}"]
    if test.framework == "boost" and test.test_filter:
        return [str(test.executable), f"--run_test={test.test_filter}"]
    return [str(test.executable)]


def env_for_runnable(test: RunnableTest) -> dict[str, str]:
    if test.framework == "leveldb_testharness" and test.test_filter:
        return {"LEVELDB_TESTS": test.test_filter}
    return {}
