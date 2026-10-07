import pandas as pd
import numpy as np
import re
import sqlite3
import os

# ==========================================
# 1. KONFIGURACJA ZMIENNYCH FINANSOWYCH
# ==========================================
USD_TO_PLN = 3.55
KOSZT_TRANSPORT_I_AUKCJA_USD = 2200
STAWKA_CLA = 0.10
STAWKA_VAT = 0.19
OPLATY_PORTOWE_TUMACZENIA_PLN = 2500
TRANSPORT_DO_PL_PLN = 1800

# NOWOŚĆ: Szacunkowy budżet na naprawę rozbitego auta z licytacji w PLN
SZACOWANY_KOSZT_NAPRAWY_PLN = 20000 

DB_PATH = '/home/eaz/Desktop/github/ai-love-cars/samochody.db'

# ==========================================
# 2. FUNKCJE POMOCNICZE I MAPOWANIE
# ==========================================
US_EU_ALIASES = {
    'ford': {'fusion': 'mondeo', 'escape': 'kuga', 'edge': 'edge'},
    'nissan': {'rogue': 'x-trail', 'rogue sport': 'qashqai'},
    'volkswagen': {'jetta': 'jetta', 'passat': 'passat'}
}

def map_model_dynamic(marka, bidfax_model, otomoto_models_for_brand):
    marka = str(marka).lower()
    b_model = str(bidfax_model).lower()
    
    if marka in US_EU_ALIASES:
        for us_name, eu_name in US_EU_ALIASES[marka].items():
            if us_name in b_model:
                return eu_name
                
    if marka == 'bmw':
        match = re.search(r'\b([1-8])\d\d\b|x[1-7]|i[3478]', b_model)
        if match:
            matched_str = match.group(0)
            if matched_str.startswith('x') or matched_str.startswith('i'):
                return matched_str 
            else:
                return f"seria {match.group(1)}" 
                
    if marka == 'mercedes-benz':
        match = re.search(r'\b([a-z])\s?\d{2,3}\b', b_model)
        if match:
            return f"klasa {match.group(1)}"
            
    if otomoto_models_for_brand is not None:
        for o_model in sorted(list(otomoto_models_for_brand), key=len, reverse=True):
            if o_model in b_model:
                return o_model
            
    return b_model.split()[0] if b_model else "nieznany"

def extract_capacity_bidfax(title):
    match = re.search(r'(\d\.\d)L', str(title), re.IGNORECASE)
    if match: return round(float(match.group(1)), 1)
    return None

def extract_capacity_otomoto(cm3):
    if pd.isna(cm3): return None
    try:
        val = float(cm3) / 1000.0
        return round(val, 1)
    except:
        return None

def is_hybrid(title, marka, model):
    keywords = [r'\bhybrid\b', r'\bmhev\b', r'\bphev\b', r'\bhev\b', r'\bhyb\b']
    title_str = str(title).lower()
    if re.search('|'.join(keywords), title_str): return True
    m = str(model).lower()
    if 'q8' in m and not 'e-tron' in title_str: return True 
    return False

# ==========================================
# 3. GŁÓWNY PROCES (MAIN)
# ==========================================
if __name__ == '__main__':
    if not os.path.exists(DB_PATH):
        print(f"❌ BŁĄD: Nie znaleziono pliku bazy danych pod ścieżką:\n{DB_PATH}")
        exit(1)

    with sqlite3.connect(DB_PATH) as conn:
        bidfax_df = pd.read_sql_query("SELECT * FROM bidfax_scd2", conn)
        otomoto_df = pd.read_sql_query("SELECT * FROM otomoto_scd2", conn)

    otomoto_df['Marka'] = otomoto_df['Marka'].astype(str).str.lower().str.strip()
    otomoto_df['Model'] = otomoto_df['Model'].astype(str).str.lower().str.strip()
    bidfax_df['Marka'] = bidfax_df['Marka'].astype(str).str.lower().str.strip()
    bidfax_df['Model'] = bidfax_df['Model'].astype(str).str.lower().str.strip()

    dict_otomoto_models = otomoto_df.groupby('Marka')['Model'].unique().to_dict()

    bidfax_df['Otomoto_Model'] = bidfax_df.apply(
        lambda row: map_model_dynamic(row['Marka'], row['Model'], dict_otomoto_models.get(row['Marka'], [])), 
        axis=1
    )
    
    bidfax_df['Pojemnosc_L'] = bidfax_df['Tytul'].apply(extract_capacity_bidfax)
    
    if 'Pojemnosc_cm3' in otomoto_df.columns:
        otomoto_df['Pojemnosc_L'] = otomoto_df['Pojemnosc_cm3'].apply(extract_capacity_otomoto)
    else:
        otomoto_df['Pojemnosc_L'] = None
    
    bidfax_df['Rocznik'] = pd.to_numeric(bidfax_df['Rocznik'], errors='coerce').fillna(0).astype(int)
    
    if 'Rok_produkcji' in otomoto_df.columns:
        otomoto_df['Rok_produkcji'] = pd.to_numeric(otomoto_df['Rok_produkcji'], errors='coerce').fillna(0).astype(int)
    else:
        otomoto_df['Rok_produkcji'] = 0
    
    bidfax_df['Czy_Hybryda'] = bidfax_df.apply(lambda row: is_hybrid(row['Tytul'], row['Marka'], row['Otomoto_Model']), axis=1)
    
    def get_excise_rate(row):
        cap = row['Pojemnosc_L']
        hybrid = row['Czy_Hybryda']
        if pd.isna(cap): rate = 0.186
        elif cap <= 2.0: rate = 0.031
        else: rate = 0.186
        if hybrid: return rate / 2.0
        return rate
        
    bidfax_df['Stawka_Akcyzy'] = bidfax_df.apply(get_excise_rate, axis=1)

    if 'Kraj_pochodzenia' in otomoto_df.columns:
        otomoto_df['Pochodzenie_Kategoria'] = np.select(
            [otomoto_df['Kraj_pochodzenia'].isin(['Stany Zjednoczone', 'Kanada']), otomoto_df['Kraj_pochodzenia'] == 'Polska'],
            ['USA_Kanada', 'Polska_Krajowe'], default='Inny_Import'
        )
    else:
        otomoto_df['Pochodzenie_Kategoria'] = 'Inny_Import'

    otomoto_agg = otomoto_df.pivot_table(
        index=['Marka', 'Model', 'Pojemnosc_L', 'Rok_produkcji'], 
        columns='Pochodzenie_Kategoria', 
        values='Cena_PLN', 
        aggfunc=['median', 'count']
    )
    otomoto_agg.columns = ['_'.join(col).strip() for col in otomoto_agg.columns.values]
    otomoto_agg = otomoto_agg.rename(columns={
        'median_Polska_Krajowe': 'Cena_Krajowe_PLN',
        'median_USA_Kanada': 'Cena_USA_Kanada_Otomoto_PLN',
        'median_Inny_Import': 'Cena_Inny_Import_PLN',
        'count_Polska_Krajowe': 'Ilosc_Krajowe',
        'count_USA_Kanada': 'Ilosc_USA_Kanada_Otomoto'
    }).reset_index()

    bidfax_df['Cena_Zakupu_USA_PLN'] = pd.to_numeric(bidfax_df['Cena_USD'], errors='coerce') * USD_TO_PLN
    bidfax_df['Fracht_Morski_Aukcja_PLN'] = KOSZT_TRANSPORT_I_AUKCJA_USD * USD_TO_PLN
    bidfax_df['Wartosc_Celna_PLN'] = bidfax_df['Cena_Zakupu_USA_PLN'] + bidfax_df['Fracht_Morski_Aukcja_PLN']
    bidfax_df['Clo_PLN'] = bidfax_df['Wartosc_Celna_PLN'] * STAWKA_CLA
    bidfax_df['Wartosc_do_Akcyzy_PLN'] = bidfax_df['Wartosc_Celna_PLN'] + bidfax_df['Clo_PLN']
    bidfax_df['Akcyza_PLN'] = bidfax_df['Wartosc_do_Akcyzy_PLN'] * bidfax_df['Stawka_Akcyzy']
    bidfax_df['Wartosc_do_VAT_PLN'] = bidfax_df['Wartosc_do_Akcyzy_PLN'] + bidfax_df['Akcyza_PLN']
    bidfax_df['VAT_PLN'] = bidfax_df['Wartosc_do_VAT_PLN'] * STAWKA_VAT
    bidfax_df['Calkowity_Koszt_Sprowadzenia_Baza_PLN'] = (
        bidfax_df['Cena_Zakupu_USA_PLN'] + bidfax_df['Fracht_Morski_Aukcja_PLN'] + 
        bidfax_df['Clo_PLN'] + bidfax_df['Akcyza_PLN'] + bidfax_df['VAT_PLN'] + 
        OPLATY_PORTOWE_TUMACZENIA_PLN + TRANSPORT_DO_PL_PLN + SZACOWANY_KOSZT_NAPRAWY_PLN
    )

    usa_agg = bidfax_df.groupby(['Marka', 'Otomoto_Model', 'Pojemnosc_L', 'Rocznik']).agg(
        Cena_Zakupu_USA_USD=('Cena_USD', lambda x: np.median(pd.to_numeric(x, errors='coerce'))),
        Procent_Hybryd=('Czy_Hybryda', 'mean'), 
        Calkowity_Koszt_Sprowadzenia_Baza_PLN=('Calkowity_Koszt_Sprowadzenia_Baza_PLN', 'median'),
        Srednie_Clo_PLN=('Clo_PLN', 'median'),
        Srednia_Akcyza_PLN=('Akcyza_PLN', 'median'),
        Sredni_VAT_PLN=('VAT_PLN', 'median'),
        Ilosc_Aukcje_USA=('ID', 'count')
    ).reset_index()
    
    usa_agg = usa_agg[usa_agg['Ilosc_Aukcje_USA'] >= 3]

    gold_layer = pd.merge(usa_agg, otomoto_agg, left_on=['Marka', 'Otomoto_Model', 'Pojemnosc_L', 'Rocznik'], right_on=['Marka', 'Model', 'Pojemnosc_L', 'Rok_produkcji'], how='inner')
    gold_layer.drop(columns=['Otomoto_Model'], inplace=True)

    # NOWOŚĆ: Usuwamy wiersze, dla których brakuje ceny referencyjnej z Otomoto (zapobiega to liczeniu na NULLach)
    gold_layer = gold_layer.dropna(subset=['Cena_USA_Kanada_Otomoto_PLN'])
    
    # NOWOŚĆ: Filtr - zostawiamy tylko te modele, których na Otomoto jest min. 3 sztuki, by jedna anomalia cenowa nie psuła wyniku
    gold_layer = gold_layer[gold_layer['Ilosc_USA_Kanada_Otomoto'] >= 3]

    # NOWOŚĆ: Zamiana NULLi w ilości aut z polskich salonów na czytelne zera
    if 'Ilosc_Krajowe' in gold_layer.columns:
        gold_layer['Ilosc_Krajowe'] = gold_layer['Ilosc_Krajowe'].fillna(0).astype(int)

    gold_layer['Budzet_Zysk_PLN'] = gold_layer['Cena_USA_Kanada_Otomoto_PLN'] - gold_layer['Calkowity_Koszt_Sprowadzenia_Baza_PLN']
    gold_layer['ROI_%'] = (gold_layer['Budzet_Zysk_PLN'] / gold_layer['Calkowity_Koszt_Sprowadzenia_Baza_PLN'] * 100).round(1)
    gold_layer['Marza_%'] = (gold_layer['Budzet_Zysk_PLN'] / gold_layer['Cena_USA_Kanada_Otomoto_PLN'] * 100).round(1)

    base_cols = [
        'Marka', 'Model', 'Rocznik', 'Pojemnosc_L', 'Procent_Hybryd', 'Ilosc_Aukcje_USA', 
        'Ilosc_USA_Kanada_Otomoto', 'Cena_USA_Kanada_Otomoto_PLN',
        'Cena_Zakupu_USA_USD', 'Srednie_Clo_PLN', 'Srednia_Akcyza_PLN', 'Sredni_VAT_PLN', 
        'Calkowity_Koszt_Sprowadzenia_Baza_PLN', 'Budzet_Zysk_PLN', 'ROI_%', 'Marza_%'
    ]
    if 'Ilosc_Krajowe' in gold_layer.columns and 'Cena_Krajowe_PLN' in gold_layer.columns:
        base_cols.insert(7, 'Ilosc_Krajowe')
        base_cols.insert(8, 'Cena_Krajowe_PLN')

    final_cols = [col for col in base_cols if col in gold_layer.columns]
    
    gold_layer = gold_layer[final_cols].sort_values(by='Marza_%', ascending=False).round(1)
    
    table_name = 'analiza_rentownosci_usa'
    
    with sqlite3.connect(DB_PATH) as conn:
        gold_layer.to_sql(table_name, conn, if_exists='replace', index=False)
