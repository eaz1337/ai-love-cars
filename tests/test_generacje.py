import sqlite3

import generacje
import generacje_otomoto as go

HTML = """
<ul>
<li><a href="https://www.otomoto.pl/osobowe/bmw/seria-3?search%5Bfilter_enum_generation%5D=gen-g20-2019" title="BMW Seria 3 G20/G21 (2019-)">x</a></li>
<li><a href="https://www.otomoto.pl/osobowe/bmw/seria-3?search%5Bfilter_enum_generation%5D=gen-f30-2012" title="BMW Seria 3 F30/F31 (2012-2020)">x</a></li>
<li><a href="https://www.otomoto.pl/osobowe/bmw/seria-3?search%5Bfilter_enum_generation%5D=gen-f30-2012" title="BMW Seria 3 F30/F31 (2012-2020)">x</a></li>
<li><a href="https://www.otomoto.pl/osobowe/bmw/seria-5?search%5Bfilter_enum_generation%5D=gen-g30-2017" title="BMW Seria 5 G30 (2017-2023)">x</a></li>
<li><a href="https://www.otomoto.pl/osobowe/bmw/seria-3?search%5Bfilter_enum_make%5D=bmw" title="BMW Seria 3 (2010-2011)">x</a></li>
</ul>
"""


def test_slug():
    assert go.slug('Seria 3') == 'seria-3'
    assert go.slug('Mercedes-Benz') == 'mercedes-benz'
    assert go.slug('CX-5') == 'cx-5'


def test_parsuj_generacje_pomija_duplikaty_i_cudze_modele():
    assert go.parsuj_generacje(HTML, 'BMW', 'Seria 3') == [('G20/G21', 2019, 9999), ('F30/F31', 2012, 2020)]


def test_parsuj_generacje_bez_linkow():
    assert go.parsuj_generacje('<html></html>', 'BMW', 'Seria 3') == []


def _slownik():
    return {('bmw', 'seria 3'): [('G20', 2019, 9999), ('F30', 2012, 2020), ('E90', 2005, 2012)]}


def test_generacja_nakladajace_sie_lata_biora_nowsza():
    assert generacje.generacja('bmw', 'seria 3', 2012, _slownik()) == 'F30 (2012-2020)'
    assert generacje.generacja('bmw', 'seria 3', 2020, _slownik()) == 'G20 (2019-)'
    assert generacje.generacja('bmw', 'seria 3', 2008, _slownik()) == 'E90 (2005-2012)'


def test_generacja_brak_slownika_lub_roku():
    assert generacje.generacja('bmw', 'seria 3', 1990, _slownik()) == generacje.BRAK_GENERACJI
    assert generacje.generacja('audi', 'a4', 2015, _slownik()) == generacje.BRAK_GENERACJI


def test_wczytaj_generacje_z_bazy_i_brak_tabeli():
    conn = sqlite3.connect(':memory:')
    assert generacje.wczytaj_generacje(conn, str.lower) == {}
    go.zainicjalizuj_tabele(conn)
    go.zapisz_model(conn, 'BMW', 'Seria 3', [('F30', 2012, 2020), ('G20', 2019, 9999)])
    assert generacje.wczytaj_generacje(conn, str.lower) == {('bmw', 'seria 3'): [('G20', 2019, 9999), ('F30', 2012, 2020)]}


def test_csv_ma_pierwszenstwo_przed_otomoto(tmp_path):
    plik = tmp_path / 'g.csv'
    plik.write_text('marka,model,generacja,rok_od,rok_do\nporsche,macan,95B,2014,2017\nporsche,macan,95B FL1,2018,2020\nporsche,macan,PPE,2024,\n')
    conn = sqlite3.connect(':memory:')
    go.zainicjalizuj_tabele(conn)
    go.zapisz_model(conn, 'Porsche', 'Macan', [('I', 2014, 9999)])
    go.zapisz_model(conn, 'BMW', 'Seria 3', [('F30', 2012, 2020)])
    s = generacje.wczytaj_generacje(conn, str.lower, str(plik))
    assert generacje.generacja('porsche', 'macan', 2019, s) == '95B FL1 (2018-2020)'
    assert generacje.generacja('porsche', 'macan', 2025, s) == 'PPE (2024-)'
    assert generacje.generacja('porsche', 'macan', 2022, s) == generacje.BRAK_GENERACJI   # CSV zastępuje Otomoto w całości
    assert generacje.generacja('bmw', 'seria 3', 2014, s) == 'F30 (2012-2020)'            # model spoza CSV z Otomoto


def test_brak_pliku_csv():
    assert generacje.wczytaj_csv('/nie/ma/takiego.csv', str.lower) == {}
    assert generacje.wczytaj_csv(None, str.lower) == {}
