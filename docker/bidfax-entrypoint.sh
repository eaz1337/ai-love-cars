#!/bin/sh
# scraper_bidfax.py podłącza się do Chrome na porcie 9222, więc najpierw uruchamiamy przeglądarkę
set -e
Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp &
export DISPLAY=:99
google-chrome --no-sandbox --disable-dev-shm-usage --remote-debugging-port=9222 \
    --user-data-dir=/data/chrome-profile about:blank >/dev/null 2>&1 &
sleep 3
exec python /app/scrapers/scraper_bidfax.py
