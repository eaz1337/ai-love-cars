import json
from datetime import datetime

import pytest

import scraper_otomoto as om


class TestCleaners:
    @pytest.mark.parametrize("val,expected", [
        ("125 000 km", 125000),
        ("125 000 km", 125000),
        ("1 998 cm3", 1998),
        ("150 KM", 150),
        ("brak", None),
        ("", None),
        (None, None),
    ])
    def test_clean_int(self, val, expected):
        assert om.clean_int(val) == expected

    @pytest.mark.parametrize("val,expected", [
        ("12 500,50", 12500.5),
        ("1000", 1000.0),
        ("brak", None),
        ("abc", None),
        (None, None),
    ])
    def test_clean_float(self, val, expected):
        assert om.clean_float(val) == expected

    @pytest.mark.parametrize("val", ["Brak", "nie podano", "NULL", "None", "  ", "", None])
    def test_normalize_nulls_empty(self, val):
        assert om.normalize_nulls(val) is None

    def test_normalize_nulls_strips(self):
        assert om.normalize_nulls("  Audi ") == "Audi"


def _page(nodes):
    inner = json.dumps({"advertSearch": {"edges": [{"node": n} for n in nodes]}})
    data = {"props": {"pageProps": {"urqlState": {"k": {"data": inner}, "bad": "x"}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


class TestParseOtomotoJson:
    def test_extracts_nodes(self):
        assert om.parse_otomoto_json(_page([{"id": 1}, {"id": 2}])) == [{"id": 1}, {"id": 2}]

    def test_no_script_tag(self):
        assert om.parse_otomoto_json("<html></html>") == []

    def test_broken_json(self):
        html = '<script id="__NEXT_DATA__">{not json</script>'
        assert om.parse_otomoto_json(html) == []


def _car(**over):
    car = {
        "id": 99, "title": "Audi A4", "url": "https://otomoto.pl/99",
        "price": {"amount": {"value": "45 000"}},
        "seller": {"name": "Jan"},
        "location": {"city": {"name": "Warszawa"}, "region": {"name": "Mazowieckie"}},
        "parameters": [
            {"key": "make", "displayValue": "audi"}, {"key": "model", "displayValue": "a4"},
            {"key": "mileage", "displayValue": "125 000 km"}, {"key": "year", "displayValue": "2016"},
            {"key": "engine_capacity", "displayValue": "1 968 cm3"}, {"key": "engine_power", "displayValue": "150 KM"},
        ],
    }
    car.update(over)
    return car


class TestWyciagnijDaneOgloszenia:
    def test_full(self):
        d = om.wyciagnij_dane_ogloszenia(_car())
        assert d["ID_Ogloszenia"] == "99"
        assert d["Cena_PLN"] == 45000.0
        assert d["Przebieg_km"] == 125000
        assert d["Rok_produkcji"] == 2016
        assert d["Pojemnosc_cm3"] == 1968
        assert d["Moc_KM"] == 150
        assert d["Miejsce"] == "Warszawa (Mazowieckie)"
        assert d["Is_Outlier"] == 0

    def test_private_seller_default(self):
        assert om.wyciagnij_dane_ogloszenia(_car(seller=None))["Sprzedawca"] == "Osoba prywatna"

    @pytest.mark.parametrize("override", [
        {"price": {"amount": {"value": "500"}}},
        {"price": {"amount": {"value": "2000000"}}},
    ])
    def test_price_outlier(self, override):
        assert om.wyciagnij_dane_ogloszenia(_car(**override))["Is_Outlier"] == 1

    def test_year_outlier(self):
        params = [{"key": "year", "displayValue": "1900"}]
        assert om.wyciagnij_dane_ogloszenia(_car(parameters=params))["Is_Outlier"] == 1

    def test_future_year_limit(self):
        params = [{"key": "year", "displayValue": str(datetime.now().year + 1)}]
        assert om.wyciagnij_dane_ogloszenia(_car(parameters=params))["Is_Outlier"] == 0

    def test_missing_nodes_do_not_crash(self):
        d = om.wyciagnij_dane_ogloszenia({"id": 1})
        assert d["Cena_PLN"] is None
        assert d["Miejsce"] is None


class TestKluczKlona:
    def base(self, **over):
        car = {"Tytul": "A", "Rok_produkcji": 2016, "Przebieg_km": 1000, "Sprzedawca": "s", "Paliwo": "p",
               "Skrzynia_biegow": "g", "Miasto": "m", "Wojewodztwo": "w"}
        car.update(over)
        return car

    def test_same_car_same_key(self):
        assert om.klucz_klona(self.base()) == om.klucz_klona(self.base())

    def test_different_mileage_different_key(self):
        assert om.klucz_klona(self.base()) != om.klucz_klona(self.base(Przebieg_km=2000))

    @pytest.mark.parametrize("field", ["Rok_produkcji", "Przebieg_km"])
    def test_missing_year_or_mileage_gives_none(self, field):
        assert om.klucz_klona(self.base(**{field: None})) is None


class TestBaza:
    def test_init_idempotent_and_insert(self, tmp_path, monkeypatch):
        monkeypatch.setattr(om, "DB_NAME", str(tmp_path / "t.db"))
        om.zainicjalizuj_baze().close()
        conn = om.zainicjalizuj_baze()
        om.wstaw_nowy_rekord(conn.cursor(), om.wyciagnij_dane_ogloszenia(_car()))
        row = conn.execute("SELECT ID_Ogloszenia, Cena_PLN, Ilosc_Powtorzen, Detale_Pobrane FROM otomoto_scd2").fetchone()
        assert row == ("99", 45000.0, 1, 0)

    def test_migration_clears_old_error_marker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(om, "DB_NAME", str(tmp_path / "t.db"))
        conn = om.zainicjalizuj_baze()
        conn.execute("INSERT INTO otomoto_scd2 (ID_Ogloszenia, Opis, Detale_Pobrane) VALUES ('1', 'Błąd pobierania', 1)")
        conn.commit()
        conn.close()
        conn = om.zainicjalizuj_baze()
        assert conn.execute("SELECT Opis, Detale_Pobrane FROM otomoto_scd2").fetchone() == (None, 0)
