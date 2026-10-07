import sqlite3

import pytest

import migruj_baze as mb


def _baza(sciezka):
    c = sqlite3.connect(sciezka)
    c.executescript("""
        CREATE TABLE otomoto_scd2 (ID_Wersji INTEGER PRIMARY KEY AUTOINCREMENT, ID_Ogloszenia TEXT, Status TEXT,
            Wazne_Od TEXT, Wazne_Do TEXT, Data_Dodania TEXT, Czas_publikacji TEXT);
        CREATE TABLE bidfax_scd2 (ID INTEGER PRIMARY KEY, Marka TEXT, Model TEXT, Condition TEXT, Rocznik INTEGER, Cena_USD INTEGER);
        -- ogłoszenie 1: fałszywe zamknięcie, potem znów aktywne
        INSERT INTO otomoto_scd2 (ID_Ogloszenia, Status, Wazne_Od, Wazne_Do, Data_Dodania, Czas_publikacji) VALUES
            ('1', 'Aktywne', '2026-01-01 10:00:00', '2026-01-05 10:00:00', '2026-01-03 00:00:00', '2025-12-20T08:00:00Z'),
            ('1', 'Zakończone', '2026-01-05 10:00:00', '9999-12-31 23:59:59', '2026-01-03 00:00:00', '2025-12-20T08:00:00Z'),
            ('1', 'Aktywne', '2026-01-10 10:00:00', '9999-12-31 23:59:59', '2026-01-03 00:00:00', '2025-12-20T08:00:00Z'),
        -- ogłoszenie 2: prawdziwe zakończenie
            ('2', 'Aktywne', '2026-01-01 10:00:00', '2026-01-05 10:00:00', '2026-01-01 00:00:00', NULL),
            ('2', 'Zakończone', '2026-01-05 10:00:00', '9999-12-31 23:59:59', '2026-01-01 00:00:00', NULL);
        INSERT INTO bidfax_scd2 (Marka, Model, Condition, Rocznik, Cena_USD) VALUES
            ('mercedes-benz', 'Mercedes-Benz E350', 'Run and Drive', 0, 0),
            ('rolls-royce', 'Rolls-Royce Ghost', 'Stationary', 2015, 500),
            ('ford', 'Fusion', 'Run And Drive', 2015, 1000);
    """)
    c.commit()
    c.close()


@pytest.fixture
def baza(tmp_path):
    sciezka = str(tmp_path / "samochody.db")
    _baza(sciezka)
    return sciezka


def _q(sciezka, sql):
    with sqlite3.connect(sciezka) as c:
        return c.execute(sql).fetchall()


class TestMigruj:
    def test_fixes_data(self, baza):
        assert mb.migruj(baza) == 'ok'
        assert _q(baza, "SELECT Status FROM otomoto_scd2 WHERE ID_Ogloszenia='1' ORDER BY ID_Wersji") == [('Aktywne',), ('Aktywne',)]
        assert _q(baza, "SELECT Status FROM otomoto_scd2 WHERE ID_Ogloszenia='2' ORDER BY ID_Wersji") == [('Aktywne',), ('Zakończone',)]
        assert _q(baza, "SELECT Model, Condition, Rocznik, Cena_USD FROM bidfax_scd2 WHERE Marka='mercedes-benz'") == [('E350', 'Run And Drive', None, None)]
        assert _q(baza, "SELECT Model FROM bidfax_scd2 WHERE Marka='rolls-royce'") == [('Ghost',)]

    def test_adds_start_date_as_earliest(self, baza):
        mb.migruj(baza)
        # publikacja (2025-12-20) wcześniejsza niż Data_Dodania (2026-01-03)
        assert _q(baza, "SELECT DISTINCT Data_Startu FROM otomoto_scd2 WHERE ID_Ogloszenia='1'") == [('2025-12-20 08:00:00',)]
        assert _q(baza, "SELECT DISTINCT Data_Startu FROM otomoto_scd2 WHERE ID_Ogloszenia='2'") == [(None,)]

    def test_can_run_twice(self, baza):
        mb.migruj(baza)
        stan = _q(baza, "SELECT * FROM otomoto_scd2 ORDER BY ID_Wersji")
        assert mb.migruj(baza) == 'ok'
        assert _q(baza, "SELECT * FROM otomoto_scd2 ORDER BY ID_Wersji") == stan

    def test_failure_rolls_back(self, baza, monkeypatch, tmp_path):
        zly = tmp_path / "zly.sql"
        zly.write_text("BEGIN;\nUPDATE bidfax_scd2 SET Model = 'X';\nUPDATE nie_ma_takiej_tabeli SET a = 1;\nCOMMIT;\n")
        monkeypatch.setattr(mb, "SQL_MIGRACJI", str(zly))
        with pytest.raises(sqlite3.OperationalError):
            mb.migruj(baza)
        assert _q(baza, "SELECT Model FROM bidfax_scd2 WHERE Marka='ford'") == [('Fusion',)]


class TestKopiaISprawdzenie:
    def test_backup_is_consistent_copy(self, baza, tmp_path):
        kopia = mb.zrob_kopie(baza, str(tmp_path))
        assert "_backup_" in kopia
        assert _q(kopia, "SELECT COUNT(*) FROM otomoto_scd2") == [(5,)]

    def test_missing_file_and_missing_tables(self, tmp_path):
        assert mb.sprawdz_baze(str(tmp_path / "brak.db"))
        pusta = str(tmp_path / "pusta.db")
        sqlite3.connect(pusta).close()
        assert any("Brak tabeli" in p for p in mb.sprawdz_baze(pusta))

    def test_locked_database_is_reported(self, baza):
        blokada = sqlite3.connect(baza, isolation_level=None)
        blokada.execute("BEGIN IMMEDIATE")
        try:
            assert any("zajęta" in p for p in mb.sprawdz_baze(baza))
        finally:
            blokada.execute("ROLLBACK")
            blokada.close()

    def test_healthy_database_has_no_problems(self, baza):
        assert mb.sprawdz_baze(baza) == []


class TestMain:
    def test_dry_run_leaves_original_untouched(self, baza, capsys):
        przed = _q(baza, "SELECT * FROM bidfax_scd2")
        assert mb.main(["--db", baza, "--dry-run"]) == 0
        assert _q(baza, "SELECT * FROM bidfax_scd2") == przed
        assert "integrity_check: ok" in capsys.readouterr().out

    def test_full_run_makes_backup_and_migrates(self, baza, tmp_path, capsys):
        assert mb.main(["--db", baza, "--tak"]) == 0
        assert list(tmp_path.glob("samochody_backup_*.db"))
        assert _q(baza, "SELECT Model FROM bidfax_scd2 WHERE Marka='rolls-royce'") == [('Ghost',)]

    def test_declined_confirmation_changes_nothing(self, baza, monkeypatch, tmp_path):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert mb.main(["--db", baza]) == 1
        assert not list(tmp_path.glob("samochody_backup_*.db"))
        assert _q(baza, "SELECT Model FROM bidfax_scd2 WHERE Marka='rolls-royce'") == [('Rolls-Royce Ghost',)]
