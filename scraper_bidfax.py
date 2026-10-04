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
MAX_PAGES = 99

MARKI_DO_POBRANIA = [
    "acura", "alfa-romeo", "aston-martin", "audi", "bentley", "bmw",
    "cadillac", "chevrolet", "chrysler", "dodge", "ferrari", "ford",
    "genesis", "gmc", "honda", "hyundai", "infiniti", "jaguar", "jeep",
    "kia", "lamborghini", "land-rover", "lexus", "lincoln", "lotus",
    "lucid", "maserati", "mazda", "mclaren-automotive", "mercedes-benz",
    "mitsubishi", "nissan", "polestar", "pontiac", "porsche", "ram",
    "rolls-royce", "saab", "subaru", "tesla", "toyota", "volkswagen", "volvo",
]

DNI_WSTECZ_POBIERANIE = 365
DATA_GRANICZNA_POBIERANIA = datetime.now() - timedelta(days=DNI_WSTECZ_POBIERANIE)
OKNO_HISTORII_DNI = 365
MIN_ROK_PRODUKCJI = 1950
MAX_ROK_PRODUKCJI = datetime.now().year + 1

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
            INSERT INTO bidfax_scd2 (
                Marka, Model, Rocznik, Tytul, VIN, Cena_USD, Aukcja, Lokalizacja, Lot_Number, 
                Condition, Damage, Mileage, Date_Of_Sale, Link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(Link) DO UPDATE SET 
                Date_Of_Sale = excluded.Date_Of_Sale,
                Cena_USD = excluded.Cena_USD,
                Condition = excluded.Condition
            WHERE bidfax_scd2.Date_Of_Sale != excluded.Date_Of_Sale 
               OR bidfax_scd2.Cena_USD != excluded.Cena_USD 
               OR bidfax_scd2.Condition != excluded.Condition
        """, dane_do_zapisu)
        conn.commit()
        return cursor.rowcount
    except Exception as e:
        logging.error(f"Błąd podczas zapisu do bazy: {e}")
        return 0

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
# NAWIGACJA - SYMULACJA KLIKNIĘĆ
# ==============================================================================
def bezpieczne_przejscie(page, url, aktualna_strona):
    """Tylko strona 1 ładuje się z URL. Reszta musi być KLIKNIĘTA fizycznie."""
    if aktualna_strona == 1:
        page.get(url)
    else:
        logging.info(f"👉 Szukam przycisku dla strony {aktualna_strona}...")
        try:
            # POPRAWIONY XPath dopasowany do faktycznej struktury Bidfax
            kolejna_strona_btn = page.ele(f'xpath://div[contains(@class, "pagi-nav")]//a[text()="{aktualna_strona}"]', timeout=5)
            
            if kolejna_strona_btn:
                kolejna_strona_btn.scroll.to_see(center=True)
                time.sleep(random.uniform(1.0, 2.0))
                kolejna_strona_btn.hover()
                time.sleep(random.uniform(0.5, 1.5))
                kolejna_strona_btn.click()
                logging.info(f"✅ Kliknięto stronę {aktualna_strona}.")
                time.sleep(3) # Czekamy aż strona zacznie się ładować
            else:
                logging.warning(f"❌ Nie znalazłem przycisku strony {aktualna_strona}! Paginacja zablokowana lub jej brak.")
                raise Exception("Brak przycisku paginacji")
                
        except Exception as e:
            raise e

# ==============================================================================
# GŁÓWNA PĘTLA
# ==============================================================================
def main():
    conn = zainicjalizuj_baze()
        
    co = ChromiumOptions()
    co.set_local_port(9222) 
    
    try:
        page = ChromiumPage(co)
    except Exception as e:
        logging.error(f"Nie udało się połączyć. Odpal Chrome komendą z portem 9222!")
        return

    logging.info("Przejęto kontrolę nad przeglądarką! Wchodzę na Bidfax...")
    page.get(DOMAIN)
    time.sleep(3)
    
    try:
        for marka in MARKI_DO_POBRANIA:
            logging.info(f"\n{'='*50}\nSKANOWANIE MARKI: {marka.upper()}\n{'='*50}")
            aktualna_strona = 1
            puste_strony_pod_rzad = 0 
            
            while aktualna_strona <= MAX_PAGES:
                url = f"{DOMAIN}{marka}/" if aktualna_strona == 1 else f"{DOMAIN}{marka}/page/{aktualna_strona}/"
                
                try:
                    start_time = time.time()
                    
                    # Nawigujemy na stronę używając bezpiecznego kliknięcia
                    bezpieczne_przejscie(page, url, aktualna_strona)
                    
                    page.wait.ele_displayed('.thumbnail offer', timeout=8)
                    html_content = page.html
                    tytul_strony = page.title.lower() if page.title else ""
                    
                    if "403 forbidden" in tytul_strony or "403 forbidden" in html_content.lower():
                        logging.error("❌ 403 FORBIDDEN. Dostałeś bana (zapewne tymczasowego).")
                        logging.warning("Skrypt usypia na 120 sekund. Zmień IP (VPN/Restart routera)!")
                        time.sleep(120)
                        continue 

                    if "cloudflare" in html_content.lower() or "just a moment" in tytul_strony:
                        logging.warning("⚠️ Cloudflare analizuje połączenie! Czekam 15s...")
                        page.wait.ele_displayed('.thumbnail offer', timeout=15)
                        html_content = page.html
                        
                except Exception as e:
                    # Łapiemy błąd paginacji by bezpiecznie przejść do nowej marki
                    if "Brak przycisku paginacji" in str(e):
                        logging.info(f"Brak kolejnych stron dla marki {marka.upper()}. Przechodzę do następnej marki.")
                        break
                        
                    logging.error(f"Błąd połączenia/timeout na stronie {aktualna_strona}: {e}")
                    time.sleep(5)
                    continue 
                
                if 'thumbnail offer' not in html_content:
                    if "404" in html_content:
                        logging.info("Koniec ofert dla tej marki (Błąd 404).")
                        break
                    else:
                        logging.warning("Brak ofert, ale strona działa (możliwa pusta strona). Pomijam.")
                        break
                
                # --- SYMULACJA CZŁOWIEKA PRZED POBRANIEM DANYCH ---
                page.scroll.to_half()
                time.sleep(random.uniform(0.5, 1.5))
                page.scroll.to_bottom()
                time.sleep(random.uniform(1.0, 2.5))
                # --------------------------------------------------

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
                    
                logging.info(f"[{marka.upper()}-S{aktualna_strona}] Zapisano: {faktycznie_zapisane} | (Czas: {czas_wyk:.1f}s)")
                
                if auta_do_bazy and faktycznie_zapisane == 0:
                    puste_strony_pod_rzad += 1
                    if puste_strony_pod_rzad >= 3:
                        logging.info(f"🛑 Baza aktualna (3 strony samych duplikatów). Przechodzę do następnej marki.")
                        break
                else:
                    puste_strony_pod_rzad = 0

                if samochody and aukcje_zbyt_stare_licznik == len(samochody):
                    logging.info(f"⏰ Dotarto do aukcji starszych niż {DNI_WSTECZ_POBIERANIE} dni. Idę dalej.")
                    break
                
                aktualna_strona += 1
                
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bidfax_scd2 WHERE Date_Of_Sale < ? AND Date_Of_Sale != 'Brak'", 
                       ((datetime.now() - timedelta(days=OKNO_HISTORII_DNI)).strftime("%Y-%m-%d"),))
        conn.commit()
        
    except KeyboardInterrupt:
        logging.info("Przerwano działanie skryptu skrótem z klawiatury (Ctrl+C).")
    finally:
        conn.close()
        logging.info("Skrypt zakończył działanie. Rozłączono z bazą.")

if __name__ == "__main__":
    main()
