"""Instala el arnés multiagente + SDD Kiro-style de harness-kit en un proyecto existente.

Uso:
    python install.py <ruta-proyecto> [opciones]

Modos:
    (por defecto)     Instala sin pisar nada. Si detecta OTRO arnés de agentes/SDD
                      en el proyecto, se detiene y explica cómo adoptarlo.
    --adopt           Migra un arnés previo: reemplaza sus agentes, CLAUDE.md/AGENTS.md
                      del arnés, docs de proceso y hooks antiguos. Todo lo que
                      reemplaza o retira se copia antes a .harness-backup/<fecha>/.
    --upgrade         Actualiza un harness-kit ya instalado (solo archivos del kit).
    --keep-existing   Instala junto a un arnés previo sin tocarlo (no recomendado).
    --force           Sobrescribe todo (con backup).

Siempre: fusiona .claude/settings.json, añade líneas faltantes a .gitignore y
.gitattributes, importa a la lista de features los specs que ya existían y, al
terminar, ejecuta la verificación del arnés.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KIT_VERSION = "0.3.0"
KIT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = KIT_DIR / "template"
MARKER = "<!-- harness-kit -->"
AGENT_NAMES = ("leader", "spec_author", "implementer", "reviewer")
BACKUP_DIR = ".harness-backup"
ALT_FEATURE_LIST = "sdd_features.json"

KIT_OWNED = {
    "tools/harness_check.py",
    "init.sh",
    "init.ps1",
    "docs/harness/proceso-sdd.md",
    "docs/harness/verificacion.md",
    "docs/harness/ejemplo-spec/requirements.md",
    "docs/harness/ejemplo-spec/design.md",
    "docs/harness/ejemplo-spec/tasks.md",
    *(f".claude/agents/{name}.md" for name in AGENT_NAMES),
}
ADOPT_REPLACEABLE = {"CHECKPOINTS.md"}
# Docs de proceso de versiones anteriores (del kit o del arnés original) que ahora viven en
# docs/harness/. Solo se retiran si el contenido lo confirma (firma) y el nombre coincide
# exactamente, también en mayúsculas: en Windows docs/sdd.md y docs/SDD.md son el mismo archivo.
LEGACY_DOCS = {
    "docs/specs.md": ("spec_ready",),
    "docs/sdd.md": ("Spec Driven Development (SDD) — formato Kiro",),
    "docs/verification.md": ("el agente no dice \"funciona\", lo demuestra",),
    "docs/spec-example/requirements.md": ("Ejemplo canónico del formato Kiro",),
    "docs/spec-example/design.md": ("# Design — cli_recent",),
    "docs/spec-example/tasks.md": ("# Tasks — cli_recent",),
    "specs/_template/requirements.md": ("Ejemplo canónico del formato Kiro",),
    "specs/_template/design.md": ("# Design — cli_recent",),
    "specs/_template/tasks.md": ("# Tasks — cli_recent",),
}
CONDITIONAL_BLOCK = re.compile(r"\{\{#([\w-]+)\}\}\n(.*?)\{\{/\1\}\}\n", re.DOTALL)
RAW_VALUES = {"COMPONENTS", "CODE_LAYOUT"}  # bloques ya formateados: no se escapan
JS_EXTENSIONS = [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"]
SPEC_NNN_FILE = re.compile(r"^SPEC-\d{3}-.+\.md$")
ARCH_CANDIDATES = ("docs/SDD.md", "docs/ARCHITECTURE.md", "ARCHITECTURE.md", "docs/arquitectura.md")
CONV_CANDIDATES = ("docs/CONVENTIONS.md", "CONTRIBUTING.md", "docs/convenciones.md")
DEFAULT_ARCH_DOC = "docs/architecture.md"
DEFAULT_CONV_DOC = "docs/conventions.md"
SIDECAR_WITH_POINTER = {
    "CLAUDE.md": "\n{marker}\n@CLAUDE.harness.md\n",
    "AGENTS.md": "\n{marker}\n> **Arnés SDD (harness-kit):** el flujo multiagente y Spec Driven Development "
                 "de este repo está descrito en [`AGENTS.harness.md`](AGENTS.harness.md). Léelo primero.\n",
}
GITIGNORE_LINES = ("__pycache__/", ".pytest_cache/", "*.tmp", f"{BACKUP_DIR}/", ".harness-cache/")
GITATTRIBUTES_LINES = ("*.sh text eol=lf",)
# Solo el NOMBRE decide si un agente propio duplica un rol del arnés; los demás son
# especialistas que el leader usa cuando tasks.md se los asigna.
OVERLAP_NAMES = re.compile(r"review|revis|leader|lider|líder|orchestr|orquest|implement|spec[-_]?author|planner",
                           re.IGNORECASE)


def _load_checker():
    spec = importlib.util.spec_from_file_location("harness_check", TEMPLATE_DIR / "tools" / "harness_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


# ── Detección del proyecto destino ───────────────────────────────────────────

def is_harness_file(rel: str, text: str) -> bool:
    """¿Este CLAUDE.md / AGENTS.md / doc pertenece a un arnés de agentes (el nuestro u otro)?"""
    if MARKER in text:
        return True
    if rel == "CLAUDE.md":
        return ".claude/agents/leader.md" in text or "Rol obligatorio: leader" in text
    if rel == "AGENTS.md":
        return "progress/current.md" in text and "feature_list" in text
    if rel == "docs/specs.md":
        return "spec_ready" in text and "EARS" in text
    return False


def classify_hook(command: str) -> str:
    if "--hook" in command and ("harness_check.py" in command or "init.sh" in command):
        return "ours"
    if re.search(r"\binit\.sh\b", command) or "/tmp/harness_init" in command:
        return "legacy"
    if "unittest discover" in command and "tail" in command:
        return "legacy"
    return "foreign"


def feature_list_state(path: Path) -> str:
    if not path.exists():
        return "missing"
    try:
        data = json.loads(_read(path))
    except ValueError:
        return "incompatible"
    if isinstance(data, dict) and isinstance(data.get("features"), list):
        return "compatible"
    return "incompatible"


def spec_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        d for d in root.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_"))
        and all((d / f).is_file() for f in checker.SPEC_FILES)
    )


def is_legacy_spec(spec_dir: Path) -> bool:
    text = _read(spec_dir / "requirements.md")
    return not checker.REQ_HEADING.search(text) and bool(checker.LEGACY_REQ_HEADING.search(text))


def agent_description(path: Path) -> str:
    match = re.search(r"^description:\s*(.+)$", _read(path), re.MULTILINE)
    return match.group(1).strip() if match else ""


def looks_like_code_project(target: Path) -> bool:
    """¿Hay un proyecto Python con código y/o tests? (scripts sueltos de apoyo no cuentan)."""
    if any((target / f).is_file() for f in ("pyproject.toml", "setup.py", "setup.cfg")):
        return True
    if (target / "src").is_dir() and any((target / "src").rglob("*.py")):
        return True
    for tests in ("tests", "test"):
        if (target / tests).is_dir() and any((target / tests).rglob("test*.py")):
            return True
    return any(
        p.is_dir() and (p / "__init__.py").is_file() and not p.name.startswith(".")
        for p in target.iterdir()
    )


def inspect_project(target: Path) -> dict:
    harness_toml = target / "harness.toml"
    existing_config = checker.load_config(target) if harness_toml.is_file() else None
    agents_dir = target / ".claude" / "agents"
    same_name = [n for n in AGENT_NAMES if (agents_dir / f"{n}.md").is_file()]
    other_agents = []
    if agents_dir.is_dir():
        for path in sorted(agents_dir.glob("*.md")):
            if path.stem in AGENT_NAMES:
                continue
            description = agent_description(path)
            overlaps = bool(OVERLAP_NAMES.search(path.stem))
            other_agents.append({"name": path.stem, "description": description, "overlaps": overlaps})

    kiro_specs = spec_dirs(target / ".kiro" / "specs")
    spec_kit = (target / ".specify").is_dir() or any((target / "specs").glob("*/spec.md"))
    legacy_specs = [d.name for d in spec_dirs(target / "specs") if is_legacy_spec(d)]
    fl_state = feature_list_state(target / "feature_list.json")

    foreign_signals = []
    if existing_config is None:
        if same_name:
            foreign_signals.append(f"agentes con el mismo nombre: {', '.join(same_name)}")
        if is_harness_file("CLAUDE.md", _read(target / "CLAUDE.md")):
            foreign_signals.append("CLAUDE.md de un arnés de agentes")
        if fl_state == "compatible":
            foreign_signals.append("feature_list.json con el mismo esquema")
        if legacy_specs:
            foreign_signals.append(f"specs en formato R<n>: {', '.join(legacy_specs)}")

    legacy_hooks = []
    settings = target / ".claude" / "settings.json"
    if settings.is_file():
        try:
            data = json.loads(_read(settings) or "{}")
        except ValueError:
            data = {}
        for event, groups in (data.get("hooks") or {}).items():
            for group in groups:
                for hook in group.get("hooks", []):
                    if classify_hook(hook.get("command", "")) == "legacy":
                        legacy_hooks.append(f"{event}: {hook['command']}")

    venv = next((v for v in checker.VENV_DIRS if (target / v / "pyvenv.cfg").is_file()), None)
    return {
        "existing_config": existing_config,
        "ours": existing_config is not None,
        "foreign_signals": foreign_signals,
        "other_agents": other_agents,
        "kiro_specs": [d.name for d in kiro_specs],
        "spec_kit": spec_kit,
        "legacy_specs": legacy_specs,
        "feature_list_state": fl_state,
        "legacy_hooks": legacy_hooks,
        "venv": venv,
        "is_code": looks_like_code_project(target),
        "spec_nnn_dir": detect_spec_nnn(target),
        "node_components": detect_node_components(target),
    }


def detect_name(target: Path) -> str:
    match = re.search(r'^\s*name\s*=\s*"([^"]+)"', _read(target / "pyproject.toml"), re.MULTILINE)
    return match.group(1) if match else target.name


def detect_src(target: Path) -> str:
    if (target / "src").is_dir():
        return "src"
    packages = [
        p.name for p in target.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
        and p.name not in ("tests", "test", "docs") and not p.name.startswith(".")
    ]
    return packages[0] if len(packages) == 1 else "src"


def detect_tests(target: Path) -> str:
    for candidate in ("tests", "test"):
        if (target / candidate).is_dir():
            return candidate
    return "tests"


def detect_test_cmd(target: Path, tests_dir: str) -> str:
    signals = [
        (target / "pytest.ini").exists(),
        (target / "conftest.py").exists(),
        (target / tests_dir / "conftest.py").exists(),
        "pytest" in _read(target / "pyproject.toml"),
        "pytest" in _read(target / "setup.cfg"),
        "pytest" in _read(target / "tox.ini"),
        any("pytest" in _read(p) for p in target.glob("requirements*.txt")),
    ]
    if any(signals):
        return "{python} -m pytest -q"
    tests_path = target / tests_dir
    if tests_path.is_dir() and any("import unittest" in _read(p) for p in tests_path.rglob("test*.py")):
        return "{python} -m unittest discover -s {tests_dir} -q"
    return "{python} -m pytest -q"


def detect_python_cmd() -> str:
    candidates = ("python", "py", "python3") if os.name == "nt" else ("python3", "python")
    for candidate in candidates:
        try:
            result = subprocess.run([candidate, "-c", "import sys"], capture_output=True, timeout=10,
                                    stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return candidate
    return "python"


def _exact_file(target: Path, rel: str) -> bool:
    path = target / rel
    return path.is_file() and path.name in os.listdir(path.parent)


def detect_spec_nnn(target: Path) -> str:
    """Carpeta con specs SPEC-NNN-*.md (con campo Estado), o "" si no hay."""
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if d not in checker.IGNORED_DIRS and not d.startswith(".")]
        if Path(dirpath).relative_to(target).parts[:1] in (("node_modules",),):
            continue
        for name in filenames:
            if SPEC_NNN_FILE.match(name) and "| Estado |" in _read(Path(dirpath, name)):
                return Path(dirpath).relative_to(target).as_posix()
    return ""


def detect_node_components(target: Path) -> list[dict]:
    components = []
    candidates = [target] + sorted(p for p in target.iterdir() if p.is_dir() and p.name not in checker.IGNORED_DIRS
                                   and not p.name.startswith("."))
    for base in candidates:
        manifest = base / "package.json"
        if not manifest.is_file():
            continue
        try:
            package = json.loads(_read(manifest))
        except ValueError:
            continue
        rel = "." if base == target else base.name
        deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        prefix = "" if rel == "." else f" --prefix {rel}"
        component = {"name": package.get("name") or rel, "path": rel, "extensions": JS_EXTENSIONS,
                     "test": f"npm{prefix} test --silent" if "test" in package.get("scripts", {}) else ""}
        if "vitest" in deps:
            component["test_related"] = f"npm{prefix} exec -- vitest related {{files}} --run"
        elif "jest" in deps:
            component["test_related"] = f"npm{prefix} exec -- jest --findRelatedTests {{files}}"
        components.append(component)
    return components


def parse_component_arg(spec: str) -> dict:
    """`nombre=ruta[:ext1,ext2[:comando de tests]]`"""
    name, _, rest = spec.partition("=")
    parts = rest.split(":", 2)
    path = (parts[0] or name).strip().strip("/")
    exts = [e.strip() for e in parts[1].split(",") if e.strip()] if len(parts) > 1 else []
    return {"name": name.strip(), "path": path,
            "extensions": [e if e.startswith(".") else f".{e}" for e in exts],
            "test": parts[2].strip() if len(parts) > 2 else ""}


def render_components(components: list[dict]) -> str:
    blocks = []
    for comp in components:
        lines = ["[[components]]"]
        for key in ("name", "path", "extensions", "tests_dir", "test", "test_fast", "test_related"):
            value = comp.get(key)
            if value in (None, "", []):
                continue
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def resolve_values(target: Path, args: argparse.Namespace, profile: dict) -> dict[str, str]:
    """Prioridad: flags > harness.toml existente > detección."""
    cfg = profile["existing_config"]
    paths = cfg["paths"] if cfg else {}
    commands = cfg["commands"] if cfg else {}

    tests_dir = args.tests or paths.get("tests_dir") or detect_tests(target)
    if args.specs_dir or (cfg and paths.get("specs_dir")):
        specs_dir = args.specs_dir or paths["specs_dir"]
    elif profile["kiro_specs"] or profile["spec_kit"]:
        specs_dir = ".kiro/specs"
    else:
        specs_dir = "specs"
    if args.feature_list or (cfg and paths.get("feature_list")):
        feature_list = args.feature_list or paths["feature_list"]
    elif profile["feature_list_state"] == "incompatible":
        feature_list = ALT_FEATURE_LIST
    else:
        feature_list = "feature_list.json"
    test_cmd = args.test_cmd or commands.get("test") or detect_test_cmd(target, tests_dir)
    python_cmd = args.python or detect_python_cmd()

    spec_nnn_dir = profile["spec_nnn_dir"]
    spec_cfg = (cfg or {}).get("spec", {})
    spec_format = args.spec_format or spec_cfg.get("format") or ("spec-nnn" if spec_nnn_dir else "kiro")
    if spec_format == "spec-nnn" and not (args.specs_dir or (cfg and paths.get("specs_dir"))):
        specs_dir = spec_nnn_dir or "docs/specs"
    tasks_dir = spec_cfg.get("tasks_dir") or (f"{specs_dir.rstrip('/')}/tareas" if spec_format == "spec-nnn" else "")

    if args.component:
        components = [parse_component_arg(c) for c in args.component]
    elif cfg and cfg.get("components"):
        components = checker.get_components(cfg)
    else:
        components = profile["node_components"]
    src_dir = args.src or paths.get("src_dir") or detect_src(target)
    if components:
        code_dirs = ", ".join(f"`{c['path']}/`" for c in components)
        code_layout = "".join(f"├── {c['path']}/".ljust(26) + f"# componente {c['name']}\n" for c in components)
        test_display = "./init.sh --tests-only"
    else:
        code_dirs = f"`{src_dir}/` y `{tests_dir}/`"
        code_layout = (f"├── {src_dir}/".ljust(26) + "# Código de la aplicación\n"
                       + f"├── {tests_dir}/".ljust(26) + "# Tests\n")
        test_display = test_cmd.replace("{python}", python_cmd).replace("{tests_dir}", tests_dir)
    arch_doc = args.architecture_doc or paths.get("architecture_doc") or next(
        (c for c in ARCH_CANDIDATES if _exact_file(target, c)), DEFAULT_ARCH_DOC)
    conv_doc = args.conventions_doc or paths.get("conventions_doc") or next(
        (c for c in CONV_CANDIDATES if _exact_file(target, c)), DEFAULT_CONV_DOC)
    return {
        "PROJECT_NAME": args.name or (cfg or {}).get("project", {}).get("name") or detect_name(target),
        "KIT_VERSION": KIT_VERSION,
        "SRC_DIR": src_dir,
        "TESTS_DIR": tests_dir,
        "SPECS_DIR": specs_dir.rstrip("/"),
        "ARCH_DOC": arch_doc,
        "CONV_DOC": conv_doc,
        "SPEC_FORMAT": spec_format,
        "TASKS_DIR": tasks_dir,
        "COMPONENTS": render_components(components),
        "CODE_DIRS": code_dirs,
        "CODE_LAYOUT": code_layout,
        "FEATURE_LIST": feature_list,
        "TEST_CMD": test_cmd,
        "TEST_CMD_DISPLAY": test_display,
        "PYTHON_CMD": python_cmd,
        "INSTALL_DATE": datetime.date.today().isoformat(),
    }


# ── Instalación ──────────────────────────────────────────────────────────────

class Installer:
    def __init__(self, target: Path, values: dict[str, str], mode: str, dry_run: bool,
                 ours: bool = False) -> None:
        self.target = target
        self.ours = ours
        self.values = values
        self.mode = mode
        self.dry_run = dry_run
        self.log: list[tuple[str, str]] = []
        self.backup_root = target / BACKUP_DIR / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.created: set[str] = set()
        self.notes: list[str] = []

    @property
    def changed(self) -> bool:
        return any(action not in ("sin cambios", "omitido") for action, _ in self.log)

    def record(self, action: str, rel: str) -> None:
        self.log.append((action, rel))
        print(f"  {action:<12} {rel}")

    def dest_rel(self, rel: str) -> str:
        mapping = {
            "feature_list.json": self.values["FEATURE_LIST"],
            DEFAULT_ARCH_DOC: self.values["ARCH_DOC"],
            DEFAULT_CONV_DOC: self.values["CONV_DOC"],
        }
        return mapping.get(rel, rel)

    def is_project_doc(self, rel: str) -> bool:
        """Documento propio del proyecto elegido como arquitectura/convenciones: nunca se pisa."""
        custom = {self.values["ARCH_DOC"], self.values["CONV_DOC"]} - {DEFAULT_ARCH_DOC, DEFAULT_CONV_DOC}
        return rel in custom

    def render(self, rel: str, text: str) -> str:
        # Bloques condicionales por formato de spec: {{#kiro}}…{{/kiro}}, {{#spec-nnn}}…{{/spec-nnn}}
        fmt = self.values.get("SPEC_FORMAT", "kiro")
        text = CONDITIONAL_BLOCK.sub(lambda m: m.group(2) if m.group(1) == fmt else "", text)
        suffix = Path(rel).suffix
        for key, value in self.values.items():
            if suffix in (".json", ".toml") and key not in RAW_VALUES:
                value = json.dumps(value)[1:-1]
            text = text.replace("{{" + key + "}}", value)
        if suffix == ".sh":
            text = text.replace("\r\n", "\n")
        return text

    def backup(self, rel: str) -> None:
        src = self.target / rel
        if self.dry_run or not src.exists():
            return
        dest = self.backup_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    def write(self, rel: str, text: str) -> None:
        if self.dry_run:
            return
        path = self.target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if rel.endswith(".sh") and os.name != "nt":
            path.chmod(path.stat().st_mode | 0o111)

    def replace(self, rel: str, text: str, action: str = "actualizado") -> None:
        self.backup(rel)
        self.write(rel, text)
        self.record(action, rel)

    def append(self, rel: str, text: str) -> None:
        if self.dry_run:
            return
        path = self.target / rel
        existing = _read(path) if path.exists() else ""
        separator = "" if not existing or existing.endswith("\n") else "\n"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(existing + separator + text)

    def install_file(self, rel: str, text: str) -> None:
        dest = self.target / rel
        if not dest.exists():
            self.write(rel, text)
            self.created.add(rel)
            self.record("creado", rel)
            return
        current = _read(dest)
        if current == text:
            self.record("sin cambios", rel)
            return
        if rel == ".claude/settings.json":
            self.merge_settings(rel, current, text)
            return
        if self.is_project_doc(rel):
            self.record("sin cambios", f"{rel} (documento del proyecto)")
            return
        if rel in SIDECAR_WITH_POINTER and not is_harness_file(rel, current):
            # Un CLAUDE.md / AGENTS.md escrito por el usuario nunca se reemplaza, ni con --force.
            self.install_root_doc(rel, current, text)
            return
        if self.mode == "force":
            self.replace(rel, text)
            return
        if rel in KIT_OWNED and self.mode in ("upgrade", "adopt"):
            self.replace(rel, text)
            return
        if rel in ADOPT_REPLACEABLE and self.mode == "adopt" and not self.ours:
            self.replace(rel, text, "reemplazado")
            return
        if rel in SIDECAR_WITH_POINTER:
            self.install_root_doc(rel, current, text)
            return
        hint = "es del proyecto; se conserva" if self.mode in ("upgrade", "adopt") else "ya existe; se conserva"
        self.record("omitido", f"{rel} ({hint})")

    def install_root_doc(self, rel: str, current: str, text: str) -> None:
        sidecar = rel.replace(".md", ".harness.md")
        if MARKER in current:
            if (self.target / sidecar).exists() and self.mode in ("upgrade", "adopt"):
                if _read(self.target / sidecar) != text:
                    self.replace(sidecar, text)
                    return
            self.record("sin cambios", f"{rel} (ya es del arnés)")
            return
        if self.mode == "adopt" and is_harness_file(rel, current):
            self.replace(rel, text, "reemplazado")
            return
        self.write(sidecar, text)
        self.record("creado", f"{sidecar} (tu {rel} se conserva)")
        self.backup(rel)
        self.append(rel, SIDECAR_WITH_POINTER[rel].format(marker=MARKER))
        self.record("referencia", f"{rel} → {sidecar}")

    def merge_settings(self, rel: str, current: str, text: str) -> None:
        try:
            existing = json.loads(current or "{}")
        except ValueError:
            self.record("omitido", f"{rel} (JSON inválido; fusiona a mano los hooks del kit)")
            self.notes.append(f"{rel} no es JSON válido: copia a mano los hooks de template/.claude/settings.json")
            return
        before = json.dumps(existing, sort_keys=True)
        ours = json.loads(text)
        hooks = existing.setdefault("hooks", {})
        removed_legacy = []
        for event in list(hooks):
            kept_groups = []
            for group in hooks[event]:
                kept = []
                for hook in group.get("hooks", []):
                    kind = classify_hook(hook.get("command", ""))
                    if kind == "ours":
                        continue
                    if kind == "legacy" and self.mode in ("adopt", "force"):
                        removed_legacy.append(f"{event}: {hook['command']}")
                        continue
                    kept.append(hook)
                if kept:
                    kept_groups.append({**group, "hooks": kept})
            if kept_groups:
                hooks[event] = kept_groups
            else:
                del hooks[event]
        for event, groups in ours.get("hooks", {}).items():
            hooks.setdefault(event, []).extend(groups)
        allow = existing.setdefault("permissions", {}).setdefault("allow", [])
        for rule in ours.get("permissions", {}).get("allow", []):
            if rule not in allow:
                allow.append(rule)
        if json.dumps(existing, sort_keys=True) == before:
            self.record("sin cambios", rel)
            return
        self.replace(rel, json.dumps(existing, indent=2, ensure_ascii=False) + "\n", "fusionado")
        for hook in removed_legacy:
            self.record("hook retirado", hook[:90])

    def retire_legacy_docs(self) -> None:
        """En adopt/upgrade/force: retira docs de proceso que ahora viven en docs/harness/ (con backup)."""
        if self.mode not in ("adopt", "upgrade", "force"):
            return
        for rel, signatures in LEGACY_DOCS.items():
            path = self.target / rel
            if not path.is_file() or path.name not in os.listdir(path.parent):
                continue  # no existe, o existe con otra capitalización (p. ej. docs/SDD.md del proyecto)
            text = _read(path)
            if not any(sig in text for sig in signatures):
                continue
            self.backup(rel)
            if not self.dry_run:
                path.unlink()
                if not any(path.parent.iterdir()):
                    path.parent.rmdir()
            self.record("retirado", f"{rel} (ahora en docs/harness/)")

    def ensure_lines(self, rel: str, lines: tuple[str, ...]) -> None:
        path = self.target / rel
        current = _read(path).splitlines() if path.exists() else []
        missing = [line for line in lines if line not in current]
        if not missing:
            return
        self.append(rel, "\n".join(missing) + "\n")
        self.record("añadido", f"{rel} (+{len(missing)} líneas)")

    def import_existing_specs(self) -> list[str]:
        """Añade a la lista de features los specs que existen en disco y no están listados."""
        rel = self.values["FEATURE_LIST"]
        path = self.target / rel
        if self.dry_run and not path.exists():
            data = {"features": []}
        else:
            try:
                data = json.loads(_read(path))
            except ValueError:
                return []
        features = data.setdefault("features", [])
        known = {f.get("name") for f in features}
        next_id = max((f.get("id", 0) for f in features if isinstance(f.get("id"), int)), default=0) + 1
        # (spec-nnn se importa antes; Kiro, después)
        imported = []
        if self.values.get("SPEC_FORMAT") == "spec-nnn":
            known_specs = {f.get("spec") for f in features}
            spec_root = self.target / self.values["SPECS_DIR"]
            for path in sorted(spec_root.glob("SPEC-*.md")) if spec_root.is_dir() else []:
                rel_spec = path.relative_to(self.target).as_posix()
                if not SPEC_NNN_FILE.match(path.name) or rel_spec in known_specs:
                    continue
                title = re.search(r"^#\s+SPEC-\d{3}\s*[:—-]\s*(.+)$", _read(path), re.MULTILINE)
                estado = checker.parse_spec_nnn(_read(path))["estado"] or "?"
                features.append({
                    "id": next_id,
                    "name": path.stem.lower(),
                    "title": title.group(1).strip() if title else path.stem,
                    "spec": rel_spec,
                    "sdd": True,
                    "status": "pending",
                })
                next_id += 1
                imported.append(f"{path.stem} (pending, spec {estado})")
            if imported:
                self.write(rel, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
                for item in imported:
                    self.record("importado", f"{rel} ← {item}")
            return imported
        busy = any(f.get("status") == "in_progress" for f in features)
        for spec_dir in spec_dirs(self.target / self.values["SPECS_DIR"]):
            if spec_dir.name in known:
                continue
            tasks = checker.parse_tasks(_read(spec_dir / "tasks.md"))
            done = bool(tasks) and all(t["checked"] or t["optional"] for t in tasks)
            started = any(t["checked"] for t in tasks)
            if done:
                status = "done"
            elif started and not busy:
                status, busy = "in_progress", True
            else:
                status = "spec_ready"
            pending = sum(1 for t in tasks if not t["checked"] and not t["optional"])
            title = re.search(r"^#\s+(.+)$", _read(spec_dir / "requirements.md"), re.MULTILINE)
            features.append({
                "id": next_id,
                "name": spec_dir.name,
                "title": title.group(1).strip() if title else spec_dir.name,
                "description": "Importada de un spec existente al instalar harness-kit.",
                "acceptance": [],
                "sdd": True,
                "imported": True,
                "status": status,
            })
            next_id += 1
            progress = f", {len(tasks) - pending}/{len(tasks)} tasks hechas" if tasks else ""
            imported.append(f"{spec_dir.name} ({status}{progress})")
        if imported:
            self.write(rel, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            for item in imported:
                self.record("importado", f"{rel} ← {item}")
        return imported

    def run(self) -> list[str]:
        for src in sorted(TEMPLATE_DIR.rglob("*")):
            if src.is_dir() or "__pycache__" in src.parts:
                continue
            rel = src.relative_to(TEMPLATE_DIR).as_posix()
            self.install_file(self.dest_rel(rel), self.render(rel, src.read_text(encoding="utf-8")))
        self.retire_legacy_docs()
        self.ensure_lines(".gitignore", GITIGNORE_LINES)
        self.ensure_lines(".gitattributes", GITATTRIBUTES_LINES)
        return self.import_existing_specs()

    def write_history(self, profile: dict, imported: list[str]) -> None:
        rel = "progress/history.md"
        if self.dry_run or not (self.target / rel).exists() or not self.changed:
            return
        notable = imported or profile["legacy_specs"] or self.mode in ("adopt", "upgrade", "force")
        if rel in self.created and not notable:
            return
        lines = [f"\n## {self.values['INSTALL_DATE']} — harness-kit {KIT_VERSION} ({self.mode})\n"]
        backups = [r for a, r in self.log if a in ("actualizado", "reemplazado", "retirado", "fusionado", "referencia")]
        if backups and self.backup_root.exists():
            lines.append(f"- Backup de archivos reemplazados: `{self.backup_root.relative_to(self.target).as_posix()}/`")
        lines += [f"- Spec importado: {item}" for item in imported]
        lines += [f"- Spec legacy pendiente de migrar: {name}" for name in profile["legacy_specs"]]
        lines += [f"- {action}: {r}" for action, r in self.log if action in ("reemplazado", "retirado", "hook retirado")]
        self.append(rel, "\n".join(lines) + "\n")


# ── Informe ──────────────────────────────────────────────────────────────────

def print_profile(profile: dict, values: dict[str, str]) -> None:
    print("Detectado:")
    if profile["ours"]:
        version = profile["existing_config"].get("project", {}).get("kit_version", "?")
        print(f"  - harness-kit ya instalado (versión {version})")
    for signal in profile["foreign_signals"]:
        print(f"  - arnés previo: {signal}")
    if profile["kiro_specs"]:
        print(f"  - specs de Kiro en .kiro/specs/: {', '.join(profile['kiro_specs'])}")
    if profile["spec_kit"]:
        print("  - GitHub spec-kit (.specify/ o specs/*/spec.md): los specs del arnés irán a .kiro/specs/")
    if profile["feature_list_state"] == "incompatible" and values["FEATURE_LIST"] != "feature_list.json":
        print(f"  - feature_list.json con otro formato: el arnés usará {values['FEATURE_LIST']}")
    if profile["spec_nnn_dir"]:
        print(f"  - specs SPEC-NNN en {profile['spec_nnn_dir']}/: formato spec-nnn (Estado, RF/CA, Dado/Cuando/Entonces)")
    for comp in profile["node_components"]:
        print(f"  - componente Node: {comp['path']}/ ({comp['test'] or 'sin script test'})")
    if not profile["is_code"] and not profile["spec_nnn_dir"] and not profile["node_components"]:
        print("  - sin código Python (parece un repo de documentación o planificación)")
    if profile["venv"]:
        print(f"  - virtualenv {profile['venv']}/: los tests se ejecutarán con su intérprete")
    specialists = [a["name"] for a in profile["other_agents"] if not a["overlaps"]]
    if specialists:
        print(f"  - agentes especialistas del proyecto: {', '.join(specialists)} "
              "(el leader los usará cuando tasks.md les asigne una task)")
    for agent in profile["other_agents"]:
        if agent["overlaps"]:
            print(f"  - agente propio: {agent['name']}  ← mismo rol que un agente del arnés")
    if not any((profile["ours"], profile["foreign_signals"], profile["kiro_specs"], profile["spec_kit"],
                profile["venv"], profile["other_agents"])):
        print("  - proyecto sin arnés previo")
    print()


def run_check(target: Path) -> int:
    proc = subprocess.run(
        [sys.executable, str(target / "tools" / "harness_check.py"), "--no-tests"],
        cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL,
    )
    lines = [line for line in proc.stdout.splitlines() if line.startswith(("[FAIL]", "[WARN]"))]
    print("\nVerificación (sin tests):")
    for line in lines:
        print(f"  {line}")
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr.strip()
    print(f"  {summary}")
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="Instala harness-kit en un proyecto.")
    parser.add_argument("target", help="ruta del proyecto destino")
    parser.add_argument("--name", help="nombre del proyecto")
    parser.add_argument("--src", help="carpeta del código")
    parser.add_argument("--tests", help="carpeta de tests")
    parser.add_argument("--specs-dir", help="carpeta de specs (por defecto: specs, o .kiro/specs si hay Kiro/spec-kit)")
    parser.add_argument("--feature-list", help="archivo de features (por defecto: feature_list.json)")
    parser.add_argument("--spec-format", choices=("kiro", "spec-nnn"), help="formato de spec (autodetectado)")
    parser.add_argument("--component", action="append", metavar="NOMBRE=RUTA[:EXTS[:TEST]]",
                        help="componente de código, repetible (p. ej. api=api:.js,.ts:npm --prefix api test)")
    parser.add_argument("--architecture-doc", help="doc de arquitectura del proyecto (por defecto: docs/architecture.md)")
    parser.add_argument("--conventions-doc", help="doc de convenciones/calidad (por defecto: docs/conventions.md)")
    parser.add_argument("--test-cmd", help="comando de tests; admite {python} y {tests_dir}")
    parser.add_argument("--python", help="comando de Python para los permisos (por defecto: autodetectado)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--adopt", action="store_true", help="migra un arnés previo (con backup)")
    mode.add_argument("--upgrade", action="store_true", help="actualiza un harness-kit ya instalado")
    mode.add_argument("--keep-existing", action="store_true", help="instala junto a un arnés previo sin tocarlo")
    mode.add_argument("--force", action="store_true", help="sobrescribe todo (con backup)")
    parser.add_argument("--dry-run", action="store_true", help="muestra qué haría sin escribir")
    parser.add_argument("--no-check", action="store_true", help="no ejecutar la verificación al final")
    args = parser.parse_args(argv)

    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"[FAIL]  {target} no es un directorio", file=sys.stderr)
        return 1
    if target == KIT_DIR or KIT_DIR in target.parents:
        print("[FAIL]  No instales el kit dentro de su propio repositorio", file=sys.stderr)
        return 1

    profile = inspect_project(target)
    values = resolve_values(target, args, profile)
    mode = next((m for m in ("adopt", "upgrade", "keep_existing", "force") if getattr(args, m)), "install")

    print(f"harness-kit {KIT_VERSION} → {target}  [modo: {mode}]" + ("  (dry-run)" if args.dry_run else ""))
    for key in ("PROJECT_NAME", "CODE_DIRS", "SPEC_FORMAT", "SPECS_DIR", "FEATURE_LIST", "ARCH_DOC", "CONV_DOC",
                "TEST_CMD_DISPLAY"):
        print(f"  {key:<17}= {values[key]}")
    print()
    print_profile(profile, values)

    planned = bool(profile["spec_nnn_dir"] or profile["node_components"] or args.component)
    if not profile["is_code"] and not planned and not profile["ours"] and mode != "force":
        print("No encuentro un proyecto Python con código y tests (no hay pyproject.toml, src/, tests/")
        print("ni paquetes). harness-kit está hecho para Python: corre pytest/unittest y sus hooks miran")
        print("archivos .py. En un repo de documentación, en uno que aún no empieza el código o en otro")
        print("stack (Node, .NET…) solo añadiría ruido: docs de proceso, progress/, feature_list.json y")
        print("reglas sobre src/ y tests/ que no aplican. Tu CLAUDE.md/AGENTS.md se conservarían igual.")
        print("Si aun así lo quieres, repite con --force.")
        return 2
    if profile["foreign_signals"] and mode in ("install", "upgrade"):
        print("Este proyecto ya tiene otro arnés de agentes/SDD. Instalar encima dejaría dos juegos de")
        print("instrucciones contradictorias. Elige:")
        print("  --adopt          migra ese arnés a harness-kit (backup en .harness-backup/)  ← recomendado")
        print("  --keep-existing  instala sin tocar lo existente (tendrás que fusionar a mano)")
        print("  --dry-run        añádelo a cualquiera de los anteriores para ver qué haría")
        return 2
    if mode == "upgrade" and not profile["ours"]:
        print("--upgrade es para proyectos con harness-kit ya instalado; usa la instalación normal.")
        return 2

    installer = Installer(target, values, "keep" if mode == "keep_existing" else mode, args.dry_run,
                          ours=profile["ours"])
    imported = installer.run()
    installer.write_history(profile, imported)

    if installer.backup_root.exists():
        print(f"\nBackup de todo lo reemplazado/retirado: {installer.backup_root.relative_to(target).as_posix()}/")
    warnings = list(installer.notes)
    if profile["legacy_hooks"] and mode not in ("adopt", "force"):
        warnings.append("hay hooks de un arnés anterior en .claude/settings.json (usa --adopt para retirarlos): "
                        + "; ".join(profile["legacy_hooks"]))
    overlapping = [a["name"] for a in profile["other_agents"] if a["overlaps"]]
    if overlapping:
        warnings.append(f"{', '.join(overlapping)} cumple(n) un rol parecido a leader/spec_author/implementer/"
                        "reviewer. No hace falta borrarlos: el reviewer del arnés es la puerta final y el leader "
                        "le pasa el checklist del revisor propio. Si prefieres uno solo, decide cuál conservar.")
    if any(".harness.md" in r for _, r in installer.log):
        warnings.append("tu CLAUDE.md/AGENTS.md ya existían: se creó un *.harness.md y una referencia al final del tuyo.")
    if warnings:
        print("\nAvisos:")
        for warning in warnings:
            print(f"  - {warning}")

    docs_to_fill = [d for d, default in ((values["ARCH_DOC"], DEFAULT_ARCH_DOC), (values["CONV_DOC"], DEFAULT_CONV_DOC))
                    if d == default]
    steps = [
        "Rellena .kiro/steering/{product,tech,structure}.md"
        + (f" y {', '.join(docs_to_fill)}" if docs_to_fill else "")
        + " (o pide a Claude: «rellena los steering files a partir del repo»).",
    ]
    if values["SPEC_FORMAT"] == "spec-nnn":
        steps.append(f"Cada SPEC de {values['SPECS_DIR']}/ es una feature en {values['FEATURE_LIST']} (pending hasta "
                     "que se apruebe). Cuando el equipo apruebe una, dile al leader: «la SPEC-00X fue aprobada por "
                     "<quiénes> el <fecha>»; él registra la aprobación, genera el plan de tareas e implementa.")
    else:
        steps.append(f"Añade features a {values['FEATURE_LIST']} con \"sdd\": true y status \"pending\".")
    if profile["legacy_specs"]:
        steps.append("Pide al leader: «migra los specs legacy a formato Kiro» "
                     f"({', '.join(profile['legacy_specs'])}).")
    if imported and values["SPEC_FORMAT"] != "spec-nnn":
        steps.append("Revisa las features importadas (\"imported\": true): las que están en spec_ready esperan "
                     "tu aprobación; la que quedó en in_progress (spec ya empezado) se retoma con «continúa con la "
                     "feature en curso».")
    steps.append("Ejecuta ./init.sh (o ./init.ps1) — incluye los tests — y luego, en Claude Code: "
                 "«implementa la siguiente feature pendiente».")
    print("\nSiguientes pasos:")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")

    if args.dry_run or args.no_check:
        return 0
    if run_check(target) != 0:
        print("\n[FAIL]  La instalación terminó pero la verificación está en rojo: revisa los [FAIL] de arriba.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
