"""Run the unchanged IR regression suite against the vendored product package.

Flat aliases exist only in this dedicated test process, because the original
tests patch names such as ``session`` and ``windows_ui_adapter``. Production
imports remain namespaced. Tests that inspect a neighboring source/resource
receive a scratch copy of the actual vendored bytes, never the old IR checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback
import unittest


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
UPSTREAM = HERE / "upstream_tests"
EXPECTED_TESTS = 572


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_snapshots():
    manifest = json.loads((HERE / "upstream-tests-manifest.json").read_text(encoding="utf-8"))
    expected = {row["path"]: row for row in manifest["files"]}
    actual = {path.name for path in UPSTREAM.iterdir() if path.is_file()}
    if actual != set(expected):
        raise RuntimeError("Upstream regression snapshot inventory changed")
    for name, row in expected.items():
        path = UPSTREAM / name
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise RuntimeError("Upstream regression snapshot bytes changed: " + name)
    tests = sorted(name[:-3] for name in expected if name.startswith("test_") and name.endswith(".py"))
    if len(tests) != manifest["test_file_count"]:
        raise RuntimeError("Unexpected upstream test file count")
    return manifest, tests


class SnapshotLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Keep test source unchanged while directing resource reads to scratch."""

    def __init__(self, virtual_directory, names):
        self.virtual_directory = virtual_directory
        self.names = set(names) | {"excel_test_helpers"}

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.names:
            return importlib.util.spec_from_loader(fullname, self, origin=str(UPSTREAM / (fullname + ".py")))
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source = UPSTREAM / (module.__name__ + ".py")
        module.__file__ = str(self.virtual_directory / source.name)
        # Compile the preserved raw bytes. No test imports/assertions are edited.
        exec(compile(source.read_bytes(), str(source), "exec"), module.__dict__)


def prepare_resources(package_root, scratch):
    copied = []
    for path in sorted(package_root.rglob("*")):
        relative = path.relative_to(package_root)
        if not path.is_file() or "__pycache__" in relative.parts or path.suffix in {".pyc", ".log"}:
            continue
        target = scratch / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        checksum = digest(path)
        if digest(target) != checksum:
            raise RuntimeError("Vendored resource scratch copy mismatch: " + str(relative))
        copied.append({"path": relative.as_posix(), "sha256": checksum})
    # These two historical offline data fixtures are not product resources.
    for name in ("input_example_validation.json", "random80_validation.json"):
        target = scratch / name
        if target.exists() and target.read_bytes() != (UPSTREAM / name).read_bytes():
            raise RuntimeError("Product/fixture JSON mismatch: " + name)
        shutil.copyfile(UPSTREAM / name, target)
    return copied


def run(output):
    started = time.monotonic()
    report = {"passed": False, "tests": 0, "failures": 0, "errors": 0, "skips": 0,
              "source_revision": "2372c29602a9538f78593699c5e34c0485373524",
              "python": sys.version, "expected_tests": EXPECTED_TESTS,
              "scope": "Unchanged upstream tests, actual namespaced vendored product modules; owned fixtures only",
              "real_equipment_tested": False, "excel_com_tested": False}
    original_path = list(sys.path)
    finder = None
    inserted_aliases = []
    try:
        manifest, test_names = verify_snapshots()
        suite_root = REPOSITORY / "suite"
        sys.path.insert(0, str(suite_root))
        package = importlib.import_module("ir_feedback")
        package_root = Path(package.__file__).resolve().parent
        if package_root != (suite_root / "ir_feedback").resolve():
            raise RuntimeError("IR package did not resolve from the integrated source")
        module_rows = []
        for path in sorted(package_root.glob("*.py")):
            if path.stem == "__init__" or path.stem.startswith("test_"):
                continue
            if path.stem in sys.modules:
                raise RuntimeError("Run regression suite in a clean process; flat alias already exists: " + path.stem)
            module = importlib.import_module("ir_feedback." + path.stem)
            if Path(module.__file__).resolve() != path.resolve():
                raise RuntimeError("IR module loaded from an unexpected path: " + path.stem)
            sys.modules[path.stem] = module
            inserted_aliases.append(path.stem)
            module_rows.append({"module": module.__name__, "path": path.relative_to(suite_root).as_posix(),
                                "sha256": digest(path)})
        with tempfile.TemporaryDirectory(prefix="bjt-ir-upstream-") as temporary:
            scratch = Path(temporary)
            report["inspected_resources"] = prepare_resources(package_root, scratch)
            finder = SnapshotLoader(scratch, test_names)
            sys.meta_path.insert(0, finder)
            suite = unittest.TestSuite()
            for name in test_names:
                module = importlib.import_module(name)
                inserted_aliases.append(name)
                suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
            result = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
        report.update(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                      skips=len(result.skipped),
                      skipped=[{"test": str(test), "reason": reason} for test, reason in result.skipped],
                      failed=[{"test": str(test), "traceback": trace} for test, trace in result.failures],
                      errored=[{"test": str(test), "traceback": trace} for test, trace in result.errors],
                      imported_product_modules=module_rows, upstream_test_files=len(test_names),
                      upstream_snapshots_byte_verified=True)
        # Re-check after tests so test-only mocks cannot hide snapshot mutations.
        verify_snapshots()
        report["passed"] = result.wasSuccessful() and result.testsRun == EXPECTED_TESTS
        if result.testsRun != EXPECTED_TESTS:
            report["count_error"] = "Collected test count differs from the preserved upstream baseline"
    except BaseException:
        report["runner_error"] = traceback.format_exc()
        report["errors"] += 1
    finally:
        if finder is not None:
            sys.meta_path.remove(finder)
        for name in inserted_aliases + ["excel_test_helpers"]:
            sys.modules.pop(name, None)
        sys.path[:] = original_path
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("passed", "tests", "failures", "errors", "skips", "elapsed_seconds")}))
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return run(parser.parse_args().output)


if __name__ == "__main__":
    raise SystemExit(main())
