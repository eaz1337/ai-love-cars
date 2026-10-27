#!/usr/bin/env bash
# Uruchamia scraper Otomoto i scraper Bidfax równolegle (logi z prefiksami),
# a po zakończeniu obu uruchamia analizę gold.
cd "$(dirname "$0")" || exit 1
export SAMOCHODY_DB=samochody.db

trap 'kill 0' INT TERM

venv/bin/python -u scrapers/scraper_otomoto.py 2>&1 | sed -u 's/^/[otomoto] /' &
venv/bin/python -u scrapers/scraper_bidfax.py  2>&1 | sed -u 's/^/[bidfax]  /' &

wait

venv/bin/python -u analysis/gold.py 2>&1 | sed -u 's/^/[gold]    /'
