"""Wersja auta (np. 'turbo gt', 'gts', '540d') wyciągana z tytułów, bez ręcznej listy dla każdej marki.

Bidfax nie podaje mocy, a tytuł zawiera tylko etykiety wersji. Otomoto ma i tytuł, i moc oraz cenę. Dlatego uczymy się z Otomoto,
które słowa z tytułu naprawdę odróżniają wersje (inna moc albo wyraźnie inna cena), i używamy tylko tych, które pojawiają się
po obu stronach. Słowa typu 'platinum edition' nie zmieniają mocy ani ceny, więc odpadają same.
"""
import re

import pandas as pd

WERSJA_PODSTAWOWA = 'podstawowa'

MIN_OGLOSZEN_SLOWA_OTOMOTO = 5   # tyle ogłoszeń z danym słowem trzeba, żeby ocenić, czy ono coś zmienia
MIN_AUKCJI_SLOWA_BIDFAX = 2      # słowo musi występować także w Bidfax, inaczej rozdzieliłoby tylko jedną stronę
PROG_ROZNICY_MOCY = 0.04         # mediana mocy aut ze słowem różni się o min. 4% od mediany grupy
PROG_ROZNICY_CENY = 0.15         # albo mediana ceny różni się o min. 15% (cena porównywana w obrębie rocznika)

# Skrzynia i napęd to osobne cechy (Otomoto ma na nie własne kolumny), nie wersja silnikowa. Dzieliłyby grupy bez potrzeby
IGNOROWANE_SLOWA = frozenset({
    'tiptronic', 'tronic', 'dsg', 'automatik', 'automatic', 'aut', 'manual', 'xdrive', 'quattro', '4matic', 'awd', '4x4',
})


def tokeny_wersji(tekst, wyklucz=()):
    """Słowa z tytułu istotne dla wersji. Usuwa rok, pojemność oraz słowa z `wyklucz` (marka i model)."""
    t = str(tekst).lower()
    t = re.sub(r'\b(19|20)\d\d\b', ' ', t)
    t = re.sub(r'\d[.,]\d\s*l?\b', ' ', t)
    t = re.sub(r'(\d)\s+([a-z])\b', r'\1\2', t)   # '328 i' -> '328i', tak jak w tytułach Otomoto
    wykluczone = {w for fraza in wyklucz for w in re.findall(r'[a-z0-9]+', str(fraza).lower())}
    wynik = set()
    for slowo in re.findall(r'[a-z0-9]+', t):
        if slowo in wykluczone or slowo in IGNOROWANE_SLOWA or (slowo.isdigit() and len(slowo) <= 2):
            continue
        wynik.add(slowo)
    return frozenset(wynik)


def buduj_slownik(otomoto, bidfax):
    """Zwraca {(marka, model): frozenset(słowa rozróżniające wersje)}.

    otomoto: kolumny Marka, Model, Pojemnosc_L, Rok_produkcji, Moc_KM, Cena_PLN, Tokeny
    bidfax:  kolumny Marka, Otomoto_Model, Tokeny
    """
    o = otomoto.copy()
    o['Moc_KM'] = pd.to_numeric(o['Moc_KM'], errors='coerce')
    o['Moc_Wzgl'] = o['Moc_KM'] / o.groupby(['Marka', 'Model', 'Pojemnosc_L'], dropna=False)['Moc_KM'].transform('median')
    o['Cena_Wzgl'] = o['Cena_PLN'] / o.groupby(['Marka', 'Model', 'Pojemnosc_L', 'Rok_produkcji'], dropna=False)['Cena_PLN'].transform('median')

    slowa = o[['Marka', 'Model', 'Moc_Wzgl', 'Cena_Wzgl', 'Tokeny']].explode('Tokeny').dropna(subset=['Tokeny'])
    stat = slowa.groupby(['Marka', 'Model', 'Tokeny']).agg(
        n=('Tokeny', 'size'), moc=('Moc_Wzgl', 'median'), cena=('Cena_Wzgl', 'median')).reset_index()
    stat = stat[stat['n'] >= MIN_OGLOSZEN_SLOWA_OTOMOTO]
    stat = stat[((stat['moc'] - 1).abs() >= PROG_ROZNICY_MOCY) | ((stat['cena'] - 1).abs() >= PROG_ROZNICY_CENY)]

    b = bidfax[['Marka', 'Otomoto_Model', 'Tokeny']].explode('Tokeny').dropna(subset=['Tokeny'])
    b = b.groupby(['Marka', 'Otomoto_Model', 'Tokeny']).size().rename('n_bidfax').reset_index()
    b = b[b['n_bidfax'] >= MIN_AUKCJI_SLOWA_BIDFAX].rename(columns={'Otomoto_Model': 'Model'})

    wspolne = stat.merge(b, on=['Marka', 'Model', 'Tokeny'])
    return {klucz: frozenset(grupa['Tokeny']) for klucz, grupa in wspolne.groupby(['Marka', 'Model'])}


def wersja(tokeny, marka, model, slownik):
    """Etykieta wersji: posortowane słowa rozróżniające, które występują w tytule; brak takich -> 'podstawowa'."""
    istotne = sorted(set(tokeny) & slownik.get((marka, model), frozenset()))
    return ' '.join(istotne) if istotne else WERSJA_PODSTAWOWA
