import sqlite3

import scraper_bidfax as bf

HTML = """
<div class="thumbnail offer">
  <span class="prices">$12,500</span>
  <div class="caption">
    <a href="https://en.bidfax.info/ford-fusion-1.html"><h2>Ford Fusion SE 2015 vin: 3FA6P0H7XFR123456</h2></a>
    <p class="short-storyup"><span class="copart"></span><span class="blackfont">TX - Dallas</span></p>
    <p>Lot number: <span class="blackfont">12345678</span></p>
    <p>Condition: <span class="blackfont">run and drive</span></p>
    <p>Damage: <span class="blackfont">Front End</span></p>
    <p>Mileage: <span class="blackfont">80000 mi</span></p>
    <p>Date of sale: <span class="blackfont">2026-01-15</span></p>
  </div>
</div>
<div class="thumbnail offer"><div class="caption"><a href="x"><h2>No price</h2></a></div></div>
"""


class TestWyciagnijCene:
    def test_formats(self):
        assert bf.wyciagnij_cene("12,500") == 12500
        assert bf.wyciagnij_cene("$12 500") == 12500

    def test_missing_is_none_not_zero(self):
        assert bf.wyciagnij_cene("") is None
        assert bf.wyciagnij_cene(None) is None
        assert bf.wyciagnij_cene("n/a") is None


class TestWyciagnijModel:
    def test_strips_make(self):
        assert bf.wyciagnij_model("Ford Fusion SE 2015", "Ford", 2015) == "Fusion SE"

    def test_hyphenated_make(self):
        assert bf.wyciagnij_model("Mercedes-Benz E350 2014", "Mercedes-Benz", 2014) == "E350"
        assert bf.wyciagnij_model("Mercedes Benz E350 2014", "Mercedes-Benz", 2014) == "E350"

    def test_no_year(self):
        assert bf.wyciagnij_model("Ford Fusion", "Ford", None) == "Brak"


class TestWyciagnijDaneZHtml:
    def test_parses_full_offer(self):
        cars = bf.wyciagnij_dane_z_html(HTML, "Ford")
        assert len(cars) == 1  # druga oferta nie ma ceny, więc jest pomijana
        car = cars[0]
        assert car["Cena_USD"] == 12500
        assert car["Rocznik"] == 2015
        assert car["Model"] == "Fusion SE"
        assert car["VIN"] == "3FA6P0H7XFR123456"
        assert car["Aukcja"] == "Copart"
        assert car["Lokalizacja"] == "TX - Dallas"
        assert car["Lot_Number"] == "12345678"
        assert car["Condition"] == "Run And Drive"
        assert car["Damage"] == "Front End"
        assert car["Date_Of_Sale"] == "2026-01-15"

    def test_year_not_taken_from_vin(self):
        html = HTML.replace("Ford Fusion SE 2015 vin: 3FA6P0H7XFR123456", "Ford Fusion SE vin: 1FA2019XXXX123456")
        assert bf.wyciagnij_dane_z_html(html, "Ford")[0]["Rocznik"] is None

    def test_offer_without_price_tag_is_skipped(self):
        html = '<div class="thumbnail offer"><div class="caption"></div></div>'
        assert bf.wyciagnij_dane_z_html(html, "Ford") == []


class TestBaza:
    def make_car(self, **over):
        car = {"Tytul": "t", "Marka": "Ford", "Model": "Fusion", "Rocznik": 2015, "VIN": "V", "Cena_USD": 1000,
               "Aukcja": "Copart", "Lokalizacja": "TX", "Lot_Number": "1", "Condition": "Run", "Damage": "Front",
               "Mileage": "1", "Date_Of_Sale": "2026-01-01", "Link": "http://a"}
        car.update(over)
        return car

    def test_upsert_updates_changed_price(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bf, "DB_NAME", str(tmp_path / "t.db"))
        conn = bf.zainicjalizuj_baze()
        bf.wstaw_ogloszenia_hurtowo(conn, [self.make_car()])
        bf.wstaw_ogloszenia_hurtowo(conn, [self.make_car(Cena_USD=2000)])
        rows = conn.execute("SELECT Cena_USD FROM bidfax_scd2").fetchall()
        assert rows == [(2000,)]

    def test_init_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bf, "DB_NAME", str(tmp_path / "t.db"))
        bf.zainicjalizuj_baze().close()
        bf.zainicjalizuj_baze().close()

    def test_usun_stare_rekordy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bf, "DB_NAME", str(tmp_path / "t.db"))
        conn = bf.zainicjalizuj_baze()
        bf.wstaw_ogloszenia_hurtowo(conn, [
            self.make_car(Link="old", Date_Of_Sale="2000-01-01"),
            self.make_car(Link="brak", Date_Of_Sale="Brak"),
        ])
        bf.usun_stare_rekordy(conn)
        links = [r[0] for r in conn.execute("SELECT Link FROM bidfax_scd2")]
        assert links == ["brak"]
