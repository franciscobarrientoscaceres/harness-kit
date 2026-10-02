# Spec Driven Development (SDD) — formato Kiro

> Flujo Kiro-style: **requirements → design → tasks → code**.
> El código no se escribe hasta que un humano aprueba el spec.
> Ejemplo canónico completo en `docs/harness/ejemplo-spec/`.

## Estructura

Cada feature con `"sdd": true` en `{{FEATURE_LIST}}` tiene su carpeta en
cuanto deja `pending`:

```
{{SPECS_DIR}}/<feature-name>/
├── requirements.md   # QUÉ se necesita: historias de usuario + criterios EARS
├── design.md         # CÓMO se construirá: arquitectura, interfaces, errores, testing
└── tasks.md          # PASOS concretos, jerárquicos, con trazabilidad a criterios
```

`<feature-name>` coincide con el campo `name` de `{{FEATURE_LIST}}`.

El contexto permanente del proyecto (lo que en Kiro son los *steering files*)
vive en `.kiro/steering/{product,tech,structure}.md`. El `spec_author` los lee
antes de redactar y **no** repite su contenido en cada spec.

## Estados de una feature

| Estado         | Significado                                                    |
|----------------|----------------------------------------------------------------|
| `pending`      | Sin spec. El `spec_author` es el primero en actuar.            |
| `spec_ready`   | Spec redactado. Esperando aprobación humana. NO se toca código.|
| `in_progress`  | Spec aprobado. `implementer` trabajando.                       |
| `done`         | Código verde, `reviewer` aprobó, sesión cerrada.               |
| `blocked`      | Atascado. Razón en `progress/current.md` o `progress/spec_<name>.md`. |

## La puerta de aprobación humana

El flujo automático se detiene **una vez**: cuando el `spec_author` termina
sus tres archivos, marca la feature como `spec_ready` y para. El humano lee
`{{SPECS_DIR}}/<feature>/` y dice "aprobado" (o pide cambios, y el `spec_author` itera).

Solo entonces el `leader` transiciona `spec_ready → in_progress` y lanza el
`implementer`.

```
pending → [spec_author] → spec_ready → ⏸ HUMANO → in_progress → [implementer → reviewer] → done
```

---

## requirements.md

Estructura fija (es lo que valida `tools/harness_check.py`):

```markdown
# Requirements — <feature-name>

## Introducción

Uno o dos párrafos: qué problema resuelve la feature y su alcance.
Qué queda FUERA de alcance.

## Requisitos

### Requisito 1: <nombre corto>

**Historia de usuario:** Como <rol>, quiero <capacidad>, para <beneficio>.

#### Criterios de aceptación

1. CUANDO <disparador> ENTONCES el sistema DEBE <respuesta>.
2. SI <condición no deseada> ENTONCES el sistema DEBE <respuesta>.

### Requisito 2: <nombre corto>
...
```

Cada criterio tiene un id estable **`N.M`** (requisito N, criterio M): `1.1`,
`1.2`, `2.1`… Es el id que usan `tasks.md`, los tests y el reviewer.

### Criterios en EARS estricto

| Patrón         | Plantilla                                                          |
|----------------|--------------------------------------------------------------------|
| **Ubicuo**     | `El sistema DEBE <acción>.`                                        |
| **Evento**     | `CUANDO <disparador> ENTONCES el sistema DEBE <acción>.`           |
| **Estado**     | `MIENTRAS <estado> el sistema DEBE <acción>.`                      |
| **Opcional**   | `DONDE <feature opcional> el sistema DEBE <acción>.`               |
| **No deseado** | `SI <evento no deseado> ENTONCES el sistema DEBE <acción>.`        |
| **Combinado**  | `MIENTRAS <estado>, CUANDO <disparador> ENTONCES el sistema DEBE <acción>.` |

Reglas duras:

- Un solo `DEBE` (o `NO DEBE`) por criterio. Si hay dos, parte en dos criterios.
- Nada de verbos blandos ("podría", "puede", "soporta", "debería").
- Cada criterio DEBE ser verificable por al menos un test concreto.
- Todo criterio del `acceptance` de `{{FEATURE_LIST}}` queda cubierto por
  algún criterio `N.M`. Cierra el archivo con una tabla de trazabilidad
  `acceptance → N.M`.
- También se acepta la variante en inglés de Kiro (`### Requirement N`,
  `**User Story:**`, `#### Acceptance Criteria`, `SHALL`), pero no mezcles
  idiomas dentro de un mismo spec.

## design.md

Secciones fijas (las de Kiro, más "Alternativas descartadas"):

1. **Overview** — resumen de la solución en un párrafo.
2. **Arquitectura** — dónde encaja en `.kiro/steering/structure.md`. Diagrama
   `mermaid` opcional si hay más de dos componentes.
3. **Componentes e interfaces** — archivos que se crean/modifican, firmas
   nuevas (funciones, clases, comandos, endpoints).
4. **Modelos de datos** — estructuras nuevas o cambios de esquema.
5. **Manejo de errores** — qué excepciones se reutilizan/añaden y cómo se
   presentan al usuario.
6. **Estrategia de testing** — qué tests se escriben y qué criterios cubre
   cada uno.
7. **Alternativas descartadas** — mínimo una, con el porqué.

NO es ingeniería desde primeros principios: apóyate en
`{{ARCH_DOC}}`, `{{CONV_DOC}}` y los steering files, y documenta
solo donde tu feature roza sus fronteras.

## tasks.md

Checklist jerárquico, en orden de ejecución, orientado a código (cada task
es algo que un agente puede hacer escribiendo o modificando código y tests):

```markdown
# Tasks — <feature-name>

- [ ] 1. Crear el modelo de datos
  - Añadir `Foo` en `src/foo.py` con validación de campos
  - _Requisitos: 1.1, 1.2_

- [ ] 2. Implementar el comando
- [ ] 2.1 Añadir `cmd_foo` en `src/cli.py`
  - Validar argumentos y delegar en `Foo`
  - _Requisitos: 2.1_
- [ ] 2.2 Escribir tests de `cmd_foo`
  - `test_foo_ok`, `test_foo_invalid_arg`
  - _Requisitos: 2.1, 2.2_

- [ ]* 3. Smoke test manual documentado
  - _Requisitos: ninguno_
```

Reglas:

- Ids `N` (task padre) y `N.M` (sub-task). Las sub-tasks también pueden ir
  indentadas bajo su padre.
- Toda task **hoja** (sin sub-tasks) cita `_Requisitos: N.M, ..._`. Se puede
  citar un requisito entero (`_Requisitos: 2_`). Para una task deliberadamente
  sin requisito: `_Requisitos: ninguno_`.
- Todo criterio `N.M` de `requirements.md` queda cubierto por al menos una task.
- `- [ ]*` marca una task **opcional**: puede quedar sin hacer en `done`.
- Cada task de código va acompañada (en ella o en la siguiente) de su test.
- El `implementer` marca `[x]` al completar cada task. El `reviewer` rechaza si
  queda alguna `[ ]` no opcional.

## Trazabilidad (regla dura)

- Cada criterio `N.M` tiene al menos un test concreto.
- Cada test nuevo debe poder mapearse a un criterio.
- El `implementer` documenta el mapa en `progress/impl_<name>.md`:

```markdown
## Trazabilidad
- 1.1 → `test_foo_requires_name`
- 1.2 → `test_foo_rejects_empty_name`
- 2.1 → `test_cmd_foo_prints_id`, `test_cmd_foo_exit_code_zero`
```

`tools/harness_check.py` comprueba que todo criterio aparece en el mapa y que
cada test citado existe en los tests del proyecto. El `reviewer`
además lee los tests para confirmar que de verdad verifican el criterio.

## Cuándo NO aplica SDD

Features con `"sdd": false` o sin el campo `sdd` (legacy, bugfixes triviales)
no tienen spec. SDD se aplica hacia adelante: no hace falta escribir specs
retroactivos para el código que ya existía al instalar el arnés.

## Specs legacy e importados

| Caso | Cómo se detecta | Qué hace `./init.sh` | Cómo normalizarlo |
|------|-----------------|----------------------|-------------------|
| **Legacy** (arnés anterior, `## R1`, `## R2`…) | `requirements.md` sin `### Requisito N` | Avisa; en `done` solo exige tasks `[x]` | Pide al leader «migra los specs legacy a formato Kiro» |
| **Importado** (spec que ya existía, p. ej. de Kiro) | `"imported": true` en `{{FEATURE_LIST}}` (lo pone el instalador) | Valida formato Kiro pero los errores son avisos; en `done` no exige trazabilidad | Pide al leader que lo migre; al terminar se quita `imported` |

Las features nuevas nunca llevan `imported`: se validan en modo estricto.

## Formato spec-nnn (un archivo por spec)

Para proyectos que ya escriben sus specs como `SPEC-NNN-*.md` (cabecera con
`Estado`, requisitos `RF/RN/RNF-NNN-xx`, criterios `CA-NNN-xx` en
Dado / Cuando / Entonces). Se activa con `[spec] format = "spec-nnn"` en
`harness.toml`; el instalador lo detecta solo.

| Pieza | Dónde |
|---|---|
| Spec (lo que se aprueba) | `{{SPECS_DIR}}/SPEC-NNN-*.md`, con la plantilla del proyecto |
| Plan de tareas (lo genera el `spec_author`) | `{{TASKS_DIR}}/SPEC-NNN-tareas.md`, tasks jerárquicas con `_Requisitos: RF-…, CA-…_` |
| Feature | entrada en `{{FEATURE_LIST}}` con `"spec": "<ruta del SPEC>"` |

```
pending (SPEC Borrador/En revisión) → ⏸ HUMANO/EQUIPO APRUEBA EL SPEC → [spec_author: tareas] → in_progress → [implementer → reviewer] → done → commit
```

**La puerta humana es el `Estado` del SPEC.** Solo el humano declara un SPEC
`Aprobada` (con fecha y aprobadores); los agentes nunca lo hacen por su cuenta.
Después ya no hay otra pausa: el plan de tareas se genera y se implementa.

`./init.sh` comprueba:

- Un SPEC en `Borrador`/`En revisión`: formato (Estado válido, IDs del mismo
  número de spec, cada `CA` con **Dado** y **Entonces**). Solo avisos.
- Una feature en `in_progress` o `done`: su SPEC está `Aprobada` con fecha y
  aprobadores (si no, **falla**: el código no empieza sin aprobación), existe
  el plan de tareas y cubre todo `CA` y todo `RF` *Must*.
- Una feature `done`: tasks no opcionales en `[x]` y **cada `CA` citado en
  algún test** (`it("CA-002-03: …")`, `test_ca_002_03_…`).
