"""Krisenvorsorge: Abgleich des Vorrats mit der Empfehlung des Österreichischen Zivilschutzverbands.

Quelle: ÖZSV „Vorrats-Checkliste – Wie mache ich meinen Haushalt krisenfest?“ (Vorratsliste,
Mengen pro Person und Woche). Empfohlen wird ein Vorrat für mindestens 7 Tage, besser 2 Wochen.
"""
from __future__ import annotations

import logging
import math
import re
import time
from datetime import date
from typing import Optional

from . import db

log = logging.getLogger("ballager.prep")

SOURCE = "Österreichischer Zivilschutzverband – Vorrats-Checkliste (Vorratsliste pro Person und Woche)"
SOURCE_URL = "https://www.zivilschutz.at/wp-content/uploads/2022/09/Vorrat_Checkliste_Web-small-rgb.pdf"

# Messgrößen: kg, l (Liter), stk (Dosen/Gläser/Packungen)
# per_week: Menge pro Person und Woche; per_person: einmalig pro Person (unabhängig von der Dauer)
# typical: übliche Packungsgröße, wenn nichts angegeben ist (für Schätzung)
GROUPS = [
    {"id": "getraenke", "name": "Wasser & Getränke", "icon": "💧"},
    {"id": "getreide", "name": "Getreideprodukte & Kartoffeln", "icon": "🌾"},
    {"id": "gemuese", "name": "Gemüse, Hülsenfrüchte & Obst", "icon": "🥫"},
    {"id": "milch", "name": "Milch & Milchprodukte", "icon": "🥛"},
    {"id": "fette", "name": "Fette & Öle", "icon": "🫒"},
    {"id": "eiweiss", "name": "Fisch, Fleisch & Aufstriche", "icon": "🐟"},
    {"id": "suesses", "name": "Süßes", "icon": "🍯"},
]

ITEMS: list[dict] = [
    {"id": "wasser", "group": "getraenke", "name": "Mineralwasser", "unit": "l", "per_week": 14, "typical": 1.5,
     "hint": "mind. 2 Liter pro Person und Tag",
     "kw": r"wasser|mineral|vöslauer|voeslauer|römerquelle|gasteiner|juvina|frizzante"},
    {"id": "saft", "group": "getraenke", "name": "Frucht- & Gemüsesäfte", "unit": "l", "per_week": 1, "typical": 1.0,
     "kw": r"saft|nektar"},
    {"id": "mehl", "group": "getreide", "name": "Mehl", "unit": "kg", "per_week": 1, "typical": 1.0,
     "kw": r"mehl(?!speise)|grieß|griess|\bfarina\b|\bflour\b"},
    {"id": "reis", "group": "getreide", "name": "Reis", "unit": "kg", "per_week": 1, "typical": 1.0,
     "kw": r"(?<!milch)reis\b|risotto|basmati|jasmin|langkorn|rundkorn|arborio|carnaroli|\briso\b|\brice\b|\briz\b"},
    {"id": "teigwaren", "group": "getreide", "name": "Teigwaren", "unit": "kg", "per_week": 0.5, "typical": 0.5,
     "kw": r"nudel|spaghetti|penne|fusilli|teigwar|pasta|makkaroni|maccheroni|spätzle|lasagne|farfalle|tagliatelle"},
    {"id": "brot", "group": "getreide", "name": "Brot (haltbar)", "unit": "kg", "per_week": 1, "typical": 0.5,
     "hint": "z.B. Knäckebrot, Zwieback, Schnittbrot in Dosen",
     "kw": r"brot(?!aufstrich)|knäcke|knaecke|zwieback|grissini|cracker|toast"},
    {"id": "kartoffeln", "group": "getreide", "name": "Kartoffeln", "unit": "kg", "per_week": 2, "typical": 2.0,
     "hint": "auch Kartoffelpüree-Pulver",
     "kw": r"kartoffel(?!chip|snack|stick)|erdäpfel(?!chip)|erdaepfel|püree|pueree"},
    {"id": "konserven", "group": "gemuese", "name": "Konservendosen", "unit": "stk", "per_week": 7,
     "hint": "z.B. Tomaten, Pfirsiche, Bohnen, Mais",
     "kw": r"dose|bohnen|mais|erbsen|linsen|kichererbsen|tomate|pfirsich|ananas|birne|obstsalat|fruchtcocktail|eintopf|ravioli|gulasch"},
    {"id": "salate", "group": "gemuese", "name": "Salate im Glas", "unit": "stk", "per_week": 1,
     "kw": r"salat|gurke|gurkerl|rote rüben|rote rueben|sauerkraut|pickles|paprika im glas|oliven"},
    {"id": "milch", "group": "milch", "name": "Haltbarmilch", "unit": "l", "per_week": 1, "typical": 1.0,
     "kw": r"h-milch|haltbarmilch|haltbare milch|(?<![a-zäöü])milch\b|vollmilch|milchpulver|hafermilch|haferdrink|sojadrink"},
    {"id": "kaese", "group": "milch", "name": "Schmelz- oder Hartkäse", "unit": "stk", "per_week": 1,
     "kw": r"käse|kaese|parmesan|emmentaler|bergkäse|gouda|grana"},
    {"id": "streichfett", "group": "fette", "name": "Streichfett", "unit": "kg", "per_week": 0.125, "typical": 0.25,
     "kw": r"butter(?!milch)|margarine|streichfett|schmalz|ghee"},
    {"id": "oel", "group": "fette", "name": "Pflanzliches Öl", "unit": "l", "per_week": 0.25, "typical": 1.0,
     "kw": r"öl\b|oel\b|speiseöl"},
    {"id": "fisch", "group": "eiweiss", "name": "Fischkonserven", "unit": "stk", "per_week": 1,
     "kw": r"thunfisch|sardine|makrele|hering|lachs|fisch|sprotte"},
    {"id": "aufstrich", "group": "eiweiss", "name": "Fleischaufstrich", "unit": "stk", "per_person": 1,
     "hint": "1 Dose pro Person",
     "kw": r"pastete|leberstreich|fleischaufstrich|aufstrich|frühstücksfleisch|corned"},
    {"id": "marmelade", "group": "suesses", "name": "Marmelade oder Honig", "unit": "stk", "per_week": 1,
     "kw": r"marmelade|konfitüre|konfituere|honig|nutella|nougat|fruchtaufstrich"},
]
ITEM_BY_ID = {i["id"]: i for i in ITEMS}
# Reihenfolge der Erkennung: spezifische Begriffe vor allgemeinen (z.B. Fruchtaufstrich vor Aufstrich,
# Thunfisch vor „Dose“, Butter vor Brot)
DETECT_ORDER = ["marmelade", "fisch", "aufstrich", "saft", "milch", "kaese", "streichfett", "oel", "mehl", "reis",
                "teigwaren", "kartoffeln", "brot", "salate", "wasser", "konserven"]

# Open-Food-Facts-Kategorien (categories_tags) -> Vorsorge-Posten. Reihenfolge = Vorrang
# (z.B. Fischkonserve vor allgemeiner Konserve). Bewusst exakte Endungen, damit z.B.
# „en:fats“ (auch bei Öl) nicht als Streichfett zählt und „en:cereals-and-potatoes“ nicht als Kartoffeln.
TAG_RULES: list[tuple[str, str]] = [
    ("fisch", r"^en:(canned-[a-z-]*(fishes|tunas|sardines|mackerels|herrings|salmons?|anchovies)|tunas|sardines|mackerels)$"),
    ("aufstrich", r"^en:([a-z-]+-)?(pates|meat-spreads|liver-sausages?)$"),
    ("marmelade", r"^en:([a-z-]+-)?(jams|marmalades|honeys|hazelnut-spreads|fruit-spreads|jellies)$"),
    ("saft", r"^en:([a-z-]+-)?(juices|nectars)$"),
    ("milch", r"^en:(?!coconut)([a-z-]+-)?(milks|milk-powders|powdered-milks|plant-milks|plant-based-milk-alternatives)$"),
    ("kaese", r"^en:([a-z-]+-)?cheeses$"),
    ("streichfett", r"^en:(butters|salted-butters|unsalted-butters|margarines|spreadable-fats|lards|ghee|clarified-butters)$"),
    ("oel", r"^en:(?!essential)([a-z-]+-)?oils$"),
    ("mehl", r"^en:([a-z-]+-)?flours$"),
    ("reis", r"^en:(?!puffed)([a-z-]+-)?rices$"),
    ("teigwaren", r"^en:([a-z-]+-)?(pastas?|noodles|spaghetti|macaroni|tagliatelle|lasagna-sheets)$"),
    ("brot", r"^en:([a-z-]+-)?(crispbreads|rusks|breads|toasts)$"),
    ("kartoffeln", r"^en:(potatoes|mashed-potatoes|potato-flakes|dehydrated-mashed-potatoes|instant-mashed-potatoes)$"),
    ("salate", r"^en:([a-z-]+-)?(pickles|gherkins|pickled-cucumbers|sauerkrauts|pickled-vegetables|olives)$"),
    ("wasser", r"^en:([a-z-]+-)?waters$"),
    ("konserven", r"^en:canned-"),
]


def detect_from_tags(tags: list[str]) -> Optional[str]:
    """Vorsorge-Posten aus den Open-Food-Facts-Kategorien eines Produkts."""
    tags = [t.lower() for t in tags or []]
    for item_id, pattern in TAG_RULES:
        rx = re.compile(pattern)
        if any(rx.match(t) for t in tags):
            return item_id
    return None

CHECKLIST = [
    {"id": "toilettenpapier", "group": "Körperpflege & Hygiene", "name": "Toilettenpapier", "kw": r"klopapier|toilettenpapier|wc-papier"},
    {"id": "notklo", "group": "Körperpflege & Hygiene", "name": "Not-Toilettenbeutel", "kw": r"toilettenbeutel|notklo|not-wc"},
    {"id": "seife", "group": "Körperpflege & Hygiene", "name": "Seife, Duschgel & Shampoo", "kw": r"seife|duschgel|shampoo"},
    {"id": "damenhygiene", "group": "Körperpflege & Hygiene", "name": "Binden & Tampons", "kw": r"binde|tampon|slipeinlage"},
    {"id": "muellbeutel", "group": "Körperpflege & Hygiene", "name": "Müllbeutel", "kw": r"müllbeutel|muellbeutel|müllsack|mistsack"},
    {"id": "zahnpflege", "group": "Körperpflege & Hygiene", "name": "Zahnbürste & Zahnpasta", "kw": r"zahnpasta|zahncreme|zahnbürste"},
    {"id": "waschmittel", "group": "Körperpflege & Hygiene", "name": "Waschmittel", "kw": r"waschmittel|waschpulver"},
    {"id": "verbandskasten", "group": "Zivilschutz-Apotheke", "name": "Verbandskasten, Pflaster, Verbände", "kw": r"verband|pflaster|mullbinde"},
    {"id": "medikamente", "group": "Zivilschutz-Apotheke", "name": "Arzneimittel & Dauermedikation", "kw": r"tablette|medikament|ibuprofen|paracetamol|insulin", "cat": "Medikamente"},
    {"id": "entkeimung", "group": "Zivilschutz-Apotheke", "name": "Entkeimungstabletten für Wasser", "kw": r"entkeim|micropur|wasserdesinfektion"},
    {"id": "apotheke_sonst", "group": "Zivilschutz-Apotheke", "name": "Fieberthermometer, Desinfektion, FFP2-Masken, Handschuhe", "kw": r"thermometer|desinfektion|ffp2|einweghandschuh"},
    {"id": "radio", "group": "Licht, Kochen & Heizen", "name": "(Kurbel-)Radio, netzunabhängig", "kw": r"radio"},
    {"id": "batterien", "group": "Licht, Kochen & Heizen", "name": "Reservebatterien", "kw": r"batterie|akku"},
    {"id": "lampen", "group": "Licht, Kochen & Heizen", "name": "Taschenlampen / LED-Lampen", "kw": r"taschenlampe|led-lampe|stirnlampe|laterne"},
    {"id": "kocher", "group": "Licht, Kochen & Heizen", "name": "Notkochstelle + Brennstoff (Gaskocher, Brennpaste …)", "kw": r"gaskocher|kartusche|brennpaste|spiritus|campingkocher|esbit"},
    {"id": "bargeld", "group": "Wichtiges", "name": "Bargeld in kleinen Scheinen", "kw": None},
    {"id": "dokumente", "group": "Wichtiges", "name": "Dokumentenmappe (für Evakuierung)", "kw": None},
    {"id": "haustier", "group": "Wichtiges", "name": "Wasser & Futter für Haustiere (falls vorhanden)", "kw": r"tierfutter|katzenfutter|hundefutter", "cat": "Tierbedarf"},
]

DEFAULT_SETTINGS = {"persons": 4, "days": 14, "checklist": {}, "checked": []}


# --------------------------------------------------------------------------
# Mengen erkennen
# --------------------------------------------------------------------------
_UNIT_FACTORS = {"kg": ("kg", 1.0), "g": ("kg", 0.001), "dag": ("kg", 0.01), "l": ("l", 1.0), "ml": ("l", 0.001),
                 "cl": ("l", 0.01), "liter": ("l", 1.0)}
_AMOUNT_RE = re.compile(
    r"(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(kg|dag|g|liter|l|ml|cl)\b", re.IGNORECASE)


def parse_amount(text: str) -> Optional[tuple[float, str]]:
    """'500 g' -> (0.5, 'kg'); '6 x 1,5 l' -> (9.0, 'l'); '1 kg' -> (1.0, 'kg'); unbekannt -> None."""
    if not text:
        return None
    m = _AMOUNT_RE.search(text.replace("½", "0.5"))
    if not m:
        return None
    count = int(m.group(1)) if m.group(1) else 1
    value = float(m.group(2).replace(",", "."))
    dim, factor = _UNIT_FACTORS[m.group(3).lower()]
    total = round(count * value * factor, 4)
    return (total, dim) if total > 0 else None


def detect_item(name: str, category: str = "") -> Optional[str]:
    text = f"{name} ".lower()
    for iid in DETECT_ORDER:
        if re.search(ITEM_BY_ID[iid]["kw"], text):
            return iid
    if (category or "").lower() == "konserven":
        return "konserven"
    return None


def product_contribution(p: dict) -> dict:
    """Bestimmt Vorsorge-Posten und Menge je Stück für ein Produkt (aus inventory.inventory)."""
    choice = p.get("prep_group") or "auto"
    if choice == "none":
        return {"item": None, "auto": False}
    item_id = detect_item(p["name"], p.get("category_name") or "") if choice == "auto" else choice
    if item_id not in ITEM_BY_ID:
        return {"item": None, "auto": choice == "auto"}
    item = ITEM_BY_ID[item_id]
    unit = (p.get("unit") or "Stk").lower()
    each, source = None, None
    if item["unit"] == "stk":
        each, source = 1.0, "stück"
    elif p.get("content_amount"):
        each, source = float(p["content_amount"]), "eingetragen"
    elif unit in ("kg", "g", "l", "ml", "liter"):
        dim, factor = _UNIT_FACTORS[unit if unit != "liter" else "l"]
        each, source = factor, "einheit"
    else:
        parsed = parse_amount(p.get("package_size") or "") or parse_amount(p.get("name") or "")
        if parsed:
            each, source = parsed[0], "packung"   # kg und l werden 1:1 gezählt (Öl, Milch …)
        else:
            each, source = float(item.get("typical") or 1.0), "geschätzt"
    return {"item": item_id, "auto": choice == "auto", "each": each, "source": source}


# --------------------------------------------------------------------------
# Auswertung
# --------------------------------------------------------------------------
def get_settings(conn) -> dict:
    s = db.get_setting(conn, "prep") or {}
    out = dict(DEFAULT_SETTINGS)
    out.update({k: v for k, v in s.items() if k in DEFAULT_SETTINGS})
    return out


def evaluate(conn, products: list[dict]) -> dict:
    cfg = get_settings(conn)
    persons, days = int(cfg["persons"]), int(cfg["days"])
    weeks = days / 7
    today = date.today().isoformat()
    have: dict[str, float] = {i["id"]: 0.0 for i in ITEMS}
    contributors: dict[str, list] = {i["id"]: [] for i in ITEMS}
    estimated: list[dict] = []
    expired_note: dict[str, float] = {i["id"]: 0.0 for i in ITEMS}
    for p in products:
        c = product_contribution(p)
        if not c.get("item"):
            continue
        valid = sum(b["quantity"] for b in p["batches"] if not b["expiry_date"] or b["expiry_date"] >= today)
        expired = sum(b["quantity"] for b in p["batches"] if b["expiry_date"] and b["expiry_date"] < today)
        amount = valid * c["each"]
        have[c["item"]] += amount
        expired_note[c["item"]] += expired * c["each"]
        contributors[c["item"]].append({"id": p["id"], "name": p["name"], "brand": p.get("brand", ""),
                                        "qty": valid, "expired_qty": expired, "unit": p.get("unit"),
                                        "each": c["each"], "amount": round(amount, 3), "source": c["source"],
                                        "auto": c["auto"]})
        if c["source"] == "geschätzt" and p["total"]:
            estimated.append({"id": p["id"], "name": p["name"], "item": c["item"]})

    groups = []
    pct_sum, n = 0.0, 0
    for g in GROUPS:
        rows = []
        for it in (i for i in ITEMS if i["group"] == g["id"]):
            target = it["per_person"] * persons if "per_person" in it else it["per_week"] * persons * weeks
            target = round(target, 3) if it["unit"] != "stk" else math.ceil(target - 1e-9)
            h = round(have[it["id"]], 3)
            pct = min(1.0, h / target) if target else 1.0
            pct_sum += pct
            n += 1
            rows.append({"id": it["id"], "name": it["name"], "unit": it["unit"], "hint": it.get("hint", ""),
                         "target": target, "have": h, "missing": round(max(0.0, target - h), 3), "pct": round(pct, 3),
                         "expired": round(expired_note[it["id"]], 3),
                         "products": sorted(contributors[it["id"]], key=lambda x: -x["amount"])})
        groups.append({**g, "items": rows})

    water = next(r for gg in groups for r in gg["items"] if r["id"] == "wasser")
    water_days = water["have"] / (2 * persons) if persons else 0

    # Checkliste: automatisch erkannt (im Vorrat) oder von Hand abgehakt
    manual = cfg.get("checklist") or {}
    checklist = []
    for c in CHECKLIST:
        found = []
        for p in products:
            if not p["total"]:
                continue
            if (c.get("kw") and re.search(c["kw"], p["name"].lower())) or (c.get("cat") and p.get("category_name") == c["cat"]):
                found.append(f"{p['total']} {p.get('unit') or 'Stk'} {p['name']}")
        checklist.append({"id": c["id"], "group": c["group"], "name": c["name"], "found": found[:3],
                          "checked": bool(manual.get(c["id"])) or bool(found)})
    done = sum(1 for c in checklist if c["checked"])
    return {
        "persons": persons, "days": days, "source": SOURCE, "source_url": SOURCE_URL,
        "score": round(pct_sum / n, 3) if n else 0, "water_days": round(water_days, 1),
        "unmatched": len(abgleich_candidates(products, cfg)),
        "groups": groups, "estimated": estimated,
        "checklist": checklist, "checklist_done": done, "checklist_total": len(checklist),
        "items": [{"id": i["id"], "name": i["name"], "unit": i["unit"], "group": i["group"]} for i in ITEMS],
    }


def save_settings(conn, data: dict) -> dict:
    cfg = get_settings(conn)
    if "persons" in data:
        cfg["persons"] = max(1, min(30, int(data["persons"])))
    if "days" in data:
        cfg["days"] = max(1, min(365, int(data["days"])))
    if isinstance(data.get("checklist"), dict):
        valid = {c["id"] for c in CHECKLIST}
        cfg["checklist"] = {k: bool(v) for k, v in data["checklist"].items() if k in valid}
    db.set_setting(conn, "prep", cfg)
    return cfg


def abgleich_candidates(products: list[dict], cfg: dict) -> list[dict]:
    """Produkte mit Barcode, die (noch) keinem Vorsorge-Posten zugeordnet sind und nie nachgeschlagen wurden."""
    checked = set(cfg.get("checked") or [])
    return [p for p in products
            if p.get("barcode") and (p.get("prep_group") or "auto") == "auto"
            and p["id"] not in checked and not product_contribution(p).get("item")]


def mark_checked(conn, ids: list[int]) -> None:
    cfg = get_settings(conn)
    cfg["checked"] = sorted(set(cfg.get("checked") or []) | set(ids))[-5000:]
    db.set_setting(conn, "prep", cfg)


def abgleich(limit: int = 40, pause: float = 0.0) -> dict:
    """Ordnet noch nicht erkannte Produkte über ihren Barcode (Open Food Facts) einem Vorsorge-Posten zu.

    Jedes Produkt wird nur einmal nachgeschlagen. Ist keine Datenbank erreichbar, wird abgebrochen
    und NICHTS als geprüft markiert – beim nächsten Mal wird es erneut versucht.
    """
    from . import inventory, lookup  # lokal: vermeidet Import-Kreis (lookup nutzt prep)
    with db.session() as conn:
        cands = abgleich_candidates(inventory.inventory(conn), get_settings(conn))
    assigned, checked_ids, offline = [], [], False
    for i, p in enumerate(cands[:limit]):
        if i and pause:
            time.sleep(pause)
        found, reached = lookup.lookup_checked(p["barcode"])
        if not reached:
            offline = True
            break
        checked_ids.append(p["id"])
        if found and found.get("prep_group") not in (None, "auto"):
            with db.session() as conn:
                with db.tx(conn):
                    conn.execute("UPDATE products SET prep_group=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                                 "WHERE id=? AND prep_group='auto'", (found["prep_group"], p["id"]))
            assigned.append({"id": p["id"], "name": p["name"], "item": found["prep_name"]})
    if checked_ids:
        with db.session() as conn:
            with db.tx(conn):
                mark_checked(conn, checked_ids)
    if assigned:
        log.info("Vorsorge-Abgleich: %s", ", ".join(f"{a['name']} → {a['item']}" for a in assigned))
    return {"checked": len(checked_ids), "assigned": assigned,
            "remaining": len(cands) - len(checked_ids), "offline": offline}
