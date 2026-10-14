#!/usr/bin/env python3
"""Scala bazę utworzoną przez pomyłkę (np. scrapers/samochody.db) z główną bazą (samochody.db).

    python tools/scal_baze.py --dry-run                  # próba na kopii, oryginały zostają nietknięte
    python tools/scal_baze.py                            # scala scrapers/samochody.db do samochody.db
    python tools/scal_baze.py --src X.db --dst Y.db

Co robi (wszystko w jednej transakcji, błąd = nic się nie zmienia; przed zmianą powstaje kopia zapasowa celu):
- Otomoto: dopisuje wszystkie wersje ze źródła z nowymi ID_Wersji. Otwarte wersje celu dla ogłoszeń widzianych w źródle są
  zamykane w chwili pierwszej wersji ze źródła (ciągłość SCD2). Otwarte wersje ogłoszeń, których źródło już nie widzi,
  zamykane są jako 'Zakończone' w chwili startu źródła (skan w źródle obejmował cały rynek).
- Bidfax: dopisuje aukcje, których jeszcze nie ma (po unikalnym Link).
- Tabele stanu scrapera Bidfax (bidfax_meta, bidfax_stan_marek) są kopiowane ze źródła, jeśli cel ich nie ma.
- Usuwa fałszywe zakończenia Otomoto (reguła z tools/migracja.sql), bo baza źródłowa nie była migrowana.
- Data_Startu liczona dla nowych wierszy tak jak w tools/migracja.sql.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NIESKONCZONOSC = '9999-12-31 23:59:59'


def kolumny(conn, tabela, baza='main'):
    return [r[1] for r in conn.execute(f'PRAGMA {baza}.table_info("{tabela}")')]


def kopia(sciezka_z, sciezka_do):
    """Spójna kopia (także przy trybie WAL)."""
    zrodlo, cel = sqlite3.connect(sciezka_z), sqlite3.connect(sciezka_do)
    with cel:
        zrodlo.backup(cel)
    zrodlo.close()
    cel.close()


def scal(dst, src):
    conn = sqlite3.connect(dst, timeout=60.0)
    conn.execute('ATTACH DATABASE ? AS src', (src,))
    raport = {}
    try:
        conn.execute('BEGIN')

        # Cel musi mieć kolumny, które są w źródle (np. Proby_Detali)
        kol_dst = kolumny(conn, 'otomoto_scd2')
        for kol in kolumny(conn, 'otomoto_scd2', 'src'):
            if kol not in kol_dst:
                conn.execute(f'ALTER TABLE main.otomoto_scd2 ADD COLUMN "{kol}" INTEGER DEFAULT 0')
        wspolne = [k for k in kolumny(conn, 'otomoto_scd2', 'src') if k != 'ID_Wersji' and k in kolumny(conn, 'otomoto_scd2')]
        lista = ', '.join(f'"{k}"' for k in wspolne)

        start_zrodla = conn.execute('SELECT MIN(Wazne_Od) FROM src.otomoto_scd2').fetchone()[0]
        conn.execute('CREATE TEMP TABLE pierwsze AS SELECT ID_Ogloszenia, MIN(Wazne_Od) AS od FROM src.otomoto_scd2 GROUP BY ID_Ogloszenia')
        conn.execute('CREATE INDEX temp.i_pierwsze ON pierwsze (ID_Ogloszenia)')

        # 1) ogłoszenia widziane też w źródle: zamykamy otwartą wersję celu w chwili pierwszej wersji ze źródła
        c = conn.execute(
            "UPDATE main.otomoto_scd2 SET Wazne_Do = (SELECT od FROM pierwsze p WHERE p.ID_Ogloszenia = main.otomoto_scd2.ID_Ogloszenia) "
            "WHERE Wazne_Do = ? AND Status = 'Aktywne' AND ID_Ogloszenia IN (SELECT ID_Ogloszenia FROM pierwsze) "
            "AND (SELECT od FROM pierwsze p WHERE p.ID_Ogloszenia = main.otomoto_scd2.ID_Ogloszenia) > Wazne_Od", (NIESKONCZONOSC,))
        raport['Otomoto: zamknięte wersje celu (ogłoszenie widziane w źródle)'] = c.rowcount
        # 2) ogłoszenia, których źródło nie widzi: zakończone w chwili startu źródła
        c = conn.execute(
            "UPDATE main.otomoto_scd2 SET Wazne_Do = ?, Status = 'Zakończone' "
            "WHERE Wazne_Do = ? AND Status = 'Aktywne' AND Wazne_Od < ? AND ID_Ogloszenia NOT IN (SELECT ID_Ogloszenia FROM pierwsze)",
            (start_zrodla, NIESKONCZONOSC, start_zrodla))
        raport['Otomoto: zakończone (znikły przed startem źródła)'] = c.rowcount

        przed = conn.execute('SELECT COUNT(*) FROM main.otomoto_scd2').fetchone()[0]
        conn.execute(f'INSERT INTO main.otomoto_scd2 ({lista}) SELECT {lista} FROM src.otomoto_scd2 ORDER BY ID_Wersji')
        raport['Otomoto: dopisane wersje'] = conn.execute('SELECT COUNT(*) FROM main.otomoto_scd2').fetchone()[0] - przed
        if 'Data_Startu' in kolumny(conn, 'otomoto_scd2'):
            conn.execute(
                "UPDATE main.otomoto_scd2 SET Data_Startu = MIN(Data_Dodania, REPLACE(SUBSTR(Czas_publikacji, 1, 19), 'T', ' ')) "
                "WHERE Data_Startu IS NULL AND Czas_publikacji IS NOT NULL")

        # Bidfax: unikalny Link
        kol_b = [k for k in kolumny(conn, 'bidfax_scd2', 'src') if k != 'ID' and k in kolumny(conn, 'bidfax_scd2')]
        lista_b = ', '.join(f'"{k}"' for k in kol_b)
        przed = conn.execute('SELECT COUNT(*) FROM main.bidfax_scd2').fetchone()[0]
        conn.execute(f'INSERT OR IGNORE INTO main.bidfax_scd2 ({lista_b}) SELECT {lista_b} FROM src.bidfax_scd2 ORDER BY ID')
        raport['Bidfax: dopisane aukcje'] = conn.execute('SELECT COUNT(*) FROM main.bidfax_scd2').fetchone()[0] - przed

        # Stan scrapera Bidfax (licznik dnia, blokada IP) przenosimy tylko, gdy cel go nie ma
        for tabela in ('bidfax_meta', 'bidfax_stan_marek'):
            w_src = conn.execute("SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?", (tabela,)).fetchone()
            w_dst = conn.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (tabela,)).fetchone()
            if w_src and not w_dst:
                conn.execute(w_src[0])
                conn.execute(f'INSERT INTO main."{tabela}" SELECT * FROM src."{tabela}"')
                raport[f'Stan: skopiowano {tabela}'] = conn.execute(f'SELECT COUNT(*) FROM main."{tabela}"').fetchone()[0]

        # Fałszywe zakończenia (niepełny skan zamknął ogłoszenie, które potem wróciło), ta sama reguła co w tools/migracja.sql.
        # Baza źródłowa nie przeszła migracji, więc je zawiera.
        c = conn.execute(
            "DELETE FROM main.otomoto_scd2 WHERE Status = 'Zakończone' AND EXISTS (SELECT 1 FROM main.otomoto_scd2 a "
            "WHERE a.ID_Ogloszenia = main.otomoto_scd2.ID_Ogloszenia AND a.Status = 'Aktywne' AND a.Wazne_Od > main.otomoto_scd2.Wazne_Od)")
        raport['Otomoto: usunięte fałszywe zakończenia'] = c.rowcount

        # Kontrola: nie może być dwóch otwartych wersji tego samego ogłoszenia
        podwojne = conn.execute(
            'SELECT COUNT(*) FROM (SELECT ID_Ogloszenia FROM main.otomoto_scd2 WHERE Wazne_Do = ? GROUP BY ID_Ogloszenia HAVING COUNT(*) > 1)',
            (NIESKONCZONOSC,)).fetchone()[0]
        raport['Kontrola: ogłoszenia z >1 otwartą wersją'] = podwojne
        if podwojne:
            raise RuntimeError(f'{podwojne} ogłoszeń ma po scaleniu kilka otwartych wersji, przerywam bez zmian')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return raport


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dst', default=os.path.join(ROOT, 'samochody.db'), help='baza docelowa (domyślnie samochody.db w roocie)')
    ap.add_argument('--src', default=os.path.join(ROOT, 'scrapers', 'samochody.db'), help='baza do dołączenia')
    ap.add_argument('--dry-run', action='store_true', help='scala na kopii w katalogu tymczasowym, oryginały zostają nietknięte')
    args = ap.parse_args()

    for p in (args.dst, args.src):
        if not os.path.exists(p):
            sys.exit(f'Brak pliku: {p}')

    if args.dry_run:
        import tempfile
        katalog = tempfile.mkdtemp(prefix='scal_baze_')
        cel = os.path.join(katalog, 'cel.db')
        kopia(args.dst, cel)
        print(f'Próba na kopii: {cel}')
    else:
        cel = args.dst
        zapas = f"{args.dst}.przed_scaleniem_{datetime.now():%Y%m%d_%H%M%S}"
        kopia(args.dst, zapas)
        print(f'Kopia zapasowa: {zapas}')

    raport = scal(cel, args.src)
    for k, v in raport.items():
        print(f'  {k}: {v}')
    print('Zakończono (tryb próbny, nic nie zmieniono).' if args.dry_run else f'Zakończono, scalono do {cel}')


if __name__ == '__main__':
    main()
