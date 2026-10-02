# Verificación — Cómo demostrar que el trabajo funciona

> Regla de oro: **el agente no dice "funciona", lo demuestra**.
> Toda feature termina con evidencia ejecutable, no con afirmaciones.

## Niveles de verificación

### Nivel 1 — Tests automáticos (obligatorio)

Toda función pública en `{{SRC_DIR}}/` tiene al menos un test en `{{TESTS_DIR}}/` que:

1. Cubre el camino feliz.
2. Cubre al menos un camino de error si la función puede fallar.

```bash
{{TEST_CMD_DISPLAY}}
```

### Nivel 2 — Test de integración (obligatorio para features de interfaz)

Las features que añaden comandos, endpoints o pantallas se verifican
ejecutando la interfaz real (subprocess para un CLI, cliente de test para una
API) contra datos temporales, no llamando a funciones internas.

### Nivel 3 — Smoke test manual (recomendado)

Antes de cerrar la sesión, ejecuta un flujo end-to-end con datos temporales y
anota los comandos y su salida en `progress/impl_<name>.md`.

### Nivel 4 — Trazabilidad de criterios (obligatorio para `"sdd": true`)

Cada criterio `N.M` de `{{SPECS_DIR}}/<name>/requirements.md` se mapea a al menos un
test concreto. El implementer documenta el mapa en `progress/impl_<name>.md`:

```markdown
## Trazabilidad
- 1.1 → `test_recent_default_limit`
- 1.2 → `test_recent_custom_limit`
- 2.1 → `test_recent_invalid_limit_zero`, `test_recent_invalid_limit_negative`
```

`./init.sh` falla si una feature `done` tiene criterios sin mapear o tests
citados que no existen.

## Verificación automática (hooks)

`.claude/settings.json` registra dos hooks que el harness ejecuta solo:

- **PostToolUse** (tras Edit/Write de un `.py`): corre los tests; si están en
  rojo, el resultado vuelve al agente para que lo corrija.
- **Stop** (al cerrar el turno): verificación completa; si falla, te avisa.

## Anti-patrones (no hacer)

- ❌ "He añadido el comando, debería funcionar." → falta test ejecutable.
- ❌ Test que solo verifica que la función no lanza excepción.
- ❌ Mocks del filesystem → usa directorios temporales reales.
- ❌ Marcar la feature como `done` sin pasar `./init.sh`.

## Verificación final antes de cerrar

```bash
./init.sh      # Linux / macOS / Git Bash
./init.ps1     # PowerShell
```

Si está en rojo, **no** marques nada como `done`. Anota el bloqueo en
`progress/current.md` y deja la feature en `blocked` en `{{FEATURE_LIST}}`.
