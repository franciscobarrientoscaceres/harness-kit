---
inclusion: always
---

# Estructura — {{PROJECT_NAME}}

<!-- RELLENAR -->
> Layout de carpetas y responsabilidades de cada capa. El `spec_author` lo usa
> para decidir dónde va cada cambio en `design.md`. Borra el marcador
> `RELLENAR` cuando esté completo.

## Layout

```
.
├── {{SRC_DIR}}/          # Código de la aplicación
│   └── ...               # _describe los módulos/paquetes principales_
├── {{TESTS_DIR}}/        # Tests (un archivo por módulo)
├── {{SPECS_DIR}}/<feature>/      # Specs Kiro-style por feature
├── progress/             # Estado de sesión y bitácora
└── docs/                 # Arquitectura, convenciones, SDD, verificación
```

## Capas y responsabilidades

| Módulo / paquete | Responsabilidad | Puede depender de |
|------------------|-----------------|-------------------|
| _..._            | _..._           | _..._             |

## Dónde va cada cosa

- _Nuevos comandos / endpoints → ..._
- _Nuevos modelos de dominio → ..._
- _Acceso a disco / red → ..._
