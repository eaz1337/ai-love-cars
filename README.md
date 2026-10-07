# ai-love-cars

A system for finding profitable cars at foreign auctions. It collects sales history from Copart and IAAI (USA) and from Japanese auctions, compares it with the Polish used-car market (Otomoto), identifies the models with the best profit potential, and finally values today's auction lots and gives you the maximum amount worth bidding.

## How it works

The project has four stages. The first two are implemented in this repository, the last two are planned.

1. Collecting final prices from US auctions (Copart, IAAI) via Bidfax. Done.
2. Collecting listings and price history from Otomoto. Done.
3. Profitability analysis: comparing auction prices with a forecast of Polish market prices six months ahead, including a parts price index for repair costs, and picking the most profitable models. Planned.
4. Valuing current auction lots from the full description and photos, and calculating the maximum bid. Planned.

Japanese auctions will be added as another source alongside Bidfax, using the same data schema, so the analysis works the same way for both import routes.

## Repository contents

| File | Purpose |
|---|---|
| `scraper_bidfax.py` | Scrapes finished Copart and IAAI auctions from en.bidfax.info |
| `scraper_otomoto.py` | Scrapes listings from otomoto.pl together with their price history |

Both scripts write to the same SQLite file, `samochody.db` (tables `bidfax_scd2` and `otomoto_scd2`), so the data can be joined by make, model and year.

## scraper_bidfax.py

The script walks through Bidfax result pages for each make and stores, for every lot: make, model, year, VIN, final price in USD, auction (Copart or IAAI), location, lot number, condition, damage type, mileage, sale date and link.

Bidfax is protected against bots, so the script drives a real Chrome browser through DrissionPage. It does not start Chrome itself: it attaches to a browser you launched with remote debugging on port 9222. It clicks through the pagination like a person, scrolls the page and uses random delays between pages and a short pause between makes. If it hits a 403 or a Cloudflare challenge, it waits `PAUZA_BLOKADA_S` (120 s) so you can change IP or pass the check in the browser, then reloads the page by URL. After `MAX_BLOKAD_POD_RZAD` (3) blocks in a row it stops. If a pagination button is missing, it opens the page URL directly.

The scope is set by constants at the top of the file. `MARKI_DO_POBRANIA` is the list of makes, `DNI_WSTECZ_POBIERANIE` is how far back to go (365 days by default), and `MIN_ROK_PRODUKCJI` and `MAX_ROK_PRODUKCJI` cut off cars that are too old or too new (1950 to next year by default). Scanning a make stops on its own when three pages in a row consist of duplicates, or when all auctions on a page are older than the configured range. Records older than `OKNO_HISTORII_DNI` are deleted after each make. A missing price or year is stored as `NULL`, not as 0, so it does not distort medians. The model is stored without the make, also for makes with a hyphen (`Mercedes-Benz E350` becomes `E350`).

## scraper_otomoto.py

The script works in two phases.

Phase 1 is a fast scan of listing pages. Pages are fetched in parallel (batches of 20 pages, 6 threads) with `curl_cffi` impersonating Chrome, and data is read from the `__NEXT_DATA__` JSON embedded in the HTML. The filter covers cars registered in Poland, sold by private sellers, for 30 selected makes, newest first. The list of makes is in `WYBRANE_MARKI`. Pages that fail after all retries are logged and counted.

Phase 2 fills in details from individual listing pages (8 threads): the full description, accident-free status, first owner, damage, seller name and the date the seller joined Otomoto. Listings that return 404 or 410 are marked as ended. A failed download is retried in later runs (`Proby_Detali`, `MAX_PROB_DETALI` = 3 attempts) and never writes an error text into `Opis`. When a price changes, the new row inherits the details of the previous one, so the page is not downloaded again.

Data is stored using the SCD2 model. Every price change closes the current row (`Wazne_Do`) and opens a new one (`Wazne_Od`), so each car keeps its full price history. Listings that disappeared from the results get the status `Zakończone`, which makes it possible to estimate time to sell. This only happens after a complete scan: if any page failed, the scan did not reach the end of the results, it returned no listings at all, or more than 30% of active listings would be closed at once, the script logs an error and closes nothing. This protects the history against blocks, page structure changes and Otomoto's page limit.

The script also detects clones, meaning the same car relisted under a new ID (same title, year, mileage, seller, fuel and gearbox; cars without year or mileage are never matched). A clone is stored as a new row that inherits `Data_Dodania` and has `Ilosc_Powtorzen` increased by one. The old row is left untouched, so price history is never overwritten. Note that a relisted car also ends the old listing as `Zakończone`, so that status does not always mean the car was sold; use `Ilosc_Powtorzen` and `Data_Dodania` to tell relistings apart.

On 403/429 responses or Cloudflare challenges the script tries to switch servers through the NordVPN CLI. If `nordvpn` is not installed, rotation is skipped and the script just retries.

## Installation

Python 3.10 or newer is required.

```bash
pip install beautifulsoup4 lxml DrissionPage curl_cffi
```

`scraper_bidfax.py` needs Google Chrome installed. The browser path (`/usr/bin/google-chrome`) and the profile path (`--user-data-dir`) are hardcoded in `main()`, so change them to your own before the first run. `scraper_otomoto.py` optionally uses the NordVPN CLI.

## Usage

```bash
python scraper_bidfax.py
python scraper_otomoto.py
```

Both scripts can be run daily. Subsequent runs only add new records and update changes, so the database grows incrementally.

## Data schema

`bidfax_scd2`: Marka, Model, Rocznik, Tytul, VIN, Cena_USD, Aukcja, Lokalizacja, Lot_Number, Condition, Damage, Mileage, Date_Of_Sale, Link.

`otomoto_scd2`: ID_Ogloszenia, Marka, Model, Tytul, Cena_PLN, Przebieg_km, Paliwo, Rok_produkcji, Pojemnosc_cm3, Moc_KM, Skrzynia_biegow, Kraj_pochodzenia, Bezwypadkowy, Pierwszy_wlasciciel, Uszkodzony, Miejsce, Sprzedawca, Nazwa_Sprzedawcy, Opis, Sprzedawca_Od, Link, Data_Dodania, Data_Ostatniego_Widzenia, Status, Wazne_Od, Wazne_Do, Ilosc_Powtorzen.

## Roadmap

### Japanese auctions

A separate scraper for Japanese auctions that writes prices to the database in a format compatible with Bidfax (make, model, year, price, condition, auction grade, mileage, sale date).

### Profitability analysis

Comparing the expected resale price in Poland (PLN) for a given make, model and year with the median final auction price (USD or JPY), after currency conversion and adding import costs: transport, customs duty, excise, VAT, auction fees, repairs and a safety margin.

Repair cost is not a flat estimate. It is calculated with a parts price index that tracks how much spare parts cost for a given make and model, so the same damage is priced differently for a Porsche than for a Ford. The data source for the index is still to be decided.

The expected resale price is not today's Otomoto price. Organizing the import and bringing a car back to top condition is expected to take about six months, so the system forecasts what a given make, model and year will be worth in six months. The forecast is based on price history from the Otomoto SCD2 data (price changes and time to sell) and on depreciation by age and mileage, and that forecast is what the auction price is compared against.

The output is a ranking of models by expected profit and by time to sell derived from listing history. Matching models requires normalizing names, because Bidfax stores the model together with the trim (e.g. `Stelvio Ti Awd`), while Otomoto gives the model alone.

### Valuing current lots

Fetching lots scheduled for today together with their full description and photos. An AI model assesses the extent of damage from the description and photos and estimates the repair cost using the parts price index, then compares that with the forecast resale price in Poland six months from now. The output is a valuation of the car and the maximum amount worth bidding, so that the target margin remains after all costs.

## Notes

The scripts collect data from third-party websites. Check the terms of service of Bidfax and Otomoto before use and keep request rates reasonable. Otomoto data includes seller information, so do not publish or share it further.

