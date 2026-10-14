FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Chrome (tylko dla scraper_bidfax) i Xvfb: Cloudflare gorzej znosi headless, więc Chrome działa na wirtualnym ekranie
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget gnupg ca-certificates xvfb \
    && wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY scrapers ./scrapers
COPY analysis ./analysis
COPY tools ./tools
COPY tests ./tests
COPY pytest.ini ./
COPY docker/bidfax-entrypoint.sh /usr/local/bin/bidfax-entrypoint.sh
RUN chmod +x /usr/local/bin/bidfax-entrypoint.sh

# Baza (samochody.db) powstaje w bieżącym katalogu, czyli w wolumenie /data
WORKDIR /data
ENV SAMOCHODY_DB=/data/samochody.db
VOLUME /data
CMD ["python", "/app/scrapers/scraper_otomoto.py"]
