# Convenciones de código

> Homogeneidad extrema. La IA predice mejor cuando el repositorio se parece
> a sí mismo en todas partes. Si el proyecto ya tiene un linter/formatter
> configurado (ruff, black, flake8...), **esa configuración manda** sobre este
> archivo; aquí se documenta lo que la herramienta no captura.

<!-- RELLENAR -->
> ⚠️ Plantilla instalada por harness-kit con valores por defecto para Python.
> Ajusta lo que no aplique y borra el marcador `RELLENAR` de arriba.

## Estilo Python

- **Versión mínima:** la indicada en `.kiro/steering/tech.md`.
- **Formato:** PEP 8. Longitud de línea según el formatter del proyecto.
- **Tipado:** anotaciones en toda función pública; `from __future__ import annotations`.
- **Imports:** stdlib, terceros, locales — separados por una línea en blanco.
- **f-strings** para interpolación.

## Nombres

| Tipo                    | Convención        | Ejemplo               |
|-------------------------|-------------------|-----------------------|
| Módulos                 | `snake_case`      | `storage.py`          |
| Clases                  | `PascalCase`      | `Note`                |
| Funciones / variables   | `snake_case`      | `load_notes`          |
| Constantes              | `UPPER_SNAKE`     | `DEFAULT_PATH`        |
| Privadas                | prefijo `_`       | `_atomic_write`       |

## Tests

- Ubicación: `{{TESTS_DIR}}/`, un archivo por módulo: `test_<módulo>.py`.
- Comando: `{{TEST_CMD_DISPLAY}}`.
- Nombres descriptivos que dicen el comportamiento:
  `test_load_returns_empty_when_file_missing`.
- Los tests que tocan disco usan `tmp_path` (pytest) o
  `tempfile.TemporaryDirectory()`, nunca mocks del filesystem.
- Cada test verifica un resultado concreto, no solo que "no lanza".

## Manejo de errores

- Excepciones del dominio con una base común (`class <Proyecto>Error(Exception)`).
- La capa de interfaz (CLI/API) captura las del dominio, muestra un mensaje
  claro y devuelve un código de error. Nunca stack traces al usuario.

## Comentarios

Por defecto **no** se escriben. Solo cuando explican un *por qué* no obvio
(workaround documentado, invariante sutil). Los nombres hacen el resto.
