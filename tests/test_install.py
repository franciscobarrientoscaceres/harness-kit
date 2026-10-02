"""Tests del instalador install.py sobre distintos tipos de proyecto."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
SPEC_EXAMPLE = KIT / "template" / "docs" / "spec-example"

_spec = importlib.util.spec_from_file_location("install", KIT / "install.py")
install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install)

PASSING_TEST = "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
LEGACY_SETTINGS = {
    "hooks": {
        "PostToolUse": [{"matcher": "Edit|Write", "hooks": [
            {"type": "command", "command": "python3 -m unittest discover -s tests -q 2>&1 | tail -3"}]}],
        "Stop": [{"hooks": [
            {"type": "command", "command": "./init.sh > /tmp/harness_init.log 2>&1 && echo ok"},
            {"type": "command", "command": "echo hook-propio"}]}],
    },
    "permissions": {"allow": ["Bash(ls)"]},
}


class InstallCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def install(self, *extra: str, check: bool = False) -> tuple[int, str]:
        out = io.StringIO()
        flags = ["--python", "python", *extra]
        if not check:
            flags.append("--no-check")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = install.main([str(self.root), *flags])
        return code, out.getvalue()

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def features(self, rel: str = "feature_list.json") -> list[dict]:
        return json.loads(self.read(rel))["features"]

    def make_legacy_harness(self) -> None:
        """Proyecto con el arnés original (R<n>, hooks con python3/tail, docs/specs.md)."""
        self.write("CLAUDE.md", "# Instrucciones\n\n## Rol obligatorio: leader\n\nVer `.claude/agents/leader.md`.\n")
        self.write("AGENTS.md", "# Mapa\n\nLee `progress/current.md` y `feature_list.json`.\n")
        for name in install.AGENT_NAMES:
            self.write(f".claude/agents/{name}.md", f"---\nname: {name}\ndescription: viejo\n---\n")
        self.write(".claude/settings.json", json.dumps(LEGACY_SETTINGS))
        self.write("docs/specs.md", "# SDD\n\nEstados: pending, spec_ready. EARS.\n")
        self.write("docs/architecture.md", "# Arquitectura propia\n")
        self.write("CHECKPOINTS.md", "# viejo\n")
        self.write("feature_list.json", json.dumps({"features": [
            {"id": 1, "name": "cli_x", "status": "done", "sdd": True}]}))
        self.write("specs/cli_x/requirements.md", "# R\n\n## R1\nEl sistema DEBE x.\n")
        self.write("specs/cli_x/design.md", "# D\n")
        self.write("specs/cli_x/tasks.md", "- [x] T1 — hacer x. Cubre: R1.\n")
        self.write("tests/test_x.py", PASSING_TEST)


class TestFreshInstall(InstallCase):
    def test_placeholders_are_replaced_everywhere(self) -> None:
        self.install("--name", "demo", "--src", "app")
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn("{{", path.read_text(encoding="utf-8"), path)
        self.assertIn("`app/`", self.read("CLAUDE.md"))

    def test_generated_json_is_valid(self) -> None:
        self.install()
        json.loads(self.read(".claude/settings.json"))
        json.loads(self.read("feature_list.json"))

    def test_init_sh_has_lf_line_endings(self) -> None:
        self.install()
        self.assertNotIn(b"\r\n", (self.root / "init.sh").read_bytes())

    def test_install_runs_check_and_is_green(self) -> None:
        code, output = self.install(check=True)
        self.assertEqual(code, 0, output)
        self.assertIn("Entorno listo", output)

    def test_second_run_changes_nothing(self) -> None:
        self.install()
        _, output = self.install()
        for action in ("creado", "actualizado", "fusionado", "referencia", "añadido", "reemplazado"):
            self.assertNotIn(f"  {action} ", output)

    def test_dry_run_writes_nothing(self) -> None:
        self.install("--dry-run")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_upgrade_requires_existing_install(self) -> None:
        code, output = self.install("--upgrade")
        self.assertEqual(code, 2)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_refuses_to_install_into_kit(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.assertEqual(install.main([str(KIT), "--no-check"]), 1)


class TestExistingProjectFiles(InstallCase):
    def test_foreign_claude_md_is_preserved_with_pointer(self) -> None:
        self.write("CLAUDE.md", "# Mis reglas\n")
        self.install()
        claude = self.read("CLAUDE.md")
        self.assertTrue(claude.startswith("# Mis reglas\n"))
        self.assertIn("@CLAUDE.harness.md", claude)
        self.assertIn("Rol obligatorio: leader", self.read("CLAUDE.harness.md"))
        _, output = self.install()
        self.assertEqual(self.read("CLAUDE.md"), claude)
        self.assertNotIn("referencia", output)

    def test_foreign_claude_md_is_not_replaced_even_with_adopt(self) -> None:
        self.write("CLAUDE.md", "# Mis reglas\n")
        self.install("--adopt")
        self.assertTrue(self.read("CLAUDE.md").startswith("# Mis reglas\n"))

    def test_existing_settings_are_merged(self) -> None:
        self.write(".claude/settings.json", json.dumps({"model": "x", "permissions": {"allow": ["Bash(ls)"]}}))
        self.install()
        merged = json.loads(self.read(".claude/settings.json"))
        self.assertEqual(merged["model"], "x")
        self.assertIn("Bash(ls)", merged["permissions"]["allow"])
        self.install("--python", "python3")
        again = json.loads(self.read(".claude/settings.json"))
        self.assertEqual(len(again["hooks"]["PostToolUse"]), 1)
        self.assertEqual(len(again["hooks"]["Stop"]), 1)

    def test_existing_docs_are_kept(self) -> None:
        self.write("docs/architecture.md", "# Mía\n")
        self.install()
        self.assertEqual(self.read("docs/architecture.md"), "# Mía\n")

    def test_incompatible_feature_list_is_left_alone(self) -> None:
        original = json.dumps([{"description": "x", "passes": False}])
        self.write("feature_list.json", original)
        code, output = self.install(check=True)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.read("feature_list.json"), original)
        self.assertEqual(self.features("sdd_features.json"), [])
        self.assertIn('feature_list = "sdd_features.json"', self.read("harness.toml"))
        self.assertIn("sdd_features.json", self.read("CLAUDE.md"))

    def test_rerun_keeps_configured_paths(self) -> None:
        self.install("--specs-dir", "docs/specs", "--tests", "t")
        _, output = self.install()
        self.assertIn('specs_dir = "docs/specs"', self.read("harness.toml"))
        self.assertIn("SPECS_DIR        = docs/specs", output)

    def test_overlapping_agents_are_reported(self) -> None:
        self.write(".claude/agents/code-reviewer.md", "---\nname: code-reviewer\ndescription: Reviews code\n---\n")
        self.write(".claude/agents/translator.md", "---\nname: translator\ndescription: Traduce textos\n---\n")
        _, output = self.install()
        self.assertIn("code-reviewer  ← mismo rol", output)
        self.assertIn("agentes especialistas del proyecto: translator", output)
        self.assertNotIn("elimina", output)

    def test_domain_specialists_are_not_flagged(self) -> None:
        self.write(".claude/agents/database-optimizer.md",
                   "---\nname: database-optimizer\ndescription: Usar para tests y planes de índices\n---\n")
        _, output = self.install()
        self.assertIn("especialistas del proyecto: database-optimizer", output)
        self.assertNotIn("mismo rol", output)

    def test_upgrade_updates_kit_files_only(self) -> None:
        self.install()
        self.write("feature_list.json", '{"features": [{"id": 1, "name": "a", "status": "pending"}]}')
        self.write("tools/harness_check.py", "# viejo\n")
        self.install("--upgrade")
        self.assertIn('"name": "a"', self.read("feature_list.json"))
        self.assertNotEqual(self.read("tools/harness_check.py"), "# viejo\n")


class TestAdoptLegacyHarness(InstallCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_legacy_harness()

    def test_default_install_stops_and_explains(self) -> None:
        code, output = self.install()
        self.assertEqual(code, 2)
        self.assertIn("--adopt", output)
        self.assertFalse((self.root / "harness.toml").exists())

    def test_adopt_replaces_harness_files_with_backup(self) -> None:
        code, output = self.install("--adopt", check=True)
        self.assertEqual(code, 0, output)
        self.assertIn("<!-- harness-kit -->", self.read("CLAUDE.md"))
        self.assertIn("<!-- harness-kit -->", self.read("AGENTS.md"))
        self.assertIn("Spec Driven Development (Kiro-style)", self.read("CHECKPOINTS.md"))
        self.assertIn("modo migración", self.read(".claude/agents/spec_author.md").lower())
        self.assertFalse((self.root / "docs/specs.md").exists())
        self.assertEqual(self.read("docs/architecture.md"), "# Arquitectura propia\n")
        backups = list((self.root / ".harness-backup").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertIn("Rol obligatorio", (backups[0] / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertTrue((backups[0] / "docs" / "specs.md").exists())

    def test_adopt_removes_legacy_hooks_and_keeps_foreign_ones(self) -> None:
        self.install("--adopt")
        settings = json.loads(self.read(".claude/settings.json"))
        commands = [h["command"] for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]
        self.assertNotIn(LEGACY_SETTINGS["hooks"]["PostToolUse"][0]["hooks"][0]["command"], commands)
        self.assertFalse(any("/tmp/harness_init" in c for c in commands))
        self.assertIn("echo hook-propio", commands)
        self.assertEqual(sum("--hook stop" in c for c in commands), 1)

    def test_adopt_keeps_features_and_reports_legacy_specs(self) -> None:
        _, output = self.install("--adopt")
        self.assertEqual(self.features()[0]["name"], "cli_x")
        self.assertIn("migra los specs legacy", output)
        self.assertIn("Spec legacy pendiente de migrar: cli_x", self.read("progress/history.md"))

    def test_adopt_is_idempotent(self) -> None:
        self.install("--adopt")
        _, output = self.install("--adopt")
        for action in ("creado", "actualizado", "reemplazado", "retirado", "importado"):
            self.assertNotIn(f"  {action} ", output)

    def test_keep_existing_installs_beside_without_touching(self) -> None:
        claude = self.read("CLAUDE.md")
        code, output = self.install("--keep-existing")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.read(".claude/agents/leader.md"), "---\nname: leader\ndescription: viejo\n---\n")
        self.assertTrue(self.read("CLAUDE.md").startswith(claude))
        self.assertIn("hooks de un arnés anterior", output)


class TestKiroAndSpecKit(InstallCase):
    def add_kiro_spec(self, name: str, done: bool = False) -> None:
        shutil.copytree(SPEC_EXAMPLE, self.root / ".kiro" / "specs" / name)
        if done:
            tasks = self.root / ".kiro" / "specs" / name / "tasks.md"
            tasks.write_text(tasks.read_text(encoding="utf-8").replace("- [ ] ", "- [x] "), encoding="utf-8")

    def test_kiro_specs_are_imported(self) -> None:
        self.add_kiro_spec("user-auth")
        self.add_kiro_spec("billing", done=True)
        code, output = self.install(check=True)
        self.assertEqual(code, 0, output)
        self.assertIn('specs_dir = ".kiro/specs"', self.read("harness.toml"))
        by_name = {f["name"]: f for f in self.features()}
        self.assertEqual(by_name["user-auth"]["status"], "spec_ready")
        self.assertEqual(by_name["billing"]["status"], "done")
        self.assertTrue(all(f["imported"] for f in by_name.values()))
        self.assertIn(".kiro/specs/<name>/", self.read(".claude/agents/spec_author.md"))

    def test_started_kiro_spec_is_imported_in_progress(self) -> None:
        self.add_kiro_spec("etl")
        tasks = self.root / ".kiro" / "specs" / "etl" / "tasks.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace("- [ ] 1.1", "- [x] 1.1"), encoding="utf-8")
        self.add_kiro_spec("otro")
        tasks2 = self.root / ".kiro" / "specs" / "otro" / "tasks.md"
        tasks2.write_text(tasks2.read_text(encoding="utf-8").replace("- [ ] 1.1", "- [x] 1.1"), encoding="utf-8")
        code, output = self.install(check=True)
        self.assertEqual(code, 0, output)
        statuses = sorted(f["status"] for f in self.features())
        self.assertEqual(statuses, ["in_progress", "spec_ready"])
        self.assertIn("/12 tasks hechas", output)

    def test_kiro_import_is_idempotent(self) -> None:
        self.add_kiro_spec("user-auth")
        self.install()
        self.install()
        self.assertEqual(len(self.features()), 1)

    def test_spec_kit_project_uses_separate_specs_dir(self) -> None:
        self.write(".specify/memory/constitution.md", "# c\n")
        self.write("specs/001-login/spec.md", "# spec-kit\n")
        code, output = self.install(check=True)
        self.assertEqual(code, 0, output)
        self.assertIn('specs_dir = ".kiro/specs"', self.read("harness.toml"))
        self.assertEqual(self.features(), [])
        self.assertEqual(self.read("specs/001-login/spec.md"), "# spec-kit\n")


class TestDetection(InstallCase):
    def test_detects_flat_package_layout(self) -> None:
        self.write("mypkg/__init__.py", "")
        self.assertEqual(install.detect_src(self.root), "mypkg")

    def test_detects_name_from_pyproject(self) -> None:
        self.write("pyproject.toml", '[project]\nname = "cool-app"\n')
        self.assertEqual(install.detect_name(self.root), "cool-app")

    def test_detects_unittest_projects(self) -> None:
        self.write("tests/test_a.py", "import unittest\n")
        self.assertIn("unittest", install.detect_test_cmd(self.root, "tests"))

    def test_detects_pytest_projects(self) -> None:
        self.write("pyproject.toml", "[tool.pytest.ini_options]\n")
        self.assertIn("pytest", install.detect_test_cmd(self.root, "tests"))

    def test_detects_venv(self) -> None:
        self.write(".venv/pyvenv.cfg", "home = x\n")
        self.assertEqual(install.inspect_project(self.root)["venv"], ".venv")


if __name__ == "__main__":
    unittest.main()
