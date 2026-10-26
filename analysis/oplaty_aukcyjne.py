"""Opłaty aukcyjne (Copart/IAAI) jako funkcja ceny zakupu, z punktów w analysis/oplaty_aukcyjne.csv.

Plik ma kolumny: cena_usd, oplata_usd, uwagi. Opłata rośnie z ceną, więc między punktami interpolujemy liniowo, a poza
zakresem przedłużamy skrajny odcinek. Im więcej prawdziwych punktów (z kalkulatora pośrednika), tym dokładniejszy wynik.
"""
import csv
import os


def wczytaj_oplaty(sciezka):
    """Zwraca listę (cena, opłata) posortowaną po cenie. Brak pliku to pusta lista (opłata aukcyjna liczona jako 0)."""
    if not sciezka or not os.path.exists(sciezka):
        return []
    with open(sciezka, newline='', encoding='utf-8') as f:
        punkty = [(float(w['cena_usd']), float(w['oplata_usd'])) for w in csv.DictReader(f) if (w.get('cena_usd') or '').strip()]
    return sorted(punkty)


def oplata_aukcyjna_usd(cena, punkty):
    if not punkty:
        return 0.0
    if len(punkty) == 1:
        return punkty[0][1]
    # Wewnątrz zakresu bierzemy odcinek, w którym leży cena; poza nim skrajny
    for (x1, y1), (x2, y2) in zip(punkty, punkty[1:]):
        if cena <= x2:
            break
    return max(0.0, y1 + (y2 - y1) * (cena - x1) / (x2 - x1))
