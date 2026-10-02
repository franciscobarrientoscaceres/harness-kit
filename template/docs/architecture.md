# Arquitectura — Qué significa "hacer un buen trabajo"

> Este documento define el estándar de calidad de **{{PROJECT_NAME}}**. Los
> agentes revisores evalúan código contra este archivo. Si no está aquí (ni en
> `.kiro/steering/`), no es un requisito.

<!-- RELLENAR -->
> ⚠️ Plantilla instalada por harness-kit. Sustituye los ejemplos por los
> principios reales del proyecto y borra el marcador `RELLENAR` de arriba.

## Principios

1. **Capas claras.** _Ejemplo:_ `{{SRC_DIR}}/` tiene las capas descritas en
   `.kiro/steering/structure.md` y solo esas. No se introducen capas nuevas
   (servicios, repositorios, ORMs) sin una razón documentada en un `design.md`.

2. **Dependencias controladas.** Solo las listadas en `.kiro/steering/tech.md`.
   Si una feature requiere una dependencia nueva, se discute primero
   (feature en `blocked` hasta que el humano decida).

3. **Errores explícitos.** Las funciones que pueden fallar lanzan excepciones
   nombradas del dominio, no devuelven `None` silenciosamente.

4. **IO en los bordes.** La lógica de dominio no lee ni escribe disco/red;
   eso vive en una capa de infraestructura.

5. _Añade aquí los principios propios del proyecto (inmutabilidad,
   atomicidad, rendimiento, seguridad...)._

## Flujo de datos

```
<entrada> ─→ <capa interfaz> ─→ <capa dominio> ─→ <capa persistencia/externos>
```

_Sustituye por el flujo real del proyecto._

## Qué NO hacer

- No usar `print()` para errores. Usa logging o `sys.stderr` y un exit code != 0.
- No mezclar IO con lógica de dominio.
- No añadir configuración global nueva sin documentarla en `tech.md`.
- _Añade aquí los anti-patrones propios del proyecto._
