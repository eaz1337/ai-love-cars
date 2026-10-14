#!/usr/bin/env bash
# Uruchamia analysis/gold.py z roota repo (tam leży samochody.db).
# Opcjonalnie: GOLD_SPRZEDAWCA='Firma'|'Osoba prywatna'|'wszyscy' ./run_gold.sh
set -euo pipefail
cd "$(dirname "$0")"
source venv/bin/activate
export SAMOCHODY_DB="${SAMOCHODY_DB:-samochody.db}"
exec python analysis/gold.py "$@"
