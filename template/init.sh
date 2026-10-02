#!/usr/bin/env bash
# init.sh — Verificación e inicialización del entorno (Linux, macOS, Git Bash).
#
# Lo ejecuta el agente al COMENZAR una sesión y antes de declarar cualquier
# feature como `done`. Si falla, la sesión no avanza.
# La lógica vive en tools/harness_check.py; este script solo busca un Python
# que funcione (en Windows `python3` suele ser un alias roto de la Store).

cd "$(dirname "$0")" || exit 1

for candidate in python3 python py; do
  if "$candidate" -c "import sys" >/dev/null 2>&1; then
    exec "$candidate" tools/harness_check.py "$@"
  fi
done

echo "[FAIL]  No se encontró ningún intérprete de Python (python3, python, py)"
exit 1
