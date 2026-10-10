import pandas as pd

import wersje


class TestTokenyWersji:
    def test_strips_make_model_year_capacity(self):
        t = wersje.tokeny_wersji("Porsche Cayenne Turbo GT 2017 3.6L", ("porsche", "cayenne"))
        assert t == {"turbo", "gt"}

    def test_joins_number_and_letter(self):
        assert wersje.tokeny_wersji("328 i", ()) == wersje.tokeny_wersji("328i", ())

    def test_ignores_gearbox_and_drive_words(self):
        assert wersje.tokeny_wersji("Cayenne S Tiptronic quattro", ("cayenne",)) == {"s"}

    def test_drops_short_numbers(self):
        assert wersje.tokeny_wersji("Seria 3 330d", ()) == {"seria", "330d"}


def _otomoto():
    rows = []
    for i in range(10):
        rows.append(("porsche", "cayenne", 4.8, 2012, 450, 110000 + i, frozenset({"gts"})))
        rows.append(("porsche", "cayenne", 4.8, 2012, 340, 45000 + i, frozenset({"s"})))
        # słowo, które nic nie zmienia: ta sama moc i cena co reszta
        rows.append(("porsche", "cayenne", 4.8, 2012, 395, 70000 + i, frozenset({"platinum"})))
    return pd.DataFrame(rows, columns=["Marka", "Model", "Pojemnosc_L", "Rok_produkcji", "Moc_KM", "Cena_PLN", "Tokeny"])


def _bidfax(tokeny):
    return pd.DataFrame({"Marka": ["porsche"] * len(tokeny), "Otomoto_Model": ["cayenne"] * len(tokeny), "Tokeny": tokeny})


class TestSlownik:
    def test_keeps_only_words_that_change_price_or_power(self):
        bidfax = _bidfax([frozenset({"gts"})] * 3 + [frozenset({"s"})] * 3 + [frozenset({"platinum"})] * 3)
        slownik = wersje.buduj_slownik(_otomoto(), bidfax)
        assert slownik[("porsche", "cayenne")] == {"gts", "s"}

    def test_word_missing_in_bidfax_is_dropped(self):
        slownik = wersje.buduj_slownik(_otomoto(), _bidfax([frozenset({"gts"})] * 3))
        assert slownik[("porsche", "cayenne")] == {"gts"}

    def test_rare_word_is_dropped(self):
        otomoto = _otomoto()
        otomoto = otomoto[~((otomoto["Tokeny"] == frozenset({"gts"})) & (otomoto["Cena_PLN"] > 110002))]
        slownik = wersje.buduj_slownik(otomoto, _bidfax([frozenset({"gts"})] * 3))
        assert ("porsche", "cayenne") not in slownik


class TestWersja:
    slownik = {("porsche", "cayenne"): frozenset({"gt", "turbo"})}

    def test_sorted_label(self):
        assert wersje.wersja({"turbo", "gt", "x"}, "porsche", "cayenne", self.slownik) == "gt turbo"

    def test_base_when_no_known_word(self):
        assert wersje.wersja({"x"}, "porsche", "cayenne", self.slownik) == wersje.WERSJA_PODSTAWOWA

    def test_unknown_model_is_base(self):
        assert wersje.wersja({"turbo"}, "audi", "a4", self.slownik) == wersje.WERSJA_PODSTAWOWA


class TestUzupelnijWersjeZMocy:
    def _df(self, wiersze):
        df = pd.DataFrame(wiersze, columns=["Marka", "Model", "Pojemnosc_L", "Moc_KM", "Wersja", "Paliwo"])
        df["Tokeny"] = [frozenset()] * len(df)
        return df

    def _wzorce(self):
        # trzy ogłoszenia GTS (405 KM) i trzy Turbo (520 KM), ta sama pojemność
        return [("porsche", "cayenne", 4.8, 405, "gts", "Benzyna")] * 3 + [("porsche", "cayenne", 4.8, 520, "turbo", "Benzyna")] * 3

    def test_assigns_version_from_power(self):
        df = self._df(self._wzorce() + [("porsche", "cayenne", 4.8, 520, wersje.WERSJA_PODSTAWOWA, "Benzyna")])
        assert wersje.uzupelnij_wersje_z_mocy(df).iloc[-1] == "turbo"

    def test_tolerance(self):
        ok = self._df(self._wzorce() + [("porsche", "cayenne", 4.8, 530, wersje.WERSJA_PODSTAWOWA, "Benzyna")])    # 1,9% od 520
        za_daleko = self._df(self._wzorce() + [("porsche", "cayenne", 4.8, 560, wersje.WERSJA_PODSTAWOWA, "Benzyna")])  # 7,7%
        assert wersje.uzupelnij_wersje_z_mocy(ok).iloc[-1] == "turbo"
        assert wersje.uzupelnij_wersje_z_mocy(za_daleko).iloc[-1] == wersje.WERSJA_PODSTAWOWA

    def test_ambiguous_match_stays_base(self):
        wzorce = [("porsche", "cayenne", 4.8, 400, "gts", "Benzyna")] * 3 + [("porsche", "cayenne", 4.8, 408, "s", "Benzyna")] * 3
        df = self._df(wzorce + [("porsche", "cayenne", 4.8, 404, wersje.WERSJA_PODSTAWOWA, "Benzyna")])
        assert wersje.uzupelnij_wersje_z_mocy(df).iloc[-1] == wersje.WERSJA_PODSTAWOWA

    def test_other_capacity_is_not_matched(self):
        df = self._df(self._wzorce() + [("porsche", "cayenne", 3.0, 520, wersje.WERSJA_PODSTAWOWA, "Benzyna")])
        assert wersje.uzupelnij_wersje_z_mocy(df).iloc[-1] == wersje.WERSJA_PODSTAWOWA

    def test_too_few_reference_cars(self):
        df = self._df([("porsche", "cayenne", 4.8, 520, "turbo", "Benzyna")] * 2 + [("porsche", "cayenne", 4.8, 520, wersje.WERSJA_PODSTAWOWA, "Benzyna")])
        assert wersje.uzupelnij_wersje_z_mocy(df).iloc[-1] == wersje.WERSJA_PODSTAWOWA

    def test_missing_power_or_capacity_is_skipped_and_titled_rows_unchanged(self):
        df = self._df(self._wzorce() + [("porsche", "cayenne", 4.8, None, wersje.WERSJA_PODSTAWOWA, "Benzyna"),
                                        ("porsche", "cayenne", None, 520, wersje.WERSJA_PODSTAWOWA, "Benzyna")])
        wynik = wersje.uzupelnij_wersje_z_mocy(df)
        assert list(wynik.iloc[-2:]) == [wersje.WERSJA_PODSTAWOWA] * 2
        assert list(wynik.iloc[:6]) == list(df["Wersja"].iloc[:6])

    def test_other_fuel_is_not_matched(self):
        df = self._df(self._wzorce() + [("porsche", "cayenne", 4.8, 520, wersje.WERSJA_PODSTAWOWA, "Diesel")])
        assert wersje.uzupelnij_wersje_z_mocy(df).iloc[-1] == wersje.WERSJA_PODSTAWOWA

    def test_scattered_power_reference_is_rejected(self):
        # 'st' to raz 125 KM (ST-Line), raz 250 KM (prawdziwy ST): nie jest jedną wersją, więc nie służy za wzorzec
        wzorce = [("ford", "focus", 2.0, 125, "st", "Benzyna")] * 3 + [("ford", "focus", 2.0, 250, "st", "Benzyna")] * 3
        df = self._df(wzorce + [("ford", "focus", 2.0, 125, wersje.WERSJA_PODSTAWOWA, "Benzyna")])
        assert wersje.uzupelnij_wersje_z_mocy(df).iloc[-1] == wersje.WERSJA_PODSTAWOWA

    def test_title_with_unknown_words_is_not_overwritten(self):
        df = self._df(self._wzorce() + [("porsche", "cayenne", 4.8, 520, wersje.WERSJA_PODSTAWOWA, "Benzyna")])
        df.at[df.index[-1], "Tokeny"] = frozenset({"prestige"})
        assert wersje.uzupelnij_wersje_z_mocy(df).iloc[-1] == wersje.WERSJA_PODSTAWOWA


class TestNadwozie:
    def test_gran_coupe_in_title(self):
        assert wersje.nadwozie('Bmw 650I Gran Coupe 2018 Black 4.4L') == 'gran coupe'
        assert wersje.nadwozie('BMW 640d Grancoupé') == 'gran coupe'

    def test_cabrio_variants(self):
        assert wersje.nadwozie('BMW 650i Cabrio') == 'cabrio'
        assert wersje.nadwozie('Mustang Convertible') == 'cabrio'

    def test_plain_title_is_standard(self):
        assert wersje.nadwozie('BMW Seria 6 650i xDrive') == 'standard'

    def test_description_used_when_title_silent(self):
        assert wersje.nadwozie('BMW Seria 6 650i', 'Piękne cabrio, serwis ASO') == 'cabrio'
        assert wersje.nadwozie('BMW Seria 6 650i', None) == 'standard'

    def test_title_beats_description(self):
        assert wersje.nadwozie('BMW 640d Gran Coupe', 'wymiana na cabrio') == 'gran coupe'

    def test_body_words_do_not_split_versions(self):
        assert 'gran' not in wersje.tokeny_wersji('BMW 650i Gran Coupe')
        assert 'cabrio' not in wersje.tokeny_wersji('BMW 650i Cabrio')
