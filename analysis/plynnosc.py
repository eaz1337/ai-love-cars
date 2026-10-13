import pandas as pd

MIN_ZDJETYCH = 3
STATUS_ZAKONCZONE = 'Zakończone'

KOLUMNY_PLYNNOSCI = ['Ilosc_Zdjetych_Otomoto', 'Zdjete_Otomoto_%', 'Mediana_Dni_Na_Rynku']


def licz_plynnosc(otomoto, klucz, min_zdjetych=MIN_ZDJETYCH):
    """Jak szybko znikają ogłoszenia z Otomoto: mediana dni od publikacji (Data_Startu) do ostatniego widzenia
    dla ogłoszeń ze statusem 'Zakończone' oraz udział zdjętych w całej grupie. Oczekuje jednego wiersza na ogłoszenie.
    Zdjęte nie znaczy dosłownie sprzedane (sprzedawca mógł je wycofać). Mediana dni jest NaN przy mniej niż `min_zdjetych`
    zdjętych. Obserwujemy ogłoszenia dopiero od początku zbierania danych, więc mediana jest zaniżona (cenzurowanie)."""
    ramka = otomoto[klucz].copy()
    ramka['_zdjete'] = (otomoto['Status'] == STATUS_ZAKONCZONE)
    start = pd.to_datetime(otomoto['Data_Startu'], errors='coerce')
    koniec = pd.to_datetime(otomoto['Data_Ostatniego_Widzenia'], errors='coerce')
    ramka['_dni'] = ((koniec - start).dt.total_seconds() / 86400).clip(lower=0).where(ramka['_zdjete'])

    wynik = ramka.groupby(klucz).agg(
        Ilosc_Zdjetych_Otomoto=('_zdjete', 'sum'),
        _wszystkie=('_zdjete', 'size'),
        Mediana_Dni_Na_Rynku=('_dni', 'median'),
    ).reset_index()
    wynik['Ilosc_Zdjetych_Otomoto'] = wynik['Ilosc_Zdjetych_Otomoto'].astype(int)
    wynik['Zdjete_Otomoto_%'] = wynik['Ilosc_Zdjetych_Otomoto'] / wynik['_wszystkie'] * 100
    wynik['Mediana_Dni_Na_Rynku'] = wynik['Mediana_Dni_Na_Rynku'].where(wynik['Ilosc_Zdjetych_Otomoto'] >= min_zdjetych)
    return wynik.drop(columns='_wszystkie')
