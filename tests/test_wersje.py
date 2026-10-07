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
