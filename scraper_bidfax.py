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

SELEKTOR_OFERTY = 'css:.thumbnail.offer'
PAUZA_BLOKADA_S = 120
MAX_BLOKAD_POD_RZAD = 3
PAUZA_MIEDZY_MARKAMI_S = (5.0, 12.0)

# ==============================================================================
# BAZA DANYCH SQLITE
# ==============================================================================
def zainicjalizuj_baze():
    # Wydłużony timeout, aby uniknąć błędu "database is locked" (drugi skrypt pisze do tej samej bazy)
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bidfax_scd2 (
            ID INTEGER PRIMARY KEY AUTOINCREMENT, Marka TEXT, Tytul TEXT, VIN TEXT, Cena_USD INTEGER,
            Aukcja TEXT, Lokalizacja TEXT, Lot_Number TEXT, Condition TEXT, Damage TEXT,
            Mileage TEXT, Date_Of_Sale TEXT, Link TEXT UNIQUE
        )
    """)
    # Osobne ALTER-y: gdy jedna kolumna już istnieje, druga nadal musi zostać dodana
    for kolumna, typ in (("Model", "TEXT"), ("Rocznik", "INTEGER")):
        try:
            cursor.execute(f"ALTER TABLE bidfax_scd2 ADD COLUMN {kolumna} {typ}")
        except sqlite3.OperationalError:
            pass

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_date_of_sale ON bidfax_scd2 (Date_Of_Sale)")
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA synchronous = NORMAL;")
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
            WHERE bidfax_scd2.Date_Of_Sale IS NOT excluded.Date_Of_Sale
               OR bidfax_scd2.Cena_USD IS NOT excluded.Cena_USD
               OR bidfax_scd2.Condition IS NOT excluded.Condition
        """, dane_do_zapisu)
        conn.commit()
        return cursor.rowcount
    except Exception as e:
        logging.error(f"Błąd podczas zapisu do bazy: {e}")
        return 0

def usun_stare_rekordy(conn):
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bidfax_scd2 WHERE Date_Of_Sale < ? AND Date_Of_Sale != 'Brak'",
                       ((datetime.now() - timedelta(days=OKNO_HISTORII_DNI)).strftime("%Y-%m-%d"),))
        conn.commit()
        cursor.close()
    except sqlite3.OperationalError as e:
        logging.error(f"Błąd czyszczenia starych rekordów (baza zablokowana): {e}")

# ==============================================================================
# PARSOWANIE HTML
# ==============================================================================
def wyciagnij_cene(tekst):
    """'12,500' / '$12 500' -> 12500. Brak liczby -> None (a nie 0, żeby nie zaniżać median)."""
    cyfry = re.sub(r'\D', '', tekst or '')
    return int(cyfry) if cyfry else None

def wyciagnij_model(tytul, marka, rocznik):
    """Model = część tytułu przed rokiem, bez marki. Marka z myślnikiem ('mercedes-benz') pasuje do 'Mercedes-Benz' i 'Mercedes Benz'."""
    if not rocznik:
        return "Brak"
    czesc_przed_rokiem = tytul.split(str(rocznik))[0].strip()
    wzorzec_marki = r'[\s\-]*'.join(re.escape(czesc) for czesc in marka.split('-'))
    model = re.sub(r'^' + wzorzec_marki + r'\b', '', czesc_przed_rokiem, count=1, flags=re.IGNORECASE).strip()
    return model or czesc_przed_rokiem or "Brak"

def wyciagnij_dane_z_html(html_content, marka):
    cars = []
    soup = BeautifulSoup(html_content, 'html.parser')
    oferty = soup.find_all('div', class_='thumbnail offer')

    for oferta in oferty:
        price_tag = oferta.find('span', class_='prices')
        if not price_tag: continue
        cena = wyciagnij_cene(price_tag.text)

        caption = oferta.find('div', class_='caption')
        if not caption: continue
        link_tag = caption.find('a')
        link = link_tag['href'] if link_tag else "Brak"
        tytul = link_tag.find('h2').text.strip() if link_tag and link_tag.find('h2') else "Brak"

        vin_match = re.search(r'vin:\s*([A-Z0-9]+)', tytul, re.IGNORECASE)
        vin = vin_match.group(1) if vin_match else "Brak"

        # Rok szukamy tylko przed "vin:", żeby nie złapać fragmentu numeru VIN
        tytul_bez_vin = re.split(r'vin:', tytul, flags=re.IGNORECASE)[0]
        rok_match = re.search(r'\b(19[5-9][0-9]|20[0-9]{2})\b', tytul_bez_vin)
        rocznik = int(rok_match.group(1)) if rok_match else None

        model = wyciagnij_model(tytul_bez_vin, marka, rocznik)

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
# WYKRYWANIE BLOKAD
# ==============================================================================
def wykryj_blokade(page, html_content):
    """Zwraca '403', 'cloudflare' albo None. Patrzy na tytuł i treść tylko wtedy, gdy na stronie nie ma ofert
    (zwykła strona ładuje skrypty z cdnjs.cloudflare.com, więc samo słowo 'cloudflare' nic nie znaczy)."""
    tytul = (page.title or "").lower()
    if "403" in tytul and "forbidden" in tytul:
        return "403"
    if "just a moment" in tytul or "attention required" in tytul:
        return "cloudflare"
    if 'thumbnail offer' not in html_content:
        html_low = html_content.lower()
        if "403 forbidden" in html_low:
            return "403"
        if "challenge-platform" in html_low or "cf-browser-verification" in html_low:
            return "cloudflare"
    return None

def strona_nie_istnieje(page):
    tytul = (page.title or "").lower()
    return "404" in tytul or "not found" in tytul

# ==============================================================================
# NAWIGACJA - SYMULACJA KLIKNIĘĆ
# ==============================================================================
def bezpieczne_przejscie(page, url, aktualna_strona, bezposrednio=False):
    """Strona 1 ładuje się z URL, kolejne są klikane fizycznie. Gdy przycisku nie ma (paginacja pokazuje tylko
    okno kilku numerów) albo po blokadzie (`bezposrednio=True`), wchodzimy bezpośrednio na URL."""
    if aktualna_strona == 1 or bezposrednio:
        page.get(url)
        return

    logging.info(f"👉 Szukam przycisku dla strony {aktualna_strona}...")
    kolejna_strona_btn = page.ele(f'xpath://div[contains(@class, "pagi-nav")]//a[text()="{aktualna_strona}"]', timeout=5)
    if not kolejna_strona_btn:
        logging.info(f"Brak przycisku strony {aktualna_strona}, wchodzę bezpośrednio na URL.")
        page.get(url)
        return

    kolejna_strona_btn.scroll.to_see(center=True)
    time.sleep(random.uniform(1.0, 2.0))
    kolejna_strona_btn.hover()
    time.sleep(random.uniform(0.5, 1.5))
    kolejna_strona_btn.click()
    logging.info(f"✅ Kliknięto stronę {aktualna_strona}.")
    time.sleep(3) # Czekamy aż strona zacznie się ładować

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
        logging.error(f"Nie udało się połączyć. Odpal Chrome komendą z portem 9222! ({e})")
        conn.close()
        return

    logging.info("Przejęto kontrolę nad przeglądarką! Wchodzę na Bidfax...")
    page.get(DOMAIN)
    time.sleep(3)

    przerwij = False
    try:
        for marka in MARKI_DO_POBRANIA:
            if przerwij:
                break
            logging.info(f"\n{'='*50}\nSKANOWANIE MARKI: {marka.upper()}\n{'='*50}")
            aktualna_strona = 1
            puste_strony_pod_rzad = 0
            blokady_pod_rzad = 0
            wymus_url = False

            while aktualna_strona <= MAX_PAGES:
                url = f"{DOMAIN}{marka}/" if aktualna_strona == 1 else f"{DOMAIN}{marka}/page/{aktualna_strona}/"

                try:
                    start_time = time.time()
                    bezpieczne_przejscie(page, url, aktualna_strona, bezposrednio=wymus_url)
                    wymus_url = False

                    page.wait.ele_displayed(SELEKTOR_OFERTY, timeout=8)
                    html_content = page.html
                    blokada = wykryj_blokade(page, html_content)

                    if blokada == "cloudflare":
                        logging.warning("⚠️ Cloudflare analizuje połączenie! Czekam do 15s...")
                        page.wait.ele_displayed(SELEKTOR_OFERTY, timeout=15)
                        html_content = page.html
                        blokada = wykryj_blokade(page, html_content)
                except Exception as e:
                    logging.error(f"Błąd połączenia/timeout na stronie {aktualna_strona}: {e}")
                    time.sleep(5)
                    wymus_url = True
                    continue

                if blokada:
                    blokady_pod_rzad += 1
                    if blokady_pod_rzad >= MAX_BLOKAD_POD_RZAD:
                        logging.error(f"❌ {blokady_pod_rzad} blokady pod rząd ({blokada}). Przerywam skrypt - zmień IP/rozwiąż captchę i uruchom ponownie.")
                        przerwij = True
                        break
                    logging.error(f"❌ Blokada ({blokada}), próba {blokady_pod_rzad}/{MAX_BLOKAD_POD_RZAD}. Czekam {PAUZA_BLOKADA_S}s (zmień IP / rozwiąż captchę w przeglądarce).")
                    time.sleep(PAUZA_BLOKADA_S)
                    wymus_url = True  # po blokadzie nie szukamy przycisku paginacji, tylko wchodzimy na URL
                    continue
                blokady_pod_rzad = 0

                if 'thumbnail offer' not in html_content:
                    if strona_nie_istnieje(page):
                        logging.info("Koniec ofert dla tej marki (Błąd 404).")
                    else:
                        logging.warning("Brak ofert na stronie (koniec marki lub pusta strona).")
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
                        except ValueError:
                            # Nieznany format: lepiej "Brak" niż surowy tekst, który psuje porównania dat w czyszczeniu
                            logging.debug(f"Nieparsowalna data sprzedaży: {data_str!r}")
                            auto['Date_Of_Sale'] = "Brak"

                    rocznik = auto['Rocznik']
                    if rocznik is not None and (rocznik < MIN_ROK_PRODUKCJI or rocznik > MAX_ROK_PRODUKCJI):
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

            # Czyszczenie po każdej marce, żeby przerwanie skryptu nie zostawiało starych rekordów
            usun_stare_rekordy(conn)
            if not przerwij:
                time.sleep(random.uniform(*PAUZA_MIEDZY_MARKAMI_S))

    except KeyboardInterrupt:
        logging.info("Przerwano działanie skryptu skrótem z klawiatury (Ctrl+C).")
    finally:
        conn.close()
        logging.info("Skrypt zakończył działanie. Rozłączono z bazą.")

if __name__ == "__main__":
    main()
