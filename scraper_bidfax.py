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
    
    # BEZPIECZNA MIGRACJA: Dodanie nowych kolumn, jeśli nie istnieją w starej bazie
    try:
        cursor.execute("ALTER TABLE bidfax_scd2 ADD COLUMN Model TEXT")
        cursor.execute("ALTER TABLE bidfax_scd2 ADD COLUMN Rocznik INTEGER")
        conn.commit()
        logging.info("🔧 Zaktualizowano strukturę bazy danych o kolumny: Model, Rocznik.")
    except sqlite3.OperationalError:
        # Kolumny już istnieją, ignorujemy
        pass

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
                Marka, Model, Rocznik, Tytul, VIN, Cena_USD, Aukcja, Lokalizacja, Lot_Number, 
                Condition, Damage, Mileage, Date_Of_Sale, Link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (marka, car['Model'], car['Rocznik'], car['Tytul'], car['VIN'], car['Cena_USD'], 
              car['Aukcja'], car['Lokalizacja'], car['Lot_Number'], car['Condition'], 
              car['Damage'], car['Mileage'], car['Date_Of_Sale'], car['Link']))
        conn.commit()
        return cursor.rowcount > 0 
    except Exception as e:
        logging.error(f"Błąd zapisu do bazy: {e}")
        return False

def usun_stare_dane_retencja(conn):
    cursor = conn.cursor()
    data_graniczna = (datetime.now() - timedelta(days=OKNO_HISTORII_DNI)).strftime("%Y-%m-%d")
    cursor.execute("DELETE FROM bidfax_scd2 WHERE Date_Of_Sale < ? AND Date_Of_Sale != 'Brak'", (data_graniczna,))
    conn.commit()

# ==============================================================================
# PARSOWANIE HTML
# ==============================================================================
def wyciagnij_dane_z_html(html_content, marka):
    cars = []
    soup = BeautifulSoup(html_content, 'html.parser')
    oferty = soup.find_all('div', class_='thumbnail offer')
    
    # Formatowanie marki (np. z "alfa-romeo" robimy "alfa romeo"), żeby łatwiej wyciąć z tytułu
    marka_czysta = marka.replace('-', ' ').lower()
    
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
        
        # WYCIĄGANIE ROCZNIKA I MODELU
        rok_match = re.search(r'\b(20[0-2][0-9])\b', tytul)
        rocznik = int(rok_match.group(1)) if rok_match else 0
        
        model = "Brak"
        if rocznik > 0:
            # Ucinamy tytuł w miejscu gdzie zaczyna się rocznik
            czesc_przed_rokiem = tytul.split(str(rocznik))[0].strip() # Zwraca np. "Alfa Romeo Stelvio Ti Awd"
            
            # Wyrzucamy nazwę marki z przodu, żeby został sam model
            if czesc_przed_rokiem.lower().startswith(marka_czysta):
                model = czesc_przed_rokiem[len(marka_czysta):].strip()
            else:
                model = czesc_przed_rokiem # Fallback w rzadkich przypadkach
        
        detale = {"Aukcja": "Brak", "Lokalizacja": "Brak", "Lot number": "Brak", "Condition": "Brak", "Damage": "Brak", "Mileage": "Brak", "Date of sale": "Brak"}
        
        for p in caption.find_all('p'):
            klasa = p.get('class', [])
            tekst = p.text.replace('\xa0', ' ').strip()
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
                            
        cars.append({"Tytul": tytul, "Marka": marka, "Model": model, "Rocznik": rocznik, "VIN": vin, 
                     "Cena_USD": cena, "Aukcja": detale["Aukcja"], "Lokalizacja": detale["Lokalizacja"], 
                     "Lot_Number": detale["Lot number"], "Condition": detale["Condition"], 
                     "Damage": detale["Damage"], "Mileage": detale["Mileage"], 
                     "Date_Of_Sale": detale["Date of sale"], "Link": link})
        
    return cars

# ==============================================================================
# GŁÓWNA PĘTLA
# ==============================================================================
def main():
    logging.info("Rozpoczynam skanowanie Bidfax (Stały profil, symulacja człowieka)...")
    conn = zainicjalizuj_baze()
        
    co = ChromiumOptions()
    co.set_browser_path('/usr/bin/google-chrome')
    co.set_local_port(9223)
    co.set_argument('--user-data-dir=/home/eaz/Desktop/drission-profile')
    
    co.headless(False) 
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--disable-infobars')
    co.set_argument('--ignore-certificate-errors')
    co.no_imgs(True) 
        
    try:
        page = ChromiumPage(co)
    except Exception as e:
        logging.error(f"Nie udało się uruchomić przeglądarki: {e}")
        return

    logging.info("Rozgrzewam przeglądarkę na google.com...")
    page.get("https://www.google.com")
    time.sleep(random.uniform(2.5, 4.0))
    
    try:
        kolejne_blokady = 0
        
        for marka in MARKI_DO_POBRANIA:
            logging.info(f"\n{'='*50}\nSKANOWANIE MARKI: {marka.upper()}\n{'='*50}")
            aktualna_strona = 1
            
            while aktualna_strona <= MAX_PAGES:
                url = f"{DOMAIN}{marka}/" if aktualna_strona == 1 else f"{DOMAIN}{marka}/page/{aktualna_strona}/"
                logging.info(f"[{marka.upper()}] Wchodzę na stronę {aktualna_strona}...")
                
                page.get(url)
                is_loaded = page.ele('.thumbnail offer', timeout=10)
                
                if not is_loaded:
                    tytul = page.title.lower() if page.title else ""
                    podejrzane_slowa = ["just a moment", "weryfikacja", "cloudflare", "attention required", "403", "429", "503", "access denied", "error", "security"]
                    is_suspicious_title = any(word in tytul for word in podejrzane_slowa)
                    has_cloudflare_cf = page.ele('#cf-wrapper', timeout=1) or page.ele('#challenge-running', timeout=1)
                    has_captcha = page.ele('.g-recaptcha', timeout=1) or page.ele('.h-captcha', timeout=1)
                    is_normal_page_structure = page.ele('tag:footer', timeout=1) or page.ele('#header', timeout=1)

                    if is_suspicious_title or has_cloudflare_cf or has_captcha or not is_normal_page_structure:
                        kolejne_blokady += 1
                        if kolejne_blokady >= 2:
                            logging.critical("🛑 WYKRYTO DWIE BLOKADY POD RZĄD! Całkowite przerwanie skryptu.")
                            return
                        logging.warning(f"⚠️ Wykryto anomalię. Próba {kolejne_blokady}/2. Masz 90 sek na naprawę...")
                        weryfikacja_udana = False
                        for _ in range(45): 
                            if page.ele('.thumbnail offer', timeout=2):
                                logging.info("✅ Weryfikacja udana! Oferty znów są widoczne.")
                                weryfikacja_udana = True
                                kolejne_blokady = 0
                                break
                            time.sleep(2)
                            
                        if not weryfikacja_udana:
                            logging.error(f"❌ Blokada nie ustąpiła na stronie {aktualna_strona} dla marki {marka}.")
                            break 
                    else:
                        logging.info("Brak ofert (dotarto do końca wyników dla tej marki).")
                        break
                else:
                    kolejne_blokady = 0
                
                page.scroll.to_bottom()
                time.sleep(1.0)
                
                # ZMIANA: Przekazujemy markę do funkcji, by mogła precyzyjnie wyciąć model!
                samochody = wyciagnij_dane_z_html(page.html, marka)
                if not samochody: break
                    
                auta_do_bazy = []
                aukcje_zbyt_stare_licznik = 0
                odrzucone_rocznik = 0
                
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
                    
                    # Filtrujemy na podstawie wyodrębnionego rocznika
                    rocznik = auto['Rocznik']
                    if rocznik > 0:
                        if rocznik < MIN_ROK_PRODUKCJI or rocznik > MAX_ROK_PRODUKCJI:
                            odrzucone_rocznik += 1
                            continue
                    
                    auta_do_bazy.append(auto)

                faktycznie_zapisane = 0
                duplikaty = 0
                for auto in auta_do_bazy:
                    czy_nowe = wstaw_ogloszenie(conn, auto, marka)
                    if czy_nowe: faktycznie_zapisane += 1
                    else: duplikaty += 1
                    
                logging.info(f"[{marka.upper()}] Nowe: {faktycznie_zapisane} | Duplikaty: {duplikaty} | Zły rocznik: {odrzucone_rocznik} | Zbyt stare datowo: {aukcje_zbyt_stare_licznik}")
                
                if len(auta_do_bazy) > 0 and duplikaty == len(auta_do_bazy):
                    logging.info(f"🛑 INTELIGENTNY HAMULEC: Baza dogoniła historię! Przechodzę do następnej marki.")
                    break
                if len(samochody) > 0 and aukcje_zbyt_stare_licznik == len(samochody):
                    logging.info(f"⏰ Dotarto do starych aukcji. Zatrzymuję skanowanie marki {marka.upper()}.")
                    break
                
                aktualna_strona += 1
                czas_czekania = random.uniform(1.2, 2.8)
                logging.info(f"Czekam {czas_czekania:.1f}s przed kolejną stroną...")
                time.sleep(czas_czekania)
        
        usun_stare_dane_retencja(conn)
        przerwa_na_marke = random.uniform(30.0, 90.0)
        logging.info(f"✅ Zakończono markę! Robię dłuższą przerwę: {przerwa_na_marke:.1f}s przed kolejną marką...")
        time.sleep(przerwa_na_marke)
            
    except Exception as e:
        logging.error(f"Krytyczny błąd w głównej pętli: {e}")
    finally:
        try: page.quit()
        except: pass
        conn.close()
        logging.info("Skrypt zakończył działanie.")

if __name__ == "__main__":
    main()
