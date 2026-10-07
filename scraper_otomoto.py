import json
import re
import time
import random
import urllib.parse
from datetime import datetime
import sqlite3
import logging
import subprocess
import threading
import bs4
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==============================================================================
# KONFIGURACJA
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

WYBRANE_MARKI = [
    "alfa-romeo", "alpina", "alpine", "aston-martin", "audi", "bentley",
    "bmw", "cadillac", "chevrolet", "chrysler", "dodge", "ferrari", "ford",
    "honda", "hyundai", "infiniti", "jaguar", "jeep", "kia", "lamborghini",
    "land-rover", "lexus", "lotus", "maserati", "mclaren", "mercedes-benz",
    "mitsubishi", "nissan", "porsche", "rolls-royce"
]

def generuj_url_otomoto(marki):
    sciezka = "--".join(marki)
    base_url = f"https://www.otomoto.pl/osobowe/{sciezka}?"
    
    params = {
        "search[filter_enum_registered]": "1",
        "search[private_business]": "private",
        "search[order]": "created_at_first:desc",
        "search[advanced_search_expanded]": "true"
    }
    
    for index, marka in enumerate(marki):
        params[f"search[make_model_generation][{index}]"] = marka
        
    return base_url + urllib.parse.urlencode(params)

ZADANIA_SCRAPOWANIA = [
    {
        "nazwa": "Wybrane_Marki_Prywatne",
        "url_bazowy": generuj_url_otomoto(WYBRANE_MARKI),
    }
]

DB_NAME = "samochody.db"
statystyki_blokad = {"ilosc_blokad": 0, "ilosc_rotacji_vpn": 0}
vpn_lock = threading.Lock()
stat_lock = threading.Lock()
thread_local = threading.local()

MAX_WORKERS_FAZA_1 = 6
MAX_WORKERS_FAZA_2 = 8

STRON_W_PACZCE = 20  
MAX_STRON = 4000
MAX_PROB_NA_STRONE = 3
LIMIT_FAZY_2 = 500  
NIESKONCZONOSC = '9999-12-31 23:59:59'
MAX_PROB_DETALI = 3          # tyle razy faza 2 próbuje pobrać detale ogłoszenia, potem odpuszcza
MAX_ZAMKNIEC_PROC = 0.30     # bezpiecznik: nie zamykamy naraz więcej niż 30% aktywnych ogłoszeń
MIN_AKTYWNYCH_DLA_BEZPIECZNIKA = 50

def get_session():
    if not hasattr(thread_local, "session"):
        thread_local.session = cffi_requests.Session(impersonate="chrome120")
    return thread_local.session

# ==============================================================================
# BAZA DANYCH SQLITE (Dodana flaga: Detale_Pobrane)
# ==============================================================================
def zainicjalizuj_baze():
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS otomoto_scd2 (
            ID_Wersji INTEGER PRIMARY KEY AUTOINCREMENT,
            ID_Ogloszenia TEXT, Marka TEXT, Model TEXT, Tytul TEXT,
            Cena_PLN REAL, Przebieg_km INTEGER, Paliwo TEXT,
            Rok_produkcji INTEGER, Pojemnosc_cm3 INTEGER, Moc_KM INTEGER,
            Skrzynia_biegow TEXT, Kraj_pochodzenia TEXT, Bezwypadkowy TEXT, Pierwszy_wlasciciel TEXT,
            Uszkodzony TEXT, Miasto TEXT, Wojewodztwo TEXT, Miejsce TEXT, Czas_publikacji TEXT,
            Sprzedawca TEXT, Nazwa_Sprzedawcy TEXT, Link TEXT,
            Data_Dodania TEXT, Data_Ostatniego_Widzenia TEXT,
            Status TEXT, Wazne_Od TEXT, Wazne_Do TEXT,
            Opis TEXT, Sprzedawca_Od TEXT, Ilosc_Powtorzen INTEGER DEFAULT 1,
            Is_Outlier INTEGER DEFAULT 0, Detale_Pobrane INTEGER DEFAULT 0, Proby_Detali INTEGER DEFAULT 0
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_aktualne ON otomoto_scd2 (ID_Ogloszenia, Wazne_Do)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wazne_status ON otomoto_scd2 (Wazne_Do, Status)")
    
    for col, col_type in [("Nazwa_Sprzedawcy", "TEXT"), ("Ilosc_Powtorzen", "INTEGER DEFAULT 1"), 
                          ("Miasto", "TEXT"), ("Wojewodztwo", "TEXT"), 
                          ("Is_Outlier", "INTEGER DEFAULT 0"), ("Detale_Pobrane", "INTEGER DEFAULT 0"),
                          ("Proby_Detali", "INTEGER DEFAULT 0")]:
        try: 
            cursor.execute(f"ALTER TABLE otomoto_scd2 ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError: 
            pass

    # Migracja: dawniej nieudane pobranie zapisywało 'Błąd pobierania' w Opis i na stałe blokowało ponowną próbę
    cursor.execute("UPDATE otomoto_scd2 SET Opis = NULL, Detale_Pobrane = 0 WHERE Opis = 'Błąd pobierania'")
    conn.commit()  # PRAGMA journal_mode nie działa wewnątrz otwartej transakcji

    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA synchronous = NORMAL;")
    conn.commit()
    return conn

def pobierz_indeks_aut(conn):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ID_Ogloszenia, Tytul, Rok_produkcji, Przebieg_km, Sprzedawca, Paliwo, Skrzynia_biegow, Cena_PLN, Data_Dodania, Ilosc_Powtorzen
        FROM otomoto_scd2
        WHERE Wazne_Do = ? AND Status = 'Aktywne'
    """, (NIESKONCZONOSC,))
    rows = cursor.fetchall()
    
    mem_db = {"ids": {}, "klony": {}}
    for row in rows:
        (car_id, tytul, rok, przebieg, sprzedawca, paliwo, skrzynia, cena, data_dod, ilosc_powt) = row
        mem_db["ids"][car_id] = {"cena": cena, "data_dodania": data_dod, "ilosc_powtorzen": ilosc_powt if ilosc_powt else 1}
        key = klucz_klona({"Tytul": tytul, "Rok_produkcji": rok, "Przebieg_km": przebieg, "Sprzedawca": sprzedawca,
                           "Paliwo": paliwo, "Skrzynia_biegow": skrzynia})
        if key:
            mem_db["klony"][key] = car_id
        
    return mem_db

def normalize_nulls(val):
    if not val: return None
    v_str = str(val).strip()
    if v_str.lower() in ["brak", "nie podano", "null", "none", ""]:
        return None
    return v_str

def clean_int(val):
    """'125 000 km' (także z twardą spacją), '1 998 cm3', '150 KM' -> liczba. Brak cyfr -> None."""
    if not val: return None
    # Tylko początek tekstu (cyfry i spacje, także twarde); jednostka na końcu, np. "cm3", nie może wejść do liczby
    liczba = re.match(r'[\d\s\u00a0]+', str(val).strip())
    cyfry = re.sub(r'\D', '', liczba.group()) if liczba else ''
    return int(cyfry) if cyfry else None

def clean_float(val):
    if not val: return None
    v = str(val).replace('\xa0', '').replace(' ', '').replace(',', '.')
    if v.lower() in ["brak", "niepodano", "none", "null"]: return None
    try: return float(v)
    except ValueError: return None

def bezpeiczny_soup(html_content):
    try: return BeautifulSoup(html_content, 'lxml')
    except bs4.FeatureNotFound: return BeautifulSoup(html_content, 'html.parser')

def parse_otomoto_json(html_content):
    cars = []
    soup = bezpeiczny_soup(html_content)
    script_tag = soup.find('script', id='__NEXT_DATA__')
    if not script_tag: return cars
    try:
        data = json.loads(script_tag.string)
        urql_state = data.get("props", {}).get("pageProps", {}).get("urqlState", {})
        for value in urql_state.values():
            if not isinstance(value, dict): continue
            try:
                inner_data = json.loads(value.get("data", "{}"))
                advert_search = inner_data.get("advertSearch")
                if not advert_search: continue
                for edge in advert_search.get("edges", []):
                    node = edge.get("node")
                    if node: cars.append(node)
            except Exception: continue
    except Exception as e:
        logging.error(f"Krytyczny błąd parsowania JSON (Faza 1): {e}")
    return cars

def wyciagnij_dane_ogloszenia(car):
    seller = car.get("seller")
    seller_name = normalize_nulls(seller.get("name") if isinstance(seller, dict) else None) or "Osoba prywatna"
    
    price_node = car.get("price") or {}
    amount_node = price_node.get("amount") or {}
    price = clean_float(amount_node.get("value") or price_node.get("value"))

    params = {}
    for p in car.get("parameters", []):
        if isinstance(p, dict):
            val = p.get("displayValue")
            if p.get("key"): params[p.get("key")] = val
            if p.get("label"): params[p.get("label").strip().lower()] = val

    location_node = car.get("location")
    if not isinstance(location_node, dict):
        location_node = (seller.get("location") if isinstance(seller, dict) else None) or {}
    if not isinstance(location_node, dict): location_node = {}

    city_data = location_node.get("city")
    city = city_data.get("name", "").strip() if isinstance(city_data, dict) else str(city_data or "").strip()
    
    region_data = location_node.get("region") or location_node.get("province")
    region = region_data.get("name", "").strip() if isinstance(region_data, dict) else str(region_data or "").strip()

    miejsce = f"{city} ({region})" if city and region else (city or region or None)

    przebieg = clean_int(params.get("mileage") or params.get("przebieg"))
    rok = clean_int(params.get("year") or params.get("rok produkcji"))
    pojemnosc = clean_int(params.get("engine_capacity") or params.get("pojemność skokowa"))
    moc = clean_int(params.get("engine_power") or params.get("moc"))
    
    is_outlier = 0
    if price and (price < 1000 or price > 1000000): is_outlier = 1
    if przebieg and przebieg > 1000000: is_outlier = 1
    if rok and (rok < 1950 or rok > datetime.now().year + 1): is_outlier = 1
    if pojemnosc and (pojemnosc < 600 or pojemnosc > 8000): is_outlier = 1
    if moc and (moc < 30 or moc > 1000): is_outlier = 1

    return {
        "ID_Ogloszenia": str(car.get("id")),
        "Marka": normalize_nulls(params.get("make")),
        "Model": normalize_nulls(params.get("model")),
        "Tytul": normalize_nulls(car.get("title")),
        "Cena_PLN": price,
        "Przebieg_km": przebieg,
        "Paliwo": normalize_nulls(params.get("fuel_type") or params.get("rodzaj paliwa")),
        "Rok_produkcji": rok,
        "Pojemnosc_cm3": pojemnosc,
        "Moc_KM": moc,
        "Skrzynia_biegow": normalize_nulls(params.get("gearbox") or params.get("skrzynia biegów")),
        "Kraj_pochodzenia": normalize_nulls(params.get("country_origin") or params.get("kraj pochodzenia")),
        "Bezwypadkowy": normalize_nulls(params.get("no_accident") or params.get("bezwypadkowy")),
        "Pierwszy_wlasciciel": normalize_nulls(params.get("original_owner") or params.get("first_owner") or params.get("pierwszy właściciel")),
        "Uszkodzony": normalize_nulls(params.get("damaged") or params.get("uszkodzony")),
        "Miasto": normalize_nulls(city),
        "Wojewodztwo": normalize_nulls(region),
        "Miejsce": normalize_nulls(miejsce),
        "Czas_publikacji": normalize_nulls(car.get("createdAt")),
        "Sprzedawca": normalize_nulls(seller_name),
        "Nazwa_Sprzedawcy": normalize_nulls(seller_name), 
        "Link": normalize_nulls(car.get("url")),
        "Opis": None,
        "Sprzedawca_Od": None,
        "Is_Outlier": is_outlier
    }

def dodaj_blokade():
    with stat_lock:
        statystyki_blokad["ilosc_blokad"] += 1

def rotuj_nordvpn_system():
    if vpn_lock.acquire(blocking=False):
        try:
            logging.warning("System: Inicjowanie zmiany serwera NordVPN (CLI)...")
            with stat_lock:
                statystyki_blokad["ilosc_rotacji_vpn"] += 1
            try:
                wynik = subprocess.run(["nordvpn", "connect", "Poland"], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=60)
                if wynik.returncode != 0:
                    logging.error(f"System: 'nordvpn connect' zakończyło się kodem {wynik.returncode}. IP mogło się nie zmienić.")
            except FileNotFoundError:
                logging.error("System: Aplikacja 'nordvpn' nie jest zainstalowana. Pomijam zmianę IP.")
            except subprocess.TimeoutExpired:
                logging.error("System: 'nordvpn connect' nie odpowiedziało w 60 s. Idę dalej.")
            time.sleep(6)
            logging.info("System: Nowy serwer NordVPN przypisany. Wznawiam ruch.")
            
            global thread_local
            thread_local = threading.local()
        finally:
            vpn_lock.release()
    else:
        time.sleep(6)

def pobierz_strone_cffi(url, page_num):
    session = get_session()
    for proba in range(1, MAX_PROB_NA_STRONE + 1):
        try:
            response = session.get(url, timeout=15)
            if response.status_code == 200:
                cars = parse_otomoto_json(response.text)
                if not cars and "cloudflare" in response.text.lower():
                    logging.warning(f"Strona {page_num}: Zatrzymane przez Cloudflare (Próba {proba}).")
                    dodaj_blokade()
                    rotuj_nordvpn_system()
                else:
                    return page_num, cars
            elif response.status_code in (403, 429):
                logging.warning(f"Strona {page_num}: BLOKADA ({response.status_code}) (Próba {proba}).")
                dodaj_blokade()
                rotuj_nordvpn_system()
        except Exception as e:
            logging.debug(f"Cichy błąd HTTP dla strony {page_num}: {e}")
        time.sleep(3 * proba)
    return page_num, None

def wstaw_nowy_rekord(cursor, car):
    cursor.execute("""
        INSERT INTO otomoto_scd2 (
            ID_Ogloszenia, Marka, Model, Tytul, Cena_PLN, Przebieg_km, Paliwo,
            Rok_produkcji, Pojemnosc_cm3, Moc_KM, Skrzynia_biegow, 
            Kraj_pochodzenia, Bezwypadkowy, Pierwszy_wlasciciel, Uszkodzony, 
            Miasto, Wojewodztwo, Miejsce, Czas_publikacji, Sprzedawca, 
            Nazwa_Sprzedawcy, Link, Data_Dodania, Data_Ostatniego_Widzenia,
            Status, Wazne_Od, Wazne_Do, Opis, Sprzedawca_Od, Ilosc_Powtorzen, Is_Outlier, Detale_Pobrane
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        car.get("ID_Ogloszenia"), car.get("Marka"), car.get("Model"), car.get("Tytul"), car.get("Cena_PLN"),
        car.get("Przebieg_km"), car.get("Paliwo"), car.get("Rok_produkcji"), car.get("Pojemnosc_cm3"), car.get("Moc_KM"),
        car.get("Skrzynia_biegow"), car.get("Kraj_pochodzenia"), car.get("Bezwypadkowy"), car.get("Pierwszy_wlasciciel"), 
        car.get("Uszkodzony"), car.get("Miasto"), car.get("Wojewodztwo"), car.get("Miejsce"),
        car.get("Czas_publikacji"), car.get("Sprzedawca"), car.get("Nazwa_Sprzedawcy"), car.get("Link"), car.get("Data_Dodania"),
        car.get("Data_Ostatniego_Widzenia"), car.get("Status"), car.get("Wazne_Od"), car.get("Wazne_Do"),
        car.get("Opis"), car.get("Sprzedawca_Od"), car.get("Ilosc_Powtorzen", 1), car.get("Is_Outlier", 0),
        car.get("Detale_Pobrane", 0)
    ))

POLA_DETALI = ["Bezwypadkowy", "Pierwszy_wlasciciel", "Uszkodzony", "Opis", "Sprzedawca_Od", "Nazwa_Sprzedawcy"]

def klucz_klona(car):
    """Klucz do wykrywania ponownych wystawień. Bez roku lub przebiegu nie da się wiarygodnie porównać aut
    (puste wartości zlałyby różne auta w jeden 'klon'), więc zwracamy None."""
    if car['Rok_produkcji'] is None or car['Przebieg_km'] is None:
        return None
    return (car['Tytul'], car['Rok_produkcji'], car['Przebieg_km'], car['Sprzedawca'], car['Paliwo'], car['Skrzynia_biegow'])

def zapisz_paczke_do_bazy(conn, paczka, dzisiaj, stats, mem_db):
    if not paczka: return
    cursor = conn.cursor()
    
    for car in paczka:
        car_id = car["ID_Ogloszenia"]
        key = klucz_klona(car)

        if car_id in mem_db["ids"]:
            stara_cena = mem_db["ids"][car_id]["cena"]
            nowa_cena = car["Cena_PLN"]

            if nowa_cena is not None and stara_cena is not None and stara_cena != nowa_cena:
                # Nowy wiersz dziedziczy detale z zamykanego, żeby faza 2 nie pobierała podstrony drugi raz
                cursor.execute(f"""
                    SELECT {", ".join(POLA_DETALI)}, Detale_Pobrane FROM otomoto_scd2
                    WHERE ID_Ogloszenia = ? AND Wazne_Do = ?
                """, (car_id, NIESKONCZONOSC))
                stary = cursor.fetchone()
                if stary:
                    for pole, wartosc in zip(POLA_DETALI, stary):
                        car[pole] = car.get(pole) or wartosc
                    car["Detale_Pobrane"] = stary[-1]

                cursor.execute("""
                    UPDATE otomoto_scd2
                    SET Wazne_Do = ?, Data_Ostatniego_Widzenia = ?
                    WHERE ID_Ogloszenia = ? AND Wazne_Do = ?
                """, (dzisiaj, dzisiaj, car_id, NIESKONCZONOSC))
                
                car["Data_Dodania"] = mem_db["ids"][car_id]["data_dodania"]
                car["Data_Ostatniego_Widzenia"] = dzisiaj
                car["Status"] = "Aktywne"
                car["Wazne_Od"] = dzisiaj
                car["Wazne_Do"] = NIESKONCZONOSC
                car["Ilosc_Powtorzen"] = mem_db["ids"][car_id]["ilosc_powtorzen"]
                wstaw_nowy_rekord(cursor, car)
                
                mem_db["ids"][car_id]["cena"] = nowa_cena
                stats["zmiana_ceny"] += 1
            else:
                cursor.execute("""
                    UPDATE otomoto_scd2
                    SET Data_Ostatniego_Widzenia = ?, Miejsce = ?, Miasto = ?, Wojewodztwo = ?
                    WHERE ID_Ogloszenia = ? AND Wazne_Do = ?
                """, (dzisiaj, car["Miejsce"], car["Miasto"], car["Wojewodztwo"], car_id, NIESKONCZONOSC))

        else:
            # Nowe ID. Jeśli to ponowne wystawienie znanego auta (klon), zapisujemy je jako NOWY wiersz, który
            # dziedziczy Data_Dodania i licznik powtórzeń. Starego wiersza nie ruszamy: jeśli ogłoszenie zniknęło,
            # zamknie je krok końcowy, a jeśli oba są aktywne, każde ma własną historię (bez przepisywania ID).
            stare_id = mem_db["klony"].get(key) if key else None
            if stare_id and stare_id in mem_db["ids"]:
                stare = mem_db["ids"][stare_id]
                car["Data_Dodania"] = stare["data_dodania"]
                car["Ilosc_Powtorzen"] = stare["ilosc_powtorzen"] + 1
                stats["wykryte_klony"] += 1
            else:
                car["Data_Dodania"] = dzisiaj
                car["Ilosc_Powtorzen"] = 1
                stats["otwarte"] += 1
            
            car["Data_Ostatniego_Widzenia"] = dzisiaj
            car["Status"] = "Aktywne"
            car["Wazne_Od"] = dzisiaj
            car["Wazne_Do"] = NIESKONCZONOSC
            wstaw_nowy_rekord(cursor, car)

            mem_db["ids"][car_id] = {"cena": car["Cena_PLN"], "data_dodania": car["Data_Dodania"], "ilosc_powtorzen": car["Ilosc_Powtorzen"]}
            if key:
                mem_db["klony"][key] = car_id

    conn.commit()

def skanuj_kategorie_wielowatkowo(konfiguracja, conn):
    nazwa_kategorii = konfiguracja["nazwa"]
    base_url = konfiguracja["url_bazowy"]

    logging.info(f"[FAZA 1] SZYBKIE SKANOWANIE: {nazwa_kategorii.upper()}")
    mem_db = pobierz_indeks_aut(conn)
    dzisiaj = datetime.now().strftime("%Y-%m-%d %H:%M:%S") 
    
    seen_in_this_run = set()
    paczka_buffer = []
    stats = {"otwarte": 0, "zmiana_ceny": 0, "wykryte_klony": 0}
    
    aktualna_strona = 1
    koniec_wynikow = False
    nieudane_strony = []
    aktywne_przed = len(mem_db["ids"])

    while aktualna_strona <= MAX_STRON and not koniec_wynikow:
        ostatnia_w_paczce = min(aktualna_strona + STRON_W_PACZCE - 1, MAX_STRON)
        logging.info(f"Faza 1: Kolejkuję zlecenia pobierania stron od {aktualna_strona} do {ostatnia_w_paczce}...")
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_FAZA_1) as executor:
            futures = {}
            for p in range(aktualna_strona, ostatnia_w_paczce + 1):
                url = f"{base_url}&page={p}"
                futures[executor.submit(pobierz_strone_cffi, url, p)] = p
            
            for future in as_completed(futures):
                page_num, cars_on_page = future.result()
                if cars_on_page is None:
                    logging.warning(f"Strona {page_num}: nie udało się pobrać po {MAX_PROB_NA_STRONE} próbach.")
                    nieudane_strony.append(page_num)
                    continue
                if not cars_on_page:
                    logging.info(f"Strona {page_num} jest pusta.")
                    koniec_wynikow = True
                    continue

                for car in cars_on_page:
                    car_id = str(car.get("id"))
                    if car_id in seen_in_this_run: continue
                    paczka_buffer.append(wyciagnij_dane_ogloszenia(car))
                    seen_in_this_run.add(car_id)

        if paczka_buffer:
            zapisz_paczke_do_bazy(conn, paczka_buffer, dzisiaj, stats, mem_db)
            logging.info(f"[DB] Zapisano blok {len(paczka_buffer)} ogłoszeń do SQLite.")
            paczka_buffer.clear()

        time.sleep(random.uniform(2.0, 4.0))
        aktualna_strona = ostatnia_w_paczce + 1

    zamkniete_ogloszenia = 0
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM otomoto_scd2
        WHERE Data_Ostatniego_Widzenia != ? AND Wazne_Do = ? AND Status = 'Aktywne'
    """, (dzisiaj, NIESKONCZONOSC))
    do_zamkniecia = cursor.fetchone()[0]

    # Zamykamy "niewidziane" ogłoszenia tylko po kompletnym skanie. Inaczej blokada, zmiana struktury strony
    # albo limit stron Otomoto zamknęłyby naraz wszystkie aktywne ogłoszenia i zepsuły historię SCD2.
    powod_pominiecia = None
    if nieudane_strony:
        powod_pominiecia = f"nie pobrano stron: {sorted(nieudane_strony)[:10]}"
    elif not koniec_wynikow:
        powod_pominiecia = f"skan nie dotarł do końca wyników (limit {MAX_STRON} stron)"
    elif not seen_in_this_run:
        powod_pominiecia = "skan nie zwrócił żadnych ogłoszeń (blokada lub zmiana struktury strony?)"
    elif aktywne_przed >= MIN_AKTYWNYCH_DLA_BEZPIECZNIKA and do_zamkniecia > MAX_ZAMKNIEC_PROC * aktywne_przed:
        powod_pominiecia = (f"do zamknięcia {do_zamkniecia} z {aktywne_przed} aktywnych "
                            f"(> {MAX_ZAMKNIEC_PROC:.0%}), to wygląda na niepełny skan")

    if powod_pominiecia:
        logging.error(f"[FAZA 1] Pomijam zamykanie ogłoszeń: {powod_pominiecia}.")
    else:
        cursor.execute("""
            INSERT INTO otomoto_scd2 (
                ID_Ogloszenia, Marka, Model, Tytul, Cena_PLN, Przebieg_km, Paliwo,
                Rok_produkcji, Pojemnosc_cm3, Moc_KM, Skrzynia_biegow, 
                Kraj_pochodzenia, Bezwypadkowy, Pierwszy_wlasciciel, Uszkodzony, 
                Miasto, Wojewodztwo, Miejsce, Czas_publikacji, Sprzedawca, 
                Nazwa_Sprzedawcy, Link, Data_Dodania, Data_Ostatniego_Widzenia,
                Status, Wazne_Od, Wazne_Do, Opis, Sprzedawca_Od, Ilosc_Powtorzen, Is_Outlier, Detale_Pobrane
            )
            SELECT 
                ID_Ogloszenia, Marka, Model, Tytul, Cena_PLN, Przebieg_km, Paliwo,
                Rok_produkcji, Pojemnosc_cm3, Moc_KM, Skrzynia_biegow, 
                Kraj_pochodzenia, Bezwypadkowy, Pierwszy_wlasciciel, Uszkodzony, 
                Miasto, Wojewodztwo, Miejsce, Czas_publikacji, Sprzedawca, 
                Nazwa_Sprzedawcy, Link, Data_Dodania, ?,
                'Zakończone', ?, ?, Opis, Sprzedawca_Od, Ilosc_Powtorzen, Is_Outlier, 1
            FROM otomoto_scd2
            WHERE Data_Ostatniego_Widzenia != ? AND Wazne_Do = ? AND Status = 'Aktywne'
        """, (dzisiaj, dzisiaj, NIESKONCZONOSC, dzisiaj, NIESKONCZONOSC))
        zamkniete_ogloszenia = cursor.rowcount

        cursor.execute("""
            UPDATE otomoto_scd2 
            SET Wazne_Do = ?
            WHERE Data_Ostatniego_Widzenia != ? AND Wazne_Do = ? AND Status = 'Aktywne'
        """, (dzisiaj, dzisiaj, NIESKONCZONOSC))
    conn.commit()

    logging.info(f"== PODSUMOWANIE FAZY 1 ==")
    logging.info(f" -> Nowe auta: {stats['otwarte']}")
    logging.info(f" -> Ilość powtórzonych wystawień (Klony): {stats['wykryte_klony']}")
    logging.info(f" -> Zmiany cen (SCD2): {stats['zmiana_ceny']}")
    logging.info(f" -> Ogłoszenia Zakończone: {zamkniete_ogloszenia}")
    logging.info(f" -> Aktualnie aktywnych w bazie: {len(mem_db['ids'])}")

def pobierz_detale_z_linku(url):
    time.sleep(random.uniform(0.5, 1.5))
    session = get_session()
    
    for proba in range(1, MAX_PROB_NA_STRONE + 1):
        try:
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                soup = bezpeiczny_soup(response.text)
                detale = {}
                
                mapowanie_testids = {
                    "no_accident": "bezwypadkowy",
                    "original_owner": "pierwszy_wlasciciel",
                    "first_owner": "pierwszy_wlasciciel", 
                    "damaged": "uszkodzony"
                }
                
                for testid, klucz_w_detalach in mapowanie_testids.items():
                    div_element = soup.find('div', {'data-testid': testid})
                    if div_element:
                        p_tags = div_element.find_all(['p', 'a', 'span'])
                        for p in reversed(p_tags):
                            tekst = p.text.strip()
                            if tekst in ["Tak", "Nie"]:
                                detale[klucz_w_detalach] = tekst
                                break
                                
                seller_header = soup.find('div', {'data-testid': 'seller-header'})
                if seller_header:
                    p_bold = seller_header.find('p', class_=lambda c: c and 'font-bold' in c)
                    if p_bold:
                        detale["nazwa_sprzedawcy"] = p_bold.text.strip()

                wszystkie_teksty = soup.find_all(['p', 'span'])
                for p_tag in wszystkie_teksty:
                    etykieta = p_tag.text.strip()
                    if "Sprzedający na OTOMOTO od" in etykieta and "sprzedawca_od" not in detale:
                        detale["sprzedawca_od"] = etykieta.replace("Sprzedający na OTOMOTO od", "").strip()
                    elif "Pierwszy właściciel" in etykieta and "pierwszy_wlasciciel" not in detale:
                        parent = p_tag.parent.parent
                        if parent:
                            words = parent.get_text(separator='|').split('|')
                            for word in reversed(words):
                                if word.strip() in ["Tak", "Nie"]:
                                    detale["pierwszy_wlasciciel"] = word.strip()
                                    break
                    elif "Bezwypadkowy" in etykieta and "bezwypadkowy" not in detale:
                        parent = p_tag.parent.parent
                        if parent:
                            words = parent.get_text(separator='|').split('|')
                            for word in reversed(words):
                                if word.strip() in ["Tak", "Nie"]:
                                    detale["bezwypadkowy"] = word.strip()
                                    break

                text_wrapper = soup.find('div', {'data-testid': 'textWrapper'})
                if text_wrapper:
                    detale["opis"] = text_wrapper.get_text(separator='\n').strip()

                script_tag = soup.find('script', id='__NEXT_DATA__')
                if script_tag:
                    try:
                        data = json.loads(script_tag.string)
                        advert = data.get("props", {}).get("pageProps", {}).get("advert", {})
                        
                        for p in advert.get("parameters", []):
                            if isinstance(p, dict):
                                key = p.get("key")
                                val = p.get("displayValue")
                                if key == "no_accident" and "bezwypadkowy" not in detale: 
                                    detale["bezwypadkowy"] = val
                                if key in ["first_owner", "original_owner"] and "pierwszy_wlasciciel" not in detale: 
                                    detale["pierwszy_wlasciciel"] = val
                                if key == "damaged" and "uszkodzony" not in detale: 
                                    detale["uszkodzony"] = val
                        
                        if "opis" not in detale and advert.get("description"):
                            html_opis = advert.get("description")
                            detale["opis"] = bezpeiczny_soup(html_opis).get_text(separator='\n').strip()
                            
                        if "sprzedawca_od" not in detale:
                            seller_badges = advert.get("seller", {}).get("featuresBadges", [])
                            for badge in seller_badges:
                                if badge.get("code") == "registration-date":
                                    detale["sprzedawca_od"] = badge.get("label", "").replace("Sprzedający na OTOMOTO od", "").strip()
                    except Exception: 
                        pass
                        
                return detale 
                
            elif response.status_code in (404, 410):
                logging.info(f"[FAZA 2] Ogłoszenie wygasło ({response.status_code}): {url}")
                return "WYGASLO"
                
            elif response.status_code in (403, 429):
                logging.warning(f"[FAZA 2] Blokada {response.status_code} na podstronie. Zmieniam VPN...")
                rotuj_nordvpn_system()
            else:
                logging.warning(f"[FAZA 2] Dziwny status {response.status_code} dla {url}")
                
        except Exception as e:
            logging.debug(f"Cichy błąd HTTP dla strony (Faza 2): {e}")
            
        time.sleep(3 * proba)
    return None

def faza_2_uzupelnij_detale(conn):
    dzisiaj = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    while True:
        cursor = conn.cursor()
        # KLUCZOWA ZMIANA: Zamiast szukać NULLi w nieskończoność, skrypt bierze tylko te auta, których jeszcze nie sprawdził (Detale_Pobrane = 0)
        cursor.execute("""
            SELECT ID_Ogloszenia, Link, Bezwypadkowy, Pierwszy_wlasciciel, Uszkodzony, Opis, Sprzedawca_Od, Nazwa_Sprzedawcy
            FROM otomoto_scd2 
            WHERE Status = 'Aktywne' 
            AND Wazne_Do = ?
            AND Detale_Pobrane = 0
            AND Link IS NOT NULL
            LIMIT ?
        """, (NIESKONCZONOSC, LIMIT_FAZY_2))
        
        do_poprawy = cursor.fetchall()
        if not do_poprawy:
            logging.info("[FAZA 2] Wszystkie aktywne ogłoszenia mają pobrane detale! (Koniec pracy).")
            break

        logging.info(f"\n[FAZA 2] Rozpoczynam wielowątkowe skanowanie paczki {len(do_poprawy)} aut (Wątki: {MAX_WORKERS_FAZA_2})...")
        
        uzupelnione = 0
        wygasle = 0
        nieudane = 0
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_FAZA_2) as executor:
            futures = {executor.submit(pobierz_detale_z_linku, row[1]): row for row in do_poprawy}
            
            for future in as_completed(futures):
                row = futures[future]
                car_id, link, b_db, p_db, u_db, o_db, s_db, ns_db = row
                detale = future.result()
                
                if detale == "WYGASLO":
                    cursor.execute("""
                        UPDATE otomoto_scd2
                        SET Status = 'Zakończone', Wazne_Do = ?, Detale_Pobrane = 1
                        WHERE ID_Ogloszenia = ? AND Wazne_Do = ?
                    """, (dzisiaj, car_id, NIESKONCZONOSC))
                    wygasle += 1
                
                elif detale is not None:
                    nowy_b = normalize_nulls(detale.get("bezwypadkowy"))
                    nowy_p = normalize_nulls(detale.get("pierwszy_wlasciciel"))
                    nowy_u = normalize_nulls(detale.get("uszkodzony"))
                    nowy_o = normalize_nulls(detale.get("opis"))
                    nowy_s = normalize_nulls(detale.get("sprzedawca_od"))
                    nowy_ns = normalize_nulls(detale.get("nazwa_sprzedawcy"))
                    
                    final_b = nowy_b if nowy_b else b_db
                    final_p = nowy_p if nowy_p else p_db
                    final_u = nowy_u if nowy_u else u_db
                    final_o = nowy_o if nowy_o else o_db
                    final_s = nowy_s if nowy_s else s_db
                    final_ns = nowy_ns if nowy_ns else ns_db
                    
                    # KLUCZOWA ZMIANA: Zawsze oznaczamy auto jako odwiedzone (Detale_Pobrane = 1)
                    cursor.execute("""
                        UPDATE otomoto_scd2
                        SET Bezwypadkowy = ?, Pierwszy_wlasciciel = ?, Uszkodzony = ?, 
                            Opis = ?, Sprzedawca_Od = ?, Nazwa_Sprzedawcy = ?, Detale_Pobrane = 1
                        WHERE ID_Ogloszenia = ? AND Wazne_Do = ?
                    """, (final_b, final_p, final_u, final_o, final_s, final_ns, car_id, NIESKONCZONOSC))
                    uzupelnione += 1
                else:
                    # Nie nadpisujemy Opis tekstem błędu. Licznik prób: po MAX_PROB_DETALI porażkach odpuszczamy.
                    cursor.execute("""
                        UPDATE otomoto_scd2
                        SET Proby_Detali = COALESCE(Proby_Detali, 0) + 1,
                            Detale_Pobrane = CASE WHEN COALESCE(Proby_Detali, 0) + 1 >= ? THEN 1 ELSE 0 END
                        WHERE ID_Ogloszenia = ? AND Wazne_Do = ?
                    """, (MAX_PROB_DETALI, car_id, NIESKONCZONOSC))
                    nieudane += 1

        conn.commit()
        logging.info(f"[FAZA 2] Paczka zakończona! Uzupełniono {uzupelnione} aut. Oznaczono jako wygasłe: {wygasle}. Nieudane (do ponowienia): {nieudane}.")
        time.sleep(5)

def main():
    logging.info("Rozpoczynamy system monitoringu Otomoto (Architektura Dwufazowa, SCD2, VPN)...\n")
    conn = zainicjalizuj_baze()
    try:
        for zadanie in ZADANIA_SCRAPOWANIA:
            skanuj_kategorie_wielowatkowo(zadanie, conn)
            
        faza_2_uzupelnij_detale(conn)
    finally:
        conn.close()
    
    print("\n" + "="*15)
    print("RAPORT SIECIOWY I BLOKADY")
    print("="*15)
    if statystyki_blokad["ilosc_blokad"] == 0:
        print(" [OK] Pełny sukces bez żadnych blokad ze strony Cloudflare.")
    else:
        print(f" -> Napotkane blokady (403/429/Cloudflare): {statystyki_blokad['ilosc_blokad']}")
        print(f" -> Wymuszone rotacje adresów IP VPN: {statystyki_blokad['ilosc_rotacji_vpn']}")
    print("="*15 + "\n")
    logging.info("Wszystkie zadania z kolejki zostały pomyślnie zakończone!")

if __name__ == "__main__":
    main()
