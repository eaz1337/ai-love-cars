import pandas as pd
import numpy as np
import re
import sqlite3
import os

from koszty import transport_ladowy_usd, wczytaj_koszty
from slownik import szkic_slownika, wczytaj_slownik, wersja_reczna
from wersje import WERSJA_PODSTAWOWA, buduj_slownik, nadwozie, tokeny_wersji, uzupelnij_wersje_z_mocy, wersja

# ==========================================
# 1. KONFIGURACJA ZMIENNYCH FINANSOWYCH
# ==========================================
USD_TO_PLN = 3.55
# Stała część: fracht morski i opłaty aukcyjne. Transport lądowy do portu zależy od lokalizacji (koszty_transportu.csv).
# 1700 + średni transport lądowy ze słownika (ok. 520) daje średnio dawne 2200 USD
KOSZT_FRACHT_I_OPLATY_USD = 1700
KOSZTY_TRANSPORTU = os.environ.get('GOLD_KOSZTY_TRANSPORTU', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'koszty_transportu.csv'))
# Ceny poniżej tego progu to placeholdery (np. 1, 2 i 25 USD przed właściwą ceną), a nie sprzedaż
MIN_CENA_AUKCJI_USD = 100
STAWKA_CLA = 0.10
STAWKA_VAT = 0.19
STAWKA_AKCYZY_ELEKTRYCZNE = 0.0   # auta elektryczne są zwolnione z akcyzy
# Auta elektryczne nie mają pojemności silnika. Żeby nie wypadały z grupowania (NaN w kluczu), dostają pojemność 0.0
POJEMNOSC_ELEKTRYCZNE_L = 0.0
OPLATY_PORTOWE_TUMACZENIA_PLN = 2500
TRANSPORT_DO_PL_PLN = 1800

# Ścieżkę można nadpisać zmienną środowiskową, domyślnie baza leży w katalogu, z którego uruchamiasz skrypt (jak w scraperach)
DB_PATH = os.environ.get('SAMOCHODY_DB', 'samochody.db')

# Typ sprzedawcy Otomoto branego do cen rynkowych: 'Osoba prywatna', 'Firma' albo 'wszyscy'. Firmy mają zwykle wyższe ceny (marża handlarza)
SPRZEDAWCA_OTOMOTO = os.environ.get('GOLD_SPRZEDAWCA', 'Osoba prywatna')

# Minimalna liczba aukcji USA i ogłoszeń Otomoto w grupie (marka, model, pojemność, rocznik)
MIN_AUKCJI_USA = 3
MIN_OGLOSZEN_OTOMOTO = 3

# Rozdzielaj grupy też po wersji (S / GTS / Turbo...), patrz wersje.py. Wyłączenie daje stare, grubsze grupy
UZYWAJ_WERSJI = os.environ.get('GOLD_WERSJE', '1') != '0'
# Ręczny słownik wersji (patrz slownik.py), ma pierwszeństwo przed wyuczonym dla modeli, które w nim są
SLOWNIK_WERSJI = os.environ.get('GOLD_SLOWNIK_WERSJI', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'slownik_wersji.csv'))

# ==========================================
# 2. FUNKCJE POMOCNICZE I MAPOWANIE
# ==========================================
US_EU_ALIASES = {
    'ford': {'fusion': 'mondeo', 'escape': 'kuga', 'edge': 'edge'},
    'nissan': {'rogue': 'x-trail', 'rogue sport': 'qashqai'},
    'volkswagen': {'jetta': 'jetta', 'passat': 'passat'}
}

# Bidfax podaje slug z URL-a ('land-rover'), Otomoto nazwę wyświetlaną ('Land Rover'). Wspólna postać: małe litery, myślniki
ALIASY_MAREK = {'mclaren-automotive': 'mclaren', 'lucid-motors': 'lucid'}

def normalizuj_marke(marka):
    """'Land Rover' / 'land-rover' -> 'land-rover', 'mclaren-automotive' -> 'mclaren'."""
    slug = re.sub(r'[\s\-]+', '-', str(marka).strip().lower())
    return ALIASY_MAREK.get(slug, slug)

def _slowo_w_tekscie(szukane, tekst):
    """True, jeśli `szukane` występuje w `tekst` jako osobne słowo (np. 'rogue' w 'rogue sv', ale nie 'e' w 'escape')."""
    return re.search(r'(?<![a-z0-9])' + re.escape(szukane) + r'(?![a-z0-9])', tekst) is not None

def map_model_dynamic(marka, bidfax_model, otomoto_models_for_brand):
    marka = str(marka).lower()
    b_model = str(bidfax_model).lower()
    
    if marka in US_EU_ALIASES:
        # Najdłuższe nazwy najpierw: 'rogue sport' (qashqai) musi wygrać z 'rogue' (x-trail)
        for us_name, eu_name in sorted(US_EU_ALIASES[marka].items(), key=lambda kv: len(kv[0]), reverse=True):
            if _slowo_w_tekscie(us_name, b_model):
                return eu_name
                
    if marka == 'bmw':
        # 328 i, 530i, m550i -> seria 3/5; x5; i3. Nazwy z dopiskiem (530i, m550i) też są łapane, wzorce są zakotwiczone
        match = re.search(r'\bm?([1-8])\d\d[a-z]{0,2}\b|\bx[1-7]\b|\bi[3478]\b', b_model)
        if match:
            matched_str = match.group(0)
            if re.fullmatch(r'x[1-7]|i[3478]', matched_str):
                return matched_str 
            else:
                return f"seria {match.group(1)}" 
                
    if marka == 'mercedes-benz':
        match = re.search(r'\b([a-z])\s?\d{2,3}\b', b_model)
        if match:
            return f"klasa {match.group(1)}"
            
    if otomoto_models_for_brand is not None:
        for o_model in sorted(list(otomoto_models_for_brand), key=len, reverse=True):
            if o_model in ('', 'nan', 'none'):
                continue
            if len(o_model) == 1:
                # Jednoliterowy model (np. 'g', 'e') tylko jako całe słowo, inaczej łapie się w środku innych nazw
                if _slowo_w_tekscie(o_model, b_model):
                    return o_model
            elif re.search(r'(?<![a-z0-9])' + re.escape(o_model), b_model):
                # Lewa granica słowa wystarczy: 'rx' pasuje do 'rx350', ale 'x5' nie pasuje do 'ex5'
                return o_model
            
    return b_model.split()[0] if b_model else "nieznany"

def odfiltruj_ceny_techniczne(bidfax, min_cena=MIN_CENA_AUKCJI_USD):
    """Usuwa aukcje z ceną poniżej progu (placeholdery). Robimy to przed wyborem ostatniej sprzedaży na VIN,
    żeby ostatnia aukcja z placeholderem nie wygrała z wcześniejszą, która ma prawdziwą cenę."""
    return bidfax[pd.to_numeric(bidfax['Cena_USD'], errors='coerce') >= min_cena]

USZKODZENIA_WYKLUCZONE = ('burn', 'biohazard', 'flood', 'water')

def odfiltruj_uszkodzenia_wykluczone(bidfax):
    """Usuwa aukcje aut spalonych, zalanych i skażonych (Burn, Engine Burn, Flood, Water/Flood, Biohazard...).
    Dopasowanie po fragmencie, bo Bidfax zapisuje to pod wieloma nazwami."""
    wzorzec = '|'.join(USZKODZENIA_WYKLUCZONE)
    return bidfax[~bidfax['Damage'].fillna('').str.contains(wzorzec, case=False, regex=True)]

def filtruj_sprzedawce(otomoto, sprzedawca=SPRZEDAWCA_OTOMOTO):
    """Zostawia ogłoszenia Otomoto danego typu sprzedawcy (kolumna Sprzedawca); 'wszyscy' niczego nie usuwa."""
    if sprzedawca.lower() == 'wszyscy' or 'Sprzedawca' not in otomoto.columns:
        return otomoto
    return otomoto[otomoto['Sprzedawca'] == sprzedawca]

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

ELEKTRYCZNE_MARKI = {'tesla', 'lucid', 'polestar'}
ELEKTRYCZNE_SLOWA = re.compile(r'\b(e-tron|electric|taycan|ioniq|ev6|ev9|mach-e|leaf|id\.\s?[3-7])\b', re.IGNORECASE)

def is_electric(title, marka, pojemnosc_l):
    """Bidfax nie podaje paliwa. Auto z pojemnością w tytule ma silnik spalinowy ('A3 E-Tron 1.4L' to hybryda plug-in),
    więc elektryczne rozpoznajemy tylko wtedy, gdy pojemności brak, po marce albo słowach typu 'e-tron', 'ioniq'."""
    if pojemnosc_l is not None and not pd.isna(pojemnosc_l):
        return False
    return marka in ELEKTRYCZNE_MARKI or ELEKTRYCZNE_SLOWA.search(str(title)) is not None

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

    # --- Otomoto: jedno ogłoszenie = jeden wiersz ---
    # Tabela SCD2 trzyma każdą wersję ceny oraz końcową kopię 'Zakończone'. Bez deduplikacji jedno ogłoszenie liczy się
    # kilka razy (zawyża liczebność grupy i wagę starych cen). Bierzemy ostatnią wersję, bez outlierów i bez braku ceny.
    otomoto_df = otomoto_df.sort_values('ID_Wersji').drop_duplicates('ID_Ogloszenia', keep='last')
    if 'Is_Outlier' in otomoto_df.columns:
        otomoto_df = otomoto_df[otomoto_df['Is_Outlier'].fillna(0) == 0]
    otomoto_df['Cena_PLN'] = pd.to_numeric(otomoto_df['Cena_PLN'], errors='coerce')
    otomoto_df = otomoto_df[otomoto_df['Cena_PLN'] > 0]
    przed = len(otomoto_df)
    otomoto_df = filtruj_sprzedawce(otomoto_df).copy()
    print(f"ℹ️  Otomoto: pominięto {przed - len(otomoto_df)} ogłoszeń spoza grupy sprzedawców '{SPRZEDAWCA_OTOMOTO}'.")

    # --- Bidfax: tylko aukcje z ceną i rocznikiem, jedna sprzedaż na VIN ---
    # Brak ceny lub rocznika to NULL (nie 0), a brak rocznika złączyłby się z 'Rok_produkcji = 0' z Otomoto.
    bidfax_df['Cena_USD'] = pd.to_numeric(bidfax_df['Cena_USD'], errors='coerce')
    bidfax_df['Rocznik'] = pd.to_numeric(bidfax_df['Rocznik'], errors='coerce')
    bidfax_df = bidfax_df[bidfax_df['Rocznik'] > 0]
    przed = len(bidfax_df)
    bidfax_df = odfiltruj_ceny_techniczne(bidfax_df).copy()
    print(f"ℹ️  Pominięto {przed - len(bidfax_df)} aukcji z ceną poniżej {MIN_CENA_AUKCJI_USD} USD (placeholdery lub brak ceny).")
    bidfax_df['Rocznik'] = bidfax_df['Rocznik'].astype(int)
    # To samo auto wystawiane na kolejnych aukcjach (niesprzedane, odsprzedane) liczy się raz: ostatnia sprzedaż
    ma_vin = bidfax_df['VIN'].notna() & (bidfax_df['VIN'] != 'Brak')
    bidfax_df['_data_sort'] = bidfax_df['Date_Of_Sale'].where(bidfax_df['Date_Of_Sale'] != 'Brak', '')
    bidfax_df = pd.concat([
        bidfax_df[~ma_vin],
        bidfax_df[ma_vin].sort_values('_data_sort').drop_duplicates('VIN', keep='last'),
    ]).drop(columns=['_data_sort'])
    # Po wyborze ostatniej sprzedaży, żeby auto spalone/zalane na późniejszej aukcji nie wróciło jako wcześniejsza "czysta" sprzedaż
    przed = len(bidfax_df)
    bidfax_df = odfiltruj_uszkodzenia_wykluczone(bidfax_df).copy()
    print(f"ℹ️  Pominięto {przed - len(bidfax_df)} aukcji aut spalonych, zalanych lub skażonych (burn/flood/water/biohazard).")

    otomoto_df['Marka'] = otomoto_df['Marka'].map(normalizuj_marke)
    otomoto_df['Model'] = otomoto_df['Model'].astype(str).str.lower().str.strip()
    bidfax_df['Marka'] = bidfax_df['Marka'].map(normalizuj_marke)
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
    
    bidfax_df['Czy_Elektryczne'] = [is_electric(t, ma, poj) for t, ma, poj in zip(bidfax_df['Tytul'], bidfax_df['Marka'], bidfax_df['Pojemnosc_L'])]
    bidfax_df.loc[bidfax_df['Czy_Elektryczne'], 'Pojemnosc_L'] = POJEMNOSC_ELEKTRYCZNE_L
    otomoto_df.loc[otomoto_df['Paliwo'] == 'Elektryczny', 'Pojemnosc_L'] = POJEMNOSC_ELEKTRYCZNE_L

    otomoto_df['Rok_produkcji'] = pd.to_numeric(otomoto_df['Rok_produkcji'], errors='coerce')
    otomoto_df = otomoto_df[otomoto_df['Rok_produkcji'] > 0].copy()
    otomoto_df['Rok_produkcji'] = otomoto_df['Rok_produkcji'].astype(int)
    
    # Nadwozie (gran coupe / cabrio / standard) rozdziela grupy niezależnie od wersji silnikowej
    otomoto_df['Nadwozie'] = [nadwozie(t, o) for t, o in zip(otomoto_df['Tytul'], otomoto_df['Opis'] if 'Opis' in otomoto_df.columns else [''] * len(otomoto_df))]
    bidfax_df['Nadwozie'] = bidfax_df['Tytul'].map(nadwozie)

    if UZYWAJ_WERSJI:
        otomoto_df['Tokeny'] = [tokeny_wersji(t, (ma, mo)) for t, ma, mo in zip(otomoto_df['Tytul'], otomoto_df['Marka'], otomoto_df['Model'])]
        bidfax_df['Tokeny'] = [tokeny_wersji(m, (ma, om)) for m, ma, om in zip(bidfax_df['Model'], bidfax_df['Marka'], bidfax_df['Otomoto_Model'])]
        slownik = buduj_slownik(otomoto_df, bidfax_df)
        otomoto_df['Wersja'] = [wersja(t, ma, mo, slownik) for t, ma, mo in zip(otomoto_df['Tokeny'], otomoto_df['Marka'], otomoto_df['Model'])]
        bidfax_df['Wersja'] = [wersja(t, ma, om, slownik) for t, ma, om in zip(bidfax_df['Tokeny'], bidfax_df['Marka'], bidfax_df['Otomoto_Model'])]
        # Ogłoszenia bez wersji w tytule dostają ją z mocy (Bidfax nie ma mocy, tam wersja zostaje z tytułu)
        wersja_z_tytulu = otomoto_df['Wersja']
        otomoto_df['Wersja'] = uzupelnij_wersje_z_mocy(otomoto_df)
        print(f"ℹ️  Wersja z mocy: {int((otomoto_df['Wersja'] != wersja_z_tytulu).sum())} z {len(otomoto_df)} ogłoszeń Otomoto "
              f"(z tytułu: {int((wersja_z_tytulu != WERSJA_PODSTAWOWA).sum())}).")

        # Ręczny słownik nadpisuje wszystko powyżej dla modeli, które w nim są
        reczny = wczytaj_slownik(SLOWNIK_WERSJI)
        if reczny:
            w_slowniku_o = pd.Series([(ma, mo) in reczny for ma, mo in zip(otomoto_df['Marka'], otomoto_df['Model'])], index=otomoto_df.index)
            otomoto_df.loc[w_slowniku_o, 'Wersja'] = [
                wersja_reczna(t, reczny[(ma, mo)], moc, poj, rok) for t, ma, mo, moc, poj, rok in zip(
                    otomoto_df.loc[w_slowniku_o, 'Tokeny'], otomoto_df.loc[w_slowniku_o, 'Marka'], otomoto_df.loc[w_slowniku_o, 'Model'],
                    pd.to_numeric(otomoto_df.loc[w_slowniku_o, 'Moc_KM'], errors='coerce'), otomoto_df.loc[w_slowniku_o, 'Pojemnosc_L'],
                    otomoto_df.loc[w_slowniku_o, 'Rok_produkcji'])]
            w_slowniku_b = pd.Series([(ma, om) in reczny for ma, om in zip(bidfax_df['Marka'], bidfax_df['Otomoto_Model'])], index=bidfax_df.index)
            bidfax_df.loc[w_slowniku_b, 'Wersja'] = [
                wersja_reczna(t, reczny[(ma, om)], rok=rok) for t, ma, om, rok in zip(
                    bidfax_df.loc[w_slowniku_b, 'Tokeny'], bidfax_df.loc[w_slowniku_b, 'Marka'], bidfax_df.loc[w_slowniku_b, 'Otomoto_Model'],
                    bidfax_df.loc[w_slowniku_b, 'Rocznik'])]
            print(f"ℹ️  Ręczny słownik wersji: {len(reczny)} modeli ({int(w_slowniku_o.sum())} ogłoszeń Otomoto, {int(w_slowniku_b.sum())} aukcji Bidfax).")

        if os.environ.get('GOLD_SZKIC_SLOWNIKA'):
            szkic_slownika(otomoto_df, slownik).to_csv(os.environ['GOLD_SZKIC_SLOWNIKA'], index=False)
            print(f"ℹ️  Zapisano szkic słownika do {os.environ['GOLD_SZKIC_SLOWNIKA']}")
    else:
        otomoto_df['Wersja'] = WERSJA_PODSTAWOWA
        bidfax_df['Wersja'] = WERSJA_PODSTAWOWA

    bidfax_df['Czy_Hybryda'] = bidfax_df.apply(lambda row: is_hybrid(row['Tytul'], row['Marka'], row['Otomoto_Model']), axis=1)
    
    def get_excise_rate(row):
        if row['Czy_Elektryczne']: return STAWKA_AKCYZY_ELEKTRYCZNE
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
        index=['Marka', 'Model', 'Pojemnosc_L', 'Wersja', 'Nadwozie', 'Rok_produkcji'], 
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
    koszty_transportu = wczytaj_koszty(KOSZTY_TRANSPORTU)
    bidfax_df['Transport_Ladowy_USD'] = bidfax_df['Lokalizacja'].apply(lambda l: transport_ladowy_usd(l, koszty_transportu))
    bidfax_df['Transport_i_Oplaty_PLN'] = (KOSZT_FRACHT_I_OPLATY_USD + bidfax_df['Transport_Ladowy_USD']) * USD_TO_PLN
    bidfax_df['Wartosc_Celna_PLN'] = bidfax_df['Cena_Zakupu_USA_PLN'] + bidfax_df['Transport_i_Oplaty_PLN']
    bidfax_df['Clo_PLN'] = bidfax_df['Wartosc_Celna_PLN'] * STAWKA_CLA
    bidfax_df['Wartosc_do_Akcyzy_PLN'] = bidfax_df['Wartosc_Celna_PLN'] + bidfax_df['Clo_PLN']
    bidfax_df['Akcyza_PLN'] = bidfax_df['Wartosc_do_Akcyzy_PLN'] * bidfax_df['Stawka_Akcyzy']
    bidfax_df['Wartosc_do_VAT_PLN'] = bidfax_df['Wartosc_do_Akcyzy_PLN'] + bidfax_df['Akcyza_PLN']
    bidfax_df['VAT_PLN'] = bidfax_df['Wartosc_do_VAT_PLN'] * STAWKA_VAT
    bidfax_df['Koszt_Import_PLN'] = (
        bidfax_df['Cena_Zakupu_USA_PLN'] + bidfax_df['Transport_i_Oplaty_PLN'] + 
        bidfax_df['Clo_PLN'] + bidfax_df['Akcyza_PLN'] + bidfax_df['VAT_PLN'] + 
        OPLATY_PORTOWE_TUMACZENIA_PLN + TRANSPORT_DO_PL_PLN
    )

    usa_agg = bidfax_df.groupby(['Marka', 'Otomoto_Model', 'Pojemnosc_L', 'Wersja', 'Nadwozie', 'Rocznik']).agg(
        Cena_Zakupu_USA_USD=('Cena_USD', 'median'),
        Procent_Hybryd=('Czy_Hybryda', 'mean'), 
        Koszt_Import_PLN=('Koszt_Import_PLN', 'median'),
        Srednie_Clo_PLN=('Clo_PLN', 'median'),
        Srednia_Akcyza_PLN=('Akcyza_PLN', 'median'),
        Sredni_VAT_PLN=('VAT_PLN', 'median'),
        Ilosc_Aukcje_USA=('ID', 'count')
    ).reset_index()
    
    usa_agg = usa_agg[usa_agg['Ilosc_Aukcje_USA'] >= MIN_AUKCJI_USA]

    gold_layer = pd.merge(usa_agg, otomoto_agg, left_on=['Marka', 'Otomoto_Model', 'Pojemnosc_L', 'Wersja', 'Nadwozie', 'Rocznik'], right_on=['Marka', 'Model', 'Pojemnosc_L', 'Wersja', 'Nadwozie', 'Rok_produkcji'], how='inner')
    gold_layer.drop(columns=['Otomoto_Model'], inplace=True)

    # NOWOŚĆ: Usuwamy wiersze, dla których brakuje ceny referencyjnej z Otomoto (zapobiega to liczeniu na NULLach)
    gold_layer = gold_layer.dropna(subset=['Cena_USA_Kanada_Otomoto_PLN'])
    
    # NOWOŚĆ: Filtr - zostawiamy tylko te modele, których na Otomoto jest min. 3 sztuki, by jedna anomalia cenowa nie psuła wyniku
    gold_layer = gold_layer[gold_layer['Ilosc_USA_Kanada_Otomoto'] >= MIN_OGLOSZEN_OTOMOTO]

    # NOWOŚĆ: Zamiana NULLi w ilości aut z polskich salonów na czytelne zera
    if 'Ilosc_Krajowe' in gold_layer.columns:
        gold_layer['Ilosc_Krajowe'] = gold_layer['Ilosc_Krajowe'].fillna(0).astype(int)

    # Naprawy nie da się wycenić z bazy (brak zdjęć), więc zamiast kosztu liczymy, ile z ceny rynkowej zostaje na naprawę
    # po odjęciu całego importu (auto, transport, cło, akcyza, VAT, opłaty).
    gold_layer['Budzet_Na_Naprawe_PLN'] = gold_layer['Cena_USA_Kanada_Otomoto_PLN'] - gold_layer['Koszt_Import_PLN']
    gold_layer['Budzet_Na_Naprawe_%'] = (gold_layer['Budzet_Na_Naprawe_PLN'] / gold_layer['Cena_USA_Kanada_Otomoto_PLN'] * 100).round(1)

    assert (gold_layer['Koszt_Import_PLN'] > 0).all(), "Ujemny lub zerowy koszt sprowadzenia"

    base_cols = [
        'Marka', 'Model', 'Wersja', 'Nadwozie', 'Rocznik', 'Pojemnosc_L', 'Procent_Hybryd', 'Ilosc_Aukcje_USA', 
        'Ilosc_USA_Kanada_Otomoto', 'Cena_USA_Kanada_Otomoto_PLN',
        'Cena_Zakupu_USA_USD', 'Srednie_Clo_PLN', 'Srednia_Akcyza_PLN', 'Sredni_VAT_PLN', 
        'Koszt_Import_PLN', 'Budzet_Na_Naprawe_PLN', 'Budzet_Na_Naprawe_%'
    ]
    if 'Ilosc_Krajowe' in gold_layer.columns and 'Cena_Krajowe_PLN' in gold_layer.columns:
        base_cols.insert(7, 'Ilosc_Krajowe')
        base_cols.insert(8, 'Cena_Krajowe_PLN')

    final_cols = [col for col in base_cols if col in gold_layer.columns]
    
    gold_layer = gold_layer[final_cols].sort_values(by='Budzet_Na_Naprawe_%', ascending=False).round(1)
    
    table_name = 'analiza_rentownosci_usa'
    
    with sqlite3.connect(DB_PATH) as conn:
        gold_layer.to_sql(table_name, conn, if_exists='replace', index=False)

    ujemne = int((gold_layer['Budzet_Na_Naprawe_PLN'] < 0).sum())
    print(f"✅ Zapisano {len(gold_layer)} grup do tabeli '{table_name}' (budżet na naprawę ujemny: {ujemne}).")
