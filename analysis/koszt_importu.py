"""Koszt sprowadzenia auta z aukcji w USA przez Rotterdam (model kalkulatora BidCars).

Wartość celna to cena auta, opłaty aukcyjne, transport lądowy do portu i fracht do Rotterdamu. Cło (10%) i VAT (21%, Holandia)
płacimy przy odprawie w Rotterdamie od wartości celnej z cłem. Akcyzę (3,1% do 2.0 l, 18,6% powyżej) płacimy w Polsce przy rejestracji.
"""
from oplaty_aukcyjne import oplata_aukcyjna_usd

FRACHT_ROTTERDAM_USD = 995
PROWIZJA_POSREDNIKA_USD = 450
VAT_OD_PROWIZJI = 0.23
STAWKA_CLA = 0.10
STAWKA_VAT_ROTTERDAM = 0.21
AGENCJA_CELNA_EUR = 500
TLUMACZENIE_DOKUMENTOW_PLN = 260
TRANSPORT_DO_PL_PLN = 2300


def licz_koszt_importu(df, usd_pln, eur_pln, punkty_oplat):
    """Dopisuje do df kolumny kosztów w PLN. Wymaga kolumn Cena_USD, Transport_Ladowy_USD i Stawka_Akcyzy."""
    cena_usd = df['Cena_USD']
    df['Oplata_Aukcyjna_USD'] = cena_usd.apply(lambda c: oplata_aukcyjna_usd(c, punkty_oplat))
    df['Cena_Zakupu_USA_PLN'] = cena_usd * usd_pln
    df['Wartosc_Celna_USD'] = cena_usd + df['Oplata_Aukcyjna_USD'] + df['Transport_Ladowy_USD'] + FRACHT_ROTTERDAM_USD
    df['Wartosc_Celna_PLN'] = df['Wartosc_Celna_USD'] * usd_pln
    df['Clo_PLN'] = df['Wartosc_Celna_PLN'] * STAWKA_CLA
    df['Wartosc_do_VAT_PLN'] = df['Wartosc_Celna_PLN'] + df['Clo_PLN']
    df['VAT_PLN'] = df['Wartosc_do_VAT_PLN'] * STAWKA_VAT_ROTTERDAM
    df['Akcyza_PLN'] = df['Wartosc_do_VAT_PLN'] * df['Stawka_Akcyzy']
    df['Koszty_Posrednika_PLN'] = PROWIZJA_POSREDNIKA_USD * usd_pln * (1 + VAT_OD_PROWIZJI) + AGENCJA_CELNA_EUR * eur_pln
    df['Koszt_Import_PLN'] = (
        df['Wartosc_Celna_PLN'] + df['Clo_PLN'] + df['VAT_PLN'] + df['Akcyza_PLN'] + df['Koszty_Posrednika_PLN']
        + TLUMACZENIE_DOKUMENTOW_PLN + TRANSPORT_DO_PL_PLN
    )
    return df
