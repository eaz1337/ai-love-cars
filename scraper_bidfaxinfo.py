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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

DB_NAME = "samochody.db"
DOMAIN = "https://en.bidfax.info/"
MAX_PAGES = 500  

# --- MARKI DO POBRANIA ---
# Podzielone na 3 paczki na pierwsze wielkie pobieranie historii. 
# Odkomentuj tylko jedną na raz! Gdy pobierzesz całą historię, odkomentuj wszystkie.
# ==============================================================================

MARKI_DO_POBRANIA = [
    # --- PACZKA 1 (12 marek) ---
    "acura", "alfa-romeo", "alpina", "alpine", 
    "aston-martin", "audi", "bentley", "bmw", 
    "cadillac", "chevrolet", "chrysler", "dodge",
    
    # --- PACZKA 2 (12 marek) ---
    # "ferrari", "ford", "genesis", "gmc", 
    # "honda", "hyundai", "infiniti", "jaguar", 
    # "jeep", "kia", "lamborghini", "land-rover",
    
    # --- PACZKA 3 (13 marek) ---
    # "lexus", "lotus", "maserati", "mclaren", 
    # "mercedes-benz", "mitsubishi", "nissan", "porsche", 
    # "ram", "rolls-royce", "subaru", "toyota", "volvo"
]

# ==============================================================================
# KONFIGURACJA CZASOWA (PIPELINE)
# ==============================================================================
# 365 dla pierwszego pobierania. Do pracy codziennej (Crona) ustaw na 7.
DNI_WSTECZ_POBIERANIE = 365 
DATA_GRANICZNA_POBIERANIA = datetime.now() - timedelta(days=DNI_WSTECZ_POBIERANIE)

OKNO_HISTORII_DNI = 365 

# === NOWY FILTR WIEKU AUTA ===
MIN_ROK_PRODUKCJI = 2015 
# Najmłodsze auta mają mieć 3 lata (skrypt sam weźmie obecny rok i odejmie 3)
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
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Marka TEXT,
            Tytul TEXT,
            VIN TEXT,
            Cena_USD INTEGER,
            Aukcja TEXT,
            Lokalizacja TEXT,
            Lot_Number TEXT,
            Condition TEXT,
            Damage TEXT,
            Mileage TEXT,
            Date_Of_Sale TEXT,
            Link TEXT UNIQUE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_date_of_sale ON bidfax_scd2 (Date_Of_Sale)")
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
        """, (
            marka, car['Tytul'], car['VIN'], car['Cena_USD'], car['Aukcja'], car['Lokalizacja'],
            car['Lot_Number'], car['Condition'], car['Damage'], car['Mileage'], 
            car['Date_Of_Sale'], car['Link']
        ))
        conn.commit()
    except Exception as e:
        logging.error(f"Błąd zapisu do bazy: {e}")

def usun_stare_dane_retencja(conn):
    cursor = conn.cursor()
    data_graniczna_usuwania = (datetime.now() - timedelta(days=OKNO_HISTORII_DNI)).strftime("%Y-%m-%d")
    
    logging.info(f"Rozpoczynam czyszczenie bazy (usuwanie aut sprzed {data_graniczna_usuwania})...")
    cursor.execute("""
        DELETE FROM bidfax_scd2 
        WHERE Date_Of_Sale < ? AND Date_Of_Sale != 'Brak'
    """, (data_graniczna_usuwania,))
    
    usuniete = cursor.rowcount
    conn.commit()
    logging.info(f"Usunięto {usuniete} przeterminowanych aukcji. Baza utrzymuje równo {OKNO_HISTORII_DNI} dni historii.")

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
        
        detale = {
            "Aukcja": "Brak", "Lokalizacja": "Brak", "Lot number": "Brak",
            "Condition": "Brak", "Damage": "Brak", "Mileage": "Brak", "Date of sale": "Brak"
        }
        
        p_tags = caption.find_all('p')
        for p in p_tags:
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
                            
        cars.append({
            "Tytul": tytul,
            "VIN": vin,
            "Cena_USD": cena,
            "Aukcja": detale["Aukcja"],
            "Lokalizacja": detale["Lokalizacja"],
            "Lot_Number": detale["Lot number"],
            "Condition": detale["Condition"],
            "Damage": detale["Damage"],
            "Mileage": detale["Mileage"],
            "Date_Of_Sale": detale["Date of sale"],
            "Link": link
        })
        
    return cars

# ==============================================================================
# GŁÓWNA PĘTLA
# ==============================================================================
def main():
    logging.info(f"Filtry: Rocznik od {MIN_ROK_PRODUKCJI} do {MAX_ROK_PRODUKCJI} | Pobieram z ostatnich {DNI_WSTECZ_POBIERANIE} dni")
    
    conn = zainicjalizuj_baze()
    co = ChromiumOptions()
    co.set_browser_path('/usr/bin/chromium-browser')
    # co.headless() 

    page = ChromiumPage(co)
    
    try:
        for marka in MARKI_DO_POBRANIA:
            logging.info(f"\n{'='*50}\nSKANOWANIE MARKI: {marka.upper()}\n{'='*50}")
            aktualna_strona = 1
            
            while aktualna_strona <= MAX_PAGES:
                url = f"{DOMAIN}{marka}/" if aktualna_strona == 1 else f"{DOMAIN}{marka}/page/{aktualna_strona}/"
                    
                logging.info(f"[{marka.upper()}] Strona {aktualna_strona}...")
                page.get(url)
                
                is_loaded = page.ele('.thumbnail offer', timeout=15)
                
                if not is_loaded:
                    if "Just a moment" in page.title or "Weryfikacja" in page.title:
                        logging.warning("Cloudflare sprawdza połączenie. Czekam 10s...")
                        time.sleep(10)
                        if not page.ele('.thumbnail offer', timeout=5):
                            logging.error(f"Zablokowano na Cloudflare dla marki {marka}.")
                            break
                    else:
                        break
                
                html_source = page.html
                samochody = wyciagnij_dane_z_html(html_source)
                
                if not samochody: break
                    
                auta_do_bazy = []
                aukcje_zbyt_stare_licznik = 0
                odrzucone_szrot_licznik = 0
                
                for auto in samochody:
                    # 1. STANDARYZACJA I FILTR DATY (Zmiana DD.MM.YYYY -> YYYY-MM-DD)
                    data_str = auto['Date_Of_Sale']
                    if data_str != "Brak":
                        try:
                            data_sprzedazy = datetime.strptime(data_str, "%d.%m.%Y")
                            auto['Date_Of_Sale'] = data_sprzedazy.strftime("%Y-%m-%d") 
                            
                            if data_sprzedazy < DATA_GRANICZNA_POBIERANIA:
                                aukcje_zbyt_stare_licznik += 1
                                continue
                        except ValueError:
                            pass
                    
                    # 2. FILTR USZKODZEŃ
                    typ_uszkodzenia = auto['Damage'].lower()
                    if any(zakazane in typ_uszkodzenia for zakazane in ZAKAZANE_USZKODZENIA):
                        odrzucone_szrot_licznik += 1
                        continue
                    
                    # 3. FILTR ROCZNIKA (min. 2015, max. rok obecny - 3)
                    rok_match = re.search(r'\b(20[0-2][0-9])\b', auto['Tytul'])
                    if rok_match:
                        rok_produkcji = int(rok_match.group(1))
                        # Zmiana: Odjazd, jeśli rocznik poniżej MIN lub powyżej MAX
                        if rok_produkcji < MIN_ROK_PRODUKCJI or rok_produkcji > MAX_ROK_PRODUKCJI:
                            odrzucone_szrot_licznik += 1
                            continue
                    
                    auta_do_bazy.append(auto)

                for auto in auta_do_bazy:
                    wstaw_ogloszenie(conn, auto, marka)
                    
                logging.info(f"[{marka.upper()}] Zapisano: {len(auta_do_bazy)} | Poza zakresem daty: {aukcje_zbyt_stare_licznik} | Odrzucono rocznik/szrot: {odrzucone_szrot_licznik}")
                
                if len(samochody) > 0 and aukcje_zbyt_stare_licznik == len(samochody):
                    logging.info(f"Dotarto do starych aukcji. Zatrzymuję skanowanie marki {marka.upper()}.")
                    break
                
                aktualna_strona += 1
                time.sleep(random.uniform(3.0, 6.0))
        
        # NA SAMYM KOŃCU: USUWANIE STARYCH DANYCH
        print("\n" + "="*50)
        usun_stare_dane_retencja(conn)
        print("="*50 + "\n")
            
    except Exception as e:
        logging.error(f"Krytyczny błąd: {e}")
    finally:
        page.quit()
        conn.close()
        logging.info("Skrypt zakończył działanie.")

if __name__ == "__main__":
    main()
