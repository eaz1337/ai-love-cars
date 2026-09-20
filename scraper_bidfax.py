import time
import random
import sqlite3
import logging
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from DrissionPage import ChromiumPage, ChromiumOptions

# ==============================================================================
# KONFIGURACJA SKRYPTU
# ==============================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

DB_NAME = "samochody.db"
DOMAIN = "https://en.bidfax.info/"
MAX_PAGES = 500

MARKI_DO_POBRANIA = [
    "acura", "alfa-romeo", "alpina", "alpine", 
    "aston-martin", "audi", "bentley", "bmw", 
    "cadillac", "chevrolet", "chrysler", "dodge"
]

DNI_WSTECZ_POBIERANIE = 365
DATA_GRANICZNA_POBIERANIA = datetime.now() - timedelta(days=DNI_WSTECZ_POBIERANIE)
OKNO_HISTORII_DNI = 365

MIN_ROK_PRODUKCJI = 2015
MAX_ROK_PRODUKCJI = datetime.now().year - 3

ZAKAZANE_USZKODZENIA = [
    "flood", "water", "burn", "biohazard", "chemical", 
    "missing/altered vin", "stripped", "rollover", "undercarriage"
]

# ==============================================================================
# BAZA DANYCH SQLITE
# ==============================================================================
def zainicjalizuj_baze():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bidfax_scd2 (
            ID INTEGER PRIMARY KEY AUTOINCREMENT, Marka TEXT, Tytul TEXT, VIN TEXT, Cena_USD INTEGER,
            Aukcja TEXT, Lokalizacja TEXT, Lot_Number TEXT, Condition TEXT, Damage TEXT,
            Mileage TEXT, Date_Of_Sale TEXT, Link TEXT UNIQUE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_date_of_sale ON bidfax_scd2 (Date_Of_Sale)")
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA synchronous = NORMAL;")
    conn.commit()
    return conn

def wstaw_ogloszenie(conn, car, marka):
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR IGNORE INTO bidfax_scd2 (
                Marka, Tytul, VIN, Cena_USD, Aukcja, Lokalizacja, Lot_Number, 
                Condition, Damage, Mileage, Date_Of_Sale, Link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (marka, car['Tytul'], car['VIN'], car['Cena_USD'], car['Aukcja'], car['Lokalizacja'], car['Lot_Number'], car['Condition'], car['Damage'], car['Mileage'], car['Date_Of_Sale'], car['Link']))
        conn.commit()
    except Exception as e:
        logging.error(f"Błąd zapisu do bazy: {e}")

def usun_stare_dane_retencja(conn):
    cursor = conn.cursor()
    data_graniczna = (datetime.now() - timedelta(days=OKNO_HISTORII_DNI)).strftime("%Y-%m-%d")
    cursor.execute("DELETE FROM bidfax_scd2 WHERE Date_Of_Sale < ? AND Date_Of_Sale != 'Brak'", (data_graniczna,))
    conn.commit()

# ==============================================================================
# PARSOWANIE HTML
# ==============================================================================
def wyciagnij_dane_z_html(html_content):
    cars = []
    soup = BeautifulSoup(html_content, 'html.parser')
    oferty = soup.find_all('div', class_='thumbnail offer')
    
    for oferta in oferty:
        price_tag = oferta.find('span', class_='prices')
        if not price_tag: continue
        cena_text = price_tag.text.strip()
        cena = int(cena_text) if cena_text.isdigit() else 0
        
        caption = oferta.find('div', class_='caption')
        if not caption: continue
        link_tag = caption.find('a')
        link = link_tag['href'] if link_tag else "Brak"
        tytul = link_tag.find('h2').text.strip() if link_tag and link_tag.find('h2') else "Brak"
        
        vin_match = re.search(r'vin:\s*([A-Z0-9]+)', tytul, re.IGNORECASE)
        vin = vin_match.group(1) if vin_match else "Brak"
        
        detale = {"Aukcja": "Brak", "Lokalizacja": "Brak", "Lot number": "Brak", "Condition": "Brak", "Damage": "Brak", "Mileage": "Brak", "Date of sale": "Brak"}
        
        for p in caption.find_all('p'):
            klasa = p.get('class', [])
            tekst = p.text.replace('&nbsp;', ' ').strip()
            if 'short-storyup' in klasa:
                if p.find('span', class_='iaai'): detale['Aukcja'] = 'IAAI'
                elif p.find('span', class_='copart'): detale['Aukcja'] = 'Copart'
                spans = p.find_all('span', class_='blackfont')
                if spans: detale['Lokalizacja'] = spans[-1].text.strip()
            else:
                for key in ["Lot number", "Condition", "Damage", "Mileage", "Date of sale"]:
                    if key in tekst:
                        val_span = p.find('span', class_='blackfont')
                        if val_span: detale[key] = val_span.text.strip()
                            
        cars.append({"Tytul": tytul, "VIN": vin, "Cena_USD": cena, "Aukcja": detale["Aukcja"], "Lokalizacja": detale["Lokalizacja"], "Lot_Number": detale["Lot number"], "Condition": detale["Condition"], "Damage": detale["Damage"], "Mileage": detale["Mileage"], "Date_Of_Sale": detale["Date of sale"], "Link": link})
        
    return cars

# ==============================================================================
# GŁÓWNA PĘTLA
# ==============================================================================
def main():
    logging.info("Rozpoczynam skanowanie Bidfax (Stały profil, symulacja człowieka)...")
    conn = zainicjalizuj_baze()
        
    co = ChromiumOptions()
    co.set_browser_path('/usr/bin/google-chrome')
    
    # --- POPRAWKI STABILNOŚCI I ANTY-BOT ---
    co.set_local_port(9223)
    co.set_argument('--user-data-dir=/home/eaz/Desktop/drission-profile') # Stały profil z ciasteczkami
    co.set_user_agent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
    
    co.headless(False) 
        
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--test-type') 
        
    try:
        page = ChromiumPage(co)
    except Exception as e:
        logging.error(f"Nie udało się uruchomić przeglądarki: {e}")
        return
    
    try:
        for marka in MARKI_DO_POBRANIA:
            logging.info(f"\n{'='*50}\nSKANOWANIE MARKI: {marka.upper()}\n{'='*50}")
            aktualna_strona = 1
            
            while aktualna_strona <= MAX_PAGES:
                url = f"{DOMAIN}{marka}/" if aktualna_strona == 1 else f"{DOMAIN}{marka}/page/{aktualna_strona}/"
                logging.info(f"[{marka.upper()}] Wchodzę na stronę {aktualna_strona}...")
                
                # Używamy nagłówka Referer udając, że kliknęliśmy "Następna strona"
                if aktualna_strona > 1:
                    poprzedni_url = f"{DOMAIN}{marka}/" if aktualna_strona == 2 else f"{DOMAIN}{marka}/page/{aktualna_strona - 1}/"
                    page.get(url, headers={'Referer': poprzedni_url})
                else:
                    page.get(url)
                
                # Czekamy na załadowanie elementów (ofert)
                is_loaded = page.ele('.thumbnail offer', timeout=10)
                
                if not is_loaded:
                    tytul = page.title.lower() if page.title else ""
                    if "just a moment" in tytul or "weryfikacja" in tytul or "cloudflare" in tytul or "attention required" in tytul or "403" in tytul:
                        logging.warning("⚠️ Wykryto Cloudflare lub 403! Masz 90 sekund na rozwiązanie problemu w przeglądarce...")
                        
                        weryfikacja_udana = False
                        for _ in range(45): 
                            if page.ele('.thumbnail offer', timeout=2):
                                logging.info("✅ Weryfikacja udana! Oferty widoczne.")
                                weryfikacja_udana = True
                                break
                            time.sleep(2)
                            
                        if not weryfikacja_udana:
                            logging.error(f"❌ Blokada nie ustąpiła na stronie {aktualna_strona} dla marki {marka}.")
                            break
                    else:
                        logging.info("Brak ofert i brak blokady - koniec stron dla tej marki.")
                        break
                
                # Scrollujemy w dół udając zachowanie prawdziwego użytkownika
                page.scroll.to_bottom()
                time.sleep(1.5)
                
                samochody = wyciagnij_dane_z_html(page.html)
                if not samochody: break
                    
                auta_do_bazy = []
                aukcje_zbyt_stare_licznik = 0
                odrzucone_szrot_licznik = 0
                
                for auto in samochody:
                    data_str = auto['Date_Of_Sale']
                    if data_str != "Brak":
                        try:
                            data_sprzedazy = datetime.strptime(data_str, "%d.%m.%Y")
                            auto['Date_Of_Sale'] = data_sprzedazy.strftime("%Y-%m-%d") 
                            if data_sprzedazy < DATA_GRANICZNA_POBIERANIA:
                                aukcje_zbyt_stare_licznik += 1
                                continue
                        except ValueError: pass
                    
                    if any(z in auto['Damage'].lower() for z in ZAKAZANE_USZKODZENIA):
                        odrzucone_szrot_licznik += 1
                        continue
                    
                    rok_match = re.search(r'\b(20[0-2][0-9])\b', auto['Tytul'])
                    if rok_match:
                        rok_produkcji = int(rok_match.group(1))
                        if rok_produkcji < MIN_ROK_PRODUKCJI or rok_produkcji > MAX_ROK_PRODUKCJI:
                            odrzucone_szrot_licznik += 1
                            continue
                    
                    auta_do_bazy.append(auto)

                for auto in auta_do_bazy:
                    wstaw_ogloszenie(conn, auto, marka)
                    
                logging.info(f"[{marka.upper()}] Zapisano: {len(auta_do_bazy)} | Stare: {aukcje_zbyt_stare_licznik} | Odrzucone: {odrzucone_szrot_licznik}")
                
                if len(samochody) > 0 and aukcje_zbyt_stare_licznik == len(samochody):
                    logging.info(f"Dotarto do starych aukcji. Zatrzymuję skanowanie marki {marka.upper()}.")
                    break
                
                aktualna_strona += 1
                
                # Wydłużony, losowy czas oczekiwania, żeby uniknąć blokady "403 Rate Limit"
                czas_czekania = random.uniform(3.5, 6.5)
                logging.info(f"Czekam {czas_czekania:.1f}s przed kolejną stroną...")
                time.sleep(czas_czekania)
        
        usun_stare_dane_retencja(conn)
            
    except Exception as e:
        logging.error(f"Krytyczny błąd: {e}")
    finally:
        try: page.quit()
        except: pass
        conn.close()
        logging.info("Skrypt zakończył działanie.")

if __name__ == "__main__":
    main()
