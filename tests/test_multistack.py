"""Tests de proyectos multi-stack y del formato de spec SPEC-NNN (caso DashboardTrina)."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness_check = _load("harness_check", KIT / "template" / "tools" / "harness_check.py")
install = _load("install", KIT / "install.py")
HAS_NODE = shutil.which("node") is not None

SPEC = """# SPEC-001: Autenticación

| Campo | Valor |
|---|---|
| Estado | {estado} |
| Versión | 0.1 |
| Aprobadores | {aprobadores} |
| Fecha de aprobación | {fecha} |

## 1. Objetivo
Login.

## 3. Requisitos funcionales
| ID | Requisito | Prioridad |
|---|---|---|
| RF-001-01 | El sitio permite iniciar sesión | Must |
| RF-001-02 | El sitio permite cerrar sesión | Should |

## 7. Criterios de aceptación
**CA-001-01: Login interno**
- **Dado** un usuario interno
- **Cuando** inicia sesión
- **Entonces** entra al sitio

**CA-001-02: Invariante**
- **Dado** un token vencido
- **Entonces** el backend responde 401

## 8. Dependencias
"""
TASKS = """# Tareas — SPEC-001

- [{mark}] 1. Login
- [{mark}] 1.1 Endpoint de login — **api**
  - _Requisitos: RF-001-01, CA-001-01_
- [{mark}] 1.2 Tests de login — **api**
  - _Requisitos: CA-001-01, CA-001-02, RF-001-02_
"""
CLAUDE_PROPIO = "# CLAUDE.md\n\nReglas de confidencialidad del proyecto.\n"


def run_main(*argv: str, stdin: str = "") -> tuple[int, str]:
    out = io.StringIO()
    original = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = harness_check.main(list(argv))
    finally:
        sys.stdin = original
    return code, out.getvalue()


class DashboardLikeCase(unittest.TestCase):
    """Repo en fase de especificación: docs/SDD.md, docs/specs/SPEC-NNN, CLAUDE.md propio, sin código."""

    components = ("api=api:.js,.ts:node -e \"process.exit(0)\"", "sql=sql:.sql")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.write("CLAUDE.md", CLAUDE_PROPIO)
        self.write("docs/SDD.md", "# Software Design Document\n")
        self.write("docs/MEJORES-PRACTICAS-BI.md", "# Mejores prácticas\n")
        self.write("docs/specs/_PLANTILLA-SPEC.md", "# SPEC-NNN: Título\n\n| Estado | Borrador |\n")
        self.set_spec("Borrador")
        flags = ["--python", "python", "--no-check", "--conventions-doc", "docs/MEJORES-PRACTICAS-BI.md"]
        for comp in self.components:
            flags += ["--component", comp]
        self.output = self.install(*flags)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def install(self, *flags: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.install_code = install.main([str(self.root), *flags])
        return out.getvalue()

    def set_spec(self, estado: str, aprobadores: str = "", fecha: str = "") -> None:
        self.write("docs/specs/SPEC-001-autenticacion.md",
                   SPEC.format(estado=estado, aprobadores=aprobadores, fecha=fecha))

    def approve(self) -> None:
        self.set_spec("Aprobada", "Misael, equipo", "2026-10-05")

    def set_status(self, status: str) -> None:
        path = self.root / "feature_list.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["features"][0]["status"] = status
        path.write_text(json.dumps(data), encoding="utf-8")

    def write_tasks(self, done: bool = False) -> None:
        self.write("docs/specs/tareas/SPEC-001-tareas.md", TASKS.format(mark="x" if done else " "))

    def check(self, *extra: str) -> tuple[int, str]:
        return run_main("--root", str(self.root), "--no-tests", *extra)


class TestInstallOnSpecRepo(DashboardLikeCase):
    def test_install_is_allowed_and_configured(self) -> None:
        self.assertEqual(self.install_code, 0, self.output)
        toml = self.read("harness.toml")
        self.assertIn('format = "spec-nnn"', toml)
        self.assertIn('specs_dir = "docs/specs"', toml)
        self.assertIn('tasks_dir = "docs/specs/tareas"', toml)
        self.assertIn('architecture_doc = "docs/SDD.md"', toml)
        self.assertIn("[[components]]", toml)
        config = harness_check.load_config(self.root)
        self.assertEqual([c["path"] for c in harness_check.get_components(config)], ["api", "sql"])

    def test_project_docs_are_untouched(self) -> None:
        self.assertEqual(self.read("docs/SDD.md"), "# Software Design Document\n")
        self.assertEqual(self.read("docs/MEJORES-PRACTICAS-BI.md"), "# Mejores prácticas\n")
        self.assertIn("SDD.md", os.listdir(self.root / "docs"))
        self.assertNotIn("sdd.md", os.listdir(self.root / "docs"))
        self.assertTrue((self.root / "docs" / "harness" / "proceso-sdd.md").is_file())
        self.assertTrue(self.read("CLAUDE.md").startswith(CLAUDE_PROPIO))

    def test_specs_are_imported_as_pending(self) -> None:
        features = json.loads(self.read("feature_list.json"))["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["spec"], "docs/specs/SPEC-001-autenticacion.md")
        self.assertEqual(features[0]["status"], "pending")
        self.assertEqual(features[0]["title"], "Autenticación")

    def test_only_spec_nnn_rules_are_rendered(self) -> None:
        for rel in ("CLAUDE.harness.md", "AGENTS.md", ".claude/agents/leader.md"):
            text = self.read(rel)
            self.assertNotIn("{{", text, rel)
        claude = self.read("CLAUDE.harness.md")
        self.assertIn("No crees specs Kiro", claude)
        self.assertNotIn("entre `spec_ready` e", claude)
        self.assertIn("APRUEBA LA SPEC", self.read("AGENTS.md"))

    def test_agents_mention_components(self) -> None:
        self.assertIn("`api/`, `sql/`", self.read("CLAUDE.harness.md"))
        self.assertIn("SPEC-NNN-tareas.md", self.read(".claude/agents/spec_author.md"))

    def test_fresh_install_is_green(self) -> None:
        code, output = self.check()
        self.assertEqual(code, 0, output)

    def test_upgrade_keeps_project_sdd(self) -> None:
        self.install("--upgrade", "--no-check", "--python", "python")
        self.assertEqual(self.read("docs/SDD.md"), "# Software Design Document\n")


class TestSpecNnnGate(DashboardLikeCase):
    def test_in_progress_without_approval_fails(self) -> None:
        self.write_tasks()
        self.set_status("in_progress")
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn("el código no empieza hasta que la spec esté Aprobada", output)

    def test_approval_needs_date_and_approvers(self) -> None:
        self.set_spec("Aprobada")
        self.write_tasks()
        self.set_status("in_progress")
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn("sin «fecha de aprobación»", output)

    def test_approved_without_tasks_fails(self) -> None:
        self.approve()
        self.set_status("in_progress")
        self.assertIn("falta el plan de tareas", self.check()[1])

    def test_approved_with_tasks_is_green(self) -> None:
        self.approve()
        self.write_tasks()
        self.set_status("in_progress")
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("CA-001-02 sin «Cuando»", output)

    def test_uncovered_criterion_fails(self) -> None:
        self.approve()
        self.write_tasks()
        tasks = self.root / "docs/specs/tareas/SPEC-001-tareas.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace("CA-001-01, CA-001-02", "CA-001-01"),
                         encoding="utf-8")
        self.set_status("in_progress")
        self.assertIn("CA-001-02 no está cubierto", self.check()[1])

    def test_missing_entonces_fails_when_in_progress(self) -> None:
        self.approve()
        spec = self.root / "docs/specs/SPEC-001-autenticacion.md"
        spec.write_text(spec.read_text(encoding="utf-8").replace("- **Entonces** el backend responde 401\n", ""),
                        encoding="utf-8")
        self.write_tasks()
        self.set_status("in_progress")
        self.assertIn("CA-001-02 no tiene Entonces", self.check()[1])

    def test_done_requires_tests_citing_each_ca(self) -> None:
        self.approve()
        self.write_tasks(done=True)
        self.set_status("done")
        self.write("api/test/login.test.js", 'test("CA-001-01: login interno", () => {});\n')
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn("ningún test cita CA-001-02", output)
        self.write("api/test/token.test.ts", 'it("ca_001_02 token vencido", () => {});\n')
        code, output = self.check()
        self.assertEqual(code, 0, output)

    def test_tests_inside_node_modules_do_not_count(self) -> None:
        self.approve()
        self.write_tasks(done=True)
        self.set_status("done")
        self.write("api/node_modules/x/a.test.js", "CA-001-01 CA-001-02\n")
        self.assertEqual(self.check()[0], 1)


class TestCriterionReference(DashboardLikeCase):
    def test_criterion_that_points_to_another_is_accepted(self) -> None:
        self.approve()
        spec = self.root / "docs/specs/SPEC-001-autenticacion.md"
        spec.write_text(spec.read_text(encoding="utf-8").replace(
            "**CA-001-02: Invariante**\n- **Dado** un token vencido\n- **Entonces** el backend responde 401\n",
            "**CA-001-02: Cerrar sesión**\n- Igual que CA-001-01.\n"), encoding="utf-8")
        self.write_tasks(done=True)
        self.set_status("done")
        self.write("api/test/login.test.js", 'test("CA-001-01: login", () => {});\n')
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("CA-001-02 remite a CA-001-01", output)


class TestPreHook(DashboardLikeCase):
    def pre(self, path: str) -> int:
        return run_main("--root", str(self.root), "--hook", "pre",
                        stdin=json.dumps({"tool_input": {"file_path": path}}))[0]

    def set_gate(self, mode: str) -> None:
        toml = self.root / "harness.toml"
        text = re.sub(r'pre_gate = "\w+"', f'pre_gate = "{mode}"', toml.read_text(encoding="utf-8"))
        toml.write_text(text, encoding="utf-8")

    def test_warns_when_editing_code_without_approved_spec(self) -> None:
        self.assertEqual(self.pre("api/src/server.js"), 1)
        self.assertEqual(self.pre("docs/specs/SPEC-001-autenticacion.md"), 0)

    def test_block_and_off_modes(self) -> None:
        self.set_gate("block")
        self.assertEqual(self.pre("sql/001_tablas.sql"), 2)
        self.set_gate("off")
        self.assertEqual(self.pre("sql/001_tablas.sql"), 0)

    def test_allows_when_feature_in_progress_with_approved_spec(self) -> None:
        self.approve()
        self.write_tasks()
        self.set_status("in_progress")
        self.assertEqual(self.pre("api/src/server.js"), 0)


class TestComponents(DashboardLikeCase):
    components = ("api=api:.js:node -e \"process.exit(process.env.ROJO ? 1 : 0)\"", "web=web:.tsx")

    def test_missing_component_is_a_warning(self) -> None:
        code, output = run_main("--root", str(self.root), "--tests-only")
        self.assertEqual(code, 0, output)
        self.assertIn("api/ aún no existe", output)

    @unittest.skipUnless(HAS_NODE, "requiere node")
    def test_node_component_runs_its_tests(self) -> None:
        self.write("api/index.js", "module.exports = 1;\n")
        code, output = run_main("--root", str(self.root), "--tests-only")
        self.assertEqual(code, 0, output)
        self.assertIn("[api] Tests verdes", output)
        os.environ["ROJO"] = "1"
        try:
            code, output = run_main("--root", str(self.root), "--tests-only")
        finally:
            del os.environ["ROJO"]
        self.assertEqual(code, 1, output)
        self.assertIn("[api] Hay tests rotos", output)

    def test_component_for_picks_by_path_and_extension(self) -> None:
        config = harness_check.load_config(self.root)
        comps = harness_check.get_components(config)
        self.assertEqual(harness_check.component_for(self.root, comps, "api/src/a.js")["name"], "api")
        self.assertEqual(harness_check.component_for(self.root, comps, "web/src/App.tsx")["name"], "web")
        self.assertIsNone(harness_check.component_for(self.root, comps, "api/README.md"))
        self.assertIsNone(harness_check.component_for(self.root, comps, "docs/x.js"))

    def test_fingerprint_ignores_node_modules(self) -> None:
        self.write("api/index.js", "x\n")
        config = harness_check.load_config(self.root)
        harness_check.mark_green(self.root, config)
        dep = self.write("api/node_modules/lib/index.js", "y\n")
        later = time.time() + 10
        os.utime(dep, (later, later))
        self.assertTrue(harness_check.code_unchanged_since_green(self.root, config))


class TestNodeDetection(unittest.TestCase):
    def test_package_json_with_vitest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "api").mkdir()
            (root / "api" / "package.json").write_text(json.dumps(
                {"name": "api", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "^2"}}),
                encoding="utf-8")
            comps = install.detect_node_components(root)
            self.assertEqual(comps[0]["path"], "api")
            self.assertEqual(comps[0]["test"], "npm --prefix api test --silent")
            self.assertIn("vitest related {files}", comps[0]["test_related"])

    def test_component_arg(self) -> None:
        comp = install.parse_component_arg("api=api:.js,ts:npm --prefix api test")
        self.assertEqual(comp, {"name": "api", "path": "api", "extensions": [".js", ".ts"],
                                "test": "npm --prefix api test"})


class TestSecretScan(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.root, capture_output=True, stdin=subprocess.DEVNULL)

    def stage(self, rel: str, text: str) -> list[str]:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        return harness_check.scan_staged_secrets(self.root)

    def test_detects_secrets_without_printing_them(self) -> None:
        secret = "AccountKey=" + "Q" * 40
        findings = self.stage("api/config.js", f'const c = "DefaultEndpointsProtocol=https;{secret}";\n')
        self.assertEqual(len(findings), 1)
        self.assertIn("api/config.js:1", findings[0])
        self.assertNotIn("Q" * 10, findings[0])

    def test_connection_string_password(self) -> None:
        self.assertTrue(self.stage("a.py", 'URL = "Server=x;Database=y;Password=Sup3rSecreta;"\n'))

    def test_env_lookup_is_not_a_secret(self) -> None:
        self.assertEqual(self.stage("a.py", 'password = os.environ["DB_PASSWORD"]\nconn(password=pwd)\n'), [])

    def test_random_client_secret_is_detected(self) -> None:
        findings = self.stage("api/src/env.js", 'const clientSecret = "Xq8~Lm2pR7vN4tKz9Wb3Hd6Yc1Fs";\n')
        self.assertEqual(len(findings), 1)
        self.assertIn("secreto o token", findings[0])

    def test_test_fixture_values_are_not_secrets(self) -> None:
        self.assertEqual(self.stage("api/tests/auth.test.js",
                                    'const cfg = { clientSecret: "secreto-de-prueba-largo", apiKey: "aaaaaaaaaaaaaaaaaaaa" };\n'),
                         [])

    def test_forbidden_files(self) -> None:
        self.assertTrue(any(".env" in f for f in self.stage(".env", "X=1\n")))

    def test_env_example_is_allowed(self) -> None:
        self.assertEqual(self.stage(".env.example", "ETL_ARENA_DB_URL=\n"), [])


if __name__ == "__main__":
    unittest.main()
