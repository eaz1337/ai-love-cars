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
MIN_WZORCOW_MOCY = 3             # tyle ogłoszeń z wersją w tytule musi mieć dana wersja, żeby służyła za wzorzec mocy
PROG_DOPASOWANIA_MOCY = 0.03     # ogłoszenie bez wersji dostaje ją, gdy moc różni się od mocy wzorca o najwyżej 3%
MAX_ROZRZUT_MOCY_WZORCA = 0.20   # wzorzec odpada, gdy moce jego ogłoszeń są rozrzucone (10. i 90. percentyl różnią się o >20% mediany),
                                 # bo to znaczy, że słowo nie jest jedną wersją (np. 'st' z 'ST-Line' i prawdziwego ST)

# Skrzynia i napęd to osobne cechy (Otomoto ma na nie własne kolumny), nie wersja silnikowa. Dzieliłyby grupy bez potrzeby
IGNOROWANE_SLOWA = frozenset({
    'tiptronic', 'tronic', 'dsg', 'automatik', 'automatic', 'aut', 'manual', 'xdrive', 'quattro', '4matic', 'awd', '4x4',
    'gran', 'cabrio', 'cabriolet', 'convertible', 'roadster',   # nadwozie ma własną kolumnę, patrz nadwozie()
})

NADWOZIE_STANDARD = 'standard'
_GRAN_COUPE = re.compile(r'\bgran[\s-]*coup[eé]\b')
_CABRIO = re.compile(r'\b(cabrio|cabriolet|convertible|roadster)\b')


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


def nadwozie(tytul, opis=''):
    """'gran coupe', 'cabrio' albo 'standard'. Coupé, sedan i kombi to dla ceny jedno 'standard'; osobno wydzielamy tylko nadwozia,
    które wyraźnie zmieniają cenę (Gran Coupé to inne, 4-drzwiowe auto, cabrio kosztuje więcej od coupé).
    Tytuł Otomoto często tego nie mówi, więc szukamy też w opisie. Tytuł ma pierwszeństwo przed opisem."""
    for tekst in (str(tytul).lower(), str(opis if opis is not None else '').lower()):
        if _GRAN_COUPE.search(tekst):
            return 'gran coupe'
        if _CABRIO.search(tekst):
            return 'cabrio'
    return NADWOZIE_STANDARD


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


def uzupelnij_wersje_z_mocy(otomoto):
    """Ogłoszenia Otomoto bez wersji w tytule (np. 'Porsche Cayenne' z 640 KM) dostają wersję z mocy.

    Wzorzec to mediana mocy ogłoszeń z wersją w tytule, osobno dla (marka, model, pojemność, paliwo), bo benzyna i diesel
    o tej samej pojemności i mocy to różne auta (530i i 530d). Ogłoszenie bez wersji dostaje wersję wzorca, jeśli jego moc jest bliska mocy tylko jednego wzorca. Gdy pasuje kilka wersji albo żadna, zostaje 'podstawowa'.
    Dotyczy tylko ogłoszeń, których tytuł to samo 'marka model' (pusty zbiór słów). Tytuł z innymi słowami (np. 'Titanium',
    '20d AWD Prestige') już coś mówi o wersji lub wyposażeniu, tylko nieznanym słownikowi, więc go nie nadpisujemy.
    Dotyczy tylko Otomoto: Bidfax nie ma mocy, więc tam wersja pochodzi wyłącznie z tytułu.

    otomoto: kolumny Marka, Model, Pojemnosc_L, Paliwo, Moc_KM, Wersja, Tokeny. Zwraca nową kolumnę Wersja (ten sam indeks).
    """
    klucz = ['Marka', 'Model', 'Pojemnosc_L', 'Paliwo']
    moc = pd.to_numeric(otomoto['Moc_KM'], errors='coerce')
    ma_dane = moc.notna() & otomoto['Pojemnosc_L'].notna() & otomoto['Paliwo'].notna()
    z_wersja = otomoto['Wersja'] != WERSJA_PODSTAWOWA

    wzorce = otomoto[ma_dane & z_wersja].assign(Moc=moc)
    wzorce = wzorce.groupby(klucz + ['Wersja'])['Moc'].agg(
        median='median', size='size', q10=lambda m: m.quantile(0.1), q90=lambda m: m.quantile(0.9)).reset_index()
    wzorce = wzorce[(wzorce['size'] >= MIN_WZORCOW_MOCY)
                    & ((wzorce['q90'] - wzorce['q10']) / wzorce['median'] <= MAX_ROZRZUT_MOCY_WZORCA)]

    bez_slow = otomoto['Tokeny'].map(len) == 0
    kandydaci = otomoto[ma_dane & ~z_wersja & bez_slow].assign(Moc=moc)[klucz + ['Moc']].rename_axis('_idx').reset_index()
    pary = kandydaci.merge(wzorce, on=klucz)
    pary = pary[(pary['Moc'] - pary['median']).abs() / pary['median'] <= PROG_DOPASOWANIA_MOCY]
    # Tylko jednoznaczne dopasowania: jedna wersja w zasięgu tolerancji
    liczba_wersji = pary.groupby('_idx')['Wersja'].nunique()
    jednoznaczne = pary[pary['_idx'].isin(liczba_wersji[liczba_wersji == 1].index)].drop_duplicates('_idx').set_index('_idx')['Wersja']

    nowa = otomoto['Wersja'].copy()
    nowa.loc[jednoznaczne.index] = jednoznaczne
    return nowa
