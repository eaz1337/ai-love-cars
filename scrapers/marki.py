"""Jedna lista marek dla obu scraperów. Slug w URL-ach Otomoto i Bidfax bywa inny ('mclaren' vs 'mclaren-automotive'),
więc każdy wpis ma oba. Marka, której jedna strona nie ma (None), jest pomijana w tym scraperze.

Dodanie marki = jeden wiersz tutaj. Marka tylko na jednej stronie nie trafi do analizy (gold.py łączy obie tabele),
więc robimy tak tylko świadomie: 'alpina' i 'alpine' zostają w Otomoto, bo tak było do tej pory.
"""
from collections import namedtuple

Marka = namedtuple('Marka', 'otomoto bidfax')

MARKI = [
    Marka('acura', 'acura'),
    Marka('alfa-romeo', 'alfa-romeo'),
    Marka('alpina', None),
    Marka('alpine', None),
    Marka('aston-martin', 'aston-martin'),
    Marka('audi', 'audi'),
    Marka('bentley', 'bentley'),
    Marka('bmw', 'bmw'),
    Marka('cadillac', 'cadillac'),
    Marka('chevrolet', 'chevrolet'),
    Marka('chrysler', 'chrysler'),
    Marka('dodge', 'dodge'),
    Marka('ferrari', 'ferrari'),
    Marka('ford', 'ford'),
    Marka('genesis', 'genesis'),
    Marka('gmc', 'gmc'),
    Marka('honda', 'honda'),
    Marka('hyundai', 'hyundai'),
    Marka('infiniti', 'infiniti'),
    Marka('jaguar', 'jaguar'),
    Marka('jeep', 'jeep'),
    Marka('kia', 'kia'),
    Marka('lamborghini', 'lamborghini'),
    Marka('land-rover', 'land-rover'),
    Marka('lexus', 'lexus'),
    Marka('lincoln', 'lincoln'),
    Marka('lotus', 'lotus'),
    Marka('lucid', 'lucid'),
    Marka('maserati', 'maserati'),
    Marka('mazda', 'mazda'),
    Marka('mclaren', 'mclaren-automotive'),
    Marka('mercedes-benz', 'mercedes-benz'),
    Marka('mitsubishi', 'mitsubishi'),
    Marka('nissan', 'nissan'),
    Marka('polestar', 'polestar'),
    Marka('pontiac', 'pontiac'),
    Marka('porsche', 'porsche'),
    Marka('ram', 'ram'),
    Marka('rolls-royce', 'rolls-royce'),
    Marka('saab', 'saab'),
    Marka('subaru', 'subaru'),
    Marka('tesla', 'tesla'),
    Marka('toyota', 'toyota'),
    Marka('volkswagen', 'volkswagen'),
    Marka('volvo', 'volvo'),
]

MARKI_OTOMOTO = [m.otomoto for m in MARKI if m.otomoto]
MARKI_BIDFAX = [m.bidfax for m in MARKI if m.bidfax]
