# harness-kit

Kit reutilizable de **Harness Engineering + Spec Driven Development estilo Kiro**
para proyectos Python trabajados con Claude Code.

Se instala sobre cualquier repo existente y lo convierte en un entorno donde un
agente de IA trabaja de forma autónoma pero **verificable**: primero escribe un
spec que tú apruebas, luego implementa contra ese spec, y un revisor
independiente comprueba que cada criterio tiene su test.

> Basado en el repo didáctico `harness-sdd` (un CLI de notas), generalizado,
> adaptado al formato Kiro completo y portable a Windows.

## Los 4 pilares

| Pilar                               | Qué instala el kit                                                               |
|-------------------------------------|----------------------------------------------------------------------------------|
| **1. El repositorio ES el sistema** | `AGENTS.md`, `feature_list.json`, `progress/`, `harness.toml`, `init.sh`/`init.ps1` |
| **2. Orquestación multi-agente**    | `.claude/agents/{leader,spec_author,implementer,reviewer}.md` + `CLAUDE.md`      |
| **3. Spec Driven Development**      | `docs/harness/proceso-sdd.md`, `docs/harness/ejemplo-spec/`, `.kiro/steering/`, puerta de aprobación humana |
| **4. Supervisión y mejora**         | `tools/harness_check.py`, hooks en `.claude/settings.json`, `CHECKPOINTS.md`     |

## Flujo

```
pending → [spec_author] → spec_ready → ⏸ HUMANO → in_progress → [implementer → reviewer] → done
```

- **leader** (tu sesión principal, forzado por `CLAUDE.md`): orquesta, nunca edita código.
- **spec_author**: escribe `specs/<feature>/{requirements,design,tasks}.md` y para.
- **Tú** lees el spec y dices «aprobado» (o pides cambios).
- **implementer**: ejecuta `tasks.md` task a task, con tests, y documenta el mapa criterio → test.
- **reviewer**: aprueba o rechaza contra el spec, los docs y `CHECKPOINTS.md`.

Los subagentes escriben sus resultados en disco (`specs/`, `progress/`) y solo
devuelven una línea con la ruta: el estado sobrevive a reinicios y no se
degrada al pasar por el chat.

## Formato de spec (Kiro)

**requirements.md** — historias de usuario + criterios EARS numerados `N.M`:

```markdown
### Requisito 1: Listar las notas más recientes

**Historia de usuario:** Como usuario del CLI, quiero ver mis notas más
recientes, para retomar rápidamente lo último que anoté.

#### Criterios de aceptación

1. CUANDO el usuario ejecuta `recent` sin `--limit` ENTONCES el sistema DEBE
   imprimir como máximo 5 notas en stdout.
2. SI `--limit` recibe un valor `<= 0` ENTONCES el sistema DEBE salir con
   exit code distinto de `0`.
```

**design.md** — Overview, Arquitectura, Componentes e interfaces, Modelos de
datos, Manejo de errores, Estrategia de testing, Alternativas descartadas.

**tasks.md** — checklist jerárquico con trazabilidad:

```markdown
- [ ] 1. Implementar el comando `recent`
- [ ] 1.1 Añadir `cmd_recent(args)` en `src/cli.py`
  - _Requisitos: 1.1, 1.2_
- [ ]* 2. Smoke test manual (opcional)
  - _Requisitos: ninguno_
```

Ejemplo completo en [`template/docs/harness/ejemplo-spec/`](template/docs/harness/ejemplo-spec/).
También se acepta la variante en inglés de Kiro (`Requirement`, `User Story`,
`Acceptance Criteria`, `SHALL`). El contexto permanente va en los *steering
files* `.kiro/steering/{product,tech,structure}.md`, igual que en Kiro.

## Formato de spec SPEC-NNN (alternativo)

Si el proyecto ya escribe sus specs como `SPEC-NNN-*.md` de un solo archivo
(cabecera con `Estado`, requisitos `RF/RN/RNF-NNN-xx`, criterios `CA-NNN-xx` en
**Dado / Cuando / Entonces**), el kit lo detecta y **no** lo convierte a Kiro:

- El SPEC es lo que el equipo aprueba; la **puerta humana es su `Estado`**.
  Solo el humano lo declara `Aprobada` (con fecha y aprobadores).
- Con el SPEC aprobado, el `spec_author` genera el plan de tareas
  (`docs/specs/tareas/SPEC-NNN-tareas.md`, con `_Requisitos: RF-…, CA-…_`) y se
  implementa sin más pausas.
- `./init.sh` **falla** si una feature en curso tiene su SPEC sin aprobar, si el
  plan no cubre todos los `CA` y `RF` *Must*, o si al cerrar algún `CA` no está
  citado en un test (`it("CA-002-03: …")`, `test_ca_002_03_…`). Un `CA` que
  remite a otro («Igual que CA-001-05») se acepta.

Detalle en `docs/harness/proceso-sdd.md` del proyecto instalado.

## Proyectos multi-stack (componentes)

Sin configuración, el proyecto es Python (`src/` + `tests/`). Para otros stacks
o monorepos, cada carpeta de código se declara como componente en `harness.toml`:

```toml
[[components]]
name = "api"
path = "api"
extensions = [".js", ".ts"]
test = "npm --prefix api test --silent"
test_related = "npm --prefix api exec -- vitest related {files} --run"   # opcional

[[components]]
name = "sql"
path = "sql"
extensions = [".sql"]          # sin `test`: se vigila pero no tiene suite
```

- El instalador crea un componente por cada `package.json` (detecta Vitest/Jest
  para `test_related`), o se pasan con `--component nombre=ruta:.ext,.ext:comando`.
- `./init.sh` corre la suite de cada componente; un componente cuya carpeta aún
  no existe es un aviso, no un error (proyectos en fase de especificación).
- El hook tras cada edición corre solo los tests del componente editado (o los
  relacionados, si hay `test_related`); el recorrido ignora `node_modules/`,
  `.venv/`, `dist/`, etc.

## Instalación en un proyecto

Requisitos: Python ≥ 3.9 (solo stdlib).

```bash
git clone https://github.com/<tu-usuario>/harness-kit
python harness-kit/install.py ../mi-proyecto
```

Por defecto el instalador **detecta qué hay en el proyecto** y actúa en
consecuencia. Siempre puedes ver qué haría antes con `--dry-run`.

### Qué detecta y qué hace

| Situación en el proyecto | Qué hace el instalador |
|--------------------------|------------------------|
| Proyecto sin arnés | Instala todo. |
| **Otro arnés de agentes/SDD** (agentes `leader`/`spec_author`/…, `CLAUDE.md` de arnés, `feature_list.json`, specs `## R<n>`) | **Se detiene** y te pide elegir `--adopt` (recomendado) o `--keep-existing`. Así nunca quedan dos juegos de instrucciones contradictorias. |
| `CLAUDE.md` / `AGENTS.md` **propios** (no de un arnés) | Los conserva: crea `CLAUDE.harness.md` / `AGENTS.harness.md` y añade una referencia al final del tuyo (`@CLAUDE.harness.md` es un import nativo de Claude Code). |
| `.claude/settings.json` existente | Lo **fusiona**: conserva tus hooks y permisos, añade los del kit sin duplicar. |
| Hooks de un arnés anterior (`python3 … \| tail`, `/tmp/…`, `init.sh`) | Con `--adopt` los retira; si no, te avisa. |
| Specs de **Kiro** en `.kiro/specs/` | Usa `.kiro/specs/` como carpeta de specs e **importa** cada spec a la lista de features con `"imported": true` (`done` si todas sus tasks están `[x]`, si no `spec_ready`). |
| **GitHub spec-kit** (`.specify/`, `specs/NNN-*/spec.md`) | No toca sus specs; los del arnés van a `.kiro/specs/`. |
| `feature_list.json` con **otro formato** (p. ej. el array de *long-running agents*) | No lo toca; el arnés usa `sdd_features.json`. |
| Agentes propios con roles parecidos (`code-reviewer`, `planner`…) | Te avisa de posible solapamiento. |
| Virtualenv (`.venv/`, `venv/`, `env/`) | Los tests se ejecutan con su intérprete. |
| Specs `SPEC-NNN-*.md` con campo `Estado` | Formato `spec-nnn`: importa cada SPEC como feature `pending` y usa su carpeta. Un repo en fase de especificación se acepta aunque aún no tenga código. |
| `package.json` (raíz o subcarpetas) | Un componente Node por cada uno, con `npm test`. |
| `docs/SDD.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`… | Los usa como documento de arquitectura/convenciones en vez de crear los suyos. |
| Repo sin código Python ni specs (documentación, presentaciones) | Se detiene y lo explica; `--force` para instalar igual. |
| `docs/architecture.md`, `docs/conventions.md`… propios | Se conservan (son contenido del proyecto). |

Al terminar ejecuta la verificación del arnés y te dice si quedó en verde.

### Modos

| Flag              | Para qué |
|-------------------|----------|
| *(ninguno)*       | Instalación segura: no pisa nada. |
| `--adopt`         | Migra un arnés previo: reemplaza sus agentes, `CLAUDE.md`/`AGENTS.md` **solo si son de arnés**, `CHECKPOINTS.md`, `docs/harness/verificacion.md`, retira `docs/specs.md` y los hooks antiguos. **Todo lo que reemplaza o retira queda en `.harness-backup/<fecha>/`** y se anota en `progress/history.md`. Conserva `feature_list.json`, specs, `progress/` y tus docs. |
| `--upgrade`       | Actualiza un harness-kit ya instalado (validador, agentes, `docs/harness/proceso-sdd.md`, ejemplo). No toca nada del proyecto. |
| `--keep-existing` | Instala junto a un arnés previo sin tocarlo (tendrás que fusionar a mano). |
| `--force`         | Sobrescribe todo (con backup), salvo tu `CLAUDE.md`/`AGENTS.md` propios, que siempre se conservan. También permite instalar en un repo sin código Python. |
| `--dry-run`       | Muestra qué haría sin escribir nada. |

### Opciones de configuración

| Flag               | Por defecto |
|--------------------|-------------|
| `--name X`         | `pyproject.toml` o el nombre de la carpeta |
| `--src DIR`        | `src/` o el único paquete en la raíz |
| `--tests DIR`      | `tests/` o `test/` |
| `--specs-dir DIR`  | `specs/` (o `.kiro/specs/` si hay Kiro o spec-kit) |
| `--feature-list F` | `feature_list.json` (o `sdd_features.json` si el existente es de otro formato) |
| `--test-cmd CMD`   | pytest o unittest según detecte; admite `{python}` y `{tests_dir}` |
| `--spec-format F`  | `kiro` o `spec-nnn` (autodetectado) |
| `--component C`    | `nombre=ruta:.ext,.ext:comando de tests`, repetible (autodetectado desde `package.json`) |
| `--architecture-doc D` / `--conventions-doc D` | Documentos del proyecto que los agentes usan como estándar (nunca se modifican) |
| `--no-check`       | No ejecutar la verificación final |

En una reinstalación los valores se leen del `harness.toml` existente, así que
no hace falta repetir los flags.

### Después de instalar

1. Rellena `.kiro/steering/*.md`, `docs/architecture.md` y `docs/conventions.md`
   (el validador avisa mientras tengan el marcador `<!-- RELLENAR -->`).
   Atajo: abre Claude Code y pídele «rellena los steering files a partir del
   código existente».
2. Añade features a `feature_list.json` con `"sdd": true` y `"status": "pending"`
   (hay un `$example_feature` como guía). El código previo no necesita spec:
   SDD se aplica hacia adelante.
3. `./init.sh` (Linux/macOS/Git Bash) o `./init.ps1` (PowerShell) → verde.
4. En Claude Code: **«implementa la siguiente feature pendiente»**.
5. Si venías de otro arnés con specs `## R<n>`: **«migra los specs legacy a
   formato Kiro»** (el `spec_author` los reescribe conservando el estado de las
   tasks y la trazabilidad, con una tabla de equivalencias `R<n> → N.M`).

### Actualizar el kit en un proyecto

```bash
git -C harness-kit pull
python harness-kit/install.py ../mi-proyecto --upgrade
```

## Commit automático al cerrar cada feature

Cuando el `reviewer` aprueba, el `implementer` marca la feature `done` y ejecuta:

```bash
./init.sh --commit <feature>
```

Ese comando hace commit **si y solo si** pasa todo: la feature está en `done`,
el spec y la trazabilidad son válidos y **la suite completa de tests** está en
verde. Si algo falla, no hay commit y el motivo queda en
`progress/impl_<feature>.md`. Los agentes no hacen `git commit` por su cuenta.

- Un commit por feature (`<feature>: <título>`, con rutas al spec, la
  trazabilidad y la revisión en el cuerpo), con la identidad de `git config`
  del repo.
- Configurable en `harness.toml`:
  ```toml
  [git]
  auto_commit = true   # false = nunca commitear
  auto_push = false    # true = push tras el commit (desactivado: es irreversible)
  ```
- Si el proyecto no es un repo git, simplemente no hace nada.
- **Escáner de secretos** (`[git] secret_scan = true`): antes de commitear
  revisa lo que entra al commit. Si encuentra claves privadas, `AccountKey`,
  firmas SAS, contraseñas en cadenas de conexión, tokens (GitHub, Anthropic,
  AWS, Slack) o archivos `.env`/`.pbix`/`.pfx`/`.pem`, **no commitea** y dice
  `archivo:línea — tipo` sin mostrar el secreto. `.env.example` sí se permite.

## Qué valida `tools/harness_check.py`

Lo ejecutan `init.sh`/`init.ps1` y los hooks. Falla (exit 1) si:

- Faltan archivos base del arnés, o `feature_list.json` es inválido
  (estado desconocido, ids/nombres duplicados, más de una feature `in_progress`).
- Una feature `sdd` en `spec_ready`/`in_progress`/`done` no tiene sus 3 archivos de spec.
- Un requisito no tiene historia de usuario o criterios; un criterio no usa
  `DEBE`/`SHALL` o usa más de uno.
- Una task hoja no cita `_Requisitos_`, cita un criterio inexistente, o algún
  criterio no está cubierto por ninguna task.
- Una feature `done` tiene tasks no opcionales sin marcar, no tiene
  `progress/impl_<name>.md`, tiene criterios sin test mapeado o cita tests
  que no existen.
- Los tests están en rojo.

- Hay archivos de test pero no se ejecutó ninguno (comando de tests mal
  configurado), o el comando usa pytest y pytest no está instalado en el
  intérprete del proyecto. Nunca "pasa en verde" sin haber corrido tests.

Solo avisa (no falla) por verbos blandos ("puede", "debería"), steering sin
rellenar, specs en el formato antiguo `## R<n>`, huecos de trazabilidad
mientras la feature sigue `in_progress`, y cualquier problema de formato en
features importadas (`"imported": true`).

**Hooks** (`.claude/settings.json`), invocados como `bash init.sh --hook …`
para que el mismo archivo funcione en Windows (Git Bash), macOS y Linux:

- `PreToolUse` antes de editar código de un componente: si no hay una feature
  en curso con spec aprobada, **avisa** (`hooks.pre_gate = "warn"`), o
  **bloquea** la edición con `"block"`; `"off"` lo desactiva.
- `PostToolUse` tras editar un `.py`: corre los tests (o `commands.test_fast`);
  si fallan devuelve exit 2 y Claude Code le pasa el error al modelo para que
  lo arregle. Se puede desactivar con `hooks.post_tests = false`.
- `Stop`: verificación completa; si falla, te lo muestra.
- Ambos tienen timeout (`hooks.post_timeout` / `hooks.stop_timeout` en
  `harness.toml`): si la suite es más lenta, avisan en vez de colgar la sesión.

## Publicarlo en GitHub

Sube este repo a GitHub (p. ej. `harness-kit`). Para cualquier proyecto, nuevo
o existente, clónalo una vez y ejecuta `install.py` apuntando al proyecto: el
kit no se copia a sí mismo, solo el contenido de `template/`. Así puedes
mejorar el kit en un solo sitio y propagar cambios con `--upgrade`.

## Desarrollo del kit

```bash
python -m unittest discover -s tests -v
```

Los documentos de proceso del arnés se instalan en `docs/harness/`
(`proceso-sdd.md`, `verificacion.md`, `ejemplo-spec/`), separados de la
documentación propia de cada proyecto.

```
harness-kit/
├── install.py            # Instalador
├── template/             # Lo que se copia al proyecto (con {{placeholders}})
│   └── tools/harness_check.py
└── tests/                # Tests del instalador y del validador
```
