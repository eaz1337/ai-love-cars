import sqlite3
from datetime import date

import pandas as pd

OKNO_DNI = 90
MIN_AUKCJI_W_OKNIE = 2

KOLUMNY_TRENDU = [
    'Cena_USA_Ostatnie_USD', 'Cena_USA_Poprzednie_USD', 'Trend_Ceny_USA_%',
    'Aukcje_Ostatnie', 'Aukcje_Poprzednie', 'Trend_Podazy_USA_%',
]


def licz_trendy(bidfax, klucz, okno_dni=OKNO_DNI, min_w_oknie=MIN_AUKCJI_W_OKNIE):
    """Porównuje ostatnie `okno_dni` dni sprzedaży z poprzednimi `okno_dni` dniami, osobno dla każdej grupy `klucz`.
    Okna liczymy od najnowszej daty sprzedaży w danych, nie od dzisiaj (scraper mógł dawno nie biec).
    Trend ceny i podaży jest NaN, gdy w którymkolwiek oknie jest mniej niż `min_w_oknie` aukcji (szum zamiast sygnału)."""
    daty = pd.to_datetime(bidfax['Date_Of_Sale'], errors='coerce')
    cena = pd.to_numeric(bidfax['Cena_USD'], errors='coerce')
    ramka = bidfax[klucz].copy()
    ramka['_data'] = daty
    ramka['_cena'] = cena
    ramka = ramka.dropna(subset=['_data', '_cena'])
    if ramka.empty:
        return pd.DataFrame(columns=klucz + KOLUMNY_TRENDU)

    koniec = ramka['_data'].max()
    granica = koniec - pd.Timedelta(days=okno_dni)
    poczatek = granica - pd.Timedelta(days=okno_dni)
    ostatnie = ramka[ramka['_data'] > granica]
    poprzednie = ramka[(ramka['_data'] > poczatek) & (ramka['_data'] <= granica)]

    def agreguj(czesc, sufiks):
        return czesc.groupby(klucz, dropna=False).agg(
            **{f'Cena_USA_{sufiks}_USD': ('_cena', 'median'), f'Aukcje_{sufiks}': ('_cena', 'count')})

    wynik = agreguj(ostatnie, 'Ostatnie').join(agreguj(poprzednie, 'Poprzednie'), how='outer').reset_index()
    for kol in ('Aukcje_Ostatnie', 'Aukcje_Poprzednie'):
        wynik[kol] = wynik[kol].fillna(0).astype(int)

    wystarczy = (wynik['Aukcje_Ostatnie'] >= min_w_oknie) & (wynik['Aukcje_Poprzednie'] >= min_w_oknie)
    wynik['Trend_Ceny_USA_%'] = ((wynik['Cena_USA_Ostatnie_USD'] / wynik['Cena_USA_Poprzednie_USD'] - 1) * 100).where(wystarczy)
    wynik['Trend_Podazy_USA_%'] = ((wynik['Aukcje_Ostatnie'] / wynik['Aukcje_Poprzednie'].replace(0, float('nan')) - 1) * 100).where(wystarczy)
    return wynik


def zapisz_snapshot(conn, tabela, dane, data_snapshotu=None):
    """Dopisuje bieżący stan analizy do tabeli historii z datą. Ponowny przebieg tego samego dnia zastępuje
    wpisy z tego dnia (bez duplikatów). Nowe kolumny w analizie są dodawane do istniejącej tabeli historii."""
    data_snapshotu = data_snapshotu or date.today().isoformat()
    snapshot = dane.copy()
    snapshot.insert(0, 'Data_Snapshotu', data_snapshotu)

    istnieje = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabela,)).fetchone()
    if istnieje:
        obecne = {r[1] for r in conn.execute(f'PRAGMA table_info("{tabela}")')}
        for kol in snapshot.columns:
            if kol not in obecne:
                typ = 'REAL' if pd.api.types.is_numeric_dtype(snapshot[kol]) else 'TEXT'
                conn.execute(f'ALTER TABLE "{tabela}" ADD COLUMN "{kol}" {typ}')
        conn.execute(f'DELETE FROM "{tabela}" WHERE Data_Snapshotu = ?', (data_snapshotu,))
    snapshot.to_sql(tabela, conn, if_exists='append', index=False)
    return len(snapshot)
