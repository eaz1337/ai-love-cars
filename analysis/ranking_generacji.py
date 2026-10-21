import pandas as pd

MIN_AUKCJI_RANKING = 10
MIN_OGLOSZEN_RANKING = 5

KLUCZ_RANKINGU = ['Marka', 'Model', 'Generacja', 'Pojemnosc_L']
KOLUMNY_RANKINGU = [*KLUCZ_RANKINGU, 'Rocznik_Od', 'Rocznik_Do', 'Ilosc_Rocznikow', 'Ilosc_Aukcje_USA', 'Ilosc_USA_Kanada_Otomoto',
                    'Cena_USA_Kanada_Otomoto_PLN', 'Ilosc_Krajowe', 'Cena_Krajowe_PLN', 'Koszt_Import_PLN', 'Budzet_Na_Naprawe_PLN', 'Budzet_Na_Naprawe_%']


def _mediana_wazona(wartosci, wagi):
    """Najmniejsza wartość, od której skumulowana waga (posortowanych wartości) sięga połowy sumy wag."""
    kolejnosc = wartosci.sort_values().index
    skumulowane = wagi[kolejnosc].cumsum() / wagi.sum()
    return wartosci[kolejnosc][skumulowane >= 0.5].iloc[0]


def licz_ranking_generacji(gold, min_aukcji=MIN_AUKCJI_RANKING, min_ogloszen=MIN_OGLOSZEN_RANKING):
    """Ranking generacji z silnikiem (pojemnością): grupy marka+model+generacja+pojemność ze wszystkich roczników naraz, posortowane
    po budżecie na naprawę w %. Cena to mediana ważona liczbą ogłoszeń Otomoto, cena krajowa liczbą ogłoszeń krajowych, koszt importu liczbą aukcji USA, a budżet na naprawę z nich wynika.
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
            Ilosc_Aukcje_USA=int(g['Ilosc_Aukcje_USA'].sum()), Ilosc_USA_Kanada_Otomoto=int(g['Ilosc_USA_Kanada_Otomoto'].sum()),
            Ilosc_Krajowe=int(g['Ilosc_Krajowe'].fillna(0).sum()))
        g = g.reset_index(drop=True)
        wiersz['Cena_USA_Kanada_Otomoto_PLN'] = _mediana_wazona(g['Cena_USA_Kanada_Otomoto_PLN'], g['Ilosc_USA_Kanada_Otomoto'].astype(float))
        krajowe = g.dropna(subset=['Cena_Krajowe_PLN'])
        wagi_krajowe = krajowe['Ilosc_Krajowe'].astype(float)
        wiersz['Cena_Krajowe_PLN'] = _mediana_wazona(krajowe['Cena_Krajowe_PLN'], wagi_krajowe) if wagi_krajowe.sum() > 0 else None
        wiersz['Koszt_Import_PLN'] = _mediana_wazona(g['Koszt_Import_PLN'], wagi.reset_index(drop=True))
        wiersz['Budzet_Na_Naprawe_PLN'] = wiersz['Cena_USA_Kanada_Otomoto_PLN'] - wiersz['Koszt_Import_PLN']
        wiersz['Budzet_Na_Naprawe_%'] = wiersz['Budzet_Na_Naprawe_PLN'] / wiersz['Cena_USA_Kanada_Otomoto_PLN'] * 100
        wiersze.append(wiersz)
    if not wiersze:
        return pd.DataFrame(columns=KOLUMNY_RANKINGU)
    ranking = pd.DataFrame(wiersze)
    ranking = ranking[(ranking['Ilosc_Aukcje_USA'] >= min_aukcji) & (ranking['Ilosc_USA_Kanada_Otomoto'] >= min_ogloszen)]
    return ranking.sort_values('Budzet_Na_Naprawe_%', ascending=False)[KOLUMNY_RANKINGU].round(1).reset_index(drop=True)
