BEGIN;

-- Indeksy, które scraper zakłada przy starcie. Bez nich DELETE z EXISTS poniżej działa bardzo wolno (pełny skan dla każdego wiersza)
CREATE INDEX IF NOT EXISTS idx_aktualne ON otomoto_scd2 (ID_Ogloszenia, Wazne_Do);
CREATE INDEX IF NOT EXISTS idx_wazne_status ON otomoto_scd2 (Wazne_Do, Status);

-- Bidfax: marka w polu Model dla marek z myślnikiem
UPDATE bidfax_scd2 SET Model = TRIM(SUBSTR(Model, LENGTH('Mercedes-Benz') + 1))
WHERE Marka = 'mercedes-benz' AND Model LIKE 'Mercedes-Benz %';
UPDATE bidfax_scd2 SET Model = TRIM(SUBSTR(Model, LENGTH('Rolls-Royce') + 1))
WHERE Marka = 'rolls-royce' AND Model LIKE 'Rolls-Royce %';

-- Bidfax: jednolity zapis stanu auta
UPDATE bidfax_scd2 SET Condition = 'Run And Drive' WHERE Condition = 'Run and Drive';

-- Bidfax: zera oznaczają brak danych (nowy scraper zapisuje NULL)
UPDATE bidfax_scd2 SET Rocznik = NULL WHERE Rocznik = 0;
UPDATE bidfax_scd2 SET Cena_USD = NULL WHERE Cena_USD = 0;

-- Otomoto: usuń fałszywe zakończenia (ogłoszenie zamknięte przez niepełny skan, które potem wróciło).
-- Prawdziwe zakończenia (bez późniejszego wiersza 'Aktywne') zostają.
DELETE FROM otomoto_scd2
WHERE Status = 'Zakończone'
  AND EXISTS (SELECT 1 FROM otomoto_scd2 a
              WHERE a.ID_Ogloszenia = otomoto_scd2.ID_Ogloszenia
                AND a.Status = 'Aktywne' AND a.Wazne_Od > otomoto_scd2.Wazne_Od);

-- Otomoto: realna data startu ogłoszenia (wcześniejsze skany nie sięgały daleko, więc Data_Dodania bywa za późna)
ALTER TABLE otomoto_scd2 ADD COLUMN Data_Startu TEXT;
UPDATE otomoto_scd2
SET Data_Startu = MIN(Data_Dodania, REPLACE(SUBSTR(Czas_publikacji, 1, 19), 'T', ' '))
WHERE Czas_publikacji IS NOT NULL;

COMMIT;
