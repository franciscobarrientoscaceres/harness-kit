# CHECKPOINTS — Evaluación del estado final

> En sistemas multi-agente no se evalúa el camino, se evalúa el destino.
> Estos son los checkpoints objetivos que un juez (humano o IA) puede usar
> para decidir si el proyecto está sano.

## C1 — El arnés está completo

- [ ] Existen los archivos base: `AGENTS.md`, `CLAUDE.md`, `init.sh`, `init.ps1`,
      `harness.toml`, `{{FEATURE_LIST}}`, `progress/current.md`, `progress/history.md`.
- [ ] Existen los docs: `{{ARCH_DOC}}`, `{{CONV_DOC}}`,
      `docs/harness/proceso-sdd.md`, `docs/harness/verificacion.md`.
- [ ] Existen los steering files `.kiro/steering/{product,tech,structure}.md`
      sin secciones `<!-- RELLENAR -->` pendientes.
- [ ] `./init.sh` (o `./init.ps1`) termina con exit code 0.

## C2 — El estado es coherente

- [ ] Como mucho una feature en `in_progress` en `{{FEATURE_LIST}}`.
- [ ] Toda feature `done` tiene tests asociados que pasan.
- [ ] `progress/current.md` está vacío o describe la sesión activa
      (no contiene basura de sesiones anteriores).

## C3 — El código respeta la arquitectura

- [ ] El código ({{CODE_DIRS}}) respeta las capas y el layout de `.kiro/steering/structure.md`
      y `{{ARCH_DOC}}`.
- [ ] No hay dependencias nuevas que no estén permitidas en `.kiro/steering/tech.md`.
- [ ] No hay `print()` sueltos para debug, ni TODOs sin contexto.

## C4 — La verificación es real

- [ ] Cada módulo público del código tiene al menos un test.
- [ ] Los tests verifican resultados concretos (no solo "no lanza excepción").
- [ ] Los tests que tocan disco usan directorios temporales reales, no mocks del filesystem.
- [ ] `{{TEST_CMD_DISPLAY}}` ejecuta > 0 tests y todos verdes.

## C5 — La sesión se cerró bien

- [ ] No hay archivos sin trackear sospechosos (`*.tmp`, `__pycache__`
      fuera del `.gitignore`).
- [ ] `progress/history.md` tiene una entrada por la última sesión.
- [ ] La última feature trabajada está reflejada en su estado correcto.

## C6 — Spec Driven Development (Kiro-style)

- [ ] Toda feature con `"sdd": true` en `spec_ready`, `in_progress` o `done`
      tiene `{{SPECS_DIR}}/<name>/` con `requirements.md`, `design.md` y `tasks.md`.
- [ ] Cada `### Requisito N` tiene **Historia de usuario** y **Criterios de
      aceptación** numerados en EARS estricto (un solo `DEBE` por criterio).
- [ ] Toda task hoja de `tasks.md` cita `_Requisitos: N.M_` existentes y todo
      criterio está cubierto por al menos una task.
- [ ] Toda feature `done` tiene todas sus tasks no opcionales marcadas `[x]`.
- [ ] Cada criterio `N.M` está mapeado a un test existente en
      `progress/impl_<name>.md`.

---

**Cómo usar este archivo:** el agente revisor (`.claude/agents/reviewer.md`)
recorre cada checkbox, marca `[x]` o `[ ]`, y rechaza el cierre si quedan
boxes vacíos en C1-C6. `tools/harness_check.py` automatiza buena parte de C1,
C2 y C6.
