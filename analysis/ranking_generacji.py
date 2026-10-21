import pandas as pd

MIN_AUKCJI_RANKING = 30
MIN_OGLOSZEN_RANKING = 10

KLUCZ_RANKINGU = ['Marka', 'Model', 'Generacja', 'Pojemnosc_L']
KOLUMNY_RANKINGU = [*KLUCZ_RANKINGU, 'Rocznik_Od', 'Rocznik_Do', 'Ilosc_Rocznikow', 'Ilosc_Aukcje_USA', 'Ilosc_USA_Kanada_Otomoto',
                    'Cena_USA_Kanada_Otomoto_PLN', 'Koszt_Import_PLN', 'Budzet_Na_Naprawe_PLN', 'Budzet_Na_Naprawe_%']


def _mediana_wazona(wartosci, wagi):
    """Najmniejsza wartość, od której skumulowana waga (posortowanych wartości) sięga połowy sumy wag."""
    kolejnosc = wartosci.sort_values().index
    skumulowane = wagi[kolejnosc].cumsum() / wagi.sum()
    return wartosci[kolejnosc][skumulowane >= 0.5].iloc[0]


def licz_ranking_generacji(gold, min_aukcji=MIN_AUKCJI_RANKING, min_ogloszen=MIN_OGLOSZEN_RANKING):
    """Ranking generacji z silnikiem (pojemnością): grupy marka+model+generacja+pojemność ze wszystkich roczników naraz, posortowane
    po budżecie na naprawę w %. Wartości to mediany ważone liczbą aukcji USA, więc liczniejsze roczniki liczą się bardziej.
    Pomijamy grupy o niskiej wiarygodności, z niepewną wersją lub podejrzanym rocznikiem oraz bez generacji, a po agregacji te,
    które mają łącznie mniej niż min_aukcji aukcji USA lub min_ogloszen ogłoszeń Otomoto."""
    ok = gold[(gold['Wiarygodnosc'] != 'niska') & (gold['Niepewna_Wersja'] == 0) & (gold['Podejrzany_Rocznik'] == 0)
              & (gold['Generacja'] != 'brak')]
    wiersze = []
    for klucz, g in ok.groupby(KLUCZ_RANKINGU, dropna=False):
        wagi = g['Ilosc_Aukcje_USA'].astype(float)
        wiersz = dict(zip(KLUCZ_RANKINGU, klucz))
        wiersz.update(
            Rocznik_Od=int(g['Rocznik'].min()), Rocznik_Do=int(g['Rocznik'].max()), Ilosc_Rocznikow=len(g),
            Ilosc_Aukcje_USA=int(g['Ilosc_Aukcje_USA'].sum()), Ilosc_USA_Kanada_Otomoto=int(g['Ilosc_USA_Kanada_Otomoto'].sum()))
        for kol in ['Cena_USA_Kanada_Otomoto_PLN', 'Koszt_Import_PLN', 'Budzet_Na_Naprawe_PLN', 'Budzet_Na_Naprawe_%']:
            wiersz[kol] = _mediana_wazona(g[kol].reset_index(drop=True), wagi.reset_index(drop=True))
        wiersze.append(wiersz)
    if not wiersze:
        return pd.DataFrame(columns=KOLUMNY_RANKINGU)
    ranking = pd.DataFrame(wiersze)
    ranking = ranking[(ranking['Ilosc_Aukcje_USA'] >= min_aukcji) & (ranking['Ilosc_USA_Kanada_Otomoto'] >= min_ogloszen)]
    return ranking.sort_values('Budzet_Na_Naprawe_%', ascending=False)[KOLUMNY_RANKINGU].round(1).reset_index(drop=True)
