import pytest

import gold
import pandas as pd


class TestNormalizujMarke:
    @pytest.mark.parametrize("marka,expected", [
        ("Land Rover", "land-rover"),
        ("land-rover", "land-rover"),
        ("Alfa Romeo", "alfa-romeo"),
        ("Aston Martin", "aston-martin"),
        ("Mercedes-Benz", "mercedes-benz"),
        (" BMW ", "bmw"),
        ("mclaren-automotive", "mclaren"),
        ("McLaren", "mclaren"),
        ("lucid-motors", "lucid"),
    ])
    def test_obie_strony_daja_ta_sama_postac(self, marka, expected):
        assert gold.normalizuj_marke(marka) == expected


class TestIsElectric:
    @pytest.mark.parametrize("title,marka", [
        ("Tesla Model 3 Long Range 2022 White", "tesla"),
        ("Audi E-Tron GT Prestige Quattro 2024 Gray", "audi"),
        ("Hyundai Ioniq 5 SEL 2022 Blue", "hyundai"),
    ])
    def test_bez_pojemnosci_elektryczne(self, title, marka):
        assert gold.is_electric(title, marka, None) is True

    def test_pojemnosc_wyklucza_elektryczne(self):
        # A3 E-Tron 1.4L to hybryda plug-in z silnikiem spalinowym
        assert gold.is_electric("Audi A3 E-Tron Prestige 2018 Black 1.4L", "audi", 1.4) is False

    def test_spalinowe_bez_pojemnosci_nie_sa_elektryczne(self):
        assert gold.is_electric("Ford Mustang GT 2020 Red", "ford", float("nan")) is False


class TestMapModelDynamic:
    def test_alias_us_to_eu(self):
        assert gold.map_model_dynamic("Ford", "Fusion SE", None) == "mondeo"

    def test_longest_alias_wins(self):
        assert gold.map_model_dynamic("Nissan", "Rogue Sport SV", None) == "qashqai"
        assert gold.map_model_dynamic("Nissan", "Rogue SV", None) == "x-trail"

    @pytest.mark.parametrize("model, expected", [
        ("Mdl 3", "model 3"),
        ("Model  Y", "model y"),
        ("Mdl Y 4D Std", "model y"),
        ("Model S 100D/75D/P100D", "model s"),
        ("Model X Plaid Tri Motor All-Wheel Drive", "model x"),
    ])
    def test_tesla_skroty_i_podwojne_spacje(self, model, expected):
        assert gold.map_model_dynamic("Tesla", model, {"model 3", "model y"}) == expected

    @pytest.mark.parametrize("title,expected", [
        ("328 i", "seria 3"),
        ("530i", "seria 5"),
        ("M550i xDrive", "seria 5"),
        ("X5 xDrive35i", "x5"),
        ("i3", "i3"),
    ])
    def test_bmw(self, title, expected):
        assert gold.map_model_dynamic("BMW", title, None) == expected

    def test_mercedes_class(self):
        assert gold.map_model_dynamic("Mercedes-Benz", "E350 4Matic", None) == "klasa e"

    def test_otomoto_model_prefix_match(self):
        assert gold.map_model_dynamic("Lexus", "RX350", {"rx", "nx"}) == "rx"

    def test_otomoto_does_not_match_inside_word(self):
        assert gold.map_model_dynamic("Tesla", "EX5", {"x5"}) == "ex5"

    def test_single_letter_model_only_as_whole_word(self):
        assert gold.map_model_dynamic("Kia", "Escape", {"e"}) == "escape"
        assert gold.map_model_dynamic("Kia", "E class", {"e"}) == "e"

    def test_fallback_first_word_and_unknown(self):
        assert gold.map_model_dynamic("Audi", "Q3 Premium", set()) == "q3"
        assert gold.map_model_dynamic("Audi", "", None) == "nieznany"


class TestCapacity:
    def test_bidfax(self):
        assert gold.extract_capacity_bidfax("2015 Ford Fusion 2.5L vin: X") == 2.5
        assert gold.extract_capacity_bidfax("2015 Ford Fusion") is None

    def test_otomoto(self):
        assert gold.extract_capacity_otomoto(1998) == 2.0
        assert gold.extract_capacity_otomoto(1598) == 1.6
        assert gold.extract_capacity_otomoto(float("nan")) is None
        assert gold.extract_capacity_otomoto("abc") is None

    def test_popraw_pojemnosc_z_tekstu(self):
        f = gold.popraw_pojemnosc_z_tekstu
        assert f(1.0, 'Ford EcoSport', 'Titanium 2.0 Silnik: 1 998cm3! Moc: 140 KM') == 2.0
        assert f(1.0, 'Ford EcoSport', 'silnik 2,0 Ti-VCT 4x4') == 2.0
        assert f(1.0, 'Ford EcoSport', 'silnik 1.0 EcoBoost, 125 KM, cena 45.000 zł') == 1.0
        assert f(1.0, 'Ford EcoSport', 'rodzaje 1.0 i 2.0 do wyboru') == 1.0
        assert f(2.0, 'Ford EcoSport', 'przebieg 83.5 tys. km, 2.0') == 2.0
        assert f(None, 'Ford', 'brak danych') is None
        assert f(None, 'Ford', '1.6 benzyna') == 1.6


class TestIsHybrid:
    @pytest.mark.parametrize("title", ["Toyota Camry Hybrid", "Kia Niro PHEV", "BMW 330e MHEV"])
    def test_keywords(self, title):
        assert gold.is_hybrid(title, "x", "y")

    def test_plain_car(self):
        assert not gold.is_hybrid("Toyota Camry 2.5L", "toyota", "camry")

    def test_audi_q8_is_hybrid_unless_etron(self):
        assert gold.is_hybrid("Audi Q8 3.0L", "audi", "Q8")
        assert not gold.is_hybrid("Audi Q8 e-tron", "audi", "Q8")


class TestOdfiltrujCenyTechniczne:
    def test_drops_placeholders_and_missing(self):
        df = pd.DataFrame({"Cena_USD": [1, 2, 25, 99, 100, 5000, None]})
        assert list(gold.odfiltruj_ceny_techniczne(df)["Cena_USD"]) == [100, 5000]

    def test_custom_threshold(self):
        df = pd.DataFrame({"Cena_USD": [50, 500]})
        assert list(gold.odfiltruj_ceny_techniczne(df, min_cena=10)["Cena_USD"]) == [50, 500]


class TestOdfiltrujUszkodzeniaWykluczone:
    def test_drops_burn_flood_water_biohazard_variants(self):
        damage = ["Burn", "Engine Burn", "Total Burn", "Flood", "Water/Flood", "Fresh Water",
                  "Salt Water", "Biohazard/Chemical", "Front End", "Normal Wear and Tear", None]
        df = pd.DataFrame({"Damage": damage})
        assert list(gold.odfiltruj_uszkodzenia_wykluczone(df)["Damage"].fillna("")) == ["Front End", "Normal Wear and Tear", ""]


class TestFiltrujSprzedawce:
    def _df(self):
        return pd.DataFrame({"Sprzedawca": ["Osoba prywatna", "Firma", "Osoba prywatna"], "Cena_PLN": [1, 2, 3]})

    def test_private_only(self):
        assert list(gold.filtruj_sprzedawce(self._df(), "Osoba prywatna")["Cena_PLN"]) == [1, 3]

    def test_firma_only(self):
        assert list(gold.filtruj_sprzedawce(self._df(), "Firma")["Cena_PLN"]) == [2]

    def test_wszyscy_keeps_all(self):
        assert len(gold.filtruj_sprzedawce(self._df(), "wszyscy")) == 3

    def test_missing_column_keeps_all(self):
        assert len(gold.filtruj_sprzedawce(self._df().drop(columns="Sprzedawca"), "Firma")) == 3


class TestUsunDuplikatyOgloszen:
    def _df(self, **zmiany):
        baza = {"Marka": "ram", "Model": "1500", "Tytul": "RAM 1500 Rebel", "Rok_produkcji": 2023, "Cena_PLN": 226600,
                "Przebieg_km": 63000, "Paliwo": "Benzyna", "Skrzynia_biegow": "Automatyczna", "Miasto": "Warszawa",
                "Nazwa_Sprzedawcy": "Auto sp. z o.o."}
        return pd.DataFrame([{**baza, "ID_Ogloszenia": "a"}, {**baza, **zmiany, "ID_Ogloszenia": "b"}])

    def test_same_car_under_two_ids_counts_once_and_keeps_latest(self):
        wynik = gold.usun_duplikaty_ogloszen(self._df())
        assert list(wynik["ID_Ogloszenia"]) == ["b"]

    @pytest.mark.parametrize("zmiana", [{"Cena_PLN": 219000}, {"Tytul": "RAM 1500 Laramie"}, {"Przebieg_km": 64500}, {"Przebieg_km": 66000}])
    def test_changed_price_title_or_slightly_higher_mileage_is_still_the_same_car(self, zmiana):
        assert list(gold.usun_duplikaty_ogloszen(self._df(**zmiana))["ID_Ogloszenia"]) == ["b"]

    @pytest.mark.parametrize("zmiana", [
        {"Przebieg_km": 70000}, {"Nazwa_Sprzedawcy": "Inna firma"}, {"Rok_produkcji": 2022}, {"Paliwo": "Diesel"},
        {"Skrzynia_biegow": "Manualna"}, {"Miasto": "Kraków"}])
    def test_different_mileage_seller_year_fuel_gearbox_or_city_is_a_different_car(self, zmiana):
        assert len(gold.usun_duplikaty_ogloszen(self._df(**zmiana))) == 2

    def test_missing_seller_name_still_dedupes(self):
        assert len(gold.usun_duplikaty_ogloszen(self._df(Nazwa_Sprzedawcy=None).assign(Nazwa_Sprzedawcy=None))) == 1

    def test_chain_of_small_mileage_steps_is_one_car(self):
        df = pd.concat([self._df(), self._df(Przebieg_km=65500).iloc[[1]].assign(ID_Ogloszenia="c"),
                        self._df(Przebieg_km=68000).iloc[[1]].assign(ID_Ogloszenia="d")], ignore_index=True)
        assert list(gold.usun_duplikaty_ogloszen(df)["ID_Ogloszenia"]) == ["d"]

    def test_dealers_get_no_mileage_tolerance(self):
        assert len(gold.usun_duplikaty_ogloszen(self._df(Przebieg_km=64500).assign(Sprzedawca="Firma"))) == 2

    def test_dealers_with_identical_mileage_still_dedupe(self):
        assert len(gold.usun_duplikaty_ogloszen(self._df().assign(Sprzedawca="Firma"))) == 1

    def test_private_sellers_get_mileage_tolerance(self):
        assert len(gold.usun_duplikaty_ogloszen(self._df(Przebieg_km=64500).assign(Sprzedawca="Osoba prywatna"))) == 1

    def test_missing_mileage_is_never_merged(self):
        df = self._df(Przebieg_km=None).assign(Przebieg_km=None)
        assert len(gold.usun_duplikaty_ogloszen(df)) == 2


class TestOznaczNiepewneWersje:
    def test_flags_only_basic_version_with_wide_price_spread(self):
        grupy = pd.DataFrame({"Wersja": ["podstawowa", "podstawowa", "laramie", "podstawowa"]})
        rozrzut = pd.Series([45.0, 10.0, 80.0, None])
        assert list(gold.oznacz_niepewne_wersje(grupy, rozrzut)) == [1, 0, 0, 0]


class TestOznaczPodejrzaneRoczniki:
    KLUCZ = dict(Marka="bmw", Model="seria 3", Pojemnosc_L=2.0, Wersja="podstawowa", Nadwozie="standard")

    def _otomoto(self, ceny, ilosc=5):
        return pd.DataFrame([dict(self.KLUCZ, Rok_produkcji=r, Mediana_Cena_USA_Kanada_Otomoto_PLN=c, Ilosc_USA_Kanada_Otomoto=ilosc) for r, c in ceny.items()])

    def _grupy(self, roczniki, ceny):
        return pd.DataFrame([dict(self.KLUCZ, Rocznik=r, Mediana_Cena_USA_Kanada_Otomoto_PLN=ceny[r]) for r in roczniki])

    def test_flags_boundary_year_with_price_jump_to_neighbor(self):
        ceny = {2018: 100_000, 2019: 130_000, 2020: 135_000}
        grupy = self._grupy([2019], ceny)
        assert list(gold.oznacz_podejrzane_roczniki(grupy, self._otomoto(ceny), pd.Series([1]))) == [1]

    def test_boundary_year_with_stable_prices_is_not_flagged(self):
        ceny = {2018: 100_000, 2019: 105_000, 2020: 108_000}
        grupy = self._grupy([2019], ceny)
        assert list(gold.oznacz_podejrzane_roczniki(grupy, self._otomoto(ceny), pd.Series([1]))) == [0]

    def test_non_boundary_year_is_never_flagged(self):
        ceny = {2018: 100_000, 2019: 130_000, 2020: 135_000}
        grupy = self._grupy([2019], ceny)
        assert list(gold.oznacz_podejrzane_roczniki(grupy, self._otomoto(ceny), pd.Series([0]))) == [0]

    def test_neighbor_with_too_few_listings_is_ignored(self):
        ceny = {2018: 100_000, 2019: 130_000}
        grupy = self._grupy([2019], ceny)
        assert list(gold.oznacz_podejrzane_roczniki(grupy, self._otomoto(ceny, ilosc=1), pd.Series([1]))) == [0]

    def test_no_neighbors_means_no_flag(self):
        ceny = {2019: 130_000}
        grupy = self._grupy([2019], ceny)
        assert list(gold.oznacz_podejrzane_roczniki(grupy, self._otomoto(ceny), pd.Series([1]))) == [0]


class TestOznaczWiarygodnosc:
    def test_levels_from_both_counts(self):
        aukcje = pd.Series([10, 12, 5, 9, 3, 50, 4])
        ogloszenia = pd.Series([5, 4, 3, 5, 3, 2, 30])
        assert list(gold.oznacz_wiarygodnosc(aukcje, ogloszenia)) == ["wysoka", "srednia", "srednia", "srednia", "niska", "niska", "niska"]

    def test_rare_exotic_is_kept_as_low_not_dropped(self):
        wynik = gold.oznacz_wiarygodnosc(pd.Series([3]), pd.Series([3]))
        assert list(wynik) == ["niska"]
