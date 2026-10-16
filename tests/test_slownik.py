import pandas as pd
import pytest

import slownik
import wersje
from wersje import WERSJA_PODSTAWOWA

CSV = """marka,model,wersja,slowa,zakaz_slow,moc_od,moc_do,pojemnosc_od,pojemnosc_do,rok_od,rok_do
porsche,cayenne,turbo gt,turbo gt,,625,650,3.9,4.1,,
porsche,cayenne,turbo,turbo,,480,550,,,,
porsche,cayenne,gts,gts,,405,440,,,,
porsche,cayenne,s,s,,385,420,,,,
ford,focus,st,st,line,220,280,,,,
bmw,seria 6,640i,640i,,,,,,,
bmw,seria 6,650i 408km,650i,,400,415,4.3,4.5,,2014
bmw,seria 6,650i 450km,650i,,445,455,4.3,4.5,2015,
"""


@pytest.fixture
def wpisy(tmp_path):
    plik = tmp_path / "s.csv"
    plik.write_text(CSV, encoding="utf-8")
    return slownik.wczytaj_slownik(str(plik))


class TestWczytaj:
    def test_missing_file_is_empty(self, tmp_path):
        assert slownik.wczytaj_slownik(str(tmp_path / "brak.csv")) == {}

    def test_groups_by_make_and_model(self, wpisy):
        assert set(wpisy) == {("porsche", "cayenne"), ("ford", "focus"), ("bmw", "seria 6")}
        assert len(wpisy[("porsche", "cayenne")]) == 4

    def test_missing_required_columns_raises(self, tmp_path):
        plik = tmp_path / "zly.csv"
        plik.write_text("marka,model,wersja,slowa\nporsche,cayenne,,\n", encoding="utf-8")
        with pytest.raises(ValueError):
            slownik.wczytaj_slownik(str(plik))


class TestWersjaReczna:
    def cayenne(self, wpisy):
        return wpisy[("porsche", "cayenne")]

    def test_most_specific_entry_wins(self, wpisy):
        assert slownik.wersja_reczna({"turbo", "gt"}, self.cayenne(wpisy)) == "turbo gt"
        assert slownik.wersja_reczna({"turbo"}, self.cayenne(wpisy)) == "turbo"

    def test_ignores_extra_words(self, wpisy):
        assert slownik.wersja_reczna({"gts", "platinum"}, self.cayenne(wpisy)) == "gts"

    def test_forbidden_word_blocks_entry(self, wpisy):
        ford = wpisy[("ford", "focus")]
        assert slownik.wersja_reczna({"st"}, ford) == "st"
        assert slownik.wersja_reczna({"st", "line"}, ford) == WERSJA_PODSTAWOWA

    def test_unknown_title_without_power_is_base(self, wpisy):
        assert slownik.wersja_reczna(set(), self.cayenne(wpisy)) == WERSJA_PODSTAWOWA

    def test_power_range_used_when_title_has_no_match(self, wpisy):
        assert slownik.wersja_reczna(set(), self.cayenne(wpisy), moc=640, pojemnosc=4.0) == "turbo gt"

    def test_capacity_range_must_match(self, wpisy):
        assert slownik.wersja_reczna(set(), self.cayenne(wpisy), moc=640, pojemnosc=3.0) == WERSJA_PODSTAWOWA

    def test_overlapping_power_ranges_are_ambiguous(self, wpisy):
        # 410 KM mieści się w 'gts' (405-440) i 's' (385-420)
        assert slownik.wersja_reczna(set(), self.cayenne(wpisy), moc=410) == WERSJA_PODSTAWOWA

    def test_title_beats_power(self, wpisy):
        assert slownik.wersja_reczna({"gts"}, self.cayenne(wpisy), moc=500) == "gts"

    def test_missing_power_is_ignored(self, wpisy):
        assert slownik.wersja_reczna(set(), self.cayenne(wpisy), moc=float("nan")) == WERSJA_PODSTAWOWA


class TestRocznik:
    def seria6(self, wpisy):
        return wpisy[("bmw", "seria 6")]

    def test_year_decides_without_power(self, wpisy):
        # Bidfax: tylko tytuł i rocznik
        assert slownik.wersja_reczna({"650i"}, self.seria6(wpisy), rok=2012) == "650i 408km"
        assert slownik.wersja_reczna({"650i"}, self.seria6(wpisy), rok=2016) == "650i 450km"

    def test_power_beats_year(self, wpisy):
        # Otomoto: 450 KM w aucie z 2012 to wersja 450, mimo rocznika
        assert slownik.wersja_reczna({"650i"}, self.seria6(wpisy), moc=450, rok=2012) == "650i 450km"
        assert slownik.wersja_reczna({"650i"}, self.seria6(wpisy), moc=407, rok=2016) == "650i 408km"

    def test_power_outside_every_range_is_base(self, wpisy):
        assert slownik.wersja_reczna({"650i"}, self.seria6(wpisy), moc=520, rok=2013) == WERSJA_PODSTAWOWA

    def test_missing_year_does_not_match_year_entry(self, wpisy):
        assert slownik.wersja_reczna({"650i"}, self.seria6(wpisy)) == WERSJA_PODSTAWOWA

    def test_entry_without_year_ignores_year(self, wpisy):
        assert slownik.wersja_reczna({"640i"}, self.seria6(wpisy), moc=320, rok=2012) == "640i"
        assert slownik.wersja_reczna({"640i"}, self.seria6(wpisy)) == "640i"


def test_szkic_slownika():
    otomoto = pd.DataFrame({
        "Marka": ["porsche"] * 4, "Model": ["cayenne"] * 4, "Moc_KM": [400, 410, 420, 430],
        "Tokeny": [frozenset({"gts"})] * 4,
    })
    szkic = slownik.szkic_slownika(otomoto, {("porsche", "cayenne"): frozenset({"gts"})})
    assert list(szkic["wersja"]) == ["gts"]
    assert szkic["n_ogloszen_otomoto"].iloc[0] == 4
    assert 400 <= szkic["moc_od"].iloc[0] <= szkic["moc_do"].iloc[0] <= 430


class TestSlownikRam1500:
    """Wersje Rama 1500 z analysis/slownik_wersji.csv: Classic to inna (starsza) generacja, a wersje wyposażenia mocno różnią cenę."""

    @pytest.fixture(scope="class")
    def wpisy(self):
        import os
        sciezka = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "analysis", "slownik_wersji.csv")
        return slownik.wczytaj_slownik(sciezka)[("ram", "1500")]

    @pytest.mark.parametrize("tytul,oczekiwana", [
        ("1500 Classic Tradesman 4X4 5'7 Box", "classic"),
        ("1500 Classic Slt", "classic"),
        ("1500 Tradesman 4X4 6'4 Box", "tradesman"),
        ("1500 St", "tradesman"),
        ("1500 Big Horn/Lone Star", "big horn"),
        ("RAM 1500 5.7 Crew Cab Bighorn", "big horn"),
        ("1500 Slt", "big horn"),
        ("RAM 1500 Crew Cab Sport", "big horn"),
        ("1500 Laramie 4X4 5'7 Box", "laramie"),
        ("RAM 1500 5.7 Crew Cab Longbed Laramie Sport", "laramie"),
        ("1500 Laramie Longhorn", "longhorn"),
        ("RAM 1500 Crew Cab Limited", "limited"),
        ("RAM 1500 Limited Longhorn", "longhorn"),
        ("1500 Rebel 4X4 5'7 Box", "rebel"),
        ("RAM 1500", "podstawowa"),
    ])
    def test_wersja(self, wpisy, tytul, oczekiwana):
        tokeny = wersje.tokeny_wersji(tytul, ("ram", "1500"))
        assert slownik.wersja_reczna(tokeny, wpisy) == oczekiwana
