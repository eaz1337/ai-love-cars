"""Ręczny słownik wersji aut (analysis/slownik_wersji.csv). Dla modeli, które w nim są, ma pierwszeństwo przed wyuczonym
słownikiem z wersje.py, więc można nim poprawić miejsca, gdzie wyuczone słowa zawodzą (np. 'ST' kontra 'ST-Line').

Kolumny pliku (separator przecinek, słowa małymi literami, rozdzielone spacją):
  marka, model        jak w Otomoto, małymi literami (model po mapowaniu, np. 'cayenne', 'seria 3')
  wersja              etykieta wersji, np. 'turbo gt'
  slowa               słowa, które MUSZĄ wystąpić w tytule (np. 'turbo gt')
  zakaz_slow          słowa, których NIE może być w tytule (opcjonalnie)
  moc_od, moc_do      zakres mocy w KM, do rozpoznania ogłoszeń Otomoto bez wersji w tytule (opcjonalnie)
  pojemnosc_od/_do    zakres pojemności w litrach dla tego samego celu (opcjonalnie)
  rok_od, rok_do      zakres rocznika (opcjonalnie), dla wersji różniących się tylko latami (np. 650i: 408 KM do 2014, 450 KM od 2015).
                      Wpis z rocznikiem pasuje po słowach tylko wtedy, gdy mieści się w nim moc ogłoszenia (jeśli jest i wpis ma zakres mocy)
                      albo, bez mocy, rocznik. Bidfax nie ma mocy, więc tam rozstrzyga rocznik
Wpis z większą liczbą słów wygrywa z krótszym ('turbo gt' z 'turbo'). Jeśli pasują dwie różne wersje, nie wybieramy żadnej.
Auto, którego nie rozpoznano, dostaje 'podstawowa'. Bidfax nie ma mocy, więc tam działa tylko dopasowanie po słowach.
"""
import csv
import os
from collections import namedtuple

import pandas as pd

from wersje import WERSJA_PODSTAWOWA, tokeny_wersji

Wpis = namedtuple('Wpis', 'wersja slowa zakaz moc_od moc_do poj_od poj_do rok_od rok_do')


def _liczba(tekst):
    tekst = (tekst or '').strip().replace(',', '.')
    return float(tekst) if tekst else None


def _slowa(tekst):
    return frozenset((tekst or '').lower().split())


def wczytaj_slownik(sciezka):
    """Zwraca {(marka, model): [Wpis, ...]}. Brak pliku to pusty słownik."""
    if not sciezka or not os.path.exists(sciezka):
        return {}
    wynik = {}
    with open(sciezka, newline='', encoding='utf-8') as f:
        for nr, w in enumerate(csv.DictReader(f), start=2):
            if not (w.get('marka') or '').strip():
                continue
            wersja = (w.get('wersja') or '').strip().lower()
            slowa = _slowa(w.get('slowa'))
            if not wersja or not slowa:
                raise ValueError(f"{sciezka}, wiersz {nr}: wersja i slowa są wymagane")
            wpis = Wpis(wersja, slowa, _slowa(w.get('zakaz_slow')), _liczba(w.get('moc_od')), _liczba(w.get('moc_do')),
                        _liczba(w.get('pojemnosc_od')), _liczba(w.get('pojemnosc_do')),
                        _liczba(w.get('rok_od')), _liczba(w.get('rok_do')))
            wynik.setdefault((w['marka'].strip().lower(), w['model'].strip().lower()), []).append(wpis)
    return wynik


def _ma_rok(w):
    return w.rok_od is not None or w.rok_do is not None


def _ma_moc(w):
    return w.moc_od is not None and w.moc_do is not None


def _pasuje_po_slowach(w, tokeny, moc, rok):
    """Słowa muszą się zgadzać. Wpis z rocznikiem dodatkowo rozstrzyga mocą ogłoszenia (jeśli jest), a bez niej rocznikiem.
    Wpisy bez rocznika ignorują moc i rocznik, jak dotąd (tytuł wystarcza)."""
    if not (w.slowa <= tokeny and not (w.zakaz & tokeny)):
        return False
    if not _ma_rok(w):
        return True
    if _ma_moc(w) and not pd.isna(moc):
        return w.moc_od <= moc <= w.moc_do
    if pd.isna(rok):
        return False
    return (w.rok_od is None or rok >= w.rok_od) and (w.rok_do is None or rok <= w.rok_do)


def _po_slowach(tokeny, wpisy, moc=float('nan'), rok=float('nan')):
    pasujace = [w for w in wpisy if _pasuje_po_slowach(w, tokeny, moc, rok)]
    if not pasujace:
        return None
    najlepsza = max(len(w.slowa) for w in pasujace)
    wersje = {w.wersja for w in pasujace if len(w.slowa) == najlepsza}
    return wersje.pop() if len(wersje) == 1 else None


def _po_mocy(moc, pojemnosc, wpisy):
    if pd.isna(moc):
        return None
    wersje = set()
    for w in wpisy:
        if not _ma_moc(w) or not (w.moc_od <= moc <= w.moc_do):
            continue
        if w.poj_od is not None and w.poj_do is not None:
            if pd.isna(pojemnosc) or not (w.poj_od <= pojemnosc <= w.poj_do):
                continue
        wersje.add(w.wersja)
    return wersje.pop() if len(wersje) == 1 else None


def wersja_reczna(tokeny, wpisy, moc=None, pojemnosc=None, rok=None):
    """Wersja ze słownika: najpierw po słowach z tytułu, potem (jeśli podano moc) po zakresie mocy, inaczej 'podstawowa'."""
    moc_n = float('nan') if moc is None else moc
    rok_n = float('nan') if rok is None else rok
    return (_po_slowach(frozenset(tokeny), wpisy, moc_n, rok_n)
            or (_po_mocy(moc, pojemnosc, wpisy) if moc is not None else None)
            or WERSJA_PODSTAWOWA)


def szkic_slownika(otomoto, slownik_wyuczony):
    """Szkic pliku CSV z wyuczonego słownika (do ręcznej poprawy): jedno słowo = jeden wpis, z zakresem mocy z Otomoto
    (10.-90. percentyl). To punkt wyjścia, nie gotowa prawda: usuń wpisy z wyposażeniem ('line', 'sport') i popraw zakresy."""
    wiersze = []
    for (marka, model), slowa in sorted(slownik_wyuczony.items()):
        podzbior = otomoto[(otomoto['Marka'] == marka) & (otomoto['Model'] == model)]
        for slowo in sorted(slowa):
            z_slowem = podzbior[podzbior['Tokeny'].map(lambda t: slowo in t)]
            moc = pd.to_numeric(z_slowem['Moc_KM'], errors='coerce').dropna()
            wiersze.append({
                'marka': marka, 'model': model, 'wersja': slowo, 'slowa': slowo, 'zakaz_slow': '',
                'moc_od': round(moc.quantile(0.1)) if len(moc) else '', 'moc_do': round(moc.quantile(0.9)) if len(moc) else '',
                'pojemnosc_od': '', 'pojemnosc_do': '', 'n_ogloszen_otomoto': len(z_slowem)})
    return pd.DataFrame(wiersze)
