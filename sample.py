import sqlite3
import pandas as pd
import os

# 1. Konfiguracja ścieżek
db_path = "samochody.db" 

# Ścieżka docelowa - folder Pobrane (Downloads)
downloads_path = os.path.expanduser('~/Downloads')
os.makedirs(downloads_path, exist_ok=True) 

# Ścieżki do plików wynikowych
otomoto_output = os.path.join(downloads_path, 'otomoto_sample.csv')
bidfax_output = os.path.join(downloads_path, 'bidfax_sample.csv')

# 2. Połączenie z bazą danych
print(f"Łączę się z bazą danych: {db_path}...")
conn = sqlite3.connect(db_path)

# 3. Pobranie próbki dla otomoto_scd2 (20 000 wierszy)
table_otomoto = "otomoto_scd2"
query_otomoto = f"SELECT * FROM {table_otomoto} ORDER BY RANDOM() LIMIT 20000;"

print(f"Pobieram 20 000 losowych wierszy z tabeli {table_otomoto}...")
df_otomoto = pd.read_sql_query(query_otomoto, conn)

print("Zapisuję plik otomoto_sample.csv...")
df_otomoto.to_csv(otomoto_output, index=False)


# 4. Pobranie próbki dla bidfax_scd2 (20 000 wierszy)
table_bidfax = "bidfax_scd2"
query_bidfax = f"SELECT * FROM {table_bidfax} ORDER BY RANDOM() LIMIT 20000;"

print(f"Pobieram 20 000 losowych wierszy z tabeli {table_bidfax}...")
df_bidfax = pd.read_sql_query(query_bidfax, conn)

print("Zapisuję plik bidfax_sample.csv...")
df_bidfax.to_csv(bidfax_output, index=False)

# Zamknięcie połączenia z bazą
conn.close()

print(f"Sukces! Próbki zostały zapisane w folderze:\n- {otomoto_output}\n- {bidfax_output}")
