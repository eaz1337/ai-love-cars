# Migracja bazy `samochody.db` po poprawkach scraperów i `gold.py`

Ten plik opisuje, co zrobić z już zebranymi danymi (ok. miesiąc scrapowania), żeby pasowały do nowej wersji kodu.

Warstwy danych w bazie:
- **silver**: tabele `bidfax_scd2` i `otomoto_scd2`, wypełniane przez scrapery. **Tylko je migrujemy** (kroki 1-3).
- **gold**: tabela `analiza_rentownosci_usa`, liczona przez `analysis/gold.py`. Nie migrujesz jej, tylko przeliczasz od nowa
  (krok 4). Każde uruchomienie kasuje ją i tworzy na nowo.
Liczby podane w nawiasach pochodzą z losowej próbki 20 000 + 20 000 wierszy. Na pełnej bazie będą większe.

Struktura tabel silver **nie jest psuta**: nowy kod tylko dodaje kolumnę `Proby_Detali` i indeks `idx_wazne_status`
w `otomoto_scd2` (robi to sam przy pierwszym uruchomieniu). Stare wersje skryptów nadal by działały na takiej bazie.
Migracja dodaje od siebie jedną kolumnę, `Data_Startu` (scrapery jej nie używają, służy do analiz).

## 0. Przygotowanie

1. Zatrzymaj `scrapers/scraper_bidfax.py` i `scrapers/scraper_otomoto.py` (nie mogą pisać do bazy w trakcie migracji).
   W Dockerze: `docker compose stop`.
2. Zaktualizuj kod na komputerze, na którym leży baza (w Dockerze dodatkowo przebuduj obraz):
   ```bash
   cd ai-love-cars && git pull
   docker compose build   # tylko w Dockerze
   ```
   Skrypty trafiły do folderów `scrapers/`, `analysis/` i `tools/`, więc stare polecenia `python scraper_otomoto.py` już nie zadziałają.
   Przy Dockerze baza leży w `./data/samochody.db`, a polecenia `sqlite3` poniżej uruchamiaj na tym pliku.
3. Kopię zapasową zrobi skrypt z kroku 2. Jeśli migrujesz ręcznie, zrób spójną kopię sam (baza działa w trybie WAL, samo `cp` w trakcie pracy skryptu może dać niekompletny plik):
   ```bash
   sqlite3 samochody.db ".backup samochody_backup_$(date +%F).db"
   ```
   Jeśli nie masz `sqlite3`, zatrzymaj skrypty i skopiuj razem `samochody.db`, `samochody.db-wal` i `samochody.db-shm`.

## 1. Diagnostyka (nic nie zmienia)

Uruchom i zapisz wyniki, żeby po migracji porównać:

```sql
-- Otomoto: ogłoszenia z końcówką 'Zakończone' i późniejszym wierszem 'Aktywne' (fałszywe zamknięcia, 775 w próbce)
SELECT COUNT(DISTINCT t.ID_Ogloszenia)
FROM otomoto_scd2 t
JOIN otomoto_scd2 a ON a.ID_Ogloszenia = t.ID_Ogloszenia AND a.Status = 'Aktywne' AND a.Wazne_Od > t.Wazne_Od
WHERE t.Status = 'Zakończone';

-- Otomoto: wiersze z Data_Dodania późniejszą o >3 dni niż publikacja (13 613 w próbce)
SELECT COUNT(*) FROM otomoto_scd2
WHERE julianday(Data_Dodania) - julianday(REPLACE(SUBSTR(Czas_publikacji, 1, 19), 'T', ' ')) > 3;

-- Otomoto: podejrzane klony (23 w próbce)
SELECT COUNT(*) FROM otomoto_scd2 WHERE Ilosc_Powtorzen >= 5;

-- Bidfax: model z nazwą marki (755 Mercedes-Benz i 53 Rolls-Royce w próbce)
SELECT Marka, COUNT(*) FROM bidfax_scd2
WHERE (Marka = 'mercedes-benz' AND Model LIKE 'Mercedes-Benz %')
   OR (Marka = 'rolls-royce' AND Model LIKE 'Rolls-Royce %')
GROUP BY Marka;

-- Bidfax: stare zera zamiast braku danych (w próbce 0)
SELECT SUM(Rocznik = 0), SUM(Cena_USD = 0) FROM bidfax_scd2;
```

## 2. Migracja

Najprościej jednym poleceniem (kopia zapasowa, migracja i kontrola w jednym kroku), z katalogu głównego repo:

```bash
python tools/migruj_baze.py --dry-run     # najpierw próba na kopii, oryginał zostaje nietknięty
python tools/migruj_baze.py               # prawdziwa migracja (pyta o potwierdzenie, --tak pomija pytanie)
```

W Dockerze: `docker compose run --rm gold python /app/tools/migruj_baze.py` (baza z `./data`).
Opcje: `--db ścieżka` (domyślnie `samochody.db` albo zmienna `SAMOCHODY_DB`), `--vacuum` (zmniejsza plik po migracji, wolniejsze).

Skrypt:
- odmawia pracy, gdy baza jest zajęta (scrapery działają), brakuje tabel albo miejsca na dysku (potrzeba ok. 2,5x rozmiaru bazy),
- robi spójną kopię `samochody_backup_RRRR-MM-DD_GGMMSS.db` obok bazy (działa też w trybie WAL) i sprawdza jej integralność,
- pokazuje diagnostykę przed i po (odpowiednik kroku 1),
- wykonuje `tools/migracja.sql` w jednej transakcji: albo przejdzie całość, albo nic się nie zmieni,
- sprawdza `integrity_check` i podaje, jak przywrócić kopię.

Można go uruchamiać wielokrotnie: kolejne przebiegi nic nie zmieniają, ale każdy robi nową kopię zapasową, więc usuwaj stare.
Na bazie z ok. 275 tys. wierszy Otomoto i 104 tys. Bidfax (680 MB) trwa to kilkanaście sekund.

Co robi `tools/migracja.sql` (pełny SQL jest w tym pliku, można go też uruchomić ręcznie: `sqlite3 -bail samochody.db < tools/migracja.sql`;
flaga `-bail` zatrzymuje skrypt na pierwszym błędzie, a otwarta transakcja jest wtedy wycofywana):
- zakłada indeksy `idx_aktualne` i `idx_wazne_status` (bez nich usuwanie fałszywych zamknięć jest bardzo wolne),
- usuwa z `Model` w Bidfax nazwę marki dla Mercedes-Benz i Rolls-Royce,
- ujednolica `Run and Drive` do `Run And Drive`,
- zamienia zera w `Rocznik` i `Cena_USD` na `NULL`,
- usuwa fałszywe zakończenia w Otomoto (zamknięte przez niepełny skan, a potem znów aktywne; prawdziwe zakończenia zostają),
- dodaje kolumnę `Data_Startu` (wcześniejsza z `Data_Dodania` i `Czas_publikacji`).

Uwagi:
- Przy ręcznym uruchomieniu SQL-a `ALTER TABLE ... ADD COLUMN Data_Startu` zgłosi błąd `duplicate column name` przy drugim razie (z `-bail` nic się wtedy nie zmieni). Skrypt `migruj_baze.py` sam pomija tę linię, gdy kolumna już jest.
- Po usunięciu wierszy plik się nie zmniejsza sam. Użyj `--vacuum` albo ręcznie `VACUUM;`.
- **Nie kasuj** wierszy z `Ilosc_Powtorzen >= 5`. To prawdopodobnie różne auta zlane w jedno przez stary kod. Historii ID i cen nie da się odtworzyć, więc takie wiersze po prostu wyłączaj z analiz (patrz niżej).

## 3. Po migracji

1. Powtórz zapytania z kroku 1. Liczba fałszywych zamknięć i wierszy z marką w modelu powinna spaść do 0. Zapytania o podejrzane klony (`Ilosc_Powtorzen >= 5`) i o `Data_Dodania` nie zmienią się, bo tych kolumn migracja nie rusza. Poprawna data startu jest w nowej kolumnie `Data_Startu`.
2. Sprawdź integralność:
   ```sql
   PRAGMA integrity_check;
   ```
3. Uruchom scrapery jak zwykle (`python scrapers/scraper_bidfax.py`, `python scrapers/scraper_otomoto.py`,
   w Dockerze `docker compose run --rm bidfax` i `docker compose run --rm otomoto`). Przy pierwszym przebiegu Otomoto
   zobaczysz w logu `Widzianych w tym skanie` i ewentualnie `Pomijam zamykanie ogłoszeń`. To bezpiecznik, który nie zamyka
   ogłoszeń po niepełnym skanie (patrz README).

## 4. Przeliczenie warstwy gold

```bash
SAMOCHODY_DB=samochody.db python analysis/gold.py     # w Dockerze: docker compose run --rm gold
```

Ścieżkę do bazy skrypt bierze ze zmiennej `SAMOCHODY_DB` (domyślnie `samochody.db` w bieżącym katalogu).
Tabela `analiza_rentownosci_usa` jest przy tym kasowana i tworzona od nowa. Czego się spodziewać:

- **Nowa kolumna `Wersja`** (np. `gts`, `turbo gt`, `540i`, a gdy nie rozpoznano: `podstawowa`). Grupy są teraz po marce, modelu,
  pojemności, wersji i roczniku, więc zapytania zakładające stary układ kolumn trzeba poprawić. Opis w README, sekcja
  *Versions in the gold layer*.
- **Mniej grup niż wcześniej.** Drobniejsze grupy częściej nie przechodzą progów (`MIN_AUKCJI_USA` i `MIN_OGLOSZEN_OTOMOTO`, po 3).
  Żeby porównać z poprzednim układem, uruchom z `GOLD_WERSJE=0`.
- **Inny koszt naprawy.** Jest liczony jako udział w cenie rynkowej zależny od typu uszkodzenia (z minimum 3000 zł), a nie stała kwota.
  Marże będą się różnić od wyników sprzed zmiany.
- Skrypt kończy się komunikatem `Zapisano N grup ... (marża ujemna: M)`. Jeśli bazy nie znajdzie, wychodzi z błędem.

## 5. Zasady przy analizie starych danych

- **Czas na rynku** licz z `Data_Startu` (nie z `Data_Dodania`): `Data_Ostatniego_Widzenia − Data_Startu`.
  `Czas_publikacji` może być odświeżany przez sprzedającego, więc wynik jest przybliżony.
- **Ogłoszenie jest sprzedane/zakończone** tylko wtedy, gdy jego ostatni wiersz (najwyższe `ID_Wersji`) ma `Status = 'Zakończone'`.
  Wiersze `Zakończone` mają `Wazne_Do = '9999-12-31 23:59:59'`, więc samo `Wazne_Do` nie odróżnia aktywnych od zakończonych. Filtruj po `Status`.
- **Jedno ogłoszenie = jeden wiersz.** Tabela SCD2 trzyma każdą wersję ceny. Do statystyk bierz ostatnią wersję na ID (tak robi `gold.py`).
- **Wyłącz podejrzane klony:** `WHERE Ilosc_Powtorzen < 5`.
- **Bidfax:** ten sam VIN może mieć kilka aukcji (578 powtórzonych VIN-ów w próbce). Dedupuj po VIN i dacie sprzedaży.
  Przebieg `0 miles (Not Actual)` (ok. 12% aut) to w praktyce brak przebiegu.

## 6. Wycofanie zmian

Jeśli coś pójdzie nie tak, zatrzymaj skrypty i przywróć kopię:

```bash
mv samochody.db samochody_zepsuta.db
cp samochody_backup_RRRR-MM-DD.db samochody.db
```

(Usuń też stare pliki `samochody.db-wal` i `samochody.db-shm`, jeśli zostały.)
