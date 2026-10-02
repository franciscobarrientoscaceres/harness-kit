# Requirements — cli_recent

> Ejemplo canónico del formato Kiro (feature ficticia de un CLI de notas).
> Cópialo como punto de partida; no lo valida `init` porque no está en
> `{{FEATURE_LIST}}`.

## Introducción

El comando `recent` permite consultar rápidamente las últimas notas creadas
sin recorrer el listado completo. Lista las N notas más recientes ordenadas
por `created_at` descendente, con el mismo formato de línea que `list`.

Fuera de alcance: filtros por fecha, paginación y formatos de salida
alternativos (JSON, tabla).

## Requisitos

### Requisito 1: Listar las notas más recientes

**Historia de usuario:** Como usuario del CLI, quiero ver mis notas más
recientes, para retomar rápidamente lo último que anoté.

#### Criterios de aceptación

1. CUANDO el usuario ejecuta `recent` sin `--limit` ENTONCES el sistema DEBE
   imprimir como máximo 5 notas en stdout.
2. CUANDO el usuario ejecuta `recent --limit <N>` con `N > 0` ENTONCES el
   sistema DEBE imprimir como máximo `N` notas en stdout.
3. CUANDO `recent` imprime notas ENTONCES el sistema DEBE ordenarlas por
   `created_at` de más reciente a más antigua.
4. CUANDO `recent` imprime una nota ENTONCES el sistema DEBE emitir una única
   línea con el formato `<id>\t<created_at>\t<title>`.

### Requisito 2: Casos límite

**Historia de usuario:** Como usuario del CLI, quiero que `recent` se comporte
de forma predecible ante entradas raras, para poder usarlo en scripts.

#### Criterios de aceptación

1. MIENTRAS no exista ninguna nota almacenada, CUANDO el usuario ejecuta
   `recent` ENTONCES el sistema DEBE salir con exit code `0` sin escribir en stdout.
2. SI `--limit` recibe un valor `<= 0` ENTONCES el sistema DEBE salir con un
   exit code distinto de `0` y un mensaje en stderr.
3. SI `--limit` recibe un valor `<= 0` ENTONCES el sistema NO DEBE modificar
   el archivo de notas.

## Trazabilidad con `acceptance` de {{FEATURE_LIST}}

| Acceptance criterion                                              | Cubierto por |
|-------------------------------------------------------------------|--------------|
| `recent` lista las 5 notas más recientes por defecto              | 1.1, 1.3     |
| `recent --limit 10` permite cambiar el número                     | 1.2          |
| Orden por `created_at` de más reciente a más antigua              | 1.3          |
| Formato `<id>\t<created_at>\t<title>` (mismo que `list`)          | 1.4          |
| Sin notas: exit 0 y no imprime nada                               | 2.1          |
| `--limit <= 0`: exit != 0 y mensaje en stderr                     | 2.2, 2.3     |
