"""Produktsuche über die freien Open-Facts-Datenbanken (Lebensmittel, Drogerie, Haushalt)."""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Optional

from . import __version__, prep

log = logging.getLogger("ballager.lookup")

SOURCES = [
    ("Open Food Facts", "https://world.openfoodfacts.org/api/v2/product/{code}.json", None),
    ("Open Beauty Facts", "https://world.openbeautyfacts.org/api/v2/product/{code}.json", "Hygiene & Drogerie"),
    ("Open Products Facts", "https://world.openproductsfacts.org/api/v2/product/{code}.json", "Haushalt"),
]
FIELDS = "product_name,product_name_de,generic_name_de,brands,categories_tags,image_front_small_url,quantity"
UA = f"BALLager/{__version__} (Home Assistant Add-on; private household inventory)"

# Schlagworte in den Open-Facts-Kategorien -> BALLager-Kategorie
CATEGORY_RULES: list[tuple[str, str]] = [
    (r"canned|conserve|konserv", "Konserven"),
    (r"flour|mehl|baking|backzutat|sugars|yeast|hefe", "Mehl & Backen"),
    (r"pasta|noodle|nudel|rice|reis|cereal|grain|getreide|muesli|oat|legume|huelsen", "Nudeln, Reis & Getreide"),
    (r"spice|gewuerz|condiment|sauce|oil|oel|vinegar|essig|salt|salz|ketchup|mustard|senf", "Gewürze, Öle & Saucen"),
    (r"chocolate|schokolad|sweet|candy|confection|biscuit|cookie|keks|snack|chips|crisps", "Süßes & Snacks"),
    (r"beverage|getraenk|water|wasser|juice|saft|drink|coffee|kaffee|tea|tee|beer|bier|wine|wein", "Getränke"),
    (r"frozen|tiefkuehl|ice-cream", "Tiefkühl"),
    (r"dair|milk|milch|cheese|kaese|yogurt|joghurt|butter|cream", "Milchprodukte"),
    (r"pet-food|tiernahrung|cat-food|dog-food", "Tierbedarf"),
]


def guess_category(tags: list[str]) -> Optional[str]:
    joined = " ".join(tags).lower()
    for pattern, cat in CATEGORY_RULES:
        if re.search(pattern, joined):
            return cat
    return None


def lookup(code: str) -> Optional[dict]:
    return lookup_checked(code)[0]


def lookup_checked(code: str) -> tuple[Optional[dict], bool]:
    """Wie lookup(), meldet zusätzlich, ob überhaupt eine Datenbank geantwortet hat.

    (None, True)  = erreichbar, Produkt aber unbekannt
    (None, False) = keine Verbindung -> später nochmal versuchen
    """
    code = re.sub(r"\D", "", code or "")
    if len(code) < 6:
        return None, True
    reached = False
    for source, url, fallback_cat in SOURCES:
        try:
            req = urllib.request.Request(
                url.format(code=code) + f"?fields={FIELDS}",
                headers={"User-Agent": UA, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            reached = True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # Datenbank erreichbar, Produkt unbekannt
                reached = True
            else:
                log.info("Suche bei %s fehlgeschlagen: HTTP %s", source, exc.code)
            continue
        except Exception as exc:  # Netzwerk weg, Timeout …
            log.info("Suche bei %s fehlgeschlagen: %s", source, exc)
            continue
        if data.get("status") != 1 or not data.get("product"):
            continue
        p = data["product"]
        name = (p.get("product_name_de") or p.get("product_name") or p.get("generic_name_de") or "").strip()
        if not name:
            continue
        brand = (p.get("brands") or "").split(",")[0].strip()
        tags = p.get("categories_tags") or []
        prep_item = prep.detect_from_tags(tags)
        return {
            "prep_group": prep_item or "auto",
            "prep_name": prep.ITEM_BY_ID[prep_item]["name"] if prep_item else "",
            "barcode": code,
            "name": name,
            "brand": brand,
            "package_size": re.sub(r"\s*(℮|\be)$", "", (p.get("quantity") or "").strip()),
            "image_url": p.get("image_front_small_url") or "",
            "category_name": guess_category(p.get("categories_tags") or []) or fallback_cat,
            "source": source,
        }, True
    return None, reached
