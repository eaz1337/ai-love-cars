import pandas as pd
import pytest

import plynnosc


def _oto(wiersze):
    return pd.DataFrame(wiersze, columns=['Model', 'Status', 'Data_Startu', 'Data_Ostatniego_Widzenia'])


def test_mediana_dni_i_udzial_zdjetych():
    df = _oto([
        ('a', 'Zakończone', '2026-09-01 00:00:00', '2026-09-03 00:00:00'),
        ('a', 'Zakończone', '2026-09-01 00:00:00', '2026-09-11 00:00:00'),
        ('a', 'Zakończone', '2026-09-01 00:00:00', '2026-09-06 00:00:00'),
        ('a', 'Aktywne', '2026-09-01 00:00:00', '2026-10-01 00:00:00'),
    ])
    w = plynnosc.licz_plynnosc(df, ['Model']).iloc[0]
    assert w['Ilosc_Zdjetych_Otomoto'] == 3
    assert w['Zdjete_Otomoto_%'] == pytest.approx(75)
    assert w['Mediana_Dni_Na_Rynku'] == pytest.approx(5)   # aktywne nie liczą się do mediany


def test_za_malo_zdjetych_daje_nan_mediany():
    df = _oto([('a', 'Zakończone', '2026-09-01 00:00:00', '2026-09-03 00:00:00'), ('a', 'Aktywne', '2026-09-01 00:00:00', '2026-10-01 00:00:00')])
    w = plynnosc.licz_plynnosc(df, ['Model']).iloc[0]
    assert pd.isna(w['Mediana_Dni_Na_Rynku'])
    assert w['Ilosc_Zdjetych_Otomoto'] == 1
