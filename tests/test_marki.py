import pytest

import marki
import scraper_bidfax as bf
import scraper_otomoto as om


def test_scrapery_uzywaja_wspolnej_listy():
    assert bf.MARKI_DO_POBRANIA == marki.MARKI_BIDFAX
    assert om.WYBRANE_MARKI == marki.MARKI_OTOMOTO


def test_slugi_sie_nie_powtarzaja():
    assert len(set(marki.MARKI_OTOMOTO)) == len(marki.MARKI_OTOMOTO)
    assert len(set(marki.MARKI_BIDFAX)) == len(marki.MARKI_BIDFAX)


def test_wspolne_marki_lacza_sie_w_analizie():
    """Marka z obu stron musi po normalizacji w gold.py dać ten sam klucz, inaczej merge ją po cichu zgubi."""
    gold = pytest.importorskip("gold")
    for m in marki.MARKI:
        if m.otomoto and m.bidfax:
            assert gold.normalizuj_marke(m.otomoto) == gold.normalizuj_marke(m.bidfax), m
