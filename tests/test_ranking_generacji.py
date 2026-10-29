import pandas as pd

import ranking_generacji as rg


def _gold(wiersze):
    baza = dict(Marka='dodge', Model='charger', Generacja='LD FL', Pojemnosc_L=5.7, Rocznik=2018, Wiarygodnosc='wysoka',
                Niepewna_Wersja=0, Podejrzany_Rocznik=0, Ilosc_Aukcje_USA=20, Ilosc_USA_Kanada_Otomoto=12,
                Mediana_Cena_USA_Kanada_Otomoto_PLN=100_000, Ilosc_Krajowe=0, Mediana_Cena_Krajowe_PLN=None, Mediana_Koszt_Import_PLN=40_000, **{'Budzet_Na_Naprawe_PLN': 60_000, 'Budzet_Na_Naprawe_%': 60.0})
    return pd.DataFrame([dict(baza, **w) for w in wiersze])


def test_grupuje_roczniki_cena_wazona_ogloszeniami_a_koszt_aukcjami():
    gold = _gold([dict(Rocznik=2016, Ilosc_Aukcje_USA=5, Ilosc_USA_Kanada_Otomoto=2, Mediana_Cena_USA_Kanada_Otomoto_PLN=80_000, Mediana_Koszt_Import_PLN=30_000),
                  dict(Rocznik=2017, Ilosc_Aukcje_USA=30, Ilosc_USA_Kanada_Otomoto=10, Mediana_Cena_USA_Kanada_Otomoto_PLN=100_000, Mediana_Koszt_Import_PLN=40_000),
                  dict(Rocznik=2018, Ilosc_Aukcje_USA=5, Ilosc_USA_Kanada_Otomoto=2, Mediana_Cena_USA_Kanada_Otomoto_PLN=120_000, Mediana_Koszt_Import_PLN=50_000)])
    wynik = rg.licz_ranking_generacji(gold)
    assert len(wynik) == 1
    w = wynik.iloc[0]
    assert (w['Rocznik_Od'], w['Rocznik_Do'], w['Ilosc_Rocznikow'], w['Ilosc_Aukcje_USA']) == (2016, 2018, 3, 40)
    assert (w['Mediana_Cena_USA_Kanada_Otomoto_PLN'], w['Mediana_Koszt_Import_PLN'], w['Budzet_Na_Naprawe_PLN'], w['Budzet_Na_Naprawe_%']) == (100_000, 40_000, 60_000, 60.0)


def test_cena_nie_zalezy_od_liczby_aukcji_usa_gdy_ogloszen_jest_wiecej_w_tanszym_roczniku():
    gold = _gold([dict(Rocznik=2018, Ilosc_Aukcje_USA=16, Ilosc_USA_Kanada_Otomoto=7, Mediana_Cena_USA_Kanada_Otomoto_PLN=38_500),
                  dict(Rocznik=2020, Ilosc_Aukcje_USA=19, Ilosc_USA_Kanada_Otomoto=3, Mediana_Cena_USA_Kanada_Otomoto_PLN=55_000)])
    assert rg.licz_ranking_generacji(gold).iloc[0]['Mediana_Cena_USA_Kanada_Otomoto_PLN'] == 38_500


def test_dodaje_ilosc_i_cene_z_rynku_polskiego_wazona_ogloszeniami_krajowymi():
    gold = _gold([dict(Rocznik=2017, Ilosc_Krajowe=9, Mediana_Cena_Krajowe_PLN=70_000), dict(Rocznik=2018, Ilosc_Krajowe=1, Mediana_Cena_Krajowe_PLN=90_000)])
    w = rg.licz_ranking_generacji(gold).iloc[0]
    assert (w['Ilosc_Krajowe'], w['Mediana_Cena_Krajowe_PLN']) == (10, 70_000)


def test_brak_ogloszen_krajowych_zostawia_pusta_cene():
    w = rg.licz_ranking_generacji(_gold([dict()])).iloc[0]
    assert w['Ilosc_Krajowe'] == 0 and pd.isna(w['Mediana_Cena_Krajowe_PLN'])


def test_rozne_silniki_tej_samej_generacji_to_osobne_wiersze_posortowane_po_budzecie():
    gold = _gold([dict(Pojemnosc_L=3.6, Ilosc_Aukcje_USA=40, Mediana_Cena_USA_Kanada_Otomoto_PLN=100_000, Mediana_Koszt_Import_PLN=45_000),
                  dict(Pojemnosc_L=5.7, Ilosc_Aukcje_USA=40, Mediana_Cena_USA_Kanada_Otomoto_PLN=100_000, Mediana_Koszt_Import_PLN=35_000)])
    assert list(rg.licz_ranking_generacji(gold)['Pojemnosc_L']) == [5.7, 3.6]


def test_pomija_niewiarygodne_niepewne_podejrzane_i_bez_generacji():
    gold = _gold([dict(Ilosc_Aukcje_USA=40, Wiarygodnosc='niska'), dict(Ilosc_Aukcje_USA=40, Niepewna_Wersja=1),
                  dict(Ilosc_Aukcje_USA=40, Podejrzany_Rocznik=1), dict(Ilosc_Aukcje_USA=40, Generacja='brak')])
    assert rg.licz_ranking_generacji(gold).empty


def test_pomija_grupy_z_za_mala_proba_po_agregacji():
    assert rg.licz_ranking_generacji(_gold([dict(Ilosc_Aukcje_USA=9)])).empty
    assert rg.licz_ranking_generacji(_gold([dict(Ilosc_Aukcje_USA=40, Ilosc_USA_Kanada_Otomoto=4)])).empty


def test_brak_pojemnosci_tworzy_wlasna_grupe():
    gold = _gold([dict(Pojemnosc_L=None, Ilosc_Aukcje_USA=40)])
    assert len(rg.licz_ranking_generacji(gold)) == 1
