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
import math
import os
import re
import shlex
import shutil
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
    "docs/harness/proceso-sdd.md",
    "docs/harness/verificacion.md",
)
STEERING_FILES = ("product.md", "tech.md", "structure.md")
FILL_MARKER = "<!-- RELLENAR -->"
NO_TESTS_EXIT_CODE = 5
VENV_DIRS = (".venv", "venv", "env")
CACHE_DIR = ".harness-cache"
# Carpetas que nunca se recorren (dependencias, builds, caches).
IGNORED_DIRS = {"node_modules", ".venv", "venv", "env", ".git", "dist", "build", "__pycache__",
                ".pytest_cache", ".next", "coverage", ".harness-cache", ".harness-backup", "bin", "obj"}
# Herramientas que se comprueban antes de lanzar un comando de tests.
KNOWN_TOOLS = {"npm", "npx", "pnpm", "yarn", "node", "bun", "deno", "dotnet", "go", "cargo", "bicep", "az", "pwsh"}
GREEN_STAMP = "last_green"

DEFAULT_CONFIG = {
    "project": {"name": ""},
    "paths": {
        "src_dir": "src",
        "tests_dir": "tests",
        "specs_dir": "specs",
        "feature_list": "feature_list.json",
        "progress_dir": "progress",
        "steering_dir": ".kiro/steering",
        "architecture_doc": "docs/architecture.md",
        "conventions_doc": "docs/conventions.md",
    },
    "commands": {"python": "", "test": "{python} -m pytest -q", "test_fast": ""},
    "hooks": {"post_tests": True, "post_scope": "related", "post_timeout": 150, "stop_timeout": 280,
              "pre_gate": "warn"},
    "harness": {"min_python": "3.9"},
    "git": {"auto_commit": True, "auto_push": False, "secret_scan": True},
    "spec": {"format": "kiro", "tasks_dir": ""},
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
# Línea que es solo una referencia en cursiva, estilo `_R1, R2.4, F-05_` (variante habitual de Kiro).
BARE_REFS = re.compile(r"^\s*(?:-\s*)?_([^_]+)_\s*$")
REF_TOKEN = re.compile(r"(?<![\w/-])R?\d+(?:\.\d+)?(?![\w/-])", re.IGNORECASE)
REQ_REF = re.compile(r"R?(\d+(?:\.\d+)?)", re.IGNORECASE)
EXTERNAL_REF = re.compile(r"[A-Za-z]+-[\w/.-]+")
# Rango de criterios: `R2.1–R2.3`, `R2.1-2.3`, `2.1–2.3` (mismo requisito).
REF_RANGE = re.compile(r"R?(\d+)\.(\d+)\s*[–—-]\s*R?(?:\1\.)?(\d+)", re.IGNORECASE)
ISSUE_LABELS = (
    ("`DEBE`; parte", "criterios con varios DEBE"),
    ("sin `_Requisitos", "tasks sin referencia a requisitos"),
    ("no está cubierto", "criterios sin task"),
    ("no usa EARS", "criterios sin DEBE/SHALL"),
    ("verbo blando", "verbos blandos"),
    ("Historia de usuario", "requisitos sin historia de usuario"),
)
TRACE_LINE = re.compile(r"^\s*-\s*(\d+\.\d+)\s*(?:→|->|:)\s*(.+)$", re.MULTILINE)


class Report:
    def __init__(self, quiet: bool = False, verbose: bool = False) -> None:
        self.quiet = quiet
        self.verbose = verbose
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

def _parse_toml_value(value: str):
    quoted = re.match(r'^"((?:[^"\\]|\\.)*)"', value)
    if quoted:
        return json.loads(f'"{quoted.group(1)}"')
    if value.startswith("'"):
        return value[1:value.index("'", 1)]
    if value.startswith("["):
        inner = value[1:value.rindex("]")] if "]" in value else value[1:]
        items = re.findall(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'', inner)
        return [json.loads(f'"{dq}"') if dq or not sq else sq for dq, sq in items]
    bare = value.split("#", 1)[0].strip()
    if bare in ("true", "false"):
        return bare == "true"
    try:
        return int(bare)
    except ValueError:
        return value


def _parse_toml_subset(text: str) -> dict:
    """Parser mínimo para Python < 3.11: secciones, [[tablas]], strings, arrays, bools e ints."""
    data: dict = {}
    section = data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        table = re.match(r"^\[\[([\w\-]+)\]\]$", line)
        if table:
            section = {}
            data.setdefault(table.group(1), []).append(section)
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
        section[pair.group(1)] = _parse_toml_value(pair.group(2).strip())
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


def _is_ref_token(token: str) -> bool:
    token = token.strip()
    return bool(REQ_REF.fullmatch(token) or REF_RANGE.fullmatch(token) or EXTERNAL_REF.fullmatch(token))


def _line_refs(line: str) -> str | None:
    labeled = TASK_REFS.search(line)
    if labeled:
        return labeled.group(1)
    bare = BARE_REFS.match(line)
    if bare and any(_is_ref_token(tok) for tok in bare.group(1).split(",")):
        return bare.group(1)
    return None


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
        if tasks:
            refs = _line_refs(line)
            if refs:
                tasks[-1]["refs_raw"].append(refs)
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
    issues: list[str] = []

    def err(message: str) -> None:
        if strict:
            report.fail(message)
        else:
            issues.append(message)

    def note(message: str) -> None:
        if strict:
            report.warn(message)
        else:
            issues.append(message)

    try:
        return _validate_kiro_spec(name, spec_dir, status, report, err, note)
    finally:
        _summarize_issues(name, issues, report)


def _summarize_issues(name: str, issues: list[str], report: Report) -> None:
    """Spec importado: una línea con el resumen en vez de cientos de avisos."""
    if not issues:
        return
    if report.verbose:
        for issue in issues:
            report.warn(issue)
        return
    counts: dict[str, int] = {}
    for issue in issues:
        label = next((lbl for key, lbl in ISSUE_LABELS if key in issue), "otras observaciones")
        counts[label] = counts.get(label, 0) + 1
    detail = ", ".join(f"{n} {label}" for label, n in counts.items())
    report.warn(f"{name}: spec importado con {len(issues)} observaciones de formato ({detail}); "
                "no bloquean. Detalle: python tools/harness_check.py --verbose")


def _validate_kiro_spec(name: str, spec_dir: Path, status: str, report: Report, err, note) -> list[str]:
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
        note(f"{prefix} los requisitos no están numerados 1..{len(reqs)} de forma consecutiva")

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
                note(f"{prefix} criterio {n}.{m} usa verbo blando «{soft.group(0)}»")

    ids = criterion_ids(reqs)
    tasks = parse_tasks(task_text)
    if not tasks:
        err(f"{prefix} tasks.md no tiene ninguna task `- [ ] N. ...`")
        return ids

    covered: set[str] = set()
    for task in tasks:
        refs: list[str] = []
        for raw in task["refs_raw"]:
            for token in (tok.strip() for tok in raw.split(",") if tok.strip()):
                span = REF_RANGE.fullmatch(token)
                if span:
                    req, first, last = span.group(1), int(span.group(2)), int(span.group(3))
                    refs.extend(f"{req}.{m}" for m in range(first, last + 1))
                else:
                    refs.append(token)
        if task["leaf"] and not refs:
            err(f"{prefix} task {task['id']} sin `_Requisitos: ..._` (usa `_Requisitos: ninguno_` si es deliberado)")
        for ref in refs:
            if ref.lower() in NO_REFS:
                continue
            req_ref = REQ_REF.fullmatch(ref)
            if req_ref:
                ref = req_ref.group(1)
            elif not re.match(r"R?\d", ref, re.IGNORECASE):
                continue  # referencia externa: hallazgos F-xx, ADR-xx, decisiones D-xx, Properties…
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


# ── Formato SPEC-NNN (un archivo por spec: Estado, RF/RN/RNF, CA Dado/Cuando/Entonces) ──

SPEC_STATES = ("Borrador", "En revisión", "Aprobada", "Rechazada", "Reemplazada")
SPEC_CODE = re.compile(r"SPEC-(\d{3})", re.IGNORECASE)
SPEC_FIELD = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$", re.MULTILINE)
SPEC_ROW_ID = re.compile(r"^\|\s*((?:RF|RNF|RN)-(\d{3})-\d+)\s*\|(.*)$", re.MULTILINE)
SPEC_CA = re.compile(r"^\*\*\s*(CA-(\d{3})-\d+)\b", re.MULTILINE)
SPEC_REF = re.compile(r"^(?:RF|RNF|RN|CA)-\d{3}-\d+$")
GHERKIN = (("dado", "given"), ("cuando", "when"), ("entonces", "then"))
TEST_FILE = re.compile(r"(^test_.*\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$)", re.IGNORECASE)


def parse_spec_nnn(text: str) -> dict:
    fields = {}
    for key, value in SPEC_FIELD.findall(text.split("\n## ", 1)[0]):
        fields.setdefault(key.strip().lower(), value.strip())
    estado_raw = fields.get("estado", "")
    estado = next((s for s in SPEC_STATES if estado_raw.lower().startswith(s.lower())), "")
    requirements = {}
    for req_id, _num, rest in SPEC_ROW_ID.findall(text):
        priority = next((p for p in ("Must", "Should", "Could") if re.search(rf"\b{p}\b", rest)), "")
        requirements[req_id] = priority
    criteria, criteria_refs = {}, {}
    matches = list(SPEC_CA.finditer(text))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw_block = text[match.end():end].split("\n## ", 1)[0]
        block = raw_block.lower()
        criteria[match.group(1)] = [pair[0] for pair in GHERKIN
                                    if not any(re.search(rf"\b{w}\b", block) for w in pair)]
        other = [ca for ca in re.findall(r"\bCA-\d{3}-\d+\b", raw_block) if ca != match.group(1)]
        if other:
            criteria_refs[match.group(1)] = other[0]  # «Igual que CA-001-05»
    return {"estado": estado, "estado_raw": estado_raw, "fields": fields,
            "requirements": requirements, "criteria": criteria, "criteria_refs": criteria_refs}


def spec_tasks_path(root: Path, config: dict, spec_path: Path) -> Path:
    tasks_dir = config["spec"].get("tasks_dir") or f"{config['paths']['specs_dir']}/tareas"
    code = SPEC_CODE.search(spec_path.name)
    stem = f"SPEC-{code.group(1)}" if code else spec_path.stem
    return root / tasks_dir / f"{stem}-tareas.md"


def _test_files(root: Path, config: dict) -> list[Path]:
    files = []
    for comp in get_components(config):
        for base in _component_roots(root, comp):
            for path in _walk_files(base, []):
                if TEST_FILE.search(path.name) or {"tests", "__tests__", "e2e"} & set(path.parts):
                    files.append(path)
    return files


def criteria_without_tests(root: Path, config: dict, criteria: list[str]) -> list[str]:
    texts = []
    for path in _test_files(root, config):
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    corpus = "\n".join(texts)
    missing = []
    for ca in criteria:
        num = ca.split("-")[1:]
        pattern = rf"CA[-_]?{num[0]}[-_]?{num[1]}(?!\d)"
        if not re.search(pattern, corpus, re.IGNORECASE):
            missing.append(ca)
    return missing


def validate_spec_nnn(feature: dict, root: Path, config: dict, report: Report) -> None:
    name, status = feature["name"], feature["status"]
    strict = status in ("in_progress", "done")
    err = report.fail if strict else report.warn
    rel = feature.get("spec", "")
    spec_path = root / rel if rel else None
    if not spec_path or not spec_path.is_file():
        if status != "pending":
            report.fail(f"{name}: no existe el spec «{rel}» indicado en el campo `spec`")
        return
    spec = parse_spec_nnn(spec_path.read_text(encoding="utf-8"))
    code = SPEC_CODE.search(spec_path.name)
    number = code.group(1) if code else ""

    if not spec["estado"]:
        err(f"{name}: Estado «{spec['estado_raw']}» no es uno de {', '.join(SPEC_STATES)}")
    for req_id in spec["requirements"]:
        if number and req_id.split("-")[1] != number:
            err(f"{name}: {req_id} no corresponde a SPEC-{number}")
    for ca, missing in spec["criteria"].items():
        if number and ca.split("-")[1] != number:
            err(f"{name}: {ca} no corresponde a SPEC-{number}")
        required = [w for w in missing if w in ("dado", "entonces")]
        if required and ca in spec["criteria_refs"]:
            report.warn(f"{name}: {ca} remite a {spec['criteria_refs'][ca]} (sin Dado/Entonces propios)")
        elif required:
            err(f"{name}: {ca} no tiene {' ni '.join(w.capitalize() for w in required)}")
        elif missing:
            report.warn(f"{name}: {ca} sin «Cuando» (válido para invariantes; revisa que sea intencional)")
    if not spec["criteria"]:
        err(f"{name}: el spec no tiene criterios de aceptación `**CA-{number or 'NNN'}-NN**`")

    if not strict:
        return
    # Puerta humana: no se implementa sin spec Aprobada (con fecha y aprobadores).
    if spec["estado"] != "Aprobada":
        report.fail(f"{name}: la feature está en {status} pero {spec_path.name} está «{spec['estado_raw']}»; "
                    "el código no empieza hasta que la spec esté Aprobada")
    else:
        for field in ("fecha de aprobación", "aprobadores"):
            if not spec["fields"].get(field):
                report.fail(f"{name}: spec Aprobada sin «{field}» registrado")

    tasks_path = spec_tasks_path(root, config, spec_path)
    tasks_rel = tasks_path.relative_to(root).as_posix()
    if not tasks_path.is_file():
        report.fail(f"{name}: falta el plan de tareas {tasks_rel}")
        return
    tasks = parse_tasks(tasks_path.read_text(encoding="utf-8"))
    if not tasks:
        report.fail(f"{name}: {tasks_rel} no tiene tasks `- [ ] N. ...`")
        return
    known = set(spec["requirements"]) | set(spec["criteria"])
    covered: set[str] = set()
    for task in tasks:
        refs = [tok.strip() for raw in task["refs_raw"] for tok in raw.split(",") if tok.strip()]
        if task["leaf"] and not refs:
            report.fail(f"{name}: task {task['id']} sin `_Requisitos: RF-…, CA-…_` (o `_Requisitos: ninguno_`)")
        for ref in refs:
            if SPEC_REF.match(ref):
                if ref.split("-")[1] == number and ref not in known:
                    report.fail(f"{name}: task {task['id']} referencia {ref}, que no existe en {spec_path.name}")
                covered.add(ref)
    for ca in spec["criteria"]:
        if ca not in covered:
            report.fail(f"{name}: {ca} no está cubierto por ninguna task de {tasks_rel}")
    for req_id, priority in spec["requirements"].items():
        if req_id not in covered:
            if req_id.startswith("RF-") and priority in ("Must", ""):
                report.fail(f"{name}: {req_id} (Must) no está cubierto por ninguna task")
            else:
                report.warn(f"{name}: {req_id} no está cubierto por ninguna task")

    if status == "done":
        pending = [t["id"] for t in tasks if not t["checked"] and not t["optional"]]
        if pending:
            report.fail(f"{name}: feature `done` con tasks sin marcar: {', '.join(pending)}")
        for ca in criteria_without_tests(root, config, list(spec["criteria"])):
            ref = spec["criteria_refs"].get(ca)
            if ref and not criteria_without_tests(root, config, [ref]):
                continue  # remite a otro criterio que sí tiene test
            report.fail(f"{name}: ningún test cita {ca} (los tests deben nombrar el criterio que verifican)")


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
    project_docs = (config["paths"]["architecture_doc"], config["paths"]["conventions_doc"])
    for rel in (*BASE_FILES, config["paths"]["feature_list"], *project_docs):
        if (root / rel).is_file():
            report.ok(f"Existe {rel}")
        else:
            report.fail(f"Falta archivo base: {rel}")
    steering = root / config["paths"]["steering_dir"]
    for fname in STEERING_FILES:
        if not (steering / fname).is_file():
            report.warn(f"Falta steering {(steering / fname).relative_to(root).as_posix()}")
    to_fill = [steering / f for f in STEERING_FILES] + [root / d for d in project_docs]
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
    spec_nnn = config["spec"].get("format") == "spec-nnn"
    for feature in features:
        status = feature.get("status")
        if spec_nnn and feature.get("sdd") and "spec" in feature:
            failures_before = len(report.failures)
            validate_spec_nnn(feature, root, config, report)
            if status in REQUIRES_SPEC and len(report.failures) == failures_before:
                report.ok(f"{feature['name']} ({status}): spec y tareas válidos")
            continue
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


def get_components(config: dict) -> list[dict]:
    """Componentes de código del proyecto. Sin `[[components]]`: un único componente Python."""
    raw = config.get("components") or []
    if isinstance(raw, dict):
        raw = [raw]
    if not raw:
        return [{
            "name": "python", "path": config["paths"]["src_dir"], "tests_dir": config["paths"]["tests_dir"],
            "extensions": [".py"], "test": config["commands"]["test"],
            "test_fast": config["commands"].get("test_fast", ""), "test_related": "", "python": True,
        }]
    components = []
    for item in raw:
        exts = item.get("extensions") or []
        if isinstance(exts, str):
            exts = [e.strip() for e in exts.split(",") if e.strip()]
        test = str(item.get("test", ""))
        components.append({
            "name": str(item.get("name") or item.get("path") or "componente"),
            "path": str(item.get("path", ".")).strip("/") or ".",
            "tests_dir": str(item.get("tests_dir", "")).strip("/"),
            "extensions": [e if e.startswith(".") else f".{e}" for e in exts],
            "test": test,
            "test_fast": str(item.get("test_fast", "")),
            "test_related": str(item.get("test_related", "")),
            "python": "{python}" in test,
        })
    return components


def _walk_files(base: Path, extensions: list[str]):
    """Archivos con esas extensiones bajo `base`, sin entrar en dependencias ni builds."""
    if base.is_file():
        if not extensions or base.suffix in extensions:
            yield base
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for name in filenames:
            if not extensions or os.path.splitext(name)[1] in extensions:
                yield Path(dirpath, name)


def _component_roots(root: Path, comp: dict) -> list[Path]:
    roots = [(root / comp["path"]).resolve()]
    if comp["tests_dir"]:
        roots.append((root / comp["tests_dir"]).resolve())
    return roots


def component_for(root: Path, components: list[dict], edited: str) -> dict | None:
    """Componente al que pertenece el archivo editado (el de ruta más específica)."""
    try:
        path = Path(edited)
        path = (path if path.is_absolute() else root / path).resolve()
    except (OSError, ValueError):
        return None
    best, best_len = None, -1
    for comp in components:
        if comp["extensions"] and path.suffix not in comp["extensions"]:
            continue
        for base in _component_roots(root, comp):
            if (path == base or base in path.parents) and len(str(base)) > best_len:
                best, best_len = comp, len(str(base))
    return best


def _python_related(root: Path, src_dir: str, tests_dir: str, edited: str) -> list[Path] | None:
    tests_path = (root / tests_dir).resolve()
    try:
        path = Path(edited)
        path = (path if path.is_absolute() else root / path).resolve()
    except (OSError, ValueError):
        return None
    if tests_path in path.parents:
        return [path] if path.name.startswith("test") else None
    # Primero el módulo; si no tiene tests propios, el paquete que lo contiene
    # (availability/motor.py → test_motor.py, si no test_availability.py).
    names = [] if path.stem == "__init__" else [path.stem]
    src_path = (root / src_dir).resolve()
    for parent in path.parents:
        if parent in (src_path, root.resolve()) or src_path not in parent.parents:
            break
        names.append(parent.name)
    for stem in names:
        found: set[Path] = set()
        for pattern in (f"test_{stem}.py", f"test_{stem}_*.py", f"{stem}_test.py"):
            found.update(tests_path.rglob(pattern))
        if found:
            return sorted(found)
    return []


def related_tests(root: Path, config: dict, edited: str) -> list[Path] | None:
    """Tests Python relacionados con el archivo editado. None = no se sabe; [] = ninguno."""
    return _python_related(root, config["paths"]["src_dir"], config["paths"]["tests_dir"], edited)


def _code_fingerprint(root: Path, config: dict) -> float:
    latest = 0.0
    for comp in get_components(config):
        for base in _component_roots(root, comp):
            for path in _walk_files(base, comp["extensions"]):
                try:
                    latest = max(latest, path.stat().st_mtime)
                except OSError:
                    continue
    return latest


def _stamp_path(root: Path) -> Path:
    return root / CACHE_DIR / GREEN_STAMP


def code_unchanged_since_green(root: Path, config: dict) -> bool:
    try:
        stamp = float(_stamp_path(root).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return _code_fingerprint(root, config) <= stamp


def mark_green(root: Path, config: dict) -> None:
    try:
        _stamp_path(root).parent.mkdir(exist_ok=True)
        _stamp_path(root).write_text(str(_code_fingerprint(root, config)), encoding="utf-8")
    except OSError:
        pass


def _targeted(command: str, targets: list[Path], root: Path) -> str:
    rels = [os.path.relpath(t, root) for t in targets]
    if "pytest" in command:
        return command + " " + " ".join(_quote(r) for r in rels)
    if "unittest discover" in command:
        return " && ".join(f"{command} -p {_quote(Path(r).name)}" for r in rels)
    return command


def _run_component(root: Path, config: dict, comp: dict, report: Report, quiet_output: bool, *,
                   fast: bool, timeout: int | None, targets: list[Path] | None, label: str) -> tuple[str, bool]:
    """Corre los tests de un componente. Devuelve (salida, ¿quedó en verde o sin tests?)."""
    if comp["python"] and comp["tests_dir"]:
        if not (root / comp["tests_dir"]).is_dir():
            report.warn(f"{label}La carpeta {comp['tests_dir']}/ no existe todavía")
            return "", True
    elif not (root / comp["path"]).exists():
        report.warn(f"{label}{comp['path']}/ aún no existe: sin tests que correr")
        return "", True

    command = (comp["test_fast"] if fast else "") or comp["test"]
    files = ""
    if targets and comp["test_related"] and not comp["python"]:
        command = comp["test_related"]
        base = (root / comp["path"]).resolve()
        files = " ".join(_quote(os.path.relpath(t, base)) for t in targets)
    if not command:
        return "", True

    python = resolve_python(root, config)
    if "{python} -m pytest" in command and not _has_module(python, "pytest"):
        report.fail(
            f"{label}pytest no está instalado en {python}: instálalo (pip install pytest) "
            "o ajusta commands.python / el comando de tests en harness.toml"
        )
        return "", False
    tool = command.split()[0] if command.split() else ""
    if tool in KNOWN_TOOLS and shutil.which(tool) is None:
        report.fail(f"{label}no encuentro `{tool}` en el PATH para correr: {command}")
        return "", False

    command = (command.replace("{python}", _quote(python)).replace("{tests_dir}", comp["tests_dir"])
               .replace("{path}", comp["path"]).replace("{files}", files))
    if targets and comp["python"]:
        command = _targeted(command, targets, root)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        report.warn(
            f"{label}Los tests superaron {timeout}s y se cortaron; usa test_fast o sube "
            "hooks.*_timeout en harness.toml"
        )
        return "", True
    output = (proc.stdout + proc.stderr).strip()
    if not quiet_output and not report.quiet:
        print(output)
    if comp["python"]:
        ran_nothing = proc.returncode == NO_TESTS_EXIT_CODE or re.search(r"\bRan 0 tests?\b", output)
        tests_path = root / (comp["tests_dir"] or comp["path"])
        has_test_files = any(tests_path.rglob("test*.py")) or any(tests_path.rglob("*_test.py"))
        if ran_nothing and has_test_files:
            report.fail(f"{label}Hay archivos de test en {comp['tests_dir']}/ pero no se ejecutó ninguno: "
                        f"revisa el comando de tests ({command})")
            return output, False
        if ran_nothing:
            report.warn(f"{label}No se encontró ningún test")
            return output, True
    if proc.returncode == 0:
        report.ok(f"{label}Tests verdes ({command})")
        return output, True
    report.fail(f"{label}Hay tests rotos ({command}, exit {proc.returncode})")
    return output, False


def run_tests(root: Path, config: dict, report: Report, quiet_output: bool, *,
              fast: bool = False, timeout: int | None = None, targets: list[Path] | None = None,
              components: list[dict] | None = None) -> str:
    report.section("4. Tests")
    every = get_components(config)
    selected = components if components is not None else every
    multi = len(every) > 1
    outputs, all_green = [], True
    for comp in selected:
        label = f"[{comp['name']}] " if multi else ""
        output, green = _run_component(root, config, comp, report, quiet_output, fast=fast, timeout=timeout,
                                       targets=targets, label=label)
        outputs.append(output)
        all_green = all_green and green
    if all_green and not targets and components is None and not fast:
        mark_green(root, config)
    return "\n".join(o for o in outputs if o)


# ── Modos de ejecución ───────────────────────────────────────────────────────

def _read_hook_payload() -> dict:
    """Evento del hook por stdin. Claude Code lo envía y cierra stdin; si stdin es una terminal
    o un pipe que nadie cierra (ejecución manual), no se bloquea: espera 2 s y sigue."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
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
        return {}
    return payload if isinstance(payload, dict) else {}


def _edited_path(extensions: list[str] | None = None) -> str | None:
    """Ruta del archivo de código editado ("" si no se sabe; None si no es código)."""
    path = ((_read_hook_payload().get("tool_input") or {}).get("file_path", "")) or ""
    extensions = extensions or [".py"]
    return path if not path or Path(path).suffix in extensions else None


def _inside_component(root: Path, components: list[dict], edited: str) -> bool:
    try:
        path = Path(edited)
        path = (path if path.is_absolute() else root / path).resolve()
    except (OSError, ValueError):
        return False
    return any(path == base or base in path.parents
               for comp in components for base in _component_roots(root, comp)
               if base != root.resolve())


def implementation_allowed(root: Path, config: dict) -> bool:
    """¿Hay una feature en curso cuya spec esté aprobada? (puerta humana del flujo SDD)."""
    try:
        data = json.loads((root / config["paths"]["feature_list"]).read_text(encoding="utf-8-sig"))
        features = data["features"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    for feature in features:
        if feature.get("status") != "in_progress":
            continue
        if config["spec"].get("format") != "spec-nnn" or "spec" not in feature:
            return True  # en Kiro, in_progress ya implica spec aprobada
        spec_path = root / feature["spec"]
        if spec_path.is_file() and parse_spec_nnn(spec_path.read_text(encoding="utf-8"))["estado"] == "Aprobada":
            return True
    return False


def pre_hook(root: Path, config: dict) -> int:
    """Antes de editar código: avisa (o bloquea) si no hay una feature en curso con spec aprobada."""
    mode = str(config["hooks"].get("pre_gate", "warn")).lower()
    if mode == "off":
        return 0
    edited = ((_read_hook_payload().get("tool_input") or {}).get("file_path", "")) or ""
    if not edited or not _inside_component(root, get_components(config), edited):
        return 0
    if implementation_allowed(root, config):
        return 0
    message = (f"[harness] Vas a editar {edited} sin una feature en curso con spec Aprobada. "
               "El flujo SDD pide aprobar la spec antes de escribir código.")
    if mode == "block":
        print(message + " Edición bloqueada (hooks.pre_gate = \"block\").", file=sys.stderr)
        return 2
    print(message, file=sys.stderr)
    return 1


def post_hook(root: Path, config: dict) -> int:
    hooks = config["hooks"]
    components = get_components(config)
    edited = _edited_path(sorted({e for c in components for e in c["extensions"]}) or None)
    if not hooks.get("post_tests", True) or edited is None:
        return 0
    selected, targets = None, None
    if edited:
        comp = component_for(root, components, edited)
        if comp is None:
            return 0  # no pertenece a ningún componente de código
        selected = [comp]
        if hooks.get("post_scope", "related") == "related" and not comp["test_fast"]:
            if comp["python"]:
                targets = _python_related(root, comp["path"], comp["tests_dir"] or comp["path"], edited)
                if targets == []:
                    return 0  # sin tests relacionados: la suite completa corre en el hook Stop
            elif comp["test_related"]:
                targets = [Path(edited) if Path(edited).is_absolute() else root / edited]
    report = Report(quiet=True)
    output = run_tests(root, config, report, quiet_output=True, fast=True,
                       timeout=int(hooks.get("post_timeout", 150)), targets=targets, components=selected)
    if report.failures:
        tail = "\n".join(output.splitlines()[-15:])
        print(f"[harness] tests en rojo tras la edición:\n{tail}", file=sys.stderr)
        return 2
    return 0


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL)


SECRET_PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "clave privada"),
    (re.compile(r"AccountKey=[A-Za-z0-9+/=]{20,}"), "clave de cuenta de Azure Storage"),
    (re.compile(r"(?:SharedAccessSignature=|[?&]sig=)[A-Za-z0-9%+/=]{20,}"), "firma SAS de Azure"),
    (re.compile(r"(?i)(?:^|[;\"'])\s*(?:password|pwd)=[^;'\"\s]{4,}"), "contraseña en cadena de conexión"),
    (re.compile(r"(?i)(?:client[_-]?secret|api[_-]?key|secret|access[_-]?token)\w*['\"]?\s*[:=]\s*['\"]"
                r"(?P<value>[A-Za-z0-9_\-.~+/=]{16,})['\"]"),
     "secreto o token en el código"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "clave de AWS"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "token de GitHub"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "API key de Anthropic"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "token de Slack"),
)
SAFE_ENV_SUFFIXES = (".example", ".sample", ".template", ".dist")
# Valores claramente ficticios (fixtures de test) que no se tratan como secretos.
PLACEHOLDER_WORDS = re.compile(r"(?i)test|prueba|fake|dummy|example|ejemplo|mock|sample|placeholder|changeme|xxx")


def _looks_random(value: str) -> bool:
    """Un secreto real es aleatorio: mezcla de clases de caracteres y alta entropía."""
    if PLACEHOLDER_WORDS.search(value):
        return False
    classes = sum(bool(re.search(p, value)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    counts = {c: value.count(c) for c in set(value)}
    entropy = -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())
    return classes >= 3 and entropy >= 3.5
FORBIDDEN_EXTENSIONS = (".pbix", ".pfx", ".pem", ".key", ".p12")


def _forbidden_file(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1].lower()
    if name == ".env" or (name.startswith(".env.") and not name.endswith(SAFE_ENV_SUFFIXES)):
        return "archivo .env"
    if name.endswith(FORBIDDEN_EXTENSIONS):
        return f"archivo {name.rsplit('.', 1)[-1]}"
    return ""


def scan_staged_secrets(root: Path) -> list[str]:
    """Problemas en lo que está en stage: `ruta:línea — tipo` (nunca muestra el secreto)."""
    findings = []
    for rel in _git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR").stdout.splitlines():
        kind = _forbidden_file(rel)
        if kind:
            findings.append(f"{rel} — {kind} (no se versiona)")
    current, line_no = "", 0
    for line in _git(root, "diff", "--cached", "-U0", "--diff-filter=ACMR").stdout.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            line_no = int(match.group(1)) if match else 0
        elif line.startswith("+") and not line.startswith("+++"):
            for pattern, kind in SECRET_PATTERNS:
                match = pattern.search(line[1:])
                if match and ("value" not in pattern.groupindex or _looks_random(match.group("value"))):
                    findings.append(f"{current}:{line_no} — {kind}")
                    break
            line_no += 1
    return findings


def commit_feature(root: Path, config: dict, name: str, verbose: bool = False) -> int:
    """Commit de una feature cerrada si y solo si la verificación completa (con tests) está en verde."""
    git_config = config.get("git", {})
    if not git_config.get("auto_commit", True):
        print("[harness] git.auto_commit = false en harness.toml: no se hace commit.")
        return 0
    try:
        inside = _git(root, "rev-parse", "--is-inside-work-tree")
    except OSError:
        print("[harness] git no está disponible: no se hace commit.")
        return 0
    if inside.returncode != 0:
        print("[harness] el proyecto no es un repositorio git: no se hace commit.")
        return 0

    feature_list = config["paths"]["feature_list"]
    try:
        features = json.loads((root / feature_list).read_text(encoding="utf-8-sig"))["features"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"[FAIL]  No se pudo leer {feature_list}: {exc}. No se hace commit.")
        return 1
    feature = next((f for f in features if f.get("name") == name), None)
    if feature is None:
        print(f"[FAIL]  La feature «{name}» no existe en {feature_list}. No se hace commit.")
        return 1
    if feature.get("status") != "done":
        print(f"[FAIL]  La feature «{name}» está en {feature.get('status')}, no en done. No se hace commit.")
        return 1

    report = Report(verbose=verbose)
    check_environment(config, report)
    check_base_files(root, config, report)
    check_features(root, config, report)
    run_tests(root, config, report, quiet_output=True)
    report.section("5. Commit")
    if report.failures:
        print(f"[FAIL]  Verificación en rojo ({len(report.failures)} error(es)): NO se hace commit.")
        return 1

    _git(root, "add", "-A")
    if git_config.get("secret_scan", True):
        findings = scan_staged_secrets(root)
        if findings:
            _git(root, "reset", "-q")
            print("[FAIL]  Posibles secretos o archivos prohibidos en el commit: NO se hace commit.")
            for finding in findings:
                print(f"          - {finding}")
            print("        Quítalos (o agrégalos a .gitignore) y vuelve a ejecutar --commit.")
            return 1
    if _git(root, "diff", "--cached", "--quiet").returncode == 0:
        print("[OK]    Todo en verde y no hay cambios pendientes: nada que commitear.")
        return 0
    title = feature.get("title") or name
    spec = feature.get("spec") or f"{config['paths']['specs_dir']}/{name}/"
    message = (f"{name}: {title}\n\nFeature #{feature.get('id')} cerrada por el arnés SDD "
               f"(reviewer APPROVED, verificación y tests en verde).\n"
               f"Spec: {spec}\nTrazabilidad: progress/impl_{name}.md\nReview: progress/review_{name}.md\n")
    result = _git(root, "commit", "-q", "-m", message)
    if result.returncode != 0:
        print(f"[FAIL]  git commit falló: {(result.stderr or result.stdout).strip()}")
        return 1
    sha = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    print(f"[OK]    Commit {sha}: {name}: {title}")

    if git_config.get("auto_push", False):
        pushed = _git(root, "push")
        if pushed.returncode != 0:
            print(f"[FAIL]  El commit {sha} quedó en local pero git push falló: {(pushed.stderr or '').strip()}")
            return 1
        print(f"[OK]    Push hecho ({sha}).")
    return 0


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
    parser.add_argument("--hook", choices=("pre", "post", "stop"), help="modo hook de Claude Code")
    parser.add_argument("--verbose", action="store_true", help="detalle completo de specs importados")
    parser.add_argument("--commit", metavar="FEATURE",
                        help="commit de una feature done si y solo si toda la verificación (con tests) pasa")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    config = load_config(root)
    if args.commit:
        return commit_feature(root, config, args.commit, verbose=args.verbose)

    hooks = config["hooks"]
    if args.hook == "post":
        return post_hook(root, config)
    if args.hook == "pre":
        return pre_hook(root, config)

    report = Report(quiet=args.hook == "stop", verbose=args.verbose)
    if not args.tests_only:
        check_environment(config, report)
        check_base_files(root, config, report)
        check_features(root, config, report)
    if not args.no_tests:
        if args.hook == "stop" and code_unchanged_since_green(root, config):
            report.ok("Sin cambios de código desde el último verde: no se repiten los tests")
        else:
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
