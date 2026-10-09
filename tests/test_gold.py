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


class TestUdzialNaprawy:
    @pytest.mark.parametrize("damage,expected", [
        ("Burn", 0.40),
        ("FLOOD", 0.35),
        ("Front End", 0.20),
        ("Rear End", 0.12),
        ("Minor Dent/Scratches", 0.05),
    ])
    def test_known_damage(self, damage, expected):
        assert gold.udzial_naprawy(damage) == expected

    def test_first_matching_pattern_wins(self):
        # 'front and rear' (0.30) stoi przed samym 'front' (0.20)
        assert gold.udzial_naprawy("Front and Rear") == 0.30

    @pytest.mark.parametrize("damage", ["Brak", None, "something odd"])
    def test_unknown_uses_default(self, damage):
        assert gold.udzial_naprawy(damage) == gold.DOMYSLNY_UDZIAL_NAPRAWY


class TestCapacity:
    def test_bidfax(self):
        assert gold.extract_capacity_bidfax("2015 Ford Fusion 2.5L vin: X") == 2.5
        assert gold.extract_capacity_bidfax("2015 Ford Fusion") is None

    def test_otomoto(self):
        assert gold.extract_capacity_otomoto(1998) == 2.0
        assert gold.extract_capacity_otomoto(1598) == 1.6
        assert gold.extract_capacity_otomoto(float("nan")) is None
        assert gold.extract_capacity_otomoto("abc") is None


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
