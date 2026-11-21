import pandas as pd

MIN_AUKCJI_RANKING = 10
MIN_OGLOSZEN_RANKING = 5

KLUCZ_RANKINGU = ['Marka', 'Model', 'Generacja', 'Pojemnosc_L']
KOLUMNY_RANKINGU = [*KLUCZ_RANKINGU, 'Rocznik_Od', 'Rocznik_Do', 'Ilosc_Rocznikow', 'Ilosc_Aukcje_USA', 'Ilosc_USA_Kanada_Otomoto',
                    'Mediana_Cena_USA_Kanada_Otomoto_PLN', 'Ilosc_Krajowe', 'Mediana_Cena_Krajowe_PLN', 'Budzet_Na_Naprawe_PLN', 'Budzet_Na_Naprawe_%', 'Mediana_Koszt_Import_PLN']


def _mediana_wazona(wartosci, wagi):
    """Najmniejsza wartość, od której skumulowana waga (posortowanych wartości) sięga połowy sumy wag."""
    kolejnosc = wartosci.sort_values().index
    skumulowane = wagi[kolejnosc].cumsum() / wagi.sum()
    return wartosci[kolejnosc][skumulowane >= 0.5].iloc[0]


def licz_ranking_generacji(gold, min_aukcji=MIN_AUKCJI_RANKING, min_ogloszen=MIN_OGLOSZEN_RANKING, z_niska_wiarygodnoscia=False):
    """Ranking generacji z silnikiem (pojemnością): grupy marka+model+generacja+pojemność ze wszystkich roczników naraz, posortowane
    po budżecie na naprawę w %. Cena to mediana ważona liczbą ogłoszeń Otomoto, cena krajowa liczbą ogłoszeń krajowych, koszt importu liczbą aukcji USA, a budżet na naprawę z nich wynika.
    Pomijamy grupy o niskiej wiarygodności (chyba że z_niska_wiarygodnoscia, np. dla rankingu z egzotykami), z niepewną wersją lub podejrzanym rocznikiem oraz bez generacji, a po agregacji te,
    które mają łącznie mniej niż min_aukcji aukcji USA lub min_ogloszen ogłoszeń Otomoto."""
    wiarygodne = True if z_niska_wiarygodnoscia else (gold['Wiarygodnosc'] != 'niska')
    ok = gold[wiarygodne & (gold['Niepewna_Wersja'] == 0) & (gold['Podejrzany_Rocznik'] == 0)
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
        wiersz['Mediana_Cena_USA_Kanada_Otomoto_PLN'] = _mediana_wazona(g['Mediana_Cena_USA_Kanada_Otomoto_PLN'], g['Ilosc_USA_Kanada_Otomoto'].astype(float))
        krajowe = g.dropna(subset=['Mediana_Cena_Krajowe_PLN'])
        wagi_krajowe = krajowe['Ilosc_Krajowe'].astype(float)
        wiersz['Mediana_Cena_Krajowe_PLN'] = _mediana_wazona(krajowe['Mediana_Cena_Krajowe_PLN'], wagi_krajowe) if wagi_krajowe.sum() > 0 else None
        wiersz['Mediana_Koszt_Import_PLN'] = _mediana_wazona(g['Mediana_Koszt_Import_PLN'], wagi.reset_index(drop=True))
        wiersz['Budzet_Na_Naprawe_PLN'] = wiersz['Mediana_Cena_USA_Kanada_Otomoto_PLN'] - wiersz['Mediana_Koszt_Import_PLN']
        wiersz['Budzet_Na_Naprawe_%'] = wiersz['Budzet_Na_Naprawe_PLN'] / wiersz['Mediana_Cena_USA_Kanada_Otomoto_PLN'] * 100
        wiersze.append(wiersz)
    if not wiersze:
        return pd.DataFrame(columns=KOLUMNY_RANKINGU)
    ranking = pd.DataFrame(wiersze)
    ranking = ranking[(ranking['Ilosc_Aukcje_USA'] >= min_aukcji) & (ranking['Ilosc_USA_Kanada_Otomoto'] >= min_ogloszen)]
    return ranking.sort_values('Budzet_Na_Naprawe_%', ascending=False)[KOLUMNY_RANKINGU].round(1).reset_index(drop=True)


def licz_ranking_egzotykow(gold, egzotyki, min_aukcji=MIN_AUKCJI_RANKING, min_ogloszen=MIN_OGLOSZEN_RANKING):
    """Ranking generacji, które nie mieszczą się w zwykłym rankingu i da się je policzyć dopiero z egzotykami (grupy poniżej progów
    aukcji/ogłoszeń). Rozłączny ze zwykłym rankingiem: pozycje z licz_ranking_generacji(gold) są tu pominięte."""
    zwykly = licz_ranking_generacji(gold, min_aukcji, min_ogloszen)
    razem = licz_ranking_generacji(pd.concat([gold, egzotyki], ignore_index=True), min_aukcji, min_ogloszen, z_niska_wiarygodnoscia=True)
    zajete = set(map(tuple, zwykly[KLUCZ_RANKINGU].to_numpy()))
    return razem[[tuple(k) not in zajete for k in razem[KLUCZ_RANKINGU].to_numpy()]].reset_index(drop=True)
