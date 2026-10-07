#!/usr/bin/env python3
"""Kopia zapasowa i migracja bazy samochody.db jednym poleceniem (opis kroków: MIGRACJA_BAZY.md).

    python tools/migruj_baze.py                      # baza: samochody.db albo zmienna SAMOCHODY_DB
    python tools/migruj_baze.py --db data/samochody.db
    python tools/migruj_baze.py --dry-run            # próba na kopii, oryginał zostaje nietknięty

Co robi: sprawdza, czy baza nie jest używana przez scrapery, robi spójną kopię (sqlite3.backup, także w trybie WAL),
pokazuje diagnostykę przed, wykonuje tools/migracja.sql w jednej transakcji (błąd = nic się nie zmienia), sprawdza integralność
i pokazuje diagnostykę po. Można uruchamiać wielokrotnie.
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

SQL_MIGRACJI = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'migracja.sql')

DIAGNOSTYKA = [
    ("Otomoto: wiersze", "SELECT COUNT(*) FROM otomoto_scd2"),
    ("Otomoto: fałszywe zamknięcia (ID)",
     "SELECT COUNT(DISTINCT t.ID_Ogloszenia) FROM otomoto_scd2 t JOIN otomoto_scd2 a ON a.ID_Ogloszenia = t.ID_Ogloszenia "
     "AND a.Status = 'Aktywne' AND a.Wazne_Od > t.Wazne_Od WHERE t.Status = 'Zakończone'"),
    ("Bidfax: wiersze", "SELECT COUNT(*) FROM bidfax_scd2"),
    ("Bidfax: marka w polu Model",
     "SELECT COUNT(*) FROM bidfax_scd2 WHERE (Marka = 'mercedes-benz' AND Model LIKE 'Mercedes-Benz %') "
     "OR (Marka = 'rolls-royce' AND Model LIKE 'Rolls-Royce %')"),
    ("Bidfax: 'Run and Drive' (stary zapis)", "SELECT COUNT(*) FROM bidfax_scd2 WHERE Condition = 'Run and Drive'"),
    ("Bidfax: zera zamiast braku danych", "SELECT COUNT(*) FROM bidfax_scd2 WHERE Rocznik = 0 OR Cena_USD = 0"),
]


def kolumny(conn, tabela):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({tabela})")]


def sprawdz_baze(sciezka):
    """Zwraca listę problemów, które uniemożliwiają migrację (pusta lista = można działać)."""
    if not os.path.exists(sciezka):
        return [f"Nie znaleziono pliku bazy: {sciezka}"]
    problemy = []
    try:
        conn = sqlite3.connect(sciezka, timeout=3)
        tabele = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for t in ('otomoto_scd2', 'bidfax_scd2'):
            if t not in tabele:
                problemy.append(f"Brak tabeli {t} (to na pewno ta baza?)")
        conn.execute("BEGIN IMMEDIATE")   # nie uda się, jeśli scraper właśnie pisze do bazy
        conn.rollback()
        conn.close()
    except sqlite3.OperationalError as e:
        problemy.append(f"Baza jest zajęta lub niedostępna ({e}). Zatrzymaj scrapery (w Dockerze: docker compose stop).")
    return problemy


def diagnostyka(conn):
    wynik = {}
    for nazwa, sql in DIAGNOSTYKA:
        try:
            wynik[nazwa] = conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            wynik[nazwa] = None
    return wynik


def zrob_kopie(sciezka, katalog=None):
    """Spójna kopia (także przy trybie WAL), sprawdzana integrity_check. Zwraca ścieżkę kopii."""
    katalog = katalog or os.path.dirname(os.path.abspath(sciezka))
    nazwa = f"{os.path.splitext(os.path.basename(sciezka))[0]}_backup_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    cel = os.path.join(katalog, nazwa)
    zrodlo, kopia = sqlite3.connect(sciezka), sqlite3.connect(cel)
    try:
        zrodlo.backup(kopia)
        if kopia.execute("PRAGMA integrity_check").fetchone()[0] != 'ok':
            raise RuntimeError("Kopia zapasowa nie przeszła integrity_check")
    finally:
        zrodlo.close()
        kopia.close()
    return cel


def migruj(sciezka, vacuum=False):
    """Wykonuje tools/migracja.sql w jednej transakcji. Błąd = rollback i wyjątek. Można wywołać wielokrotnie."""
    sql = open(SQL_MIGRACJI, encoding='utf-8').read()
    conn = sqlite3.connect(sciezka, timeout=30, isolation_level=None)
    try:
        if 'Data_Startu' in kolumny(conn, 'otomoto_scd2'):
            sql = re.sub(r'^ALTER TABLE otomoto_scd2 ADD COLUMN Data_Startu TEXT;\s*$', '', sql, flags=re.MULTILINE)
        try:
            conn.executescript(sql)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        if vacuum:
            conn.execute("VACUUM")
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def pokaz(przed, po):
    for nazwa in przed:
        print(f"  {nazwa:<42} {przed[nazwa]!s:>10} -> {po[nazwa]!s:>10}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db', default=os.environ.get('SAMOCHODY_DB', 'samochody.db'), help="ścieżka do bazy (domyślnie samochody.db)")
    ap.add_argument('--dry-run', action='store_true', help="migruj kopię w katalogu tymczasowym, oryginał zostaje nietknięty")
    ap.add_argument('--vacuum', action='store_true', help="po migracji zmniejsz plik (VACUUM, wolniejsze)")
    ap.add_argument('--tak', action='store_true', help="nie pytaj o potwierdzenie")
    args = ap.parse_args(argv)

    problemy = sprawdz_baze(args.db)
    if problemy:
        print("\n".join(f"❌ {p}" for p in problemy))
        return 1
    rozmiar = os.path.getsize(args.db)
    wolne = shutil.disk_usage(os.path.dirname(os.path.abspath(args.db))).free
    if wolne < rozmiar * 2.5:
        print(f"❌ Za mało miejsca na dysku: potrzeba ok. {rozmiar * 2.5 / 1e9:.1f} GB (kopia + migracja), wolne {wolne / 1e9:.1f} GB.")
        return 1

    with sqlite3.connect(args.db) as c:
        przed = diagnostyka(c)
    print(f"Baza: {args.db} ({rozmiar / 1e6:.0f} MB)")

    if args.dry_run:
        katalog = tempfile.mkdtemp(prefix='migracja_dry_')
        cel = os.path.join(katalog, 'proba.db')
        zrodlo, kopia = sqlite3.connect(args.db), sqlite3.connect(cel)
        zrodlo.backup(kopia); zrodlo.close(); kopia.close()
        print(f"Tryb próbny: migruję kopię {cel}, oryginał zostaje bez zmian.")
        integralnosc = migruj(cel, args.vacuum)
        with sqlite3.connect(cel) as c:
            po = diagnostyka(c)
        pokaz(przed, po)
        print(f"integrity_check: {integralnosc}")
        shutil.rmtree(katalog, ignore_errors=True)
        return 0 if integralnosc == 'ok' else 1

    if not args.tak:
        print("Przed migracją robię kopię zapasową obok bazy. Scrapery muszą być zatrzymane.")
        if input("Kontynuować? [t/N] ").strip().lower() not in ('t', 'tak', 'y', 'yes'):
            print("Przerwano, nic nie zmieniono.")
            return 1

    kopia = zrob_kopie(args.db)
    print(f"✅ Kopia zapasowa: {kopia}")
    try:
        integralnosc = migruj(args.db, args.vacuum)
    except Exception as e:
        print(f"❌ Migracja nie powiodła się, baza nie została zmieniona: {e}\n   Kopia zapasowa: {kopia}")
        return 1
    with sqlite3.connect(args.db) as c:
        po = diagnostyka(c)
    pokaz(przed, po)
    print(f"integrity_check: {integralnosc}")
    if integralnosc != 'ok':
        print(f"❌ Baza po migracji nie przeszła kontroli. Przywróć kopię: {kopia}")
        return 1
    print(f"✅ Gotowe. Wycofanie zmian: zatrzymaj skrypty i podmień {args.db} na {kopia}.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
