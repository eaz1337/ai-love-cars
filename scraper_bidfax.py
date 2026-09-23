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
    "acura",
    "alfa-romeo",
    "aston-martin",
    "audi",
    "bentley",
    "bmw",
    "cadillac",
    "chevrolet",
    "chrysler",
    "dodge",
    "ferrari",
    "ford",
    "genesis",
    "gmc",
    "honda",
    "hyundai",
    "infiniti",
    "jaguar",
    "jeep",
    "kia",
    "lamborghini",
    "land-rover",
    "lexus",
    "lincoln",
    "lotus",
    "lucid",
    "maserati",
    "mazda",
    "mclaren-automotive",
    "mercedes-benz",
    "mitsubishi",
    "nissan",
    "polestar",
    "pontiac",
    "porsche",
    "ram",
    "rolls-royce",
    "saab",
    "subaru",
    "tesla",
    "toyota",
    "volkswagen",
    "volvo",
]

# Pobieramy tylko ostatnie 3 dni, by codziennie szybkim sprintem aktualizować bazę
DNI_WSTECZ_POBIERANIE = 3
DATA_GRANICZNA_POBIERANIA = datetime.now() - timedelta(days=DNI_WSTECZ_POBIERANIE)
OKNO_HISTORII_DNI = 365
MIN_ROK_PRODUKCJI = 2008
MAX_ROK_PRODUKCJI = datetime.now().year + 1

# ==============================================================================
# BAZA DANYCH SQLITE (SZYBKI ZAPIS)
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
    try:
        cursor.execute("ALTER TABLE bidfax_scd2 ADD COLUMN Model TEXT")
        cursor.execute("ALTER TABLE bidfax_scd2 ADD COLUMN Rocznik INTEGER")
        conn.commit()
    except sqlite3.OperationalError: 
        pass

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_date_of_sale ON bidfax_scd2 (Date_Of_Sale)")
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA synchronous = OFF;")
    conn.commit()
    return conn

def wstaw_ogloszenia_hurtowo(conn, auta):
    cursor = conn.cursor()
    dane_do_zapisu = [
        (car['Marka'], car['Model'], car['Rocznik'], car['Tytul'], car['VIN'], car['Cena_USD'], 
         car['Aukcja'], car['Lokalizacja'], car['Lot_Number'], car['Condition'], 
         car['Damage'], car['Mileage'], car['Date_Of_Sale'], car['Link']) 
        for car in auta
    ]
    try:
        cursor.executemany("""
            INSERT OR IGNORE INTO bidfax_scd2 (
                Marka, Model, Rocznik, Tytul, VIN, Cena_USD, Aukcja, Lokalizacja, Lot_Number, 
                Condition, Damage, Mileage, Date_Of_Sale, Link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, dane_do_zapisu)
        conn.commit()
        return cursor.rowcount
    except Exception as e:
        return 0

# ==============================================================================
# 🕵️ ZACHOWANIA LUDZKIE (ANTY-BOT)
# ==============================================================================
def losowa_przerwa(strony_bez_przerwy):
    if strony_bez_przerwy > random.randint(15, 35):
        czas_przerwy = random.uniform(25.0, 75.0)
        logging.info(f"☕ [HUMAN BEHAVIOR] Przerwa na kawę. Skrypt zatrzymany na {czas_przerwy:.0f} sekund.")
        time.sleep(czas_przerwy)
        return 0
    return strony_bez_przerwy

def symuluj_panike_czlowieka(page):
    time.sleep(random.uniform(1.0, 2.5))
    page.scroll.down(random.randint(100, 300))
    time.sleep(random.uniform(0.5, 1.5))
    page.scroll.up(random.randint(50, 150))
    time.sleep(random.uniform(2.0, 4.0))

# ==============================================================================
# PARSOWANIE HTML
# ==============================================================================
def wyciagnij_dane_z_html(html_content, marka):
    cars = []
    soup = BeautifulSoup(html_content, 'html.parser')
    oferty = soup.find_all('div', class_='thumbnail offer')
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
        
        rok_match = re.search(r'\b(20[0-2][0-9])\b', tytul)
        rocznik = int(rok_match.group(1)) if rok_match else 0
        
        model = "Brak"
        if rocznik > 0:
            czesc_przed_rokiem = tytul.split(str(rocznik))[0].strip()
            if czesc_przed_rokiem.lower().startswith(marka_czysta): model = czesc_przed_rokiem[len(marka_czysta):].strip()
            else: model = czesc_przed_rokiem
        
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
    conn = zainicjalizuj_baze()
        
    # ULTIMATE BYPASS: Podłączamy się do ręcznie otwartej przeglądarki!
    co = ChromiumOptions()
    co.set_local_port(9222) 
    
    try:
        # Skrypt nie otwiera Chrome'a, tylko "włamuje się" do już otwartego okna na porcie 9222
        page = ChromiumPage(co)
    except Exception as e:
        logging.error(f"Nie udało się połączyć. Upewnij się, że odpaliłeś Chrome komendą z portem 9222!")
        return

    logging.info("Przejęto kontrolę nad przeglądarką! Wchodzę na Bidfax...")
    page.get(DOMAIN)
    symuluj_panike_czlowieka(page) 
    
    strony_od_przerwy = 0

    try:
        for marka in MARKI_DO_POBRANIA:
            logging.info(f"\n{'='*50}\nSKANOWANIE MARKI: {marka.upper()}\n{'='*50}")
            aktualna_strona = 1
            
            while aktualna_strona <= MAX_PAGES:
                url = f"{DOMAIN}{marka}/" if aktualna_strona == 1 else f"{DOMAIN}{marka}/page/{aktualna_strona}/"
                
                strony_od_przerwy += 1
                strony_od_przerwy = losowa_przerwa(strony_od_przerwy)

                try:
                    start_time = time.time()
                    page.get(url) 
                    
                    time.sleep(random.uniform(0.6, 1.3)) 
                    html_content = page.html
                    
                    tytul_strony = page.title.lower() if page.title else ""
                    if "cloudflare" in html_content.lower() or "just a moment" in tytul_strony:
                        logging.warning("⚠️ Wykryto blokadę Cloudflare! Udaję człowieka...")
                        symuluj_panike_czlowieka(page)
                        html_content = page.html
                        
                except Exception as e:
                    logging.error(f"Błąd sieci: {e}")
                    time.sleep(5)
                    continue
                
                if 'thumbnail offer' not in html_content:
                    if "404" in html_content:
                        logging.info("Koniec ofert dla tej marki.")
                        break
                    else:
                        time.sleep(2)
                        continue
                
                samochody = wyciagnij_dane_z_html(html_content, marka)
                if not samochody: break
                    
                auta_do_bazy = []
                aukcje_zbyt_stare_licznik = 0
                
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
                    
                    rocznik = auto['Rocznik']
                    if rocznik > 0 and (rocznik < MIN_ROK_PRODUKCJI or rocznik > MAX_ROK_PRODUKCJI):
                        continue
                    
                    auta_do_bazy.append(auto)

                faktycznie_zapisane = wstaw_ogloszenia_hurtowo(conn, auta_do_bazy) if auta_do_bazy else 0
                czas_wyk = time.time() - start_time
                    
                logging.info(f"[{marka.upper()}-S{aktualna_strona}] Dodano: {faktycznie_zapisane} | (Czas zapytania: {czas_wyk:.1f}s)")
                
                # INTELIGENTNE HAMULCE
                if auta_do_bazy and faktycznie_zapisane == 0:
                    logging.info(f"🛑 Baza zaktualizowana (same duplikaty). Przechodzę do następnej marki.")
                    break
                if samochody and aukcje_zbyt_stare_licznik == len(samochody):
                    logging.info(f"⏰ Dotarto do starszych aukcji dla {marka.upper()} (ponad {DNI_WSTECZ_POBIERANIE} dni). Idę dalej.")
                    break
                
                aktualna_strona += 1
                
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bidfax_scd2 WHERE Date_Of_Sale < ? AND Date_Of_Sale != 'Brak'", 
                       ((datetime.now() - timedelta(days=OKNO_HISTORII_DNI)).strftime("%Y-%m-%d"),))
        conn.commit()
        
    except KeyboardInterrupt:
        logging.info("Przerwano działanie skryptu skrótem z klawiatury (Ctrl+C).")
    finally:
        # Usunąłem page.quit() żeby skrypt nie zamknął Ci niespodziewanie Twojej głównej przeglądarki!
        conn.close()
        logging.info("Skrypt zakończył działanie. Rozłączono z przeglądarką.")

if __name__ == "__main__":
    main()
