import json

import pandas as pd
import pytest

import kursy
import koszt_importu
import oplaty_aukcyjne

USD, EUR = 3.9293, 4.4026
PUNKTY = [(5225.0, 925.0), (19600.0, 1466.0)]


def _auto(cena, transport, akcyza=0.0):
    return pd.DataFrame({'Cena_USD': [cena], 'Transport_Ladowy_USD': [transport], 'Stawka_Akcyzy': [akcyza]})


class TestOplataAukcyjna:
    def test_punkty_kalkulatora_daja_dokladna_oplate(self):
        assert oplaty_aukcyjne.oplata_aukcyjna_usd(5225, PUNKTY) == 925
        assert oplaty_aukcyjne.oplata_aukcyjna_usd(19600, PUNKTY) == 1466

    def test_miedzy_punktami_interpolacja(self):
        assert 925 < oplaty_aukcyjne.oplata_aukcyjna_usd(10000, PUNKTY) < 1466

    def test_poza_zakresem_przedluza_odcinek_i_nie_schodzi_ponizej_zera(self):
        assert oplaty_aukcyjne.oplata_aukcyjna_usd(30000, PUNKTY) > 1466
        assert oplaty_aukcyjne.oplata_aukcyjna_usd(0, [(100.0, 10.0), (200.0, 5.0)]) >= 0

    def test_brak_punktow_to_zero(self):
        assert oplaty_aukcyjne.oplata_aukcyjna_usd(5000, []) == 0

    def test_wczytanie_csv(self, tmp_path):
        plik = tmp_path / 'o.csv'
        plik.write_text('cena_usd,oplata_usd,uwagi\n19600,1466,\n5225,925,\n', encoding='utf-8')
        assert oplaty_aukcyjne.wczytaj_oplaty(str(plik)) == PUNKTY
        assert oplaty_aukcyjne.wczytaj_oplaty(str(tmp_path / 'brak.csv')) == []


class TestKosztImportu:
    @pytest.mark.parametrize('cena, transport, oczekiwane', [
        (19600, 520, 125231),   # kalkulator BidCars, auto z Mazowieckiego, bez akcyzy
        (5225, 465, 46737),     # Mercedes CLA 250, akcyza 0 zł
    ])
    def test_zgodny_z_kalkulatorem_bidcars(self, cena, transport, oczekiwane):
        wynik = koszt_importu.licz_koszt_importu(_auto(cena, transport), USD, EUR, PUNKTY)
        assert wynik['Koszt_Import_PLN'].iloc[0] == pytest.approx(oczekiwane, abs=250)

    def test_akcyza_jest_naliczana_od_wartosci_z_cleem_bez_vat(self):
        wynik = koszt_importu.licz_koszt_importu(_auto(10000, 500, akcyza=0.031), USD, EUR, PUNKTY)
        assert wynik['Akcyza_PLN'].iloc[0] == pytest.approx(wynik['Wartosc_do_VAT_PLN'].iloc[0] * 0.031)

    def test_vat_holenderski_od_wartosci_celnej_z_cleem(self):
        wynik = koszt_importu.licz_koszt_importu(_auto(10000, 500), USD, EUR, PUNKTY)
        assert wynik['VAT_PLN'].iloc[0] == pytest.approx(wynik['Wartosc_do_VAT_PLN'].iloc[0] * 0.21)


class TestKursy:
    def test_kurs_z_nbp_trafia_do_cache(self, tmp_path):
        cache = tmp_path / 'c.json'
        wynik = kursy.kursy(('USD',), cache=str(cache), pobierz=lambda w: (3.9, '2026-10-26'))
        assert wynik['USD']['kurs'] == 3.9
        assert json.loads(cache.read_text())['USD']['kurs'] == 3.9

    def test_gdy_nbp_nie_dziala_bierze_cache_a_potem_wartosc_awaryjna(self, tmp_path, monkeypatch):
        monkeypatch.delenv('GOLD_USD_PLN', raising=False)
        cache = tmp_path / 'c.json'
        cache.write_text(json.dumps({'USD': {'kurs': 3.7, 'data': '2026-10-01'}}))

        def blad(_):
            raise OSError('brak sieci')
        assert kursy.kursy(('USD',), cache=str(cache), pobierz=blad)['USD']['kurs'] == 3.7
        assert kursy.kursy(('EUR',), cache=str(tmp_path / 'nowy.json'), pobierz=blad)['EUR']['kurs'] == kursy.KURSY_AWARYJNE['EUR']

    def test_zmienna_srodowiskowa_ma_pierwszenstwo(self, tmp_path, monkeypatch):
        monkeypatch.setenv('GOLD_USD_PLN', '4.2')
        wynik = kursy.kursy(('USD',), cache=str(tmp_path / 'c.json'), pobierz=lambda w: (3.9, 'x'))
        assert wynik['USD'] == {'kurs': 4.2, 'zrodlo': 'zmienna środowiskowa'}
