# Tasks — cli_recent

> El `implementer` marca `[x]` cada task al completarla. `- [ ]*` = opcional.

- [ ] 1. Implementar el comando `recent`
- [ ] 1.1 Añadir `cmd_recent(args)` en `src/cli.py`
  - Validar `args.limit > 0` antes de cargar (lanza `NoteError` si no)
  - Cargar con `storage.load()`, ordenar por `created_at` desc, aplicar `[:args.limit]`
  - Imprimir cada nota como `f"{id}\t{created_at}\t{title}"`
  - _Requisitos: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3_
- [ ] 1.2 Registrar el subparser `recent` en `build_parser()`
  - `--limit` con `type=int`, `default=5`; `set_defaults(func=cmd_recent)`
  - _Requisitos: 1.1, 1.2_

- [ ] 2. Tests del comando `recent`
- [ ] 2.1 `test_recent_default_limit_orders_desc`
  - Crear > 5 notas con `created_at` distintos; verificar 5 líneas en orden desc
  - _Requisitos: 1.1, 1.3_
- [ ] 2.2 `test_recent_custom_limit_format`
  - `--limit K` con `K < N`; verificar `K` líneas con formato `<id>\t<created_at>\t<title>`
  - _Requisitos: 1.2, 1.4_
- [ ] 2.3 `test_recent_empty_outputs_nothing`
  - Sin notas: exit 0 y stdout vacío
  - _Requisitos: 2.1_
- [ ] 2.4 `test_recent_invalid_limit_zero` y `test_recent_invalid_limit_negative`
  - exit != 0, stderr no vacío, archivo de notas sin cambios
  - _Requisitos: 2.2, 2.3_

- [ ] 3. Cierre
- [ ] 3.1 Documentar el mapa criterio → test en `progress/impl_cli_recent.md`
  - _Requisitos: 1, 2_
- [ ] 3.2 Ejecutar `./init.sh` y confirmar todo verde
  - _Requisitos: ninguno_

- [ ]* 4. Smoke test manual con un archivo temporal, anotado en `progress/impl_cli_recent.md`
  - _Requisitos: ninguno_
