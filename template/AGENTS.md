<!-- harness-kit -->
# AGENTS.md — Mapa de navegación para agentes de IA

> Este archivo es el **punto de entrada** para cualquier agente que trabaje en
> **{{PROJECT_NAME}}**. NO es una biblia de reglas: es un **mapa**. Lee solo lo
> que necesites cuando lo necesites (divulgación progresiva).

---

## 1. Antes de empezar (obligatorio)

1. Ejecuta `./init.sh` (Linux/macOS/Git Bash) o `./init.ps1` (PowerShell) y
   verifica que termina sin errores. Si falla, **para** y resuelve el entorno
   antes de tocar código.
2. Lee `progress/current.md` para entender en qué estado quedó la última sesión.
3. Lee `{{FEATURE_LIST}}`. Toda feature nueva (`"sdd": true`) pasa por
   **Spec Driven Development** — ver `docs/sdd.md` y §4 de este archivo.
4. Lee el contexto permanente del proyecto en `.kiro/steering/`
   (`product.md`, `tech.md`, `structure.md`).

## 2. Mapa del repositorio

| Archivo / carpeta            | Qué contiene                                                                | Cuándo leerlo |
|------------------------------|-----------------------------------------------------------------------------|---------------|
| `{{FEATURE_LIST}}`          | Features con estado (`pending` / `spec_ready` / `in_progress` / `done` / `blocked`) | Siempre, al empezar |
| `progress/current.md`        | Estado de la sesión actual                                                  | Siempre, al empezar |
| `progress/history.md`        | Bitácora append-only de sesiones anteriores                                 | Si necesitas contexto histórico |
| `.kiro/steering/product.md`  | Qué es el producto, para quién, objetivos                                   | Antes de redactar un spec |
| `.kiro/steering/tech.md`     | Stack, comandos, dependencias permitidas                                    | Antes de diseñar o implementar |
| `.kiro/steering/structure.md`| Layout de carpetas y capas                                                  | Antes de diseñar o implementar |
| `{{SPECS_DIR}}/<feature>/`           | `requirements.md` + `design.md` + `tasks.md` (Kiro-style)                   | Antes de implementar cualquier feature con `"sdd": true` |
| `docs/spec-example/`           | Ejemplo canónico del formato Kiro                                           | Al redactar un spec nuevo |
| `docs/architecture.md`       | Qué significa "hacer un buen trabajo" en este proyecto                      | Antes de implementar |
| `docs/conventions.md`        | Reglas de estilo, nombres, errores, tests                                   | Antes de escribir código |
| `docs/sdd.md`              | Proceso SDD: formato Kiro, EARS, puerta de aprobación humana                | Antes de redactar o leer un spec |
| `docs/verification.md`       | Cómo verificar que tu trabajo funciona (incluye trazabilidad)               | Antes de declarar una tarea como `done` |
| `CHECKPOINTS.md`             | Criterios objetivos de "estado final correcto"                              | Para auto-evaluarte |
| `harness.toml`               | Rutas (`src_dir`, `tests_dir`) y comando de tests                           | Si necesitas saber dónde está el código |
| `tools/harness_check.py`     | Validador del arnés (lo llaman `init.sh`, `init.ps1` y los hooks)           | Si `init` falla y no entiendes por qué |
| `.claude/agents/`            | Subagentes `leader`, `spec_author`, `implementer`, `reviewer`               | Si orquestas trabajo |
| `{{SRC_DIR}}/`               | Código de la aplicación                                                     | Para implementar |
| `{{TESTS_DIR}}/`             | Tests automáticos (`{{TEST_CMD_DISPLAY}}`)                                  | Para verificar |

## 3. Reglas duras (no negociables)

- **Una sola feature a la vez.** No mezcles cambios de varias features en la misma sesión.
- **No declares una feature `done` sin pruebas verdes.** Ejecuta `./init.sh` y
  asegúrate de que termina en verde.
- **No saltes la fase de spec.** Toda feature con `"sdd": true` pasa por
  `spec_author` y obtiene aprobación humana antes de tocar código.
- **No saltes la puerta de aprobación humana.** El leader detiene el flujo
  en `spec_ready` y espera.
- **Documenta lo que haces** en `progress/current.md` mientras trabajas, no al final.
- **Deja el repositorio limpio** antes de cerrar la sesión (ver §5).
- **Si no sabes algo, busca en `docs/` y `.kiro/steering/`** antes de inventarlo.

## 4. Flujo de trabajo (SDD)

```
pending → [spec_author] → spec_ready → ⏸ HUMANO → in_progress → [implementer → reviewer] → done
```

1. El leader detecta la primera feature `pending` con `"sdd": true`.
2. El leader lanza `spec_author`, que crea
   `{{SPECS_DIR}}/<name>/{requirements,design,tasks}.md` en formato Kiro y marca el
   status como `spec_ready`.
3. **Pausa.** El humano lee el spec en `{{SPECS_DIR}}/<name>/` y aprueba (o pide cambios).
4. Una vez aprobado, el leader cambia el status a `in_progress` y lanza `implementer`.
5. El implementer ejecuta `tasks.md` en orden, marcando cada task `[x]`.
6. El reviewer verifica trazabilidad criterio `N.M` ↔ test y tasks completas;
   aprueba o rechaza.
7. Si aprueba, el implementer marca `done` y mueve el resumen a
   `progress/history.md`.

## 5. Cierre de sesión (lifecycle)

Antes de terminar:

1. Ejecuta `./init.sh` — todo verde.
2. Si la feature está acabada: `status: "done"` en `{{FEATURE_LIST}}`.
3. Mueve el resumen de `progress/current.md` al final de `progress/history.md`.
4. Vacía `progress/current.md` dejando solo la plantilla.
5. No dejes archivos temporales, ni `print()` de debug, ni TODOs sin contexto.

## 6. Si te bloqueas

- Relee la sección relevante de `docs/` y `.kiro/steering/`.
- Si la herramienta no hace lo que esperas, **no inventes un workaround**:
  documenta el bloqueo en `progress/current.md` y para la sesión.
