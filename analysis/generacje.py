"""Przypisanie rocznika do generacji modelu. Źródła, od najważniejszego:
  1. ręczny plik analysis/generacje.csv (kolumny: marka, model, generacja, rok_od, rok_do; pusty rok_do = trwa nadal),
     z liftami jako osobnymi wierszami; dla modeli, które w nim są, ma pierwszeństwo,
  2. słownik z Otomoto (tabela `generacje_otomoto`, scrapers/generacje_otomoto.py) dla pozostałych modeli, bez liftów.

Grupowanie po generacji zamiast po pojedynczym roczniku daje większe próby (np. BMW F30 2012-2020 zamiast osobnych roczników).
Gdy generacje się nakładają (E36 1990-1999 i E46 1998-2007), rocznik dostaje nowszą z nich.
"""
import csv
import os

import pandas as pd

TABELA = 'generacje_otomoto'
BRAK_GENERACJI = 'brak'
OTWARTY_KONIEC = 9999


def _sortuj(slownik):
    for generacje in slownik.values():
        generacje.sort(key=lambda g: g[1], reverse=True)
    return slownik


def wczytaj_csv(sciezka, normalizuj_marke):
    """Zwraca {(marka, model): [(nazwa, rok_od, rok_do), ...]} z ręcznego pliku. Brak pliku to pusty słownik."""
    if not sciezka or not os.path.exists(sciezka):
        return {}
    wynik = {}
    with open(sciezka, newline='', encoding='utf-8') as f:
        for w in csv.DictReader(f):
            klucz = (normalizuj_marke(w['marka']), w['model'].strip().lower())
            do = (w.get('rok_do') or '').strip()
            wynik.setdefault(klucz, []).append((w['generacja'].strip(), int(w['rok_od']), int(do) if do else OTWARTY_KONIEC))
    return _sortuj(wynik)


def wczytaj_generacje(conn, normalizuj_marke, sciezka_csv=None):
    """Zwraca {(marka, model): [(nazwa, rok_od, rok_do), ...]} posortowane od najnowszej generacji.
    Modele z pliku CSV nadpisują Otomoto; brak tabeli Otomoto lub pliku to po prostu brak tego źródła."""
    wynik = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABELA,)).fetchone():
        for marka, model, nazwa, od, do in conn.execute(f"SELECT Marka, Model, Generacja, Rok_Od, Rok_Do FROM {TABELA}"):
            wynik.setdefault((normalizuj_marke(marka), str(model).strip().lower()), []).append((nazwa, od, do))
    wynik = _sortuj(wynik)
    wynik.update(wczytaj_csv(sciezka_csv, normalizuj_marke))
    return wynik


def generacja(marka, model, rok, slownik):
    """Etykieta 'F30/F31 (2012-2020)' albo BRAK_GENERACJI, gdy model nie ma słownika lub rocznik nie mieści się w żadnej generacji."""
    for nazwa, od, do in slownik.get((marka, model), []):
        if od <= rok <= do:
            return f"{nazwa} ({od}-{'' if do == OTWARTY_KONIEC else do})"
    return BRAK_GENERACJI


def dodaj_generacje(df, kol_marka, kol_model, kol_rok, slownik):
    """Zwraca serię z generacją dla każdego wiersza df."""
    return pd.Series([generacja(ma, mo, int(r), slownik) for ma, mo, r in zip(df[kol_marka], df[kol_model], df[kol_rok])], index=df.index)
