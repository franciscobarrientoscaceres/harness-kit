"""Verificación del arnés: entorno, feature_list, specs Kiro-style, trazabilidad y tests.

Uso:
    python tools/harness_check.py              # verificación completa (lo que hace init.sh)
    python tools/harness_check.py --no-tests   # todo menos los tests
    python tools/harness_check.py --tests-only # solo los tests
    python tools/harness_check.py --hook post  # modo hook PostToolUse (Claude Code)
    python tools/harness_check.py --hook stop  # modo hook Stop (Claude Code)

Solo usa la stdlib. Exit code 0 = verde, 1 = rojo, 2 = rojo en modo `--hook post`
(Claude Code reinyecta stderr al modelo para que corrija).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
from pathlib import Path

VALID_STATUS = ("pending", "spec_ready", "in_progress", "done", "blocked")
REQUIRES_SPEC = ("spec_ready", "in_progress", "done")
SPEC_FILES = ("requirements.md", "design.md", "tasks.md")
BASE_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "CHECKPOINTS.md",
    "harness.toml",
    "progress/current.md",
    "progress/history.md",
    "docs/architecture.md",
    "docs/conventions.md",
    "docs/sdd.md",
    "docs/verification.md",
)
STEERING_FILES = ("product.md", "tech.md", "structure.md")
FILL_MARKER = "<!-- RELLENAR -->"
NO_TESTS_EXIT_CODE = 5
VENV_DIRS = (".venv", "venv", "env")

DEFAULT_CONFIG = {
    "project": {"name": ""},
    "paths": {
        "src_dir": "src",
        "tests_dir": "tests",
        "specs_dir": "specs",
        "feature_list": "feature_list.json",
        "progress_dir": "progress",
        "steering_dir": ".kiro/steering",
    },
    "commands": {"python": "", "test": "{python} -m pytest -q", "test_fast": ""},
    "hooks": {"post_tests": True, "post_timeout": 150, "stop_timeout": 280},
    "harness": {"min_python": "3.9"},
}

REQ_HEADING = re.compile(r"^###\s+(?:Requisito|Requirement)\s+(\d+)\b.*$", re.IGNORECASE | re.MULTILINE)
LEGACY_REQ_HEADING = re.compile(r"^##\s+R\d+\b", re.MULTILINE)
STORY = re.compile(r"\*\*\s*(?:Historia de usuario|User Story)\s*:?\s*\*\*", re.IGNORECASE)
ACCEPTANCE = re.compile(
    r"^####\s+(?:Criterios de aceptaci[oó]n|Acceptance Criteria)\b.*$", re.IGNORECASE | re.MULTILINE
)
CRITERION = re.compile(r"^(\d+)\.\s+(.+)$")
MODAL = re.compile(r"\b(?:DEBE|SHALL)\b")
SOFT_VERB = re.compile(r"\b(?:podr[ií]a|puede|soporta|deber[ií]a|should|could|may)\b", re.IGNORECASE)
TASK = re.compile(r"^(\s*)- \[([ xX])\](\*?)\s+(\d+(?:\.\d+)*)\.?\s+(.*)$")
TASK_REFS = re.compile(r"_(?:Requisitos|Requirements)\s*:\s*([^_]+)_", re.IGNORECASE)
NO_REFS = {"ninguno", "ninguna", "none", "-", "—", "n/a"}
TRACE_LINE = re.compile(r"^\s*-\s*(\d+\.\d+)\s*(?:→|->|:)\s*(.+)$", re.MULTILINE)


class Report:
    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def section(self, title: str) -> None:
        if not self.quiet:
            print(f"\n-- {title} " + "-" * max(0, 56 - len(title)))

    def ok(self, msg: str) -> None:
        if not self.quiet:
            print(f"[OK]    {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        if not self.quiet:
            print(f"[WARN]  {msg}")

    def fail(self, msg: str) -> None:
        self.failures.append(msg)
        if not self.quiet:
            print(f"[FAIL]  {msg}")


# ── Configuración ────────────────────────────────────────────────────────────

def _parse_toml_subset(text: str) -> dict:
    """Parser mínimo para Python < 3.11: secciones, strings, bools e ints."""
    data: dict = {}
    section = data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = re.match(r"^\[([\w.\-]+)\]$", line)
        if header:
            section = data
            for part in header.group(1).split("."):
                section = section.setdefault(part, {})
            continue
        pair = re.match(r"^([\w\-]+)\s*=\s*(.+)$", line)
        if not pair:
            continue
        key, value = pair.group(1), pair.group(2).strip()
        quoted = re.match(r'^"((?:[^"\\]|\\.)*)"', value)
        if quoted:
            section[key] = json.loads(f'"{quoted.group(1)}"')
        elif value.startswith("'"):
            section[key] = value[1:value.index("'", 1)]
        elif value.split("#", 1)[0].strip() in ("true", "false"):
            section[key] = value.split("#", 1)[0].strip() == "true"
        else:
            try:
                section[key] = int(value.split("#", 1)[0].strip())
            except ValueError:
                section[key] = value
    return data


def load_config(root: Path) -> dict:
    config = {k: dict(v) for k, v in DEFAULT_CONFIG.items()}
    path = root / "harness.toml"
    if not path.is_file():
        return config
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib

        loaded = tomllib.loads(text)
    except ModuleNotFoundError:
        loaded = _parse_toml_subset(text)
    for key, value in loaded.items():
        if isinstance(value, dict):
            config.setdefault(key, {}).update(value)
        else:
            config[key] = value
    return config


# ── Parsing de specs Kiro-style ──────────────────────────────────────────────

def parse_requirements(text: str) -> tuple[dict[int, dict], list[str]]:
    """Devuelve {n: {"story": bool, "criteria": {m: texto}}} y errores de estructura."""
    errors: list[str] = []
    headings = list(REQ_HEADING.finditer(text))
    reqs: dict[int, dict] = {}
    for i, heading in enumerate(headings):
        n = int(heading.group(1))
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        block = text[heading.end():end]
        if n in reqs:
            errors.append(f"Requisito {n} duplicado")
            continue
        criteria: dict[int, str] = {}
        acceptance = ACCEPTANCE.search(block)
        if acceptance:
            current = None
            for line in block[acceptance.end():].splitlines():
                if line.startswith("#"):
                    break
                match = CRITERION.match(line)
                if match:
                    current = int(match.group(1))
                    if current in criteria:
                        errors.append(f"criterio {n}.{current} duplicado")
                    criteria[current] = match.group(2).strip()
                elif current is not None and line.strip() and line[:1] in (" ", "\t"):
                    criteria[current] += " " + line.strip()
                elif line.strip():
                    current = None
        reqs[n] = {"story": bool(STORY.search(block)), "has_acceptance": bool(acceptance), "criteria": criteria}
    return reqs, errors


def parse_tasks(text: str) -> list[dict]:
    tasks: list[dict] = []
    for line in text.splitlines():
        match = TASK.match(line)
        if match:
            tasks.append({
                "id": match.group(4),
                "checked": match.group(2).lower() == "x",
                "optional": match.group(3) == "*",
                "text": match.group(5),
                "refs_raw": [],
            })
            refs = TASK_REFS.search(line)
            if refs:
                tasks[-1]["refs_raw"].append(refs.group(1))
        elif tasks:
            refs = TASK_REFS.search(line)
            if refs:
                tasks[-1]["refs_raw"].append(refs.group(1))
    for task in tasks:
        task["leaf"] = not any(other["id"].startswith(task["id"] + ".") for other in tasks)
    return tasks


def criterion_ids(reqs: dict[int, dict]) -> list[str]:
    return [f"{n}.{m}" for n in sorted(reqs) for m in sorted(reqs[n]["criteria"])]


def validate_kiro_spec(name: str, spec_dir: Path, status: str, report: Report,
                       strict: bool = True) -> list[str]:
    """Valida requirements/tasks en formato Kiro. Devuelve los ids `N.M` de criterios.

    Con `strict=False` (features importadas) los problemas de formato son avisos.
    """
    err = report.fail if strict else report.warn
    req_text = (spec_dir / "requirements.md").read_text(encoding="utf-8")
    task_text = (spec_dir / "tasks.md").read_text(encoding="utf-8")
    prefix = f"{name}:"

    reqs, structure_errors = parse_requirements(req_text)
    for error in structure_errors:
        err(f"{prefix} requirements.md — {error}")
    if not reqs:
        err(f"{prefix} requirements.md no tiene ningún `### Requisito N`")
        return []

    expected = list(range(1, len(reqs) + 1))
    if sorted(reqs) != expected:
        report.warn(f"{prefix} los requisitos no están numerados 1..{len(reqs)} de forma consecutiva")

    for n, req in sorted(reqs.items()):
        if not req["story"]:
            err(f"{prefix} Requisito {n} sin `**Historia de usuario:**`")
        if not req["has_acceptance"]:
            err(f"{prefix} Requisito {n} sin `#### Criterios de aceptación`")
        elif not req["criteria"]:
            err(f"{prefix} Requisito {n} sin criterios numerados")
        for m, criterion in sorted(req["criteria"].items()):
            modals = MODAL.findall(criterion)
            if not modals:
                err(f"{prefix} criterio {n}.{m} no usa EARS (`DEBE`/`SHALL`)")
            elif len(modals) > 1:
                err(f"{prefix} criterio {n}.{m} tiene {len(modals)} `DEBE`; parte en varios criterios")
            soft = SOFT_VERB.search(criterion)
            if soft:
                report.warn(f"{prefix} criterio {n}.{m} usa verbo blando «{soft.group(0)}»")

    ids = criterion_ids(reqs)
    tasks = parse_tasks(task_text)
    if not tasks:
        err(f"{prefix} tasks.md no tiene ninguna task `- [ ] N. ...`")
        return ids

    covered: set[str] = set()
    for task in tasks:
        refs: list[str] = []
        for raw in task["refs_raw"]:
            refs.extend(token.strip() for token in raw.split(",") if token.strip())
        if task["leaf"] and not refs:
            err(f"{prefix} task {task['id']} sin `_Requisitos: ..._` (usa `_Requisitos: ninguno_` si es deliberado)")
        for ref in refs:
            if ref.lower() in NO_REFS:
                continue
            if re.fullmatch(r"\d+\.\d+", ref):
                if ref not in ids:
                    err(f"{prefix} task {task['id']} referencia el criterio inexistente {ref}")
                covered.add(ref)
            elif re.fullmatch(r"\d+", ref):
                if int(ref) not in reqs:
                    err(f"{prefix} task {task['id']} referencia el requisito inexistente {ref}")
                covered.update(c for c in ids if c.startswith(ref + "."))
            else:
                err(f"{prefix} task {task['id']} tiene una referencia inválida «{ref}»")

    for criterion in ids:
        if criterion not in covered:
            err(f"{prefix} criterio {criterion} no está cubierto por ninguna task")

    if status == "done":
        pending = [t["id"] for t in tasks if not t["checked"] and not t["optional"]]
        if pending:
            report.fail(f"{prefix} feature `done` con tasks sin marcar: {', '.join(pending)}")

    return ids


def validate_legacy_spec(name: str, spec_dir: Path, status: str, report: Report) -> None:
    report.warn(f"{name}: spec en formato legacy `## R<n>`; no se valida estilo Kiro")
    if status == "done":
        task_text = (spec_dir / "tasks.md").read_text(encoding="utf-8")
        if re.search(r"^\s*- \[ \]", task_text, re.MULTILINE):
            report.fail(f"{name}: feature `done` con tasks `[ ]` en tasks.md")


# ── Trazabilidad criterio ↔ test ─────────────────────────────────────────────

def _defined_tests(tests_dir: Path) -> set[str]:
    names: set[str] = set()
    if not tests_dir.is_dir():
        return names
    for path in tests_dir.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        names.update(re.findall(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", text, re.MULTILINE))
    return names


def validate_traceability(name: str, ids: list[str], status: str, config: dict,
                          root: Path, report: Report, strict: bool = True) -> None:
    impl = root / config["paths"]["progress_dir"] / f"impl_{name}.md"
    complain = report.fail if status == "done" and strict else report.warn
    if not impl.is_file():
        if status == "done":
            complain(f"{name}: falta {impl.relative_to(root).as_posix()} con el mapa criterio → test")
        return

    mapping: dict[str, list[str]] = {}
    for criterion, rhs in TRACE_LINE.findall(impl.read_text(encoding="utf-8")):
        mapping.setdefault(criterion, []).extend(re.findall(r"`([^`]+)`", rhs))

    defined = _defined_tests(root / config["paths"]["tests_dir"])
    for criterion in ids:
        tests = mapping.get(criterion)
        if not tests:
            complain(f"{name}: criterio {criterion} sin test en impl_{name}.md")
            continue
        for test in tests:
            short = re.split(r"[:.]", test.replace("::", "."))[-1]
            if short not in defined:
                complain(f"{name}: criterio {criterion} mapea a `{test}`, que no existe en tests")


# ── Bloques de verificación ──────────────────────────────────────────────────

def check_environment(config: dict, report: Report) -> None:
    report.section("1. Entorno")
    minimum = tuple(int(x) for x in str(config["harness"].get("min_python", "3.9")).split("."))
    version = ".".join(str(x) for x in sys.version_info[:3])
    if sys.version_info[: len(minimum)] < minimum:
        report.fail(f"Python {version} < {'.'.join(map(str, minimum))}")
    else:
        report.ok(f"Python {version} ({sys.executable})")


def check_base_files(root: Path, config: dict, report: Report) -> None:
    report.section("2. Archivos base del arnés")
    for rel in (*BASE_FILES, config["paths"]["feature_list"]):
        if (root / rel).is_file():
            report.ok(f"Existe {rel}")
        else:
            report.fail(f"Falta archivo base: {rel}")
    steering = root / config["paths"]["steering_dir"]
    for fname in STEERING_FILES:
        if not (steering / fname).is_file():
            report.warn(f"Falta steering {(steering / fname).relative_to(root).as_posix()}")
    to_fill = [steering / f for f in STEERING_FILES] + [root / "docs/architecture.md", root / "docs/conventions.md"]
    for path in to_fill:
        if path.is_file() and FILL_MARKER in path.read_text(encoding="utf-8"):
            report.warn(f"{path.relative_to(root).as_posix()} aún tiene secciones sin rellenar")


def check_features(root: Path, config: dict, report: Report) -> None:
    feature_list = config["paths"]["feature_list"]
    report.section(f"3. {feature_list} y specs")
    try:
        data = json.loads((root / feature_list).read_text(encoding="utf-8-sig"))
        features = data["features"]
        if not isinstance(features, list):
            raise TypeError("`features` no es una lista")
    except FileNotFoundError:
        return
    except (ValueError, KeyError, TypeError) as exc:
        report.fail(f"{feature_list} inválido: {exc}")
        return

    seen_ids: set = set()
    seen_names: set = set()
    for feature in features:
        missing = [k for k in ("id", "name", "status") if k not in feature]
        if missing:
            report.fail(f"feature sin campos {missing}: {feature}")
            continue
        if feature["id"] in seen_ids:
            report.fail(f"id duplicado: {feature['id']}")
        if feature["name"] in seen_names:
            report.fail(f"name duplicado: {feature['name']}")
        seen_ids.add(feature["id"])
        seen_names.add(feature["name"])
        if feature["status"] not in VALID_STATUS:
            report.fail(f"estado inválido en feature {feature['id']}: {feature['status']}")

    in_progress = [f for f in features if f.get("status") == "in_progress"]
    if len(in_progress) > 1:
        names = ", ".join(f["name"] for f in in_progress)
        report.fail(f"hay {len(in_progress)} features en in_progress (máximo 1): {names}")
    report.ok(f"{feature_list} leído ({len(features)} features)")

    specs_dir = root / config["paths"]["specs_dir"]
    for feature in features:
        status = feature.get("status")
        if not feature.get("sdd") or status not in REQUIRES_SPEC:
            continue
        name = feature["name"]
        spec_dir = specs_dir / name
        missing = [f for f in SPEC_FILES if not (spec_dir / f).is_file()]
        if missing:
            for fname in missing:
                rel = (spec_dir / fname).relative_to(root).as_posix()
                report.fail(f"feature {feature['id']} ({name}) en {status} sin {rel}")
            continue
        req_text = (spec_dir / "requirements.md").read_text(encoding="utf-8")
        if not REQ_HEADING.search(req_text) and LEGACY_REQ_HEADING.search(req_text):
            validate_legacy_spec(name, spec_dir, status, report)
            continue
        strict = not feature.get("imported")
        failures_before = len(report.failures)
        ids = validate_kiro_spec(name, spec_dir, status, report, strict=strict)
        if status in ("in_progress", "done"):
            validate_traceability(name, ids, status, config, root, report, strict=strict)
        if len(report.failures) == failures_before:
            kind = "importado, validación tolerante" if not strict else status
            report.ok(f"spec {name} ({kind}): {len(ids)} criterios válidos")


def resolve_python(root: Path, config: dict) -> str:
    """Intérprete para los tests: commands.python > virtualenv del proyecto > el actual."""
    configured = str(config["commands"].get("python") or "").strip()
    if configured:
        candidate = root / configured
        return str(candidate) if candidate.exists() else configured
    for venv in VENV_DIRS:
        for parts in (("Scripts", "python.exe"), ("bin", "python")):
            candidate = root.joinpath(venv, *parts)
            if candidate.is_file():
                return str(candidate)
    return sys.executable


def _quote(arg: str) -> str:
    return subprocess.list2cmdline([arg]) if os.name == "nt" else shlex.quote(arg)


def _has_module(python: str, module: str) -> bool:
    try:
        result = subprocess.run([python, "-c", f"import {module}"], capture_output=True, timeout=60,
                                stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def run_tests(root: Path, config: dict, report: Report, quiet_output: bool, *,
              fast: bool = False, timeout: int | None = None) -> str:
    report.section("4. Tests")
    tests_dir = config["paths"]["tests_dir"]
    if not (root / tests_dir).is_dir():
        report.warn(f"La carpeta {tests_dir}/ no existe todavía")
        return ""
    command = (config["commands"].get("test_fast") if fast else "") or config["commands"]["test"]
    python = resolve_python(root, config)
    if "{python} -m pytest" in command and not _has_module(python, "pytest"):
        report.fail(
            f"pytest no está instalado en {python}: instálalo (pip install pytest) "
            "o ajusta commands.python / commands.test en harness.toml"
        )
        return ""
    command = command.replace("{python}", _quote(python)).replace("{tests_dir}", tests_dir)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        report.warn(
            f"Los tests superaron {timeout}s y se cortaron; usa commands.test_fast o sube "
            "hooks.*_timeout en harness.toml"
        )
        return ""
    output = (proc.stdout + proc.stderr).strip()
    if not quiet_output and not report.quiet:
        print(output)
    ran_nothing = proc.returncode == NO_TESTS_EXIT_CODE or re.search(r"\bRan 0 tests?\b", output)
    has_test_files = any((root / tests_dir).rglob("test*.py")) or any((root / tests_dir).rglob("*_test.py"))
    if ran_nothing and has_test_files:
        report.fail(f"Hay archivos de test en {tests_dir}/ pero no se ejecutó ninguno: revisa commands.test ({command})")
    elif ran_nothing:
        report.warn("No se encontró ningún test")
    elif proc.returncode == 0:
        report.ok(f"Tests verdes ({command})")
    else:
        report.fail(f"Hay tests rotos ({command}, exit {proc.returncode})")
    return output


# ── Modos de ejecución ───────────────────────────────────────────────────────

def _edited_python_file() -> bool:
    """En modo hook post: True si la herramienta tocó un .py (o si no se puede saber).

    Claude Code envía el evento por stdin y lo cierra; si stdin es una terminal o
    un pipe que nadie cierra (ejecución manual), no se bloquea: espera 2 s y sigue.
    """
    if sys.stdin is None or sys.stdin.isatty():
        return True
    received: list[str] = []
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        received.append(sys.stdin.read())
    else:
        def read_fd() -> None:
            chunks = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            received.append(b"".join(chunks).decode("utf-8", "replace"))

        # os.read sobre el descriptor (no sys.stdin) para que un hilo bloqueado no
        # impida terminar el proceso en Windows.
        reader = threading.Thread(target=read_fd, daemon=True)
        reader.start()
        reader.join(2)
    try:
        payload = json.loads(received[0] or "{}") if received else {}
    except (ValueError, OSError):
        return True
    path = (payload.get("tool_input") or {}).get("file_path", "")
    return not path or path.endswith(".py")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="Verificación del arnés")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--no-tests", action="store_true", help="no ejecutar los tests")
    parser.add_argument("--tests-only", action="store_true", help="solo ejecutar los tests")
    parser.add_argument("--hook", choices=("post", "stop"), help="modo hook de Claude Code")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    config = load_config(root)

    hooks = config["hooks"]
    if args.hook == "post":
        if not hooks.get("post_tests", True) or not _edited_python_file():
            return 0
        report = Report(quiet=True)
        output = run_tests(root, config, report, quiet_output=True, fast=True,
                           timeout=int(hooks.get("post_timeout", 150)))
        if report.failures:
            tail = "\n".join(output.splitlines()[-15:])
            print(f"[harness] tests en rojo tras la edición:\n{tail}", file=sys.stderr)
            return 2
        return 0

    report = Report(quiet=args.hook == "stop")
    if not args.tests_only:
        check_environment(config, report)
        check_base_files(root, config, report)
        check_features(root, config, report)
    if not args.no_tests:
        timeout = int(hooks.get("stop_timeout", 280)) if args.hook == "stop" else None
        run_tests(root, config, report, quiet_output=False, timeout=timeout)

    if args.hook == "stop":
        if report.failures:
            lines = "\n".join(f"  - {f}" for f in report.failures)
            print(f"[harness] verificación en rojo antes de cerrar:\n{lines}", file=sys.stderr)
            return 1
        print("[harness] verificación OK")
        return 0

    report.section("5. Resumen")
    if report.failures:
        print(f"[FAIL]  Entorno NO está listo: {len(report.failures)} error(es), {len(report.warnings)} aviso(s).")
        return 1
    print(f"[OK]    Entorno listo ({len(report.warnings)} aviso(s)). Puedes empezar a trabajar.")
    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # os._exit: un hilo leyendo un stdin que nadie cierra no debe retener el proceso.
    os._exit(exit_code)
