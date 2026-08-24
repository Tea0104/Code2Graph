from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import urllib.request

from .runner import CommandResult, run_command
from .recipes import DynamicBuildRecipe


@dataclass(frozen=True)
class BuildAttempt:
    build_system: str
    source_subdir: str
    configure_command: list[str] | None
    build_command: list[str]
    build_dir: Path
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class BuildOutcome:
    ok: bool
    build_system: str | None
    build_dir: Path | None
    configure: CommandResult | None = None
    build: CommandResult | None = None
    error_stage: str | None = None
    error: str | None = None
    dependency: CommandResult | None = None
    dependency_prefixes: dict[str, str] = field(default_factory=dict)

    def log_excerpt(self) -> str:
        chunks = []
        for result in (self.dependency, self.configure, self.build):
            if result is not None:
                chunks.append(result.excerpt())
        text = "\n".join(item for item in chunks if item)
        return text[-4000:]


def _python_package_bin_dirs() -> list[Path]:
    """Return binary directories shipped by Python tool packages.

    On the shared dataset host we often have `cmake`/`ninja` installed as
    Python packages inside the experiment venv, but not exposed on PATH for
    non-interactive SSH commands.  Discovering those package-local binaries
    makes the dynamic probe much less dependent on machine-global setup.
    """

    candidates: list[Path] = [Path(sys.executable).parent]
    for package in ("cmake", "ninja"):
        spec = importlib.util.find_spec(package)
        if spec is None or spec.origin is None:
            continue
        package_dir = Path(spec.origin).resolve().parent
        candidates.extend([
            package_dir / "data" / "bin",
            package_dir / "bin",
        ])
    result: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.is_dir():
            result.append(path)
    return result


def _tool_path(name: str) -> str | None:
    extra_path = os.pathsep.join(str(path) for path in _python_package_bin_dirs())
    path_env = os.environ.get("PATH", "")
    search_path = os.pathsep.join(part for part in [extra_path, path_env] if part)
    return shutil.which(name, path=search_path)


def _tool_exists(name: str) -> bool:
    return _tool_path(name) is not None


def _coverage_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    extra_path = os.pathsep.join(str(path) for path in _python_package_bin_dirs())
    if extra_path:
        env["PATH"] = os.pathsep.join(part for part in [extra_path, env.get("PATH", "")] if part)
    if base:
        env.update(base)
    return env


def _coverage_toolchain() -> tuple[dict[str, str], str, str, str]:
    if _tool_exists("clang") and _tool_exists("clang++") and _clang_profile_runtime_available():
        flags = "-O0 -g -fprofile-instr-generate -fcoverage-mapping -DCATCH_CONFIG_NO_POSIX_SIGNALS"
        return {"CC": "clang", "CXX": "clang++"}, flags, flags, "llvm"
    flags = "-O0 -g --coverage -DCATCH_CONFIG_NO_POSIX_SIGNALS"
    return {
        "CC": os.environ.get("CODE2GRAPH_CC", "gcc"),
        "CXX": os.environ.get("CODE2GRAPH_CXX", "g++"),
    }, flags, flags, "gcov"


def _clang_profile_runtime_available() -> bool:
    for root in (Path("/usr/lib"), Path("/usr/local/lib")):
        if not root.exists():
            continue
        if list(root.glob("llvm-*/lib/clang/*/lib/linux/libclang_rt.profile-*.a")):
            return True
        if list(root.glob("clang/*/lib/linux/libclang_rt.profile-*.a")):
            return True
    return False


def _copy_source_for_in_tree_build(source_dir: Path, sandbox_dir: Path) -> Path:
    """Copy a source tree into the sandbox for build systems that write in-tree.

    Some dataset roots are intentionally read-only.  Plain Makefiles usually
    emit objects next to sources, so running them against the original dataset
    fails before we ever reach coverage.  A sandbox copy keeps the original
    project immutable while preserving Makefile-relative paths.
    """

    target = sandbox_dir / "make_source"
    if target.is_dir():
        return target

    def ignore(_directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            lowered = name.lower()
            if name in {".git", ".hg", ".svn", "__pycache__"}:
                ignored.add(name)
            elif lowered in {"build", "cmake-build-debug", "cmake-build-release"}:
                ignored.add(name)
            elif lowered.endswith((".o", ".obj", ".a", ".so", ".dll", ".dylib", ".exe", ".gcda", ".gcno", ".gcov")):
                ignored.add(name)
        return ignored

    shutil.copytree(source_dir, target, ignore=ignore)
    return target


def _cmake_project_needs_gtest(cmake_source: Path) -> bool:
    cmake_files = [cmake_source / "CMakeLists.txt"]
    cmake_files.extend(sorted(cmake_source.glob("**/CMakeLists.txt"))[:200])
    seen: set[Path] = set()
    for cmake_file in cmake_files:
        if cmake_file in seen or not cmake_file.is_file():
            continue
        seen.add(cmake_file)
        try:
            text = cmake_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if re.search(r"find_package\s*\(\s*GTest\b", text, re.I):
            return True
        if re.search(r"#\s*include\s*<gtest/gtest\.h>", text, re.I):
            return True
    for pattern in ("*test*.cc", "*test*.cpp", "*test*.cxx", "*gtest*.cc", "*gtest*.cpp", "*gtest*.cxx"):
        for source_file in sorted(cmake_source.glob(f"**/{pattern}"))[:200]:
            if not source_file.is_file():
                continue
            try:
                text = source_file.read_text(encoding="utf-8", errors="ignore")[:20000]
            except OSError:
                continue
            if "gtest/gtest.h" in text or "gmock/gmock.h" in text:
                return True
    return False


def ensure_googletest_install() -> Path | None:
    """Build a local GTest package for CMake projects that call find_package(GTest).

    Many dataset projects are small and otherwise buildable, but the shared
    server intentionally has no system-level libgtest-dev.  Installing
    googletest into a task-scoped /tmp cache lets CMake resolve GTest without
    mutating the dataset project or requiring root.
    """

    cmake = _tool_path("cmake")
    if not cmake:
        return None
    cache_root = Path(os.environ.get("CODE2GRAPH_DYNAMIC_DEPS", "/tmp/code2graph_dynamic_deps"))
    install_dir = cache_root / "googletest-1.14.0-install"
    config = install_dir / "lib" / "cmake" / "GTest" / "GTestConfig.cmake"
    if config.is_file():
        return install_dir

    source_dir = cache_root / "googletest-1.14.0"
    archive = cache_root / "googletest-1.14.0.tar.gz"
    build_dir = cache_root / "googletest-1.14.0-build"
    cache_root.mkdir(parents=True, exist_ok=True)
    if not source_dir.is_dir():
        if not archive.is_file():
            url = "https://github.com/google/googletest/archive/refs/tags/v1.14.0.tar.gz"
            try:
                urllib.request.urlretrieve(url, archive)
            except Exception:
                return None
        try:
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(cache_root)
        except Exception:
            return None
    env = _coverage_env()
    configure = run_command(
        [
            cmake,
            "-S",
            str(source_dir),
            "-B",
            str(build_dir),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={install_dir}",
            "-DBUILD_GMOCK=ON",
            "-Dgtest_force_shared_crt=ON",
        ],
        cwd=cache_root,
        timeout=120,
        env=env,
    )
    if not configure.ok:
        return None
    build = run_command(
        [cmake, "--build", str(build_dir), "--target", "install", "--parallel", "2"],
        cwd=cache_root,
        timeout=240,
        env=env,
    )
    if not build.ok:
        return None
    return install_dir if config.is_file() else None


def _recipe_conan_requires(recipe: DynamicBuildRecipe | None) -> list[str]:
    if recipe is None:
        return []
    result = list(recipe.conan_requires)
    if recipe.gtest_provider and recipe.gtest_provider.startswith("conan:"):
        result.append(recipe.gtest_provider.split(":", 1)[1])
    # Preserve order while removing duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for requirement in result:
        if requirement not in seen:
            seen.add(requirement)
            ordered.append(requirement)
    return ordered


def _extract_cmake_data_value(path: Path, variable: str) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(rf"set\({re.escape(variable)}\s+\"([^\"]+)\"", text)
    if match:
        return match.group(1)
    match = re.search(rf"set\({re.escape(variable)}\s+([^)\\s]+)", text)
    if match:
        return match.group(1).strip('"')
    return None


def _conan_generated_prefixes(output_dir: Path) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for data_file in output_dir.glob("*-*-data.cmake"):
        package_name = data_file.name.split("-", 1)[0]
        for variable in (
            f"{package_name}_PACKAGE_FOLDER_RELEASE",
            f"{package_name}_PACKAGE_FOLDER_DEBUG",
        ):
            value = _extract_cmake_data_value(data_file, variable)
            if value:
                prefixes[package_name.lower()] = value
                break
    return prefixes


def _find_conan_package_prefix(conan_home: Path, package: str) -> Path | None:
    # Fallback for recipes that need raw include/lib paths rather than
    # find_package() metadata.  Conan 2 stores built package contents under
    # <CONAN_HOME>/p/b/<short>/p.
    header_name = "gtest/gtest.h" if package.lower() == "gtest" else None
    if header_name is None or not conan_home.exists():
        return None
    for header in sorted(conan_home.glob(f"p/b/*/p/include/{header_name}")):
        return header.parents[2]
    return None


def ensure_conan_dependencies(
    recipe: DynamicBuildRecipe | None,
    sandbox_dir: Path,
    *,
    timeout: int,
) -> tuple[dict[str, Path], CommandResult | None]:
    requirements = _recipe_conan_requires(recipe)
    if not requirements:
        return {}, None
    conan = _tool_path("conan")
    if not conan:
        return {}, CommandResult(
            command=["conan"],
            cwd=str(sandbox_dir),
            returncode=127,
            stdout="",
            stderr="conan not found",
            elapsed_seconds=0,
        )
    output_dir = sandbox_dir / "conan"
    conan_home = Path(os.environ.get("CONAN_HOME", str(sandbox_dir / "conan_home")))
    output_dir.mkdir(parents=True, exist_ok=True)
    conan_home.mkdir(parents=True, exist_ok=True)
    env = _coverage_env({"CONAN_HOME": str(conan_home)})
    profile = run_command(
        [conan, "profile", "detect", "--force"],
        cwd=sandbox_dir,
        timeout=min(timeout, 120),
        env=env,
    )
    if not profile.ok:
        return {}, profile
    command = [
        conan,
        "install",
        *(f"--requires={requirement}" for requirement in requirements),
        "--generator=CMakeDeps",
        "--generator=CMakeToolchain",
        f"--output-folder={output_dir}",
        "--build=missing",
    ]
    install = run_command(command, cwd=sandbox_dir, timeout=timeout, env=env)
    if not install.ok:
        return {}, install
    prefixes = {
        key: Path(value)
        for key, value in _conan_generated_prefixes(output_dir).items()
    }
    if "gtest" not in prefixes:
        fallback = _find_conan_package_prefix(conan_home, "gtest")
        if fallback is not None:
            prefixes["gtest"] = fallback
    if output_dir.is_dir():
        prefixes["conan_cmake"] = output_dir
    return prefixes, install


def _cmake_dirs_from_recipe(source_dir: Path, recipe: DynamicBuildRecipe | None) -> list[Path] | None:
    if recipe is None or recipe.cmake_source_subdirs is None:
        return None
    result: list[Path] = []
    for subdir in recipe.cmake_source_subdirs:
        path = source_dir / subdir
        if path.is_dir() and path not in result:
            result.append(path)
    return result


def _expand_recipe_args(
    args: list[str],
    *,
    gtest_install: Path | None,
    dependency_prefixes: dict[str, Path] | None = None,
) -> list[str]:
    dependency_prefixes = dependency_prefixes or {}
    gtest_prefix = dependency_prefixes.get("gtest") or gtest_install
    conan_cmake = dependency_prefixes.get("conan_cmake")
    replacements = {
        "$gtest_prefix": str(gtest_prefix) if gtest_prefix is not None else "",
        "$gtest_include": str(gtest_prefix / "include") if gtest_prefix is not None else "",
        "$gtest_lib": str(gtest_prefix / "lib") if gtest_prefix is not None else "",
        "$conan_cmake": str(conan_cmake) if conan_cmake is not None else "",
    }
    expanded: list[str] = []
    for arg in args:
        value = arg
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
        expanded.append(value)
    return expanded


def build_attempts(
    source_dir: Path,
    sandbox_dir: Path,
    *,
    recipe: DynamicBuildRecipe | None = None,
    dependency_prefixes: dict[str, Path] | None = None,
) -> list[BuildAttempt]:
    attempts: list[BuildAttempt] = []
    compiler_env, c_flags, cxx_flags, coverage_tool = _coverage_toolchain()
    env = _coverage_env({**compiler_env, "CODE2GRAPH_COVERAGE_TOOL": coverage_tool})

    cmake_dirs = _cmake_dirs_from_recipe(source_dir, recipe)
    if cmake_dirs is None:
        cmake_dirs = [source_dir]
        cmake_dirs.extend(
            path.parent
            for path in sorted(source_dir.glob("*/*/CMakeLists.txt"))[:5]
            if path.parent not in cmake_dirs
        )
        cmake_dirs.extend(
            path.parent
            for path in sorted(source_dir.glob("*/CMakeLists.txt"))[:5]
            if path.parent not in cmake_dirs
        )
    if _tool_exists("cmake"):
        for index, cmake_source in enumerate(cmake_dirs):
            if not (cmake_source / "CMakeLists.txt").is_file():
                continue
            use_conan_gtest = bool(recipe and recipe.gtest_provider and recipe.gtest_provider.startswith("conan:"))
            gtest_install = None if use_conan_gtest else ensure_googletest_install() if (recipe and recipe.force_gtest) or _cmake_project_needs_gtest(cmake_source) else None
            generator = ["-G", "Ninja"] if _tool_exists("ninja") else []
            build_dir = sandbox_dir / f"cmake_{index}"
            rel = str(cmake_source.relative_to(source_dir)) if cmake_source != source_dir else "."
            dependency_args = []
            if gtest_install is not None:
                dependency_args.extend([
                    f"-DCMAKE_PREFIX_PATH={gtest_install}",
                    f"-DGTest_ROOT={gtest_install}",
                    f"-DGTEST_ROOT={gtest_install}",
                ])
            conan_cmake = (dependency_prefixes or {}).get("conan_cmake")
            if conan_cmake is not None:
                dependency_args.extend([
                    f"-DCMAKE_PREFIX_PATH={conan_cmake}",
                    f"-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES={conan_cmake / 'conan_provider.cmake'}" if (conan_cmake / "conan_provider.cmake").is_file() else "",
                ])
                dependency_args = [item for item in dependency_args if item]
            conan_gtest = (dependency_prefixes or {}).get("gtest")
            if conan_gtest is not None:
                dependency_args.extend([
                    f"-DCMAKE_PREFIX_PATH={conan_cmake};{conan_gtest}" if conan_cmake is not None else f"-DCMAKE_PREFIX_PATH={conan_gtest}",
                    f"-DGTest_ROOT={conan_gtest}",
                    f"-DGTEST_ROOT={conan_gtest}",
                ])
            attempts.append(BuildAttempt(
                build_system="cmake",
                source_subdir=rel,
                configure_command=[
                    "cmake",
                    "-S",
                    str(cmake_source),
                    "-B",
                    str(build_dir),
                    *generator,
                    "-DCMAKE_BUILD_TYPE=Debug",
                    "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
                    "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
                    "-DBUILD_TESTING=ON",
                    *dependency_args,
                    f"-DCMAKE_C_FLAGS={c_flags}",
                    f"-DCMAKE_CXX_FLAGS={cxx_flags}",
                    *(_expand_recipe_args(
                        recipe.cmake_args,
                        gtest_install=gtest_install,
                        dependency_prefixes=dependency_prefixes,
                    ) if recipe else []),
                ],
                build_command=[
                    "cmake",
                    "--build",
                    str(build_dir),
                    "--parallel",
                    "2",
                    *(["--target", *recipe.build_targets] if recipe and recipe.build_targets else []),
                    *(["--", *recipe.build_args] if recipe and recipe.build_args else []),
                ],
                build_dir=build_dir,
                env=env,
            ))

    if _tool_exists("meson") and (source_dir / "meson.build").is_file():
        build_dir = sandbox_dir / "meson"
        attempts.append(BuildAttempt(
            build_system="meson",
            source_subdir=".",
            configure_command=[
                "meson",
                "setup",
                str(build_dir),
                str(source_dir),
                "-Db_coverage=true",
                "--buildtype=debug",
            ],
            build_command=["meson", "compile", "-C", str(build_dir)],
            build_dir=build_dir,
            env=env,
        ))

    makefile = source_dir / "Makefile"
    lowercase_makefile = source_dir / "makefile"
    makefile_path = makefile if makefile.is_file() else lowercase_makefile
    generated_cmake_makefile = False
    if makefile_path.is_file():
        try:
            makefile_head = makefile_path.read_text(encoding="utf-8", errors="ignore")[:20000]
            generated_cmake_makefile = "cmake_check_build_system" in makefile_head
        except OSError:
            generated_cmake_makefile = False

    if makefile_path.is_file() and not generated_cmake_makefile:
        make_source_dir = _copy_source_for_in_tree_build(source_dir, sandbox_dir)
        attempts.append(BuildAttempt(
            build_system="make",
            source_subdir=".",
            configure_command=None,
            build_command=[
                "make",
                "-j2",
                f"CXX={compiler_env['CXX']}",
                f"CC={compiler_env['CC']}",
                f"CXXFLAGS={cxx_flags}",
                f"CFLAGS={c_flags}",
                f"LDFLAGS={cxx_flags}",
            ],
            build_dir=make_source_dir,
            env=env,
        ))

    return attempts


def configure_and_build(
    source_dir: Path,
    sandbox_dir: Path,
    *,
    configure_timeout: int = 120,
    build_timeout: int = 300,
    recipe: DynamicBuildRecipe | None = None,
) -> BuildOutcome:
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    last: BuildOutcome | None = None
    dependency_prefixes, dependency_result = ensure_conan_dependencies(
        recipe,
        sandbox_dir,
        timeout=max(build_timeout, 300),
    )
    if dependency_result is not None and not dependency_result.ok:
        return BuildOutcome(
            ok=False,
            build_system="dependency",
            build_dir=sandbox_dir,
            dependency=dependency_result,
            error_stage="dependency",
            error=dependency_result.excerpt(),
        )
    for attempt in build_attempts(
        source_dir,
        sandbox_dir,
        recipe=recipe,
        dependency_prefixes=dependency_prefixes,
    ):
        attempt.build_dir.mkdir(parents=True, exist_ok=True)
        configure_result = None
        if attempt.configure_command is not None:
            configure_result = run_command(
                attempt.configure_command,
                cwd=source_dir,
                timeout=configure_timeout,
                env=attempt.env,
            )
            if not configure_result.ok:
                last = BuildOutcome(
                    ok=False,
                    build_system=attempt.build_system,
                    build_dir=attempt.build_dir,
                    dependency=dependency_result,
                    dependency_prefixes={key: str(value) for key, value in dependency_prefixes.items()},
                    configure=configure_result,
                    error_stage="configure",
                    error=configure_result.excerpt(),
                )
                continue
        build_result = run_command(
            attempt.build_command,
            cwd=attempt.build_dir,
            timeout=build_timeout,
            env=attempt.env,
        )
        if build_result.ok:
            return BuildOutcome(
                ok=True,
                build_system=attempt.build_system,
                build_dir=attempt.build_dir,
                dependency=dependency_result,
                dependency_prefixes={key: str(value) for key, value in dependency_prefixes.items()},
                configure=configure_result,
                build=build_result,
            )
        last = BuildOutcome(
            ok=False,
            build_system=attempt.build_system,
            build_dir=attempt.build_dir,
            dependency=dependency_result,
            dependency_prefixes={key: str(value) for key, value in dependency_prefixes.items()},
            configure=configure_result,
            build=build_result,
            error_stage="build",
            error=build_result.excerpt(),
        )
    return last or BuildOutcome(
        ok=False,
        build_system=None,
        build_dir=None,
        error_stage="discover",
        error="No supported build system found",
    )


def discover_executables(build_dir: Path, source_dir: Path) -> list[Path]:
    roots = [build_dir]
    if build_dir != source_dir:
        roots.append(source_dir)
    seen: set[Path] = set()
    result: list[Path] = []
    testish = re.compile(r"(test|tests|unittest|unit_test|public|check)", re.I)
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            try:
                executable = os.access(path, os.X_OK)
                if not executable:
                    with path.open("rb") as handle:
                        executable = handle.read(4) == b"\x7fELF"
            except OSError:
                executable = False
            if not executable:
                continue
            if path.suffix.lower() in {".o", ".a", ".so", ".dylib", ".cmd", ".bat", ".ps1"}:
                continue
            rel = str(path.relative_to(root))
            rel_parts = set(Path(rel).parts)
            if "CMakeFiles" in rel_parts and not testish.search(rel):
                continue
            if testish.search(rel) or root == build_dir:
                result.append(path)
    return sorted(result, key=lambda item: (0 if testish.search(str(item)) else 1, len(str(item)), str(item)))[:100]
