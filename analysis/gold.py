import pandas as pd
import numpy as np
import re
import sqlite3
import os
from datetime import date, datetime

from generacje import dodaj_generacje, dodaj_granice, wczytaj_generacje
from ranking_generacji import licz_ranking_generacji
from plynnosc import KOLUMNY_PLYNNOSCI, licz_plynnosc
from trendy import KOLUMNY_TRENDU, MIN_AUKCJI_W_OKNIE, OKNO_DNI, licz_trendy, zapisz_snapshot
from koszty import transport_ladowy_usd, wczytaj_koszty
from koszt_importu import licz_koszt_importu
from kursy import kursy
from oplaty_aukcyjne import wczytaj_oplaty
from slownik import szkic_slownika, wczytaj_slownik, wersja_reczna
from wersje import WERSJA_PODSTAWOWA, buduj_slownik, nadwozie, tokeny_wersji, uzupelnij_wersje_z_mocy, wersja

# ==========================================
# 1. KONFIGURACJA ZMIENNYCH FINANSOWYCH
# ==========================================
# Kursy USD i EUR bierzemy z NBP przy każdym uruchomieniu (patrz kursy.py), a koszty importu liczy koszt_importu.py
# Transport lądowy do portu zależy od lokalizacji (koszty_transportu.csv), opłata aukcyjna od ceny (oplaty_aukcyjne.csv)
OPLATY_AUKCYJNE = os.environ.get('GOLD_OPLATY_AUKCYJNE', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oplaty_aukcyjne.csv'))
KOSZTY_TRANSPORTU = os.environ.get('GOLD_KOSZTY_TRANSPORTU', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'koszty_transportu.csv'))
# Ceny poniżej tego progu to placeholdery (np. 1, 2 i 25 USD przed właściwą ceną), a nie sprzedaż
MIN_CENA_AUKCJI_USD = 100
STAWKA_AKCYZY_ELEKTRYCZNE = 0.0   # auta elektryczne są zwolnione z akcyzy
# Auta elektryczne nie mają pojemności silnika. Żeby nie wypadały z grupowania (NaN w kluczu), dostają pojemność 0.0
POJEMNOSC_ELEKTRYCZNE_L = 0.0

TABELA_HISTORII = 'analiza_rentownosci_usa_historia'
TABELA_RANKINGU = 'ranking_generacji_usa'
# Egzotyki: grupy z mniejszą próbką niż progi poniżej (ale z co najmniej 1 aukcją i 1 ogłoszeniem), trzymane osobno, żeby nie
# mieszały się z rankingiem i nie trafiały do snapshotów i rankingu generacji. Mediana z 1-2 sztuk to orientacja, nie wycena
TABELA_EGZOTYKOW = 'egzotyki_usa'

# Baza leży w roocie repo niezależnie od katalogu uruchomienia (jak w scraperach); SAMOCHODY_DB nadpisuje ścieżkę
DB_PATH = os.environ.get('SAMOCHODY_DB') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'samochody.db')

# Typ sprzedawcy Otomoto branego do cen rynkowych: 'Osoba prywatna', 'Firma' albo 'wszyscy'. Firmy mają zwykle wyższe ceny (marża handlarza)
SPRZEDAWCA_OTOMOTO = os.environ.get('GOLD_SPRZEDAWCA', 'Osoba prywatna')

# Minimalna liczba aukcji USA i ogłoszeń Otomoto w grupie (marka, model, pojemność, rocznik)
MIN_AUKCJI_USA = 3
MIN_OGLOSZEN_OTOMOTO = 3

# Grupa bez rozpoznanej wersji ('podstawowa') z takim rozrzutem cen Otomoto ((q90 - q10) / mediana) miesza prawdopodobnie
# kilka wersji wyposażenia, więc jej marża jest niepewna. Takie grupy lądują na końcu rankingu z flagą Niepewna_Wersja
MAX_ROZRZUT_CEN_OTOMOTO = 0.30
# Aukcja z pierwszego lub ostatniego roku generacji łatwo trafia do cen sąsiedniej generacji (rok modelowy USA kontra rok
# produkcji w Polsce). Jeśli cena Otomoto dla takiego rocznika różni się od ceny roku wcześniej lub później o więcej niż tyle,
# grupa dostaje flagę Podejrzany_Rocznik i ląduje niżej w rankingu
MAX_ROZNICA_SASIEDNICH_LAT = 0.15
# Wiarygodność grupy z liczby próbek (aukcje USA, ogłoszenia Otomoto z USA/Kanady): (min. aukcji, min. ogłoszeń) -> etykieta.
# Niska nie jest wycinana, bo egzotyki mają po kilka sztuk; tabela tylko ją oznacza i sortuje niżej
WIARYGODNOSC_PROGI = [(10, 5, 'wysoka'), (5, 3, 'srednia')]
WIARYGODNOSC_NISKA = 'niska'
WIARYGODNOSC_KOLEJNOSC = ['wysoka', 'srednia', 'niska']
# Cechy, po których dwa ogłoszenia Otomoto to to samo auto wystawione dwa razy pod różnymi ID. Bez ceny i tytułu (przy ponownym
# wystawieniu sprzedawcy zmieniają jedno i drugie); przebieg porównujemy z tolerancją, bo auto jeżdżące na co dzień ma go większy
KLUCZ_DUPLIKATU_OTOMOTO = ['Marka', 'Model', 'Nazwa_Sprzedawcy', 'Rok_produkcji', 'Paliwo', 'Skrzynia_biegow', 'Miasto']
# Do tylu km różnicy przebiegu to to samo auto (ok. 800 km/mies. przy ogłoszeniach dzielonych średnio ok. 19 dniami); od ok. 10 000 km
# to już dwa różne auta tego samego sprzedawcy wystawione w tym samym czasie
TOLERANCJA_PRZEBIEGU_KM = 3000
SPRZEDAWCA_PRYWATNY = 'Osoba prywatna'

# Rozdzielaj grupy też po wersji (S / GTS / Turbo...), patrz wersje.py. Wyłączenie daje stare, grubsze grupy
UZYWAJ_WERSJI = os.environ.get('GOLD_WERSJE', '1') != '0'
# Generacje z liftami (patrz generacje.py): ręczny plik ma pierwszeństwo przed generacjami z Otomoto
PLIK_GENERACJI = os.environ.get('GOLD_GENERACJE', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'generacje.csv'))
# Ręczny słownik wersji (patrz slownik.py), ma pierwszeństwo przed wyuczonym dla modeli, które w nim są
SLOWNIK_WERSJI = os.environ.get('GOLD_SLOWNIK_WERSJI', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'slownik_wersji.csv'))

# ==========================================
# 2. FUNKCJE POMOCNICZE I MAPOWANIE
# ==========================================
US_EU_ALIASES = {
    'ford': {'fusion': 'mondeo', 'escape': 'kuga', 'edge': 'edge'},
    'nissan': {'rogue': 'x-trail', 'rogue sport': 'qashqai'},
    'volkswagen': {'jetta': 'jetta', 'passat': 'passat'},
    # Bidfax skraca 'Model' do 'Mdl' i czasem podwaja spację ('Model  Y')
    'tesla': {f'{skrot} {litera}': f'model {litera}' for skrot in ('model', 'mdl') for litera in '3sxy'},
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
    b_model = re.sub(r'\s+', ' ', str(bidfax_model).lower())
    
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

def usun_duplikaty_ogloszen(otomoto, klucz=KLUCZ_DUPLIKATU_OTOMOTO, tolerancja_km=TOLERANCJA_PRZEBIEGU_KM):
    """To samo auto wystawione ponownie pod nowym ID (ten sam sprzedawca, rok, paliwo, skrzynia i miasto, przebieg w granicach
    tolerancji, cena i tytuł dowolne) liczy się raz, inaczej zawyża liczebność grupy i wagę swojej ceny w medianie.
    Kolejne ogłoszenia z różnicą przebiegu do tolerancji tworzą jedną grupę. Zostaje najnowsze (ostatni wiersz).
    Tolerancja dotyczy tylko osób prywatnych (jeżdżą autem na co dzień); handlarze mają wiele podobnych aut o bliskich przebiegach,
    więc u nich liczy się dokładny przebieg. Bez kolumny Sprzedawca wszyscy traktowani są jak osoby prywatne.
    Ogłoszenia bez przebiegu zostają bez zmian, bo nie ma jak ich porównać."""
    klucz = [k for k in klucz if k in otomoto.columns]
    prywatny = otomoto['Sprzedawca'] == SPRZEDAWCA_PRYWATNY if 'Sprzedawca' in otomoto.columns else True
    d = otomoto.assign(_poz=range(len(otomoto)), _km=pd.to_numeric(otomoto['Przebieg_km'], errors='coerce'),
                       _tolerancja=np.where(prywatny, tolerancja_km, 0))
    z_przebiegiem = d[d['_km'].notna()].sort_values('_km', kind='stable')
    nowa_grupa = z_przebiegiem.groupby(klucz, dropna=False)['_km'].diff() > z_przebiegiem['_tolerancja']
    z_przebiegiem['_grupa'] = nowa_grupa.groupby([z_przebiegiem[k] for k in klucz], dropna=False).cumsum()
    z_przebiegiem = z_przebiegiem.sort_values('_poz').drop_duplicates(klucz + ['_grupa'], keep='last')
    wynik = pd.concat([z_przebiegiem, d[d['_km'].isna()]]).sort_values('_poz')
    return wynik.drop(columns=['_poz', '_km', '_tolerancja', '_grupa'], errors='ignore')

def oznacz_niepewne_wersje(grupy, rozrzut_proc, wersja_podstawowa=WERSJA_PODSTAWOWA, max_rozrzut=MAX_ROZRZUT_CEN_OTOMOTO):
    """Kolumna 0/1: grupa bez rozpoznanej wersji, której ceny Otomoto są mocno rozrzucone (rozrzut w %, brak = 0)."""
    return ((grupy['Wersja'] == wersja_podstawowa) & (rozrzut_proc.fillna(0) > max_rozrzut * 100)).astype(int)

def oznacz_podejrzane_roczniki(grupy, otomoto_agg, graniczny, max_roznica=MAX_ROZNICA_SASIEDNICH_LAT, min_ogloszen=MIN_OGLOSZEN_OTOMOTO):
    """Kolumna 0/1: rocznik graniczny, którego cena Otomoto z USA/Kanady różni się o więcej niż max_roznica od roku wcześniej
    lub później (sąsiad liczy się, gdy ma co najmniej min_ogloszen ogłoszeń; bez sąsiadów nie ma podstaw do flagi)."""
    klucz = ['Marka', 'Model', 'Pojemnosc_L', 'Wersja', 'Nadwozie']
    cena = 'Mediana_Cena_USA_Kanada_Otomoto_PLN'
    lewa = grupy[klucz + ['Rocznik', cena]].reset_index(drop=True)
    roznica = np.zeros(len(lewa))
    wiarygodni = otomoto_agg[otomoto_agg['Ilosc_USA_Kanada_Otomoto'] >= min_ogloszen]
    for przesuniecie in (-1, 1):
        sasiad = wiarygodni[klucz + ['Rok_produkcji', cena]].rename(columns={cena: '_cena_sasiada'})
        sasiad = sasiad.assign(Rocznik=sasiad['Rok_produkcji'] - przesuniecie).drop(columns='Rok_produkcji')
        polaczone = lewa.merge(sasiad, on=klucz + ['Rocznik'], how='left')
        roznica = np.maximum(roznica, (polaczone[cena] / polaczone['_cena_sasiada'] - 1).abs().fillna(0).to_numpy())
    return pd.Series(((roznica > max_roznica) & (graniczny.to_numpy() == 1)).astype(int), index=grupy.index)

def rozdziel_egzotyki(gold, min_aukcji=MIN_AUKCJI_USA, min_ogloszen=MIN_OGLOSZEN_OTOMOTO):
    """Dzieli grupy na (główne, egzotyki): egzotyk ma mniej niż min_aukcji aukcji USA albo mniej niż min_ogloszen ogłoszeń Otomoto."""
    egzotyk = (gold['Ilosc_Aukcje_USA'] < min_aukcji) | (gold['Ilosc_USA_Kanada_Otomoto'] < min_ogloszen)
    return gold[~egzotyk], gold[egzotyk]

def oznacz_wiarygodnosc(aukcje, ogloszenia, progi=WIARYGODNOSC_PROGI):
    """'wysoka' / 'srednia' / 'niska' z liczby aukcji USA i ogłoszeń Otomoto (pierwszy próg, który oba warunki spełnia)."""
    wynik = pd.Series(WIARYGODNOSC_NISKA, index=aukcje.index)
    for min_aukcji, min_ogloszen, etykieta in reversed(progi):
        wynik[(aukcje >= min_aukcji) & (ogloszenia >= min_ogloszen)] = etykieta
    return wynik

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

POJEMNOSC_W_TEKSCIE = re.compile(
    r'(?<![\d./,])([1-9][.,]\d)(?![\d.,])(?!\s*(?:tys|mln|zł|pln|kw|km\b|kg|m\b|l/|%|s\b))|(?<!\d)([1-9])\s?(\d{3})\s?cm', re.IGNORECASE)
TOLERANCJA_POJEMNOSCI_L = 0.3

def popraw_pojemnosc_z_tekstu(pojemnosc_l, tytul, opis):
    """Sprzedawcy często zostawiają w formularzu Otomoto domyślne 998 cm3, a w tytule lub opisie podają prawdziwą pojemność ('2.0 Ti-VCT',
    '1 998cm3'). Gdy tekst wskazuje dokładnie jedną pojemność i różni się ona od pola o więcej niż tolerancja, bierzemy tę z tekstu."""
    kandydaci = set()
    for m in POJEMNOSC_W_TEKSCIE.finditer(f"{tytul or ''} {opis or ''}"):
        wartosc = float(m.group(1).replace(',', '.')) if m.group(1) else round(float(m.group(2) + m.group(3)) / 1000.0, 1)
        if 0.6 <= wartosc <= 8.0:
            kandydaci.add(round(wartosc, 1))
    if len(kandydaci) != 1:
        return pojemnosc_l
    z_tekstu = kandydaci.pop()
    if pojemnosc_l is None or pd.isna(pojemnosc_l) or abs(z_tekstu - pojemnosc_l) > TOLERANCJA_POJEMNOSCI_L:
        return z_tekstu
    return pojemnosc_l

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
    otomoto_df = usun_duplikaty_ogloszen(otomoto_df)
    print(f"ℹ️  Otomoto: pominięto {przed - len(otomoto_df)} ogłoszeń tego samego auta wystawionych pod innym ID.")
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
        opisy = otomoto_df['Opis'] if 'Opis' in otomoto_df.columns else [''] * len(otomoto_df)
        otomoto_df['Pojemnosc_L'] = [popraw_pojemnosc_z_tekstu(poj, t, o) for poj, t, o in zip(otomoto_df['Pojemnosc_L'], otomoto_df['Tytul'], opisy)]
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
        'median_Polska_Krajowe': 'Mediana_Cena_Krajowe_PLN',
        'median_USA_Kanada': 'Mediana_Cena_USA_Kanada_Otomoto_PLN',
        'median_Inny_Import': 'Mediana_Cena_Inny_Import_PLN',
        'count_Polska_Krajowe': 'Ilosc_Krajowe',
        'count_USA_Kanada': 'Ilosc_USA_Kanada_Otomoto'
    }).reset_index()

    # Płynność liczymy dla aut z USA/Kanady, bo tylko ich ceny trafiają do porównania w analizie
    klucz_otomoto = ['Marka', 'Model', 'Pojemnosc_L', 'Wersja', 'Nadwozie', 'Rok_produkcji']
    plynnosc = licz_plynnosc(otomoto_df[otomoto_df['Pochodzenie_Kategoria'] == 'USA_Kanada'], klucz_otomoto)
    otomoto_agg = otomoto_agg.merge(plynnosc, on=klucz_otomoto, how='left')

    # Rozrzut cen aut z USA/Kanady w grupie: wysoki przy 'podstawowa' znaczy, że w grupie siedzi kilka wersji wyposażenia
    ceny_usa = otomoto_df[otomoto_df['Pochodzenie_Kategoria'] == 'USA_Kanada'].groupby(klucz_otomoto)['Cena_PLN']
    rozrzut = ((ceny_usa.quantile(0.9) - ceny_usa.quantile(0.1)) / ceny_usa.median() * 100).rename('Rozrzut_Cen_Otomoto_%').reset_index()
    otomoto_agg = otomoto_agg.merge(rozrzut, on=klucz_otomoto, how='left')

    bidfax_df['Cena_USD'] = pd.to_numeric(bidfax_df['Cena_USD'], errors='coerce')
    kursy_walut = kursy()
    print(f"ℹ️  Kursy: USD {kursy_walut['USD']['kurs']:.4f} ({kursy_walut['USD']['zrodlo']}), EUR {kursy_walut['EUR']['kurs']:.4f} ({kursy_walut['EUR']['zrodlo']}).")
    koszty_transportu = wczytaj_koszty(KOSZTY_TRANSPORTU)
    bidfax_df['Transport_Ladowy_USD'] = bidfax_df['Lokalizacja'].apply(lambda l: transport_ladowy_usd(l, koszty_transportu))
    bidfax_df = licz_koszt_importu(bidfax_df, kursy_walut['USD']['kurs'], kursy_walut['EUR']['kurs'], wczytaj_oplaty(OPLATY_AUKCYJNE))

    usa_agg = bidfax_df.groupby(['Marka', 'Otomoto_Model', 'Pojemnosc_L', 'Wersja', 'Nadwozie', 'Rocznik']).agg(
        Mediana_Cena_Zakupu_USA_USD=('Cena_USD', 'median'),
        Procent_Hybryd=('Czy_Hybryda', 'mean'), 
        Mediana_Koszt_Import_PLN=('Koszt_Import_PLN', 'median'),
        Mediana_Clo_PLN=('Clo_PLN', 'median'),
        Mediana_Akcyza_PLN=('Akcyza_PLN', 'median'),
        Mediana_VAT_PLN=('VAT_PLN', 'median'),
        Ilosc_Aukcje_USA=('ID', 'count')
    ).reset_index()
    
    # Progi liczebności stosujemy dopiero po złączeniu z Otomoto (rozdziel_egzotyki), bo grupy poniżej nich idą do osobnej tabeli
    klucz_grupy = ['Marka', 'Otomoto_Model', 'Pojemnosc_L', 'Wersja', 'Nadwozie', 'Rocznik']
    usa_agg = usa_agg.merge(licz_trendy(bidfax_df, klucz_grupy), on=klucz_grupy, how='left')

    gold_layer = pd.merge(usa_agg, otomoto_agg, left_on=['Marka', 'Otomoto_Model', 'Pojemnosc_L', 'Wersja', 'Nadwozie', 'Rocznik'], right_on=['Marka', 'Model', 'Pojemnosc_L', 'Wersja', 'Nadwozie', 'Rok_produkcji'], how='inner')
    gold_layer.drop(columns=['Otomoto_Model'], inplace=True)
    with sqlite3.connect(DB_PATH) as conn:
        slownik_generacji = wczytaj_generacje(conn, normalizuj_marke, PLIK_GENERACJI)
    gold_layer['Generacja'] = dodaj_generacje(gold_layer, 'Marka', 'Model', 'Rocznik', slownik_generacji)

    # NOWOŚĆ: Usuwamy wiersze, dla których brakuje ceny referencyjnej z Otomoto (zapobiega to liczeniu na NULLach)
    gold_layer = gold_layer.dropna(subset=['Mediana_Cena_USA_Kanada_Otomoto_PLN'])
    
    # NOWOŚĆ: Zamiana NULLi w ilości aut z polskich salonów na czytelne zera
    if 'Ilosc_Krajowe' in gold_layer.columns:
        gold_layer['Ilosc_Krajowe'] = gold_layer['Ilosc_Krajowe'].fillna(0).astype(int)

    # Naprawy nie da się wycenić z bazy (brak zdjęć), więc zamiast kosztu liczymy, ile z ceny rynkowej zostaje na naprawę
    # po odjęciu całego importu (auto, transport, cło, akcyza, VAT, opłaty).
    gold_layer['Budzet_Na_Naprawe_PLN'] = gold_layer['Mediana_Cena_USA_Kanada_Otomoto_PLN'] - gold_layer['Mediana_Koszt_Import_PLN']
    gold_layer['Budzet_Na_Naprawe_%'] = (gold_layer['Budzet_Na_Naprawe_PLN'] / gold_layer['Mediana_Cena_USA_Kanada_Otomoto_PLN'] * 100).round(1)

    gold_layer['Niepewna_Wersja'] = oznacz_niepewne_wersje(gold_layer, gold_layer['Rozrzut_Cen_Otomoto_%'])

    gold_layer['Rocznik_Graniczny'] = dodaj_granice(gold_layer, 'Marka', 'Model', 'Rocznik', slownik_generacji)
    gold_layer['Podejrzany_Rocznik'] = oznacz_podejrzane_roczniki(gold_layer, otomoto_agg, gold_layer['Rocznik_Graniczny'])

    gold_layer['Wiarygodnosc'] = oznacz_wiarygodnosc(gold_layer['Ilosc_Aukcje_USA'], gold_layer['Ilosc_USA_Kanada_Otomoto'])

    assert (gold_layer['Mediana_Koszt_Import_PLN'] > 0).all(), "Ujemny lub zerowy koszt sprowadzenia"

    base_cols = [
        'Marka', 'Model', 'Generacja', 'Wersja', 'Nadwozie', 'Rocznik', 'Pojemnosc_L', 'Procent_Hybryd', 'Ilosc_Aukcje_USA', 
        'Ilosc_USA_Kanada_Otomoto', 'Mediana_Cena_USA_Kanada_Otomoto_PLN', *KOLUMNY_PLYNNOSCI,
        'Mediana_Cena_Zakupu_USA_USD', *KOLUMNY_TRENDU, 'Mediana_Clo_PLN', 'Mediana_Akcyza_PLN', 'Mediana_VAT_PLN', 
        'Mediana_Koszt_Import_PLN', 'Budzet_Na_Naprawe_PLN', 'Budzet_Na_Naprawe_%', 'Rozrzut_Cen_Otomoto_%', 'Niepewna_Wersja', 'Rocznik_Graniczny', 'Podejrzany_Rocznik', 'Wiarygodnosc'
    ]
    if 'Ilosc_Krajowe' in gold_layer.columns and 'Mediana_Cena_Krajowe_PLN' in gold_layer.columns:
        base_cols.insert(7, 'Ilosc_Krajowe')
        base_cols.insert(8, 'Mediana_Cena_Krajowe_PLN')

    final_cols = [col for col in base_cols if col in gold_layer.columns]
    
    # Na końcu rankingu: grupy ze zmieszanymi wersjami, potem z podejrzanym rocznikiem, potem te z małą próbką (w obrębie wiarygodności sortuje marża)
    gold_layer = gold_layer[final_cols]
    # Główna tabela: min. 3 aukcje i 3 ogłoszenia Otomoto, by jedna anomalia cenowa nie psuła wyniku; reszta to egzotyki
    gold_layer, egzotyki = rozdziel_egzotyki(gold_layer)
    egzotyki = egzotyki.sort_values('Budzet_Na_Naprawe_%', ascending=False).round(1)
    kolejnosc = gold_layer['Wiarygodnosc'].map(WIARYGODNOSC_KOLEJNOSC.index)
    gold_layer = gold_layer.assign(_kol=kolejnosc).sort_values(
        by=['Niepewna_Wersja', 'Podejrzany_Rocznik', '_kol', 'Budzet_Na_Naprawe_%'], ascending=[True, True, True, False]).drop(columns='_kol').round(1)
    
    table_name = 'analiza_rentownosci_usa'
    
    with sqlite3.connect(DB_PATH) as conn:
        # Tabela jest nadpisywana przy każdym przebiegu, więc data mówi, kiedy dane zostały przeładowane
        gold_do_zapisu = gold_layer.copy()
        gold_do_zapisu.insert(0, 'Data_Przeladowania', datetime.now().strftime('%Y-%m-%d %H:%M'))
        gold_do_zapisu.to_sql(table_name, conn, if_exists='replace', index=False)
        # Historię marży i trendów trzymamy w osobnym snapshocie z datą
        egzotyki_do_zapisu = egzotyki.copy()
        egzotyki_do_zapisu.insert(0, 'Data_Przeladowania', gold_do_zapisu['Data_Przeladowania'].iloc[0])
        egzotyki_do_zapisu.to_sql(TABELA_EGZOTYKOW, conn, if_exists='replace', index=False)
        zapisano = zapisz_snapshot(conn, TABELA_HISTORII, gold_layer)
        ranking = licz_ranking_generacji(gold_layer)
        ranking.insert(0, 'Data_Przeladowania', gold_do_zapisu['Data_Przeladowania'].iloc[0])
        ranking.to_sql(TABELA_RANKINGU, conn, if_exists='replace', index=False)
    print(f"ℹ️  Snapshot: dopisano {zapisano} grup do '{TABELA_HISTORII}' ({date.today().isoformat()}).")
    print(f"ℹ️  Trend ceny USA policzony dla {int(gold_layer['Trend_Ceny_USA_%'].notna().sum())} z {len(gold_layer)} grup "
          f"(okno {OKNO_DNI} dni, min. {MIN_AUKCJI_W_OKNIE} aukcje w każdym oknie).")

    bez_generacji = int((gold_layer['Generacja'] == 'brak').sum())
    print(f"ℹ️  Generacja: {len(gold_layer) - bez_generacji} z {len(gold_layer)} grup przypisano ({len(slownik_generacji)} modeli w słowniku).")

    print(f"ℹ️  Rocznik graniczny: {int(gold_layer['Rocznik_Graniczny'].sum())} grup, z czego podejrzanych (cena Otomoto różni się >{MAX_ROZNICA_SASIEDNICH_LAT:.0%} od sąsiedniego roku): {int(gold_layer['Podejrzany_Rocznik'].sum())}.")
    ujemne = int((gold_layer['Budzet_Na_Naprawe_PLN'] < 0).sum())
    print(f"ℹ️  Niepewna wersja (podstawowa, rozrzut cen Otomoto > {MAX_ROZRZUT_CEN_OTOMOTO:.0%}): {int(gold_layer['Niepewna_Wersja'].sum())} grup, na końcu rankingu.")
    print("ℹ️  Wiarygodność: " + ", ".join(f"{e} {int((gold_layer['Wiarygodnosc'] == e).sum())}" for e in WIARYGODNOSC_KOLEJNOSC) + ".")
    print(f"ℹ️  Ranking generacji z silnikiem: {len(ranking)} pozycji w tabeli '{TABELA_RANKINGU}'.")
    print(f"ℹ️  Egzotyki (poniżej {MIN_AUKCJI_USA} aukcji lub {MIN_OGLOSZEN_OTOMOTO} ogłoszeń): {len(egzotyki)} grup w tabeli '{TABELA_EGZOTYKOW}'.")
    print(f"✅ Zapisano {len(gold_layer)} grup do tabeli '{table_name}' (budżet na naprawę ujemny: {ujemne}).")
