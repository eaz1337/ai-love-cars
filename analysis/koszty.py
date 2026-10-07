"""Koszt transportu lądowego z lokalizacji aukcji do portu (analysis/koszty_transportu.csv).

Plik ma kolumny: klucz, port, transport_ladowy_usd, uwagi. Klucz to kod stanu lub prowincji ('TX', 'ON') albo cała nazwa
lokalizacji małymi literami ('anaheim consolidated') dla miejsc bez stanu w nazwie. Wiersz '*' to wartość domyślna.
Kwoty w pliku to szacunki do ręcznej weryfikacji, nie dane z cennika przewoźnika.
"""
import csv
import os
import re

DOMYSLNY_KLUCZ = '*'


def stan_z_lokalizacji(lokalizacja):
    """'Tx - Houston' (Copart) i 'San Diego (CA)' (IAAI) -> 'TX' / 'CA'. Bez stanu w nazwie -> None."""
    tekst = str(lokalizacja).strip()
    m = re.match(r'^([A-Za-z]{2}) - ', tekst) or re.search(r'\(([A-Za-z]{2})\)\s*$', tekst)
    return m.group(1).upper() if m else None


def wczytaj_koszty(sciezka):
    """Zwraca {klucz: koszt_usd}. Brak pliku to pusty słownik (wtedy transport lądowy liczymy jako 0)."""
    if not sciezka or not os.path.exists(sciezka):
        return {}
    with open(sciezka, newline='', encoding='utf-8') as f:
        return {w['klucz'].strip().lower() if w['klucz'].strip() != DOMYSLNY_KLUCZ else DOMYSLNY_KLUCZ:
                float(w['transport_ladowy_usd']) for w in csv.DictReader(f) if (w.get('klucz') or '').strip()}


def transport_ladowy_usd(lokalizacja, koszty):
    """Najpierw dokładna nazwa lokalizacji, potem stan, na końcu wartość domyślna ('*'), a bez niej 0."""
    dokladna = str(lokalizacja).strip().lower()
    if dokladna in koszty:
        return koszty[dokladna]
    stan = stan_z_lokalizacji(lokalizacja)
    if stan and stan.lower() in koszty:
        return koszty[stan.lower()]
    return koszty.get(DOMYSLNY_KLUCZ, 0.0)
