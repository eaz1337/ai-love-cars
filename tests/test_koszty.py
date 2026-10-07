import csv
import os

import koszty

CSV = """klucz,port,transport_ladowy_usd,uwagi
TX,Houston (TX),350,
CA,Los Angeles (CA),300,
anaheim consolidated,Los Angeles (CA),250,
*,(nieznany),500,
"""


def _koszty(tmp_path):
    plik = tmp_path / "k.csv"
    plik.write_text(CSV, encoding="utf-8")
    return koszty.wczytaj_koszty(str(plik))


class TestStan:
    def test_copart_and_iaai_formats(self):
        assert koszty.stan_z_lokalizacji("Tx - Houston") == "TX"
        assert koszty.stan_z_lokalizacji("San Diego (CA)") == "CA"
        assert koszty.stan_z_lokalizacji("Avenel New Jersey (NJ)") == "NJ"

    def test_no_state(self):
        assert koszty.stan_z_lokalizacji("Dream Rides") is None


class TestTransport:
    def test_by_state(self, tmp_path):
        k = _koszty(tmp_path)
        assert koszty.transport_ladowy_usd("Tx - Dallas", k) == 350
        assert koszty.transport_ladowy_usd("Fremont (CA)", k) == 300

    def test_exact_location_beats_state(self, tmp_path):
        assert koszty.transport_ladowy_usd("Anaheim Consolidated", _koszty(tmp_path)) == 250

    def test_unknown_uses_default(self, tmp_path):
        k = _koszty(tmp_path)
        assert koszty.transport_ladowy_usd("Dream Rides", k) == 500
        assert koszty.transport_ladowy_usd("Zz - Nowhere", k) == 500

    def test_no_file_means_zero(self, tmp_path):
        assert koszty.wczytaj_koszty(str(tmp_path / "brak.csv")) == {}
        assert koszty.transport_ladowy_usd("Tx - Dallas", {}) == 0.0


def test_shipped_file_is_complete():
    """Plik z repo: każdy wiersz ma kwotę, jest wartość domyślna, a kwoty są sensowne."""
    sciezka = os.path.join(os.path.dirname(__file__), "..", "analysis", "koszty_transportu.csv")
    with open(sciezka, newline="", encoding="utf-8") as f:
        wiersze = list(csv.DictReader(f))
    assert any(w["klucz"] == "*" for w in wiersze)
    assert len({w["klucz"] for w in wiersze}) == len(wiersze)
    assert all(50 <= float(w["transport_ladowy_usd"]) <= 5000 for w in wiersze)
