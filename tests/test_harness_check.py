"""Tests del validador tools/harness_check.py sobre proyectos instalados en temporales."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
TEMPLATE_SPEC = KIT / "template" / "docs" / "spec-example"

_spec = importlib.util.spec_from_file_location("harness_check", KIT / "template" / "tools" / "harness_check.py")
harness_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness_check)

_install_spec = importlib.util.spec_from_file_location("install", KIT / "install.py")
install = importlib.util.module_from_spec(_install_spec)
_install_spec.loader.exec_module(install)

TRACE_OK = """## Trazabilidad
- 1.1 → `test_recent_default_limit_orders_desc`
- 1.2 → `test_recent_custom_limit_format`
- 1.3 → `test_recent_default_limit_orders_desc`
- 1.4 → `test_recent_custom_limit_format`
- 2.1 → `test_recent_empty_outputs_nothing`
- 2.2 → `test_recent_invalid_limit_zero`
- 2.3 → `test_recent_invalid_limit_zero`
"""
TESTS_OK = """import unittest

class TestRecent(unittest.TestCase):
    def test_recent_default_limit_orders_desc(self): pass
    def test_recent_custom_limit_format(self): pass
    def test_recent_empty_outputs_nothing(self): pass
    def test_recent_invalid_limit_zero(self): pass
"""


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            install.main([str(self.root), "--name", "demo", "--python", "python", "--no-check"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def add_feature(self, name: str = "cli_recent", status: str = "spec_ready", sdd: bool = True,
                    with_spec: bool = True, feature_id: int = 1, **extra) -> Path:
        path = self.root / "feature_list.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["features"].append({"id": feature_id, "name": name, "status": status, "sdd": sdd, **extra})
        path.write_text(json.dumps(data), encoding="utf-8")
        spec_dir = self.root / "specs" / name
        if with_spec:
            shutil.copytree(TEMPLATE_SPEC, spec_dir, dirs_exist_ok=True)
        return spec_dir

    def edit(self, path: Path, old: str, new: str) -> None:
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def mark_all_tasks_done(self, spec_dir: Path) -> None:
        tasks = spec_dir / "tasks.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace("- [ ] ", "- [x] "), encoding="utf-8")

    def write_impl_and_tests(self, trace: str = TRACE_OK, tests: str = TESTS_OK) -> None:
        (self.root / "progress" / "impl_cli_recent.md").write_text(trace, encoding="utf-8")
        (self.root / "tests").mkdir(exist_ok=True)
        (self.root / "tests" / "test_cli.py").write_text(tests, encoding="utf-8")

    def check(self, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = harness_check.main(["--root", str(self.root), "--no-tests", *extra])
        return code, out.getvalue()

    def assert_fails_with(self, fragment: str) -> None:
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn(fragment, output)


class TestBaseline(ProjectCase):
    def test_fresh_install_is_green(self) -> None:
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("sin rellenar", output)

    def test_missing_base_file_fails(self) -> None:
        (self.root / "docs" / "sdd.md").unlink()
        self.assert_fails_with("Falta archivo base: docs/sdd.md")

    def test_template_spec_is_valid_kiro(self) -> None:
        self.add_feature(status="spec_ready")
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("spec cli_recent (spec_ready): 7 criterios válidos", output)

    def test_pending_feature_needs_no_spec(self) -> None:
        self.add_feature(status="pending", with_spec=False)
        self.assertEqual(self.check()[0], 0)

    def test_non_sdd_feature_needs_no_spec(self) -> None:
        self.add_feature(status="done", sdd=False, with_spec=False)
        self.assertEqual(self.check()[0], 0)


class TestFeatureList(ProjectCase):
    def test_two_in_progress_fails(self) -> None:
        self.add_feature(name="a", status="in_progress", with_spec=False, sdd=False, feature_id=1)
        self.add_feature(name="b", status="in_progress", with_spec=False, sdd=False, feature_id=2)
        self.assert_fails_with("2 features en in_progress")

    def test_invalid_status_fails(self) -> None:
        self.add_feature(status="wip", with_spec=False)
        self.assert_fails_with("estado inválido")

    def test_duplicate_id_fails(self) -> None:
        self.add_feature(name="a", status="pending", feature_id=1, with_spec=False)
        self.add_feature(name="b", status="pending", feature_id=1, with_spec=False)
        self.assert_fails_with("id duplicado")

    def test_spec_ready_without_spec_files_fails(self) -> None:
        self.add_feature(status="spec_ready", with_spec=False)
        self.assert_fails_with("sin specs/cli_recent/requirements.md")


class TestKiroFormat(ProjectCase):
    def test_missing_user_story_fails(self) -> None:
        spec = self.add_feature()
        self.edit(spec / "requirements.md", "**Historia de usuario:** Como usuario del CLI, quiero ver", "Quiero ver")
        self.assert_fails_with("Requisito 1 sin `**Historia de usuario:**`")

    def test_criterion_without_modal_fails(self) -> None:
        spec = self.add_feature()
        self.edit(spec / "requirements.md", "ENTONCES el sistema DEBE\n   imprimir como máximo 5", "imprime\n   5")
        self.assert_fails_with("criterio 1.1 no usa EARS")

    def test_criterion_with_two_modals_fails(self) -> None:
        spec = self.add_feature()
        self.edit(spec / "requirements.md", "sin escribir en stdout.", "y DEBE no escribir en stdout.")
        self.assert_fails_with("criterio 2.1 tiene 2 `DEBE`")

    def test_soft_verb_only_warns(self) -> None:
        spec = self.add_feature()
        self.edit(spec / "requirements.md", "con `N > 0`", "que puede ser `N > 0`")
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("verbo blando «puede»", output)

    def test_english_kiro_variant_is_accepted(self) -> None:
        spec = self.add_feature()
        req = spec / "requirements.md"
        text = req.read_text(encoding="utf-8")
        for es, en in (("### Requisito", "### Requirement"), ("**Historia de usuario:**", "**User Story:**"),
                       ("#### Criterios de aceptación", "#### Acceptance Criteria"), ("DEBE", "SHALL")):
            text = text.replace(es, en)
        req.write_text(text, encoding="utf-8")
        tasks = spec / "tasks.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace("_Requisitos:", "_Requirements:"),
                         encoding="utf-8")
        code, output = self.check()
        self.assertEqual(code, 0, output)

    def test_leaf_task_without_refs_fails(self) -> None:
        spec = self.add_feature()
        self.edit(spec / "tasks.md", "  - _Requisitos: 2.1_\n", "")
        self.assert_fails_with("task 2.3 sin `_Requisitos: ..._`")

    def test_task_referencing_unknown_criterion_fails(self) -> None:
        spec = self.add_feature()
        self.edit(spec / "tasks.md", "_Requisitos: 2.1_", "_Requisitos: 2.1, 9.9_")
        self.assert_fails_with("criterio inexistente 9.9")

    def test_uncovered_criterion_fails(self) -> None:
        spec = self.add_feature()
        tasks = spec / "tasks.md"
        text = tasks.read_text(encoding="utf-8")
        text = text.replace("1.4, 2.1, 2.2, 2.3_", "1.4, 2.1, 2.2_").replace("_Requisitos: 2.2, 2.3_", "_Requisitos: 2.2_")
        text = text.replace("_Requisitos: 1, 2_", "_Requisitos: 1_")
        tasks.write_text(text, encoding="utf-8")
        self.assert_fails_with("criterio 2.3 no está cubierto por ninguna task")

    def test_legacy_spec_only_warns(self) -> None:
        spec = self.add_feature(status="spec_ready")
        (spec / "requirements.md").write_text("# Requirements\n\n## R1\nEl sistema DEBE x.\n", encoding="utf-8")
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("formato legacy", output)


class TestDoneAndTraceability(ProjectCase):
    def test_done_with_unchecked_task_fails(self) -> None:
        spec = self.add_feature(status="done")
        self.write_impl_and_tests()
        self.assert_fails_with("tasks sin marcar: 1, 1.1")

    def test_done_allows_unchecked_optional_task(self) -> None:
        spec = self.add_feature(status="done")
        self.mark_all_tasks_done(spec)
        self.assertIn("- [ ]* 4.", (spec / "tasks.md").read_text(encoding="utf-8"))
        self.write_impl_and_tests()
        code, output = self.check()
        self.assertEqual(code, 0, output)

    def test_done_without_impl_file_fails(self) -> None:
        spec = self.add_feature(status="done")
        self.mark_all_tasks_done(spec)
        self.assert_fails_with("falta progress/impl_cli_recent.md")

    def test_done_with_unmapped_criterion_fails(self) -> None:
        spec = self.add_feature(status="done")
        self.mark_all_tasks_done(spec)
        self.write_impl_and_tests(trace=TRACE_OK.replace("- 2.3 → `test_recent_invalid_limit_zero`\n", ""))
        self.assert_fails_with("criterio 2.3 sin test")

    def test_done_with_nonexistent_test_fails(self) -> None:
        spec = self.add_feature(status="done")
        self.mark_all_tasks_done(spec)
        self.write_impl_and_tests(tests=TESTS_OK.replace("test_recent_empty_outputs_nothing", "test_other"))
        self.assert_fails_with("`test_recent_empty_outputs_nothing`, que no existe")

    def test_in_progress_with_gaps_only_warns(self) -> None:
        spec = self.add_feature(status="in_progress")
        self.write_impl_and_tests(trace="## Trazabilidad\n- 1.1 → `test_recent_default_limit_orders_desc`\n")
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("criterio 2.3 sin test", output)

    def test_done_fully_traced_is_green(self) -> None:
        spec = self.add_feature(status="done")
        self.mark_all_tasks_done(spec)
        self.write_impl_and_tests()
        code, output = self.check()
        self.assertEqual(code, 0, output)

    def test_pytest_style_test_ids_resolve(self) -> None:
        spec = self.add_feature(status="done")
        self.mark_all_tasks_done(spec)
        trace = TRACE_OK.replace("`test_recent_empty_outputs_nothing`",
                                 "`tests/test_cli.py::TestRecent::test_recent_empty_outputs_nothing`")
        self.write_impl_and_tests(trace=trace)
        self.assertEqual(self.check()[0], 0)


class TestImported(ProjectCase):
    def test_imported_format_problems_are_warnings(self) -> None:
        spec = self.add_feature(imported=True)
        self.edit(spec / "requirements.md", "sin escribir en stdout.", "y DEBE no escribir en stdout.")
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("[WARN]  cli_recent: criterio 2.1 tiene 2", output)

    def test_imported_done_without_traceability_is_green(self) -> None:
        spec = self.add_feature(status="done", imported=True)
        self.mark_all_tasks_done(spec)
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("falta progress/impl_cli_recent.md", output)

    def test_same_spec_without_imported_flag_fails(self) -> None:
        spec = self.add_feature()
        self.edit(spec / "requirements.md", "sin escribir en stdout.", "y DEBE no escribir en stdout.")
        self.assertEqual(self.check()[0], 1)


class TestConfigurablePaths(ProjectCase):
    def test_custom_specs_dir_and_feature_list(self) -> None:
        self.edit(self.root / "harness.toml", 'specs_dir = "specs"', 'specs_dir = ".kiro/specs"')
        self.edit(self.root / "harness.toml", 'feature_list = "feature_list.json"', 'feature_list = "sdd.json"')
        (self.root / "feature_list.json").rename(self.root / "sdd.json")
        data = json.loads((self.root / "sdd.json").read_text(encoding="utf-8"))
        data["features"].append({"id": 1, "name": "foo", "status": "spec_ready", "sdd": True})
        (self.root / "sdd.json").write_text(json.dumps(data), encoding="utf-8")
        shutil.copytree(TEMPLATE_SPEC, self.root / ".kiro" / "specs" / "foo")
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("sdd.json leído (1 features)", output)
        self.assertIn("spec foo (spec_ready)", output)


class TestRunTests(ProjectCase):
    def run_full(self, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        original_stdin = sys.stdin
        sys.stdin = io.StringIO('{"tool_input": {"file_path": "src/x.py"}}')
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = harness_check.main(["--root", str(self.root), "--tests-only", *extra])
        finally:
            sys.stdin = original_stdin
        return code, out.getvalue()

    def test_post_hook_does_not_block_on_open_stdin(self) -> None:
        self.use_unittest()
        self.write_failing_test()
        script = self.root / "tools" / "harness_check.py"
        proc = subprocess.Popen([sys.executable, str(script), "--hook", "post"], stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            returncode = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail("el hook se quedó bloqueado esperando stdin")
        finally:
            proc.stdin.close()
        self.assertEqual(returncode, 2)

    def use_unittest(self) -> None:
        self.edit(self.root / "harness.toml", "{python} -m pytest -q", "{python} -m unittest discover -s {tests_dir} -q")

    def test_failing_test_makes_check_red(self) -> None:
        self.use_unittest()
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_x.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.assertEqual(1, 2)\n",
            encoding="utf-8")
        code, output = self.run_full()
        self.assertEqual(code, 1, output)
        self.assertIn("Hay tests rotos", output)

    def write_failing_test(self) -> None:
        (self.root / "tests").mkdir(exist_ok=True)
        (self.root / "tests" / "test_x.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.assertEqual(1, 2)\n",
            encoding="utf-8")

    def set_toml(self, old: str, new: str) -> None:
        self.edit(self.root / "harness.toml", old, new)

    def test_missing_pytest_fails_instead_of_passing_silently(self) -> None:
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_x.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        original = harness_check._has_module
        harness_check._has_module = lambda python, module: False
        try:
            code, output = self.run_full()
        finally:
            harness_check._has_module = original
        self.assertEqual(code, 1, output)
        self.assertIn("pytest no está instalado", output)

    def test_test_files_but_nothing_ran_fails(self) -> None:
        self.use_unittest()
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_x.py").write_text("def test_pytest_style():\n    assert True\n", encoding="utf-8")
        code, output = self.run_full()
        self.assertEqual(code, 1, output)
        self.assertIn("no se ejecutó ninguno", output)

    def test_post_hook_uses_test_fast(self) -> None:
        self.use_unittest()
        self.write_failing_test()
        self.set_toml('test_fast = ""', "test_fast = '{python} -c \"print(1)\"'")
        code, output = self.run_full("--hook", "post")
        self.assertEqual(code, 0, output)

    def test_post_hook_can_be_disabled(self) -> None:
        self.use_unittest()
        self.write_failing_test()
        self.set_toml("post_tests = true", "post_tests = false")
        self.assertEqual(self.run_full("--hook", "post")[0], 0)

    def test_post_hook_skips_non_python_edits(self) -> None:
        self.use_unittest()
        self.write_failing_test()
        out = io.StringIO()
        original_stdin = sys.stdin
        sys.stdin = io.StringIO('{"tool_input": {"file_path": "docs/x.md"}}')
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = harness_check.main(["--root", str(self.root), "--hook", "post"])
        finally:
            sys.stdin = original_stdin
        self.assertEqual(code, 0)

    def test_slow_tests_time_out_as_warning(self) -> None:
        self.use_unittest()
        self.write_failing_test()
        self.set_toml('test_fast = ""', "test_fast = '{python} -c \"import time; time.sleep(5)\"'")
        self.set_toml("post_timeout = 150", "post_timeout = 1")
        self.assertEqual(self.run_full("--hook", "post")[0], 0)

    def test_project_venv_is_preferred(self) -> None:
        bindir = self.root / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
        bindir.mkdir(parents=True)
        exe = bindir / ("python.exe" if sys.platform == "win32" else "python")
        exe.write_text("", encoding="utf-8")
        config = harness_check.load_config(self.root)
        self.assertEqual(harness_check.resolve_python(self.root, config), str(exe))
        self.set_toml('python = ""', 'python = "custom-python"')
        config = harness_check.load_config(self.root)
        self.assertEqual(harness_check.resolve_python(self.root, config), "custom-python")

    def test_post_hook_returns_2_on_red_tests(self) -> None:
        self.use_unittest()
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_x.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.assertEqual(1, 2)\n",
            encoding="utf-8")
        code, output = self.run_full("--hook", "post")
        self.assertEqual(code, 2, output)
        self.assertIn("tests en rojo", output)


class TestTomlFallback(unittest.TestCase):
    def test_subset_parser_reads_installed_config(self) -> None:
        text = '[paths]\nsrc_dir = "app"  # comentario\n[commands]\ntest = "{python} -m pytest -q"\n' \
               '[harness]\nstrict = true\nretries = 3\n'
        data = harness_check._parse_toml_subset(text)
        self.assertEqual(data["paths"]["src_dir"], "app")
        self.assertEqual(data["commands"]["test"], "{python} -m pytest -q")
        self.assertIs(data["harness"]["strict"], True)
        self.assertEqual(data["harness"]["retries"], 3)


if __name__ == "__main__":
    unittest.main()
