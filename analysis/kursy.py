"""Kursy walut z NBP (tabela A, średni kurs z ostatniego dnia notowań).

Ostatni pobrany kurs ląduje w kursy_cache.json. Gdy NBP jest niedostępne, bierzemy kurs z cache, a bez niego wartości
awaryjne. Zmienne GOLD_USD_PLN i GOLD_EUR_PLN nadpisują wszystko (przydatne w testach i do ręcznych scenariuszy).
"""
import json
import os
import urllib.request

URL_NBP = 'https://api.nbp.pl/api/exchangerates/rates/a/{waluta}/?format=json'
PLIK_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kursy_cache.json')
KURSY_AWARYJNE = {'USD': 3.90, 'EUR': 4.40}
TIMEOUT_S = 10


def pobierz_kurs_nbp(waluta, timeout=TIMEOUT_S):
    """Zwraca (kurs, data) z NBP albo rzuca wyjątek, gdy API nie odpowiada."""
    with urllib.request.urlopen(URL_NBP.format(waluta=waluta.lower()), timeout=timeout) as odpowiedz:
        notowanie = json.load(odpowiedz)['rates'][0]
    return float(notowanie['mid']), notowanie['effectiveDate']


def _wczytaj_cache(sciezka):
    try:
        with open(sciezka, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def kursy(waluty=('USD', 'EUR'), cache=PLIK_CACHE, pobierz=pobierz_kurs_nbp):
    """Zwraca {waluta: {'kurs': float, 'zrodlo': str}}. Kolejność: zmienna środowiskowa, NBP, cache, wartość awaryjna."""
    zapisane = _wczytaj_cache(cache)
    wynik = {}
    for waluta in waluty:
        reczny = os.environ.get(f'GOLD_{waluta}_PLN')
        if reczny:
            wynik[waluta] = {'kurs': float(reczny), 'zrodlo': 'zmienna środowiskowa'}
            continue
        try:
            kurs, data = pobierz(waluta)
            zapisane[waluta] = {'kurs': kurs, 'data': data}
            wynik[waluta] = {'kurs': kurs, 'zrodlo': f'NBP {data}'}
        except Exception:
            if waluta in zapisane:
                wynik[waluta] = {'kurs': zapisane[waluta]['kurs'], 'zrodlo': f"cache z {zapisane[waluta].get('data', '?')}"}
            else:
                wynik[waluta] = {'kurs': KURSY_AWARYJNE[waluta], 'zrodlo': 'wartość awaryjna'}
    try:
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump(zapisane, f, indent=2)
    except OSError:
        pass
    return wynik
