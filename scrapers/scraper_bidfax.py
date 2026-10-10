import argparse
import os
import time
import random
import sqlite3
import logging
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from DrissionPage import ChromiumPage, ChromiumOptions
from marki import MARKI_BIDFAX

# ==============================================================================
# KONFIGURACJA SKRYPTU
# ==============================================================================
# Skrypt jest zaprojektowany do częstego, krótkiego uruchamiania (cron co kilka godzin): każdy przebieg robi mały
# kawałek pracy i kończy się, a przy pierwszej blokadzie zapisuje stan i zostawia IP w spokoju (patrz BACKOFF_BLOKADY).
#   python scraper_bidfax.py          -> tryb przyrostowy: tylko najnowsze strony każdej marki
#   python scraper_bidfax.py --pelny  -> backfill wznawiany między uruchomieniami (stan w tabeli bidfax_stan_marek)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

DB_NAME = "samochody.db"
DOMAIN = "https://en.bidfax.info/"
MAX_PAGES = 99
MAX_PAGES_PRZYROST = MAX_PAGES   # bez limitu: markę kończy dopiero strona bez nowych aut (DUPLIKATY_STOP_PRZYROST)

MARKI_DO_POBRANIA = MARKI_BIDFAX  # wspólna lista z Otomoto: scrapers/marki.py

DNI_WSTECZ_POBIERANIE = 365
DATA_GRANICZNA_POBIERANIA = datetime.now() - timedelta(days=DNI_WSTECZ_POBIERANIE)
OKNO_HISTORII_DNI = 365
MIN_ROK_PRODUKCJI = 1950
MAX_ROK_PRODUKCJI = datetime.now().year + 1

SELEKTOR_OFERTY = 'css:.thumbnail.offer'
MAX_BLEDOW_POD_RZAD = 3          # tyle razy z rzędu ponawiamy stronę po błędzie połączenia/timeoucie, potem porzucamy markę
MAX_MAREK_Z_BLEDEM_POD_RZAD = 3  # tyle marek z rzędu porzuconych przez błędy = przeglądarka/sieć nie działa, kończymy skrypt
DUPLIKATY_STOP_PRZYROST = 2      # strony bez nowych/zmienionych aut pod rząd, po których kończymy markę (przyrost)
DUPLIKATY_STOP_PELNY = 3         # to samo dla backfillu

# Limity i tempo (ruch ma wyglądać jak człowiek, a nie jak crawler)
LIMIT_STRON_DZIENNIE = 2000
NOCNA_PRZERWA_GODZINY = (1, 6)   # [od, do) - w tych godzinach skrypt się nie uruchamia
LIMIT_STRON_NA_PRZEBIEG = 80     # po tylu stronach przebieg się kończy (kolejne uruchomienie = odpoczynek dla IP)
PAUZA_MIEDZY_STRONAMI_S = (3.0, 5.0)
DLUGA_PAUZA_SZANSA = 0.05        # co ok. 20. stronę dłuższa przerwa, żeby ruch nie był równomierny
DLUGA_PAUZA_S = (30.0, 90.0)
PAUZA_MIEDZY_MARKAMI_S = (3.0, 6.0)
SZANSA_KLIKNIECIA_PAGINACJI = 0.0  # klikanie paginacji kosztowało ~5s na stronę; wejście po URL

# Po blokadzie nie próbujemy ponownie z tego samego IP: kolejne próby tylko wydłużają ban.
# n-ta kolejna blokada (bez udanej strony pomiędzy) = n-ty wpis; ostatni wpis powtarza się w nieskończoność.
BACKOFF_BLOKADY = [timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6)]

# Stały profil Chrome zachowuje ciasteczko cf_clearance między uruchomieniami (nowa sesja = nowy challenge).
# Dotyczy przeglądarki startowanej przez skrypt; Chrome już działający na porcie (Docker) zostaje przejęty tak jak jest.
PORT_CHROME = 9222
PROFIL_CHROME = os.environ.get("BIDFAX_PROFIL") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "czysty_profil_bidfax")
# Proxy tylko rezydencjalne i 'sticky' (to samo IP przez całą sesję). Zmiana IP przy tym samym cf_clearance = blokada.
# Format: http://user:pass@host:port. Bez zmiennej skrypt idzie z domowego IP, co zwykle jest najlepsze.
PROXY = os.environ.get("BIDFAX_PROXY")

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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bidfax_stan_marek (
            Marka TEXT PRIMARY KEY, Nastepna_Strona INTEGER, Zakonczona INTEGER, Zaktualizowano TEXT
        )
    """)
    cursor.execute("CREATE TABLE IF NOT EXISTS bidfax_meta (Klucz TEXT PRIMARY KEY, Wartosc TEXT)")
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
                     "Lot_Number": detale["Lot number"], "Condition": detale["Condition"].title(),
                     "Damage": detale["Damage"], "Mileage": detale["Mileage"],
                     "Date_Of_Sale": detale["Date of sale"], "Link": link})
    return cars

def przygotuj_do_zapisu(samochody):
    """Normalizuje datę sprzedaży i odrzuca auta spoza okna dat/lat. Zwraca (auta_do_bazy, ile_zbyt_starych)."""
    auta_do_bazy = []
    zbyt_stare = 0
    for auto in samochody:
        data_str = auto['Date_Of_Sale']
        if data_str != "Brak":
            try:
                data_sprzedazy = datetime.strptime(data_str, "%d.%m.%Y")
                auto['Date_Of_Sale'] = data_sprzedazy.strftime("%Y-%m-%d")
                if data_sprzedazy < DATA_GRANICZNA_POBIERANIA:
                    zbyt_stare += 1
                    continue
            except ValueError:
                # Nieznany format: lepiej "Brak" niż surowy tekst, który psuje porównania dat w czyszczeniu
                logging.debug(f"Nieparsowalna data sprzedaży: {data_str!r}")
                auto['Date_Of_Sale'] = "Brak"

        rocznik = auto['Rocznik']
        if rocznik is not None and (rocznik < MIN_ROK_PRODUKCJI or rocznik > MAX_ROK_PRODUKCJI):
            continue
        auta_do_bazy.append(auto)
    return auta_do_bazy, zbyt_stare

# ==============================================================================
# STAN SKANOWANIA (wznawianie, backoff, dzienny limit)
# ==============================================================================
def pobierz_meta(conn, klucz, domyslna=None):
    wiersz = conn.execute("SELECT Wartosc FROM bidfax_meta WHERE Klucz = ?", (klucz,)).fetchone()
    return wiersz[0] if wiersz else domyslna

def ustaw_meta(conn, klucz, wartosc):
    conn.execute("INSERT INTO bidfax_meta (Klucz, Wartosc) VALUES (?, ?) "
                 "ON CONFLICT(Klucz) DO UPDATE SET Wartosc = excluded.Wartosc", (klucz, str(wartosc)))
    conn.commit()

def blokada_do(conn):
    """Moment, przed którym nie wolno łączyć się z Bidfax (None = brak blokady)."""
    tekst = pobierz_meta(conn, "blokada_do")
    if not tekst:
        return None
    teraz = datetime.now()
    do = datetime.fromisoformat(tekst)
    return do if do > teraz else None

def zarejestruj_blokade(conn):
    """Zwiększa licznik kolejnych blokad, ustawia pauzę z backoffem i zwraca (numer_blokady, do_kiedy)."""
    n = int(pobierz_meta(conn, "blokady_pod_rzad", 0)) + 1
    do = datetime.now() + BACKOFF_BLOKADY[min(n - 1, len(BACKOFF_BLOKADY) - 1)]
    ustaw_meta(conn, "blokady_pod_rzad", n)
    ustaw_meta(conn, "blokada_do", do.isoformat(timespec="seconds"))
    return n, do

def zarejestruj_sukces(conn):
    if int(pobierz_meta(conn, "blokady_pod_rzad", 0)):
        ustaw_meta(conn, "blokady_pod_rzad", 0)

def strony_dzisiaj(conn):
    dzien, _, licznik = pobierz_meta(conn, "licznik_dnia", "").partition("|")
    return int(licznik) if dzien == datetime.now().strftime("%Y-%m-%d") and licznik else 0

def dolicz_strone(conn):
    ustaw_meta(conn, "licznik_dnia", f"{datetime.now():%Y-%m-%d}|{strony_dzisiaj(conn) + 1}")

def w_nocnej_przerwie():
    od, do = NOCNA_PRZERWA_GODZINY
    return od <= datetime.now().hour < do

def stan_marek_pelny(conn):
    """{marka: (nastepna_strona, zakonczona)} dla backfillu."""
    return {m: (s, bool(z)) for m, s, z in conn.execute("SELECT Marka, Nastepna_Strona, Zakonczona FROM bidfax_stan_marek")}

def zapisz_stan_marki(conn, marka, nastepna_strona, zakonczona):
    conn.execute("INSERT INTO bidfax_stan_marek (Marka, Nastepna_Strona, Zakonczona, Zaktualizowano) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(Marka) DO UPDATE SET Nastepna_Strona = excluded.Nastepna_Strona, "
                 "Zakonczona = excluded.Zakonczona, Zaktualizowano = excluded.Zaktualizowano",
                 (marka, nastepna_strona, int(zakonczona), datetime.now().isoformat(timespec="seconds")))
    conn.commit()

# ==============================================================================
# PRZEGLĄDARKA I WYKRYWANIE BLOKAD
# ==============================================================================
def wykryj_blokade(tytul, html_content):
    """Zwraca '403', 'cloudflare' albo None. Patrzy na tytuł i treść tylko wtedy, gdy na stronie nie ma ofert
    (zwykła strona ładuje skrypty z cdnjs.cloudflare.com, więc samo słowo 'cloudflare' nic nie znaczy)."""
    tytul = (tytul or "").lower()
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

class Sesja:
    """Jedna przeglądarka z trwałym profilem: nawigacja, zachowanie jak człowiek, wykrywanie blokad."""

    def __init__(self):
        co = ChromiumOptions()
        co.set_local_port(PORT_CHROME)
        co.set_load_mode("eager")  # nie czekamy na obrazki i reklamy, parsujemy sam HTML
        co.set_user_data_path(os.path.abspath(PROFIL_CHROME))
        if PROXY:
            co.set_proxy(PROXY)
        self.page = ChromiumPage(co)

    def start(self):
        self.page.get(DOMAIN)
        time.sleep(3)

    def _przejdz(self, url, numer_strony, bezposrednio):
        """Strona 1 ładuje się z URL. Kolejne bywają klikane (losowo), a gdy przycisku nie ma (paginacja pokazuje
        tylko okno kilku numerów) albo po błędzie, wchodzimy bezpośrednio na URL."""
        if numer_strony == 1 or bezposrednio or random.random() > SZANSA_KLIKNIECIA_PAGINACJI:
            self.page.get(url)
            return
        przycisk = self.page.ele(f'xpath://div[contains(@class, "pagi-nav")]//a[text()="{numer_strony}"]', timeout=5)
        if not przycisk:
            self.page.get(url)
            return
        przycisk.scroll.to_see(center=True)
        time.sleep(random.uniform(1.0, 2.0))
        przycisk.hover()
        time.sleep(random.uniform(0.5, 1.5))
        przycisk.click()
        time.sleep(3)  # czekamy aż strona zacznie się ładować

    def pobierz(self, url, numer_strony, bezposrednio=False):
        """Zwraca (html, blokada). Błędy połączenia lecą wyjątkiem do wywołującego."""
        self._przejdz(url, numer_strony, bezposrednio)
        self.page.wait.ele_displayed(SELEKTOR_OFERTY, timeout=8)
        html = self.page.html
        blokada = wykryj_blokade(self.page.title, html)
        if blokada == "cloudflare":
            logging.warning("⚠️ Cloudflare analizuje połączenie! Czekam do 15s...")
            self.page.wait.ele_displayed(SELEKTOR_OFERTY, timeout=15)
            html = self.page.html
            blokada = wykryj_blokade(self.page.title, html)
        return html, blokada

    def strona_nie_istnieje(self):
        tytul = (self.page.title or "").lower()
        return "404" in tytul or "not found" in tytul

    def zachowuj_sie_jak_czlowiek(self):
        self.page.scroll.to_bottom()
        time.sleep(random.uniform(0.2, 0.5))

def zapisz_surowy_html(html):
    """Ostatnia udana strona na dysku: gdy Bidfax zmieni układ, parser da się poprawić bez ponownego pobierania."""
    try:
        sciezka = os.path.join(os.path.dirname(os.path.abspath(DB_NAME)), "bidfax_ostatnia_strona.html")
        with open(sciezka, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as e:
        logging.debug(f"Nie zapisano surowego HTML: {e}")

# ==============================================================================
# SKANER
# ==============================================================================
class Skaner:
    # Wyniki skanowania marki
    OK, BLOKADA, BLAD, LIMIT = "ok", "blokada", "blad", "limit"

    def __init__(self, conn, sesja, pelny, bez_limitow=False):
        self.conn = conn
        self.sesja = sesja
        self.pelny = pelny
        self.bez_limitow = bez_limitow
        self.strony_w_przebiegu = 0
        self.max_stron = MAX_PAGES if pelny else MAX_PAGES_PRZYROST
        self.duplikaty_stop = DUPLIKATY_STOP_PELNY if pelny else DUPLIKATY_STOP_PRZYROST

    def _pauza_miedzy_stronami(self):
        if random.random() < DLUGA_PAUZA_SZANSA:
            pauza = random.uniform(*DLUGA_PAUZA_S)
            logging.info(f"☕ Dłuższa przerwa: {pauza:.0f}s")
        else:
            pauza = random.uniform(*PAUZA_MIEDZY_STRONAMI_S)
        time.sleep(pauza)

    def skanuj_marke(self, marka, strona_start=1):
        """Zwraca wynik skanowania. W trybie pełnym zapisuje postęp, żeby kolejne uruchomienie wznowiło od tej strony."""
        strona = strona_start
        duplikaty_pod_rzad = 0
        bledy_pod_rzad = 0
        wymus_url = False
        pierwsza = True

        while strona <= self.max_stron:
            if not self.bez_limitow and strony_dzisiaj(self.conn) >= LIMIT_STRON_DZIENNIE:
                logging.info(f"Dzienny limit {LIMIT_STRON_DZIENNIE} stron wyczerpany. Wznowię jutro.")
                return self.LIMIT
            if not self.bez_limitow and self.strony_w_przebiegu >= LIMIT_STRON_NA_PRZEBIEG:
                logging.info(f"Limit {LIMIT_STRON_NA_PRZEBIEG} stron na przebieg osiągnięty. Kończę, żeby dać IP odpocząć "
                             f"(wznów później; w trybie --pelny postęp jest zapisany).")
                return self.LIMIT
            if not pierwsza:
                self._pauza_miedzy_stronami()
            pierwsza = False

            url = f"{DOMAIN}{marka}/" if strona == 1 else f"{DOMAIN}{marka}/page/{strona}/"
            try:
                start_time = time.time()
                html, blokada = self.sesja.pobierz(url, strona, bezposrednio=wymus_url)
                wymus_url = False
            except Exception as e:
                bledy_pod_rzad += 1
                logging.error(f"Błąd połączenia/timeout na stronie {strona} (próba {bledy_pod_rzad}/{MAX_BLEDOW_POD_RZAD}): {e}")
                if bledy_pod_rzad >= MAX_BLEDOW_POD_RZAD:
                    logging.error(f"Porzucam markę {marka.upper()} na stronie {strona}: {bledy_pod_rzad} błędy pod rząd.")
                    return self.BLAD
                time.sleep(5)
                wymus_url = True
                continue
            bledy_pod_rzad = 0
            dolicz_strone(self.conn)
            self.strony_w_przebiegu += 1

            if blokada:
                n, do = zarejestruj_blokade(self.conn)
                logging.error(f"❌ Blokada ({blokada}) na {marka.upper()} str. {strona}. Kończę przebieg i odpuszczam IP "
                              f"do {do:%Y-%m-%d %H:%M} (blokada nr {n} z rzędu). Jeśli trzeba, rozwiąż captchę w przeglądarce.")
                return self.BLOKADA
            zarejestruj_sukces(self.conn)

            if 'thumbnail offer' not in html:
                if self.sesja.strona_nie_istnieje():
                    logging.info("Koniec ofert dla tej marki (Błąd 404).")
                else:
                    logging.warning("Brak ofert na stronie (koniec marki lub pusta strona).")
                break

            zapisz_surowy_html(html)
            self.sesja.zachowuj_sie_jak_czlowiek()

            samochody = wyciagnij_dane_z_html(html, marka)
            if not samochody:
                break

            auta_do_bazy, zbyt_stare = przygotuj_do_zapisu(samochody)
            zapisane = wstaw_ogloszenia_hurtowo(self.conn, auta_do_bazy) if auta_do_bazy else 0
            logging.info(f"[{marka.upper()}-S{strona}] Zapisano: {zapisane} | (Czas: {time.time() - start_time:.1f}s)")

            if self.pelny:
                zapisz_stan_marki(self.conn, marka, strona + 1, False)

            if auta_do_bazy and zapisane == 0:
                duplikaty_pod_rzad += 1
                if duplikaty_pod_rzad >= self.duplikaty_stop:
                    logging.info(f"🛑 Baza aktualna ({duplikaty_pod_rzad} str. bez nowych aut). Przechodzę do następnej marki.")
                    break
            else:
                duplikaty_pod_rzad = 0

            if zbyt_stare == len(samochody):
                logging.info(f"⏰ Dotarto do aukcji starszych niż {DNI_WSTECZ_POBIERANIE} dni. Idę dalej.")
                break

            strona += 1

        if self.pelny:
            zapisz_stan_marki(self.conn, marka, 1, True)
        usun_stare_rekordy(self.conn)  # po każdej marce, żeby przerwanie skryptu nie zostawiało starych rekordów
        return self.OK

    def uruchom(self, od_nowa=False):
        marki = list(MARKI_DO_POBRANIA)
        random.shuffle(marki)  # inna kolejność przy każdym uruchomieniu zamiast alfabetycznego marszu

        stan = {}
        if self.pelny:
            if od_nowa:
                self.conn.execute("DELETE FROM bidfax_stan_marek")
                self.conn.commit()
            stan = stan_marek_pelny(self.conn)
            marki = [m for m in marki if not stan.get(m, (1, False))[1]]
            if not marki:
                logging.info("Backfill zakończony dla wszystkich marek. Użyj --od-nowa, by zacząć nowy cykl.")
                return
            # Najpierw marki rozpoczęte, żeby domykać je przed ruszeniem nowych
            marki.sort(key=lambda m: stan.get(m, (1, False))[0] == 1)

        marki_z_bledem_pod_rzad = 0
        for marka in marki:
            logging.info(f"\n{'='*50}\nSKANOWANIE MARKI: {marka.upper()} ({'pełne' if self.pelny else 'przyrost'})\n{'='*50}")
            wynik = self.skanuj_marke(marka, stan.get(marka, (1, False))[0])

            if wynik in (self.BLOKADA, self.LIMIT):
                return
            if wynik == self.BLAD:
                marki_z_bledem_pod_rzad += 1
                if marki_z_bledem_pod_rzad >= MAX_MAREK_Z_BLEDEM_POD_RZAD:
                    logging.error(f"❌ {marki_z_bledem_pod_rzad} marki pod rząd porzucone przez błędy. Przerywam skrypt - sprawdź przeglądarkę i sieć.")
                    return
            else:
                marki_z_bledem_pod_rzad = 0
            time.sleep(random.uniform(*PAUZA_MIEDZY_MARKAMI_S))

# ==============================================================================
# GŁÓWNA FUNKCJA
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Scraper Bidfax (przyrostowy lub pełny, wznawiany).")
    parser.add_argument("--pelny", action="store_true", help="backfill do 99 stron na markę, wznawiany między uruchomieniami")
    parser.add_argument("--od-nowa", action="store_true", help="z --pelny: zeruje postęp i zaczyna nowy cykl")
    parser.add_argument("--bez-limitow", action="store_true", help="ignoruje nocną przerwę, dzienny limit i aktywną pauzę po blokadzie")
    args = parser.parse_args()

    conn = zainicjalizuj_baze()
    try:
        if not args.bez_limitow:
            do = blokada_do(conn)
            if do:
                logging.info(f"Pauza po blokadzie do {do:%Y-%m-%d %H:%M}. Nie łączę się z Bidfax.")
                return
            if w_nocnej_przerwie():
                logging.info(f"Nocna przerwa ({NOCNA_PRZERWA_GODZINY[0]}:00-{NOCNA_PRZERWA_GODZINY[1]}:00). Kończę.")
                return
            if strony_dzisiaj(conn) >= LIMIT_STRON_DZIENNIE:
                logging.info(f"Dzienny limit {LIMIT_STRON_DZIENNIE} stron wyczerpany. Kończę.")
                return

        try:
            sesja = Sesja()
        except Exception as e:
            logging.error(f"Nie udało się uruchomić/połączyć z Chrome na porcie {PORT_CHROME}! ({e})")
            return

        logging.info("Przejęto kontrolę nad przeglądarką! Wchodzę na Bidfax...")
        sesja.start()
        Skaner(conn, sesja, args.pelny, args.bez_limitow).uruchom(od_nowa=args.od_nowa)
    except KeyboardInterrupt:
        logging.info("Przerwano działanie skryptu skrótem z klawiatury (Ctrl+C).")
    finally:
        conn.close()
        logging.info("Skrypt zakończył działanie. Rozłączono z bazą.")

if __name__ == "__main__":
    main()
