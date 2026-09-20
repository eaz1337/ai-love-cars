import json
import time
import random
import urllib.parse
from datetime import datetime
import sqlite3
import logging
import subprocess
import threading
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

# 1. Lista marek do pobrania
WYBRANE_MARKI = [
    "alfa-romeo", "alpina", "alpine", "aston-martin", "audi", "bentley",
    "bmw", "cadillac", "chevrolet", "chrysler", "dodge", "ferrari", "ford",
    "honda", "hyundai", "infiniti", "jaguar", "jeep", "kia", "lamborghini",
    "land-rover", "lexus", "lotus", "maserati", "mclaren", "mercedes-benz",
    "mitsubishi", "nissan", "porsche", "rolls-royce"
]

# 2. Generator linku
def generuj_url_otomoto(marki):
    """
    Funkcja generuje poprawny link Otomoto na podstawie podanej listy marek.
    """
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

# 3. Definicja zadania z użyciem generatora
ZADANIA_SCRAPOWANIA = [
    {
        "nazwa": "Wybrane_Marki_Prywatne",
        "url_bazowy": generuj_url_otomoto(WYBRANE_MARKI),
    }
]

DB_NAME = "samochody.db"

statystyki_blokad = {"ilosc_blokad": 0, "ilosc_rotacji_vpn": 0}
vpn_lock = threading.Lock()

# Zmienna globalna przechowująca sesje dla każdego wątku
thread_local = threading.local()

MAX_WORKERS_FAZA_1 = 6
MAX_WORKERS_FAZA_2 = 8  # Zwiększone do 8 dzięki optymalizacji sesji

STRON_W_PACZCE = 20  
MAX_STRON = 4000
MAX_PROB_NA_STRONE = 3
LIMIT_FAZY_2 = 500  

NIESKONCZONOSC = '9999-12-31 23:59:59'

def get_session():
    """Funkcja tworzy sesję curl_cffi przypisaną do konkretnego wątku.
       Zapewnia to utrzymanie połączenia Keep-Alive i omija ciągłe TLS Handshake."""
    if not hasattr(thread_local, "session"):
        thread_local.session = cffi_requests.Session(impersonate="chrome120")
    return thread_local.session

# ==============================================================================
# BAZA DANYCH SQLITE
# ==============================================================================
def zainicjalizuj_baze():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS otomoto_scd2 (
            ID_Wersji INTEGER PRIMARY KEY AUTOINCREMENT,
            ID_Ogloszenia TEXT,
            Marka TEXT, Model TEXT, Tytul TEXT,
            Cena_PLN REAL, Przebieg_km INTEGER, Paliwo TEXT,
            Rok_produkcji INTEGER, Pojemnosc_cm3 INTEGER, Moc_KM INTEGER,
            Skrzynia_biegow TEXT, 
            Kraj_pochodzenia TEXT, Bezwypadkowy TEXT, Pierwszy_wlasciciel TEXT,
            Uszkodzony TEXT, Miejsce TEXT, Czas_publikacji TEXT,
            Sprzedawca TEXT, Nazwa_Sprzedawcy TEXT, Link TEXT,
            Data_Dodania TEXT,
            Data_Ostatniego_Widzenia TEXT,
            Status TEXT,
            Wazne_Od TEXT,
            Wazne_Do TEXT,
            Opis TEXT,
            Sprzedawca_Od TEXT,
            Ilosc_Powtorzen INTEGER DEFAULT 1
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_aktualne ON otomoto_scd2 (ID_Ogloszenia, Wazne_Do)")
    
    try:
        cursor.execute("ALTER TABLE otomoto_scd2 ADD COLUMN Nazwa_Sprzedawcy TEXT DEFAULT 'Brak'")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE otomoto_scd2 ADD COLUMN Ilosc_Powtorzen INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass

    # Przyspieszenie zapisu bazy danych (zapobiega blokowaniu przy wielu zapisach)
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
        mem_db["ids"][car_id] = {
            "cena": cena,
            "data_dodania": data_dod,
            "ilosc_powtorzen": ilosc_powt if ilosc_powt else 1
        }
        key = (tytul, rok, przebieg, sprzedawca, paliwo, skrzynia)
        mem_db["klony"][key] = car_id
        
    return mem_db

def clean_int(val):
    if not val or val == "Brak": return None
    v = str(val).replace(' ', '').replace('km', '').replace('cm3', '').replace('KM', '')
    try: return int(v)
    except ValueError: return None

def clean_float(val):
    if not val or val == "Brak": return None
    v = str(val).replace(' ', '').replace(',', '.')
    try: return float(v)
    except ValueError: return None

def parse_otomoto_json(html_content):
    cars = []
    # OPTYMALIZACJA: uzywamy 'lxml' zamiast 'html.parser'
    soup = BeautifulSoup(html_content, 'lxml')
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
    seller_name = seller.get("name", "Osoba prywatna") if isinstance(seller, dict) else "Osoba prywatna"
    price_node = car.get("price") or {}
    price = clean_float(price_node.get("amount", {}).get("value") or price_node.get("value"))

    params = {}
    for p in car.get("parameters", []):
        if isinstance(p, dict):
            val = p.get("displayValue")
            if p.get("key"): params[p.get("key")] = val
            if p.get("label"): params[p.get("label").strip().lower()] = val

    location_node = car.get("location")
    if not isinstance(location_node, dict):
        location_node = car.get("seller", {}).get("location", {})

    city_data = location_node.get("city")
    city = city_data.get("name", "") if isinstance(city_data, dict) else str(city_data or "")
    region_data = location_node.get("region") or location_node.get("province")
    region = region_data.get("name", "") if isinstance(region_data, dict) else str(region_data or "")

    city = city.strip()
    region = region.strip()
    miejsce = f"{city} ({region})" if city and region else (city or region or "Brak")

    pierwszy_wlasciciel = params.get("original_owner") or params.get("first_owner") or params.get("pierwszy właściciel", "Brak")

    return {
        "ID_Ogloszenia": str(car.get("id")),
        "Marka": params.get("make", "Brak"),
        "Model": params.get("model", "Brak"),
        "Tytul": car.get("title", "Brak"),
        "Cena_PLN": price,
        "Przebieg_km": clean_int(params.get("mileage") or params.get("przebieg")),
        "Paliwo": params.get("fuel_type") or params.get("rodzaj paliwa") or "Brak",
        "Rok_produkcji": clean_int(params.get("year") or params.get("rok produkcji")),
        "Pojemnosc_cm3": clean_int(params.get("engine_capacity") or params.get("pojemność skokowa")),
        "Moc_KM": clean_int(params.get("engine_power") or params.get("moc")),
        "Skrzynia_biegow": params.get("gearbox") or params.get("skrzynia biegów", "Brak"),
        "Kraj_pochodzenia": params.get("country_origin") or params.get("kraj pochodzenia", "Brak"),
        "Bezwypadkowy": params.get("no_accident") or params.get("bezwypadkowy", "Brak"),
        "Pierwszy_wlasciciel": pierwszy_wlasciciel,
        "Uszkodzony": params.get("damaged") or params.get("uszkodzony", "Brak"),
        "Miejsce": miejsce,
        "Czas_publikacji": car.get("createdAt", "Brak"),
        "Sprzedawca": seller_name.strip(),
        "Nazwa_Sprzedawcy": seller_name.strip(), 
        "Link": car.get("url", "Brak"),
        "Opis": "Brak",
        "Sprzedawca_Od": "Brak"
    }

def rotuj_nordvpn_system():
    if vpn_lock.acquire(blocking=False):
        try:
            logging.warning("System: Inicjowanie zmiany serwera NordVPN (CLI)...")
            statystyki_blokad["ilosc_rotacji_vpn"] += 1
            subprocess.run(["nordvpn", "connect", "Poland"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(6)
            logging.info("System: Nowy serwer NordVPN przypisany. Wznawiam ruch.")
            
            # Restartujemy sesje po zmianie IP, żeby nie wisiały
            global thread_local
            thread_local = threading.local()
        finally:
            vpn_lock.release()
    else:
        time.sleep(6)

def pobierz_strone_cffi(url, page_num):
    # OPTYMALIZACJA: re-używanie sesji z Keep-Alive
    session = get_session()
    
    for proba in range(1, MAX_PROB_NA_STRONE + 1):
        try:
            response = session.get(url, timeout=15)
            if response.status_code == 200:
                cars = parse_otomoto_json(response.text)
                if not cars and "cloudflare" in response.text.lower():
                    logging.warning(f"Strona {page_num}: Zatrzymane przez Cloudflare (Próba {proba}).")
                    statystyki_blokad["ilosc_blokad"] += 1
                    rotuj_nordvpn_system()
                else:
                    return page_num, cars
            elif response.status_code in (403, 429):
                logging.warning(f"Strona {page_num}: BLOKADA ({response.status_code}) (Próba {proba}).")
                statystyki_blokad["ilosc_blokad"] += 1
                rotuj_nordvpn_system()
        except Exception:
            pass
        time.sleep(3 * proba)
    return page_num, None

def wstaw_nowy_rekord(cursor, car):
    cursor.execute("""
        INSERT INTO otomoto_scd2 (
            ID_Ogloszenia, Marka, Model, Tytul, Cena_PLN, Przebieg_km, Paliwo,
            Rok_produkcji, Pojemnosc_cm3, Moc_KM, Skrzynia_biegow, 
            Kraj_pochodzenia, Bezwypadkowy, Pierwszy_wlasciciel, Uszkodzony, Miejsce,
            Czas_publikacji, Sprzedawca, Nazwa_Sprzedawcy, Link, Data_Dodania, Data_Ostatniego_Widzenia,
            Status, Wazne_Od, Wazne_Do, Opis, Sprzedawca_Od, Ilosc_Powtorzen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        car["ID_Ogloszenia"], car["Marka"], car["Model"], car["Tytul"], car["Cena_PLN"],
        car["Przebieg_km"], car["Paliwo"], car["Rok_produkcji"], car["Pojemnosc_cm3"], car["Moc_KM"],
        car["Skrzynia_biegow"], car["Kraj_pochodzenia"],
        car["Bezwypadkowy"], car["Pierwszy_wlasciciel"], car["Uszkodzony"], car["Miejsce"],
        car["Czas_publikacji"], car["Sprzedawca"], car.get("Nazwa_Sprzedawcy", "Brak"), car["Link"], car["Data_Dodania"],
        car["Data_Ostatniego_Widzenia"], car["Status"], car["Wazne_Od"], car["Wazne_Do"],
        car.get("Opis", "Brak"), car.get("Sprzedawca_Od", "Brak"), car.get("Ilosc_Powtorzen", 1)
    ))

def zapisz_paczke_do_bazy(conn, paczka, dzisiaj, stats, mem_db):
    if not paczka: return
    cursor = conn.cursor()
    
    for car in paczka:
        car_id = car["ID_Ogloszenia"]
        key = (car['Tytul'], car['Rok_produkcji'], car['Przebieg_km'], car['Sprzedawca'], car['Paliwo'], car['Skrzynia_biegow'])

        if car_id in mem_db["ids"]:
            stara_cena = mem_db["ids"][car_id]["cena"]
            nowa_cena = car["Cena_PLN"]

            if nowa_cena is not None and stara_cena is not None and stara_cena != nowa_cena:
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
                    SET Data_Ostatniego_Widzenia = ?, Miejsce = ?
                    WHERE ID_Ogloszenia = ? AND Wazne_Do = ?
                """, (dzisiaj, car["Miejsce"], car_id, NIESKONCZONOSC))

        else:
            stare_id = mem_db["klony"].get(key)
            if stare_id and stare_id in mem_db["ids"]:
                cursor.execute("""
                    UPDATE otomoto_scd2
                    SET Data_Ostatniego_Widzenia = ?,
                        Ilosc_Powtorzen = Ilosc_Powtorzen + 1,
                        ID_Ogloszenia = ?,
                        Link = ?,
                        Cena_PLN = ?
                    WHERE ID_Ogloszenia = ? AND Wazne_Do = ?
                """, (dzisiaj, car_id, car["Link"], car["Cena_PLN"], stare_id, NIESKONCZONOSC))
                
                mem_data = mem_db["ids"].pop(stare_id)
                mem_data["ilosc_powtorzen"] += 1
                mem_data["cena"] = car["Cena_PLN"]
                mem_db["ids"][car_id] = mem_data
                mem_db["klony"][key] = car_id
                stats["wykryte_klony"] += 1
                continue
            else:
                car["Data_Dodania"] = dzisiaj
                car["Data_Ostatniego_Widzenia"] = dzisiaj
                car["Status"] = "Aktywne"
                car["Wazne_Od"] = dzisiaj
                car["Wazne_Do"] = NIESKONCZONOSC
                car["Ilosc_Powtorzen"] = 1
                wstaw_nowy_rekord(cursor, car)

                mem_db["ids"][car_id] = {"cena": car["Cena_PLN"], "data_dodania": car["Data_Dodania"], "ilosc_powtorzen": 1}
                mem_db["klony"][key] = car_id
                stats["otwarte"] += 1

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
                if cars_on_page is None: continue
                if not cars_on_page:
                    logging.info(f"Strona {page_num} jest pusta (koniec ogłoszeń na liście).")
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

        # Tu jest pauza między seriami stron z listy, może zostać, nie psuje wydajności
        time.sleep(random.uniform(2.0, 4.0))
        aktualna_strona = ostatnia_w_paczce + 1

    cursor = conn.cursor()
    cursor.execute(f"""
        INSERT INTO otomoto_scd2 (
            ID_Ogloszenia, Marka, Model, Tytul, Cena_PLN, Przebieg_km, Paliwo,
            Rok_produkcji, Pojemnosc_cm3, Moc_KM, Skrzynia_biegow, 
            Kraj_pochodzenia, Bezwypadkowy, Pierwszy_wlasciciel, Uszkodzony, Miejsce,
            Czas_publikacji, Sprzedawca, Nazwa_Sprzedawcy, Link, Data_Dodania, Data_Ostatniego_Widzenia,
            Status, Wazne_Od, Wazne_Do, Opis, Sprzedawca_Od, Ilosc_Powtorzen
        )
        SELECT 
            ID_Ogloszenia, Marka, Model, Tytul, Cena_PLN, Przebieg_km, Paliwo,
            Rok_produkcji, Pojemnosc_cm3, Moc_KM, Skrzynia_biegow, 
            Kraj_pochodzenia, Bezwypadkowy, Pierwszy_wlasciciel, Uszkodzony, Miejsce,
            Czas_publikacji, Sprzedawca, Nazwa_Sprzedawcy, Link, Data_Dodania, ?,
            'Zakończone', ?, ?, Opis, Sprzedawca_Od, Ilosc_Powtorzen
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
    # OPTYMALIZACJA: To tutaj wątki śpią! Równolegle i niezależnie od siebie.
    time.sleep(random.uniform(0.5, 1.5))
    
    # OPTYMALIZACJA: Re-używamy połączenia dla tego wątku.
    session = get_session()
    
    for proba in range(1, MAX_PROB_NA_STRONE + 1):
        try:
            response = session.get(url, timeout=10) # Krótszy timeout, szybciej przerwie gdy zatnie proxy/vpn
            
            if response.status_code == 200:
                # OPTYMALIZACJA: Prędkość. Używamy 'lxml' do parsowania wielkiego kodu źródłowego
                soup = BeautifulSoup(response.text, 'lxml')
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
                            detale["opis"] = BeautifulSoup(html_opis, 'lxml').get_text(separator='\n').strip()
                            
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
            pass # Usunięto print dla czystości logów
            
        time.sleep(3 * proba)
    return None

def faza_2_uzupelnij_detale(conn):
    dzisiaj = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    while True:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ID_Ogloszenia, Link, Bezwypadkowy, Pierwszy_wlasciciel, Uszkodzony, Opis, Sprzedawca_Od, Nazwa_Sprzedawcy
            FROM otomoto_scd2 
            WHERE Status = 'Aktywne' 
            AND Wazne_Do = ?
            AND (Bezwypadkowy = 'Brak' OR Pierwszy_wlasciciel = 'Brak' OR Uszkodzony = 'Brak' OR Opis = 'Brak' OR Sprzedawca_Od = 'Brak' OR Nazwa_Sprzedawcy = 'Brak') 
            AND Link != 'Brak'
            LIMIT ?
        """, (NIESKONCZONOSC, LIMIT_FAZY_2))
        
        do_poprawy = cursor.fetchall()
        if not do_poprawy:
            logging.info("[FAZA 2] Wszystkie aktywne ogłoszenia mają już pobrany pełny opis i specyfikację!")
            break

        logging.info(f"\n[FAZA 2] Rozpoczynam wielowątkowe skanowanie paczki {len(do_poprawy)} aut (Wątki: {MAX_WORKERS_FAZA_2})...")
        
        uzupelnione = 0
        wygasle = 0
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_FAZA_2) as executor:
            futures = {executor.submit(pobierz_detale_z_linku, row[1]): row for row in do_poprawy}
            
            # Główna pętla programu - ODBIERA gotowe wyniki jak tylko wątek je wypluje.
            # OPTYMALIZACJA: Usunęliśmy stąd blokujące "time.sleep" 
            for future in as_completed(futures):
                row = futures[future]
                car_id, link, b_db, p_db, u_db, o_db, s_db, ns_db = row
                detale = future.result()
                
                if detale == "WYGASLO":
                    cursor.execute("""
                        UPDATE otomoto_scd2
                        SET Status = 'Zakończone', Wazne_Do = ?
                        WHERE ID_Ogloszenia = ? AND Wazne_Do = ?
                    """, (dzisiaj, car_id, NIESKONCZONOSC))
                    wygasle += 1
                
                elif detale is not None:
                    nowy_b = detale.get("bezwypadkowy")
                    nowy_p = detale.get("pierwszy_wlasciciel")
                    nowy_u = detale.get("uszkodzony")
                    nowy_o = detale.get("opis")
                    nowy_s = detale.get("sprzedawca_od")
                    nowy_ns = detale.get("nazwa_sprzedawcy")
                    
                    final_b = nowy_b if nowy_b else (b_db if b_db != 'Brak' else 'Nie podano')
                    final_p = nowy_p if nowy_p else (p_db if p_db != 'Brak' else 'Nie podano')
                    final_u = nowy_u if nowy_u else (u_db if u_db != 'Brak' else 'Nie podano')
                    final_o = nowy_o if nowy_o else (o_db if o_db != 'Brak' else 'Brak opisu')
                    final_s = nowy_s if nowy_s else (s_db if s_db != 'Brak' else 'Nie podano')
                    final_ns = nowy_ns if nowy_ns else ns_db
                    
                    cursor.execute("""
                        UPDATE otomoto_scd2
                        SET Bezwypadkowy = ?, Pierwszy_wlasciciel = ?, Uszkodzony = ?, Opis = ?, Sprzedawca_Od = ?, Nazwa_Sprzedawcy = ?
                        WHERE ID_Ogloszenia = ? AND Wazne_Do = ?
                    """, (final_b, final_p, final_u, final_o, final_s, final_ns, car_id, NIESKONCZONOSC))
                    
                    uzupelnione += 1

        conn.commit()
        logging.info(f"[FAZA 2] Paczka zakończona! Zaktualizowano {uzupelnione} aut. Oznaczono jako wygasłe: {wygasle}.")
        logging.info("Czekam 5 sekund przed wzięciem kolejnej paczki...")
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
