import sqlite3

import pandas as pd
import pytest

import trendy


def _aukcje(wiersze):
    return pd.DataFrame(wiersze, columns=['Model', 'Date_Of_Sale', 'Cena_USD'])


class TestLiczTrendy:
    def test_spadek_ceny_i_wzrost_podazy(self):
        df = _aukcje([
            ('a', '2026-01-10', 10000), ('a', '2026-01-20', 10000),
            ('a', '2026-05-01', 8000), ('a', '2026-05-10', 8000), ('a', '2026-05-20', 8000), ('a', '2026-06-01', 8000),
        ])
        w = trendy.licz_trendy(df, ['Model'], okno_dni=120, min_w_oknie=2).iloc[0]
        assert w['Trend_Ceny_USA_%'] == pytest.approx(-20)
        assert w['Trend_Podazy_USA_%'] == 100

    def test_za_malo_aukcji_w_oknie_daje_nan(self):
        df = _aukcje([('a', '2026-01-10', 10000), ('a', '2026-05-01', 8000), ('a', '2026-05-10', 8000)])
        w = trendy.licz_trendy(df, ['Model'], okno_dni=120, min_w_oknie=2).iloc[0]
        assert pd.isna(w['Trend_Ceny_USA_%']) and pd.isna(w['Trend_Podazy_USA_%'])
        assert w['Aukcje_Ostatnie'] == 2 and w['Aukcje_Poprzednie'] == 1

    def test_puste_dane(self):
        assert trendy.licz_trendy(_aukcje([]), ['Model']).empty


class TestSnapshot:
    def test_ponowny_przebieg_tego_samego_dnia_nie_dubluje(self):
        conn = sqlite3.connect(':memory:')
        dane = pd.DataFrame({'Marka': ['x'], 'Budzet': [1.0]})
        trendy.zapisz_snapshot(conn, 'h', dane, '2026-10-13')
        trendy.zapisz_snapshot(conn, 'h', dane, '2026-10-13')
        trendy.zapisz_snapshot(conn, 'h', dane, '2026-10-14')
        assert conn.execute('SELECT count(*) FROM h').fetchone()[0] == 2

    def test_nowa_kolumna_jest_dodawana(self):
        conn = sqlite3.connect(':memory:')
        trendy.zapisz_snapshot(conn, 'h', pd.DataFrame({'Marka': ['x']}), '2026-10-13')
        trendy.zapisz_snapshot(conn, 'h', pd.DataFrame({'Marka': ['x'], 'Nowa': [2.0]}), '2026-10-14')
        assert conn.execute('SELECT Nowa FROM h ORDER BY Data_Snapshotu').fetchall() == [(None,), (2.0,)]
