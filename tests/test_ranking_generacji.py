import pandas as pd

import ranking_generacji as rg


def _gold(wiersze):
    baza = dict(Marka='dodge', Model='charger', Generacja='LD FL', Pojemnosc_L=5.7, Rocznik=2018, Wiarygodnosc='wysoka',
                Niepewna_Wersja=0, Podejrzany_Rocznik=0, Ilosc_Aukcje_USA=20, Ilosc_USA_Kanada_Otomoto=12,
                Cena_USA_Kanada_Otomoto_PLN=100_000, Koszt_Import_PLN=40_000, **{'Budzet_Na_Naprawe_PLN': 60_000, 'Budzet_Na_Naprawe_%': 60.0})
    return pd.DataFrame([dict(baza, **w) for w in wiersze])


def test_grupuje_roczniki_i_liczy_mediane_wazona_aukcjami():
    gold = _gold([dict(Rocznik=2016, Ilosc_Aukcje_USA=5, **{'Budzet_Na_Naprawe_%': 50.0}),
                  dict(Rocznik=2017, Ilosc_Aukcje_USA=30, **{'Budzet_Na_Naprawe_%': 65.0}),
                  dict(Rocznik=2018, Ilosc_Aukcje_USA=5, **{'Budzet_Na_Naprawe_%': 70.0})])
    wynik = rg.licz_ranking_generacji(gold)
    assert len(wynik) == 1
    w = wynik.iloc[0]
    assert (w['Rocznik_Od'], w['Rocznik_Do'], w['Ilosc_Rocznikow'], w['Ilosc_Aukcje_USA']) == (2016, 2018, 3, 40)
    assert w['Budzet_Na_Naprawe_%'] == 65.0


def test_rozne_silniki_tej_samej_generacji_to_osobne_wiersze_posortowane_po_budzecie():
    gold = _gold([dict(Pojemnosc_L=3.6, Ilosc_Aukcje_USA=40, **{'Budzet_Na_Naprawe_%': 55.0}),
                  dict(Pojemnosc_L=5.7, Ilosc_Aukcje_USA=40, **{'Budzet_Na_Naprawe_%': 65.0})])
    assert list(rg.licz_ranking_generacji(gold)['Pojemnosc_L']) == [5.7, 3.6]


def test_pomija_niewiarygodne_niepewne_podejrzane_i_bez_generacji():
    gold = _gold([dict(Ilosc_Aukcje_USA=40, Wiarygodnosc='niska'), dict(Ilosc_Aukcje_USA=40, Niepewna_Wersja=1),
                  dict(Ilosc_Aukcje_USA=40, Podejrzany_Rocznik=1), dict(Ilosc_Aukcje_USA=40, Generacja='brak')])
    assert rg.licz_ranking_generacji(gold).empty


def test_pomija_grupy_z_za_mala_proba_po_agregacji():
    assert rg.licz_ranking_generacji(_gold([dict(Ilosc_Aukcje_USA=10)])).empty
    assert rg.licz_ranking_generacji(_gold([dict(Ilosc_Aukcje_USA=40, Ilosc_USA_Kanada_Otomoto=2)])).empty


def test_brak_pojemnosci_tworzy_wlasna_grupe():
    gold = _gold([dict(Pojemnosc_L=None, Ilosc_Aukcje_USA=40)])
    assert len(rg.licz_ranking_generacji(gold)) == 1
