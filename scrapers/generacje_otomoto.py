"""Słownik generacji aut z Otomoto (tabela `generacje_otomoto`).

Strona modelu (otomoto.pl/osobowe/<marka>/<model>) ma listę generacji z latami produkcji, np. 'BMW Seria 3 F30/F31 (2012-2020)'.
Skrypt przechodzi po parach marka/model z bazy ogłoszeń i zapisuje: nazwę generacji oraz lata od-do.
Generacje służą do grupowania roczników w analizie (patrz analysis/generacje.py).

Uruchomienie: python generacje_otomoto.py   (pomija modele pobrane w ciągu ostatnich ODSWIEZ_DNI dni)
"""
import logging
import random
import re
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

DB_NAME = "samochody.db"
TABELA = "generacje_otomoto"
MIN_OGLOSZEN_MODELU = 30    # rzadkie modele nie trafiają do analizy, szkoda na nie requestów
ODSWIEZ_DNI = 90            # generacje zmieniają się rzadko
MAX_BLOKAD_Z_RZEDU = 3
OTWARTY_KONIEC = 9999       # generacja produkowana nadal: '(2019-)'

_TYTUL = re.compile(r'\((\d{4})\s*-\s*(\d{4})?\)\s*$')


def slug(tekst):
    """'Seria 3' -> 'seria-3', 'Mercedes-Benz' -> 'mercedes-benz'."""
    return re.sub(r'[^a-z0-9-]', '', re.sub(r'[\s_]+', '-', str(tekst).strip().lower()))


def parsuj_generacje(html, marka, model):
    """Zwraca listę (nazwa, rok_od, rok_do) z linków filtra generacji na stronie modelu. Brak generacji daje pustą listę."""
    soup = BeautifulSoup(html, 'html.parser')
    sciezka = f"/osobowe/{slug(marka)}/{slug(model)}"
    prefiks = f"{marka} {model}".lower()
    wyniki, widziane = [], set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'filter_enum_generation' not in urllib.parse.unquote(href) or sciezka not in href:
            continue
        tytul = (a.get('title') or a.get_text()).strip()
        m = _TYTUL.search(tytul)
        if not m:
            continue
        rok_od = int(m.group(1))
        rok_do = int(m.group(2)) if m.group(2) else OTWARTY_KONIEC
        nazwa = tytul[:m.start()].strip()
        if nazwa.lower().startswith(prefiks):
            nazwa = nazwa[len(prefiks):].strip()
        nazwa = nazwa or f"{rok_od}-{'' if rok_do == OTWARTY_KONIEC else rok_do}"
        if (nazwa, rok_od) not in widziane:
            widziane.add((nazwa, rok_od))
            wyniki.append((nazwa, rok_od, rok_do))
    return wyniki


def zainicjalizuj_tabele(conn):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABELA} (
            Marka TEXT, Model TEXT, Generacja TEXT, Rok_Od INTEGER, Rok_Do INTEGER, Pobrano TEXT,
            PRIMARY KEY (Marka, Model, Generacja, Rok_Od)
        )
    """)
    # Modele bez generacji też zapisujemy z pustą nazwą, żeby nie odpytywać ich przy każdym uruchomieniu
    conn.execute(f"CREATE TABLE IF NOT EXISTS {TABELA}_sprawdzone (Marka TEXT, Model TEXT, Pobrano TEXT, PRIMARY KEY (Marka, Model))")
    conn.commit()


def modele_do_pobrania(conn):
    od = (datetime.now() - timedelta(days=ODSWIEZ_DNI)).strftime('%Y-%m-%d')
    return conn.execute(f"""
        SELECT o.Marka, o.Model, COUNT(*) FROM otomoto_scd2 o
        LEFT JOIN {TABELA}_sprawdzone s ON s.Marka = o.Marka AND s.Model = o.Model
        WHERE o.Wazne_Do = '9999-12-31 23:59:59' AND o.Marka IS NOT NULL AND o.Model IS NOT NULL
          AND (s.Pobrano IS NULL OR s.Pobrano < ?)
        GROUP BY o.Marka, o.Model HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC
    """, (od, MIN_OGLOSZEN_MODELU)).fetchall()


def zapisz_model(conn, marka, model, generacje):
    dzis = datetime.now().strftime('%Y-%m-%d')
    conn.execute(f"DELETE FROM {TABELA} WHERE Marka = ? AND Model = ?", (marka, model))
    conn.executemany(f"INSERT OR REPLACE INTO {TABELA} VALUES (?, ?, ?, ?, ?, ?)",
                     [(marka, model, n, od, do, dzis) for n, od, do in generacje])
    conn.execute(f"INSERT OR REPLACE INTO {TABELA}_sprawdzone VALUES (?, ?, ?)", (marka, model, dzis))
    conn.commit()


def main():
    session = cffi_requests.Session(impersonate="chrome120")
    conn = sqlite3.connect(DB_NAME, timeout=60.0)
    zainicjalizuj_tabele(conn)
    modele = modele_do_pobrania(conn)
    logging.info(f"Modeli do pobrania generacji: {len(modele)}")
    blokady = 0
    for marka, model, _ in modele:
        url = f"https://www.otomoto.pl/osobowe/{slug(marka)}/{slug(model)}"
        try:
            r = session.get(url, timeout=15)
        except Exception as e:
            logging.warning(f"{marka} {model}: błąd połączenia ({e})")
            continue
        if r.status_code in (403, 429):
            blokady += 1
            logging.warning(f"Blokada {r.status_code} ({blokady}/{MAX_BLOKAD_Z_RZEDU}). Przerywam, bo kolejne requesty pogorszą sprawę.")
            if blokady >= MAX_BLOKAD_Z_RZEDU:
                break
            time.sleep(30)
            continue
        blokady = 0
        if r.status_code == 200:
            generacje = parsuj_generacje(r.text, marka, model)
            zapisz_model(conn, marka, model, generacje)
            logging.info(f"{marka} {model}: {len(generacje)} generacji")
        elif r.status_code in (404, 410):
            zapisz_model(conn, marka, model, [])
            logging.info(f"{marka} {model}: brak strony modelu ({r.status_code}), pomijam")
        else:
            logging.warning(f"{marka} {model}: status {r.status_code}")
        time.sleep(random.uniform(1.5, 3.0))
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
