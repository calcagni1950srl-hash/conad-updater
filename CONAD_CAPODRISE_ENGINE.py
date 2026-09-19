# USER_AUTHORIZED_V81_FIVE_REFERENCES_2026_09_19
import hashlib
import html
import json
import re
import shutil
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import fitz
import requests

STORE_CODE = "010548"
STORE_NAME = "Conad Superstore Capodrise"
STORE_ADDRESS = "Via Retella ex Giard. del Sole, SNC, 81020 Capodrise (CE)"
STORE_PAGE = "https://www.conad.it/ricerca-negozi/conad-superstore-via-retella-ex-giarddel-sole-snc-81020-capodrise--010548"
STORE_API = "https://www.conad.it/api/corporate/it-it.getPointOfServiceByAnacanId.json?anacanId=010548"
FULL_DB = Path("prezzi_conad_capodrise.db")
APP_DB = Path("prezzi_conad_capodrise_app.db")
AUDIT = Path("conad_capodrise_audit.json")

MONTHS = {
    "GENNAIO": 1, "FEBBRAIO": 2, "MARZO": 3, "APRILE": 4,
    "MAGGIO": 5, "GIUGNO": 6, "LUGLIO": 7, "AGOSTO": 8,
    "SETTEMBRE": 9, "OTTOBRE": 10, "NOVEMBRE": 11, "DICEMBRE": 12,
}

NON_FOOD_BASE = {
    "Cura persona", "Articoli per la casa", "Prima infanzia", "Animali domestici"
}

STOP_PATTERNS = [
    r"^SOLO TITOLARI", r"^OLARI$", r"^MASSIMO ACQUISTABILE",
    r"^\d+ PEZZI ASSORTITI$", r"^\d+ PEZZI$",
    r"^OFFERTA VALIDA", r"^dal \d+", r"^SETTEMBRE$",
    r"^DA MERCOLED", r"^A DOMENICA", r"^2026$",
    r"^SUPER OFFERTA", r"^SUP$", r"^O$", r"^PER O$",
    r"^\d+$", r"^-$", r"^%$", r"^-\d+",
]

UNIT_RE = re.compile(r"€\s*([0-9]+[,.][0-9]{2})\s*al\s*(Kg|L)", re.I)
PRICE_INT_RE = re.compile(r"\d{1,3}")
PRICE_DEC_RE = re.compile(r",[0-9]{2}")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (SmartCampania public-price updater)",
    "Accept-Language": "it-IT,it;q=0.9",
})


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def normalized(value):
    value = (value or "").replace("ﬁ", "fi").replace("ﬀ", "ff").replace("’", "'")
    value = value.lower()
    value = re.sub(r"[^a-z0-9àèéìòù%]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def verify_store():
    r = SESSION.get(STORE_API, timeout=60)
    r.raise_for_status()
    raw = r.text
    low = raw.lower()
    if STORE_CODE not in raw or "capodrise" not in low:
        raise RuntimeError("La API ufficiale non conferma il punto vendita 010548 Capodrise.")
    return {
        "status": r.status_code,
        "has_store_code": STORE_CODE in raw,
        "has_capodrise": "capodrise" in low,
        "has_pac": "pac" in low,
    }


def flyer_urls_from_store_page():
    r = SESSION.get(STORE_PAGE, timeout=60)
    r.raise_for_status()
    body = html.unescape(r.text)
    found = []
    for url in re.findall(
        r'https://www\.conad\.it/assets/common/volantini/[^"\s<]+?\.pdf(?:\?[^"\s<]*)?',
        body,
        re.I,
    ):
        url = url.replace("&amp;", "&")
        path = urlsplit(url).path.lower()
        if "superstore_campania.pdf" not in path:
            continue
        key = url.split("?", 1)[0]
        if key not in [x.split("?", 1)[0] for x in found]:
            found.append(url)
    if not found:
        raise RuntimeError("Nessun volantino Superstore Campania trovato nella pagina ufficiale 010548.")
    return found


def parse_validity(text):
    compact = clean_text(text).upper()
    patterns = [
        re.compile(
            r"OFFERTA\s+VALIDA\s+DA\s+[A-ZÀ-Ù]+\s+(\d{1,2})\s+A\s+[A-ZÀ-Ù]+\s+(\d{1,2})\s+"
            r"(GENNAIO|FEBBRAIO|MARZO|APRILE|MAGGIO|GIUGNO|LUGLIO|AGOSTO|SETTEMBRE|OTTOBRE|NOVEMBRE|DICEMBRE)\s+(20\d{2})"
        ),
        re.compile(
            r"DA\s+[A-ZÀ-Ù]+\s+(\d{1,2})\s+A\s+[A-ZÀ-Ù]+\s+(\d{1,2})\s+"
            r"(GENNAIO|FEBBRAIO|MARZO|APRILE|MAGGIO|GIUGNO|LUGLIO|AGOSTO|SETTEMBRE|OTTOBRE|NOVEMBRE|DICEMBRE)\s+(20\d{2})"
        ),
    ]
    for pat in patterns:
        m = pat.search(compact)
        if m:
            d1, d2, month_name, year = m.groups()
            month = MONTHS[month_name]
            return date(int(year), month, int(d1)), date(int(year), month, int(d2))
    return None, None


def choose_active_flyer():
    today = date.today()
    attempts = []
    for url in flyer_urls_from_store_page():
        rr = SESSION.get(url, timeout=120)
        rr.raise_for_status()
        pdf = fitz.open(stream=rr.content, filetype="pdf")
        text = "\n".join(page.get_text("text") for page in pdf)
        start, end = parse_validity(text)
        row = {
            "url": url,
            "bytes": len(rr.content),
            "pages": len(pdf),
            "valid_from": start.isoformat() if start else None,
            "valid_to": end.isoformat() if end else None,
        }
        attempts.append(row)
        if start and end and start <= today <= end:
            return rr.content, row, attempts
    raise RuntimeError(
        "Nessun volantino Superstore Campania ufficiale risulta valido oggi. "
        + json.dumps(attempts, ensure_ascii=False)
    )


def page_entries(page):
    out = []
    for block_index, block in enumerate(page.get_text("dict").get("blocks", [])):
        if "lines" not in block:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = clean_text(span.get("text"))
                if not text:
                    continue
                out.append({
                    "text": text,
                    "bbox": [float(v) for v in span.get("bbox", [])],
                    "size": float(span.get("size") or 0),
                    "block_index": block_index,
                })
    return out


def detect_prices(entries):
    integers = []
    decimals = []
    for span in entries:
        text = span["text"]
        if span["size"] >= 40 and PRICE_INT_RE.fullmatch(text):
            integers.append(span)
        if span["size"] >= 20 and PRICE_DEC_RE.fullmatch(text):
            decimals.append(span)

    prices = []
    used_decimal = set()
    for integer in integers:
        ix1, iy1, ix2, iy2 = integer["bbox"]
        candidates = []
        for idx, decimal in enumerate(decimals):
            if idx in used_decimal:
                continue
            dx = abs(decimal["bbox"][0] - ix2)
            dy = abs(decimal["bbox"][1] - (iy1 + (iy2 - iy1) * 0.4))
            if dx < 10 and dy < 45:
                candidates.append((dx + dy / 10.0, idx, decimal))
        if not candidates:
            continue
        _, idx, decimal = min(candidates, key=lambda item: item[0])
        used_decimal.add(idx)
        price = float(integer["text"] + "." + decimal["text"][1:])
        x = (ix1 + decimal["bbox"][2]) / 2.0
        y = (iy1 + iy2) / 2.0

        duplicate = any(
            abs(p["x"] - x) < 2 and abs(p["y"] - y) < 2 and abs(p["price"] - price) < 0.001
            for p in prices
        )
        if duplicate:
            continue

        prices.append({
            "price": price,
            "x": x,
            "y": y,
            "blocks": {integer["block_index"], decimal["block_index"]},
        })
    return sorted(prices, key=lambda p: (p["y"], p["x"]))


def build_cards(page):
    entries = page_entries(page)
    prices = detect_prices(entries)
    cards = []

    for index, price in enumerate(prices):
        desc_candidates = []
        for span in entries:
            text = span["text"]
            if UNIT_RE.search(text):
                continue
            x = (span["bbox"][0] + span["bbox"][2]) / 2.0
            y = (span["bbox"][1] + span["bbox"][3]) / 2.0
            dy = price["y"] - y
            if not (-25 <= dy <= 160):
                continue
            if abs(x - price["x"]) > 145 or span["size"] > 12.5:
                continue

            eligible = []
            for other_index, other in enumerate(prices):
                other_dy = other["y"] - y
                if -25 <= other_dy <= 160 and abs(x - other["x"]) <= 145:
                    score = abs(x - other["x"]) / 100.0 + abs(other_dy) / 160.0
                    if other_dy < 0:
                        score += 0.2
                    eligible.append((score, other_index))
            if eligible and min(eligible, key=lambda item: item[0])[1] != index:
                continue
            desc_candidates.append((y, x, text))

        desc_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        lines = []
        for _, _, text in desc_candidates:
            if any(re.search(pattern, text, re.I) for pattern in STOP_PATTERNS):
                continue
            if text == "€" or PRICE_DEC_RE.fullmatch(text):
                continue
            if len(text) < 2:
                continue
            if not lines or lines[-1] != text:
                lines.append(text)

        unit_candidates = []
        for span in entries:
            text = span["text"]
            match = UNIT_RE.search(text)
            if not match:
                continue
            x = (span["bbox"][0] + span["bbox"][2]) / 2.0
            y = (span["bbox"][1] + span["bbox"][3]) / 2.0
            same_block = span["block_index"] in price["blocks"]
            dx = abs(x - price["x"])
            dy = abs(y - price["y"])
            if same_block or (dx < 90 and dy < 70):
                score = (0 if same_block else 10) + dx + dy * 0.5
                unit_candidates.append((score, text))
        unit_text = min(unit_candidates, key=lambda item: item[0])[1] if unit_candidates else None

        cards.append({
            "price": price["price"],
            "x": round(price["x"], 2),
            "y": round(price["y"], 2),
            "description": clean_text(" ".join(lines)),
            "unit_text": unit_text,
        })
    return cards


def parse_unit_price(text):
    if not text:
        return None, None
    match = UNIT_RE.search(text)
    if not match:
        return None, None
    value = float(match.group(1).replace(",", "."))
    unit = "EUR/KG" if match.group(2).lower() == "kg" else "EUR/L"
    return value, unit


def parse_quantity(description, unit_text):
    text = normalized(description)
    if "all etto" in text:
        return 0.1, "KG", True, "etto"

    values = []
    multipack = re.compile(r"(\d+)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(kg|g|ml|cl|l)\b", re.I)
    for m in multipack.finditer(text):
        count = int(m.group(1))
        qty = float(m.group(2).replace(",", "."))
        unit = m.group(3).lower()
        if unit == "g":
            values.append((count * qty / 1000.0, "KG", m.group(0)))
        elif unit == "kg":
            values.append((count * qty, "KG", m.group(0)))
        elif unit == "ml":
            values.append((count * qty / 1000.0, "L", m.group(0)))
        elif unit == "cl":
            values.append((count * qty / 100.0, "L", m.group(0)))
        elif unit == "l":
            values.append((count * qty, "L", m.group(0)))

    masked = multipack.sub(" ", text)
    single = re.compile(r"(?<![\d/])(\d+(?:[.,]\d+)?)\s*(kg|g|ml|cl|l)\b", re.I)
    for m in single.finditer(masked):
        qty = float(m.group(1).replace(",", "."))
        unit = m.group(2).lower()
        if unit == "g":
            values.append((qty / 1000.0, "KG", m.group(0)))
        elif unit == "kg":
            values.append((qty, "KG", m.group(0)))
        elif unit == "ml":
            values.append((qty / 1000.0, "L", m.group(0)))
        elif unit == "cl":
            values.append((qty / 100.0, "L", m.group(0)))
        elif unit == "l":
            values.append((qty, "L", m.group(0)))

    unique = []
    seen = set()
    for value, unit, raw in values:
        key = (round(value, 4), unit)
        if key not in seen:
            seen.add(key)
            unique.append((value, unit, raw))

    if len(unique) == 1:
        value, unit, raw = unique[0]
        return value, unit, False, raw
    if len(unique) > 1:
        return None, None, False, "multiple_quantities"

    raw = clean_text((unit_text or "") + " " + description)
    if re.search(r"€\s*al\s*kg\b", raw, re.I) or re.search(r"\bal\s*kg\b", description, re.I):
        return 1.0, "KG", True, "per_kg"
    if re.search(r"€\s*al\s*l\b", raw, re.I):
        return 1.0, "L", True, "per_l"
    return None, None, False, "no_quantity"


def classify_food(description):
    text = normalized(description)

    exclude = [
        "detersiv", "sapone", "shampoo", "balsamo", "deodorante", "dentifric",
        "forcelle", "scovolino", "bagno doccia", "pianta ", "vaso da",
        "alimento per gatti", "pannolini", "ambipur", "oxi action",
        "vino ", "spumante", "grappa", "whiskey", "gin ", "amaro ", "birra ",
        "lavatrice", "televisore", "smart tv", "friggitrice", "caffettiera",
        "profumo", "disinfettante", "gel mani", "amuchina",
    ]
    if any(word in text for word in exclude):
        return None

    groups = [
        ("Pasta e riso", ["pasta ", "riso ", "farina ", "gnocch", "lasagn", "risott"]),
        ("Formaggi, latte e uova", [
            "mozzarella", "formaggio", "philadelphia", "robiola", "provolone",
            "galbanino", "sottilette", "yogurt", "latte ", "burro", "ricotta",
            "stracciatella", "grana ", "parmig", "pecorino", "fruttolo",
        ]),
        ("Carne e salumi", [
            "pollo", "tacchino", "prosciutto", "salame", "mortadella", "bresaola",
            "salsiccia", "bovino", "suino", "hamburger", "pancetta", "lonza",
            "carne ", "cordon bleu", "birbe",
        ]),
        ("Pesce", [
            "merluzzo", "pesce", "alici", "acciugh", "polpo", "gamber",
            "trance di", "mare ", "salmone", "trota", "orata", "tonno",
        ]),
        ("Frutta e verdura", [
            "banana", "mela", "mele ", "insalata", "cavolini", "patate",
            "fagiolini", "anguria", "susine", "mirtilli", "verdure", "peperoni",
            "uva ", "prugne", "frutta", "ortaggi",
        ]),
        ("Condimenti e conserve", [
            "passata", "pomodor", "pelati", "olio ", "aceto", "pesto", "legumi",
            "fagioli", "ceci", "lenticchie", "salse", "salsa ", "capperi", "olive",
        ]),
        ("Surgelati e gelati", ["gelato", "surgel", "sofficini", "minestrone", "pizza "]),
        ("Panetteria e snack salati", [
            "cracker", "pane ", "pan bauletto", "tortillas", "gallette",
            "patatine", "arachidi", "snack",
        ]),
        ("Biscotti, cereali e dolci", [
            "nutella", "biscott", "plumcake", "pavesini", "croissant", "fagottino",
            "confettura", "cereale", "special k", "oro saiwa", "mousse",
        ]),
        ("Bevande e preparati", [
            "caffe", "te ", "bevanda", "succo", "nettare", "estath",
            "red bull", "analcolico", "crodino",
        ]),
    ]
    for category, words in groups:
        if any(word in text for word in words):
            return category
    return None


def clean_description(description):
    text = clean_text(description)
    text = re.sub(r"\b€\s*al\s*(?:kg|l|pezzo)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def accepted_offer(card, page_no):
    description = clean_description(card["description"])
    low = normalized(description)

    if not description or len(description) < 4 or len(description) > 260:
        return None, "bad_description"
    if "a partire da" in low:
        return None, "starting_from"
    if any(junk in low for junk in ("approvata anche da", "regolamento completo", "gustour it", "sfoglia il catalogo")):
        return None, "marketing_or_legal"
    if card["price"] <= 0 or card["price"] > 60:
        return None, "price_out_of_range"

    quantity, quantity_unit, variable_weight, quantity_reason = parse_quantity(description, card["unit_text"])
    if quantity is None or quantity <= 0 or quantity > 10:
        return None, "quantity_ambiguous"

    unit_price, unit_price_unit = parse_unit_price(card["unit_text"])
    if unit_price is not None:
        expected = unit_price * quantity
        tolerance = max(0.10, card["price"] * 0.04)
        if abs(expected - card["price"]) > tolerance:
            return None, "unit_price_mismatch"

    category = classify_food(description)
    if not category:
        return None, "not_food_or_not_useful"

    if unit_price is None:
        unit_price = round(card["price"] / quantity, 4)
        unit_price_unit = "EUR/KG" if quantity_unit == "KG" else "EUR/L"

    stable = normalized(description)
    code = "FLYER:" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:20].upper()

    return {
        "product_code": code,
        "product_name": description,
        "category1": category,
        "quantity_value": round(quantity, 4),
        "quantity_unit": quantity_unit,
        "price_eur": round(card["price"], 2),
        "unit_price": round(unit_price, 4),
        "unit_price_unit": unit_price_unit,
        "variable_weight": bool(variable_weight),
        "page": page_no,
        "quantity_reason": quantity_reason,
        "unit_text": card["unit_text"],
    }, None


def parse_flyer(pdf_bytes):
    pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    accepted = []
    rejected = {}
    raw_cards = 0

    for page_no, page in enumerate(pdf, 1):
        cards = build_cards(page)
        raw_cards += len(cards)
        for card in cards:
            offer, reason = accepted_offer(card, page_no)
            if offer is None:
                rejected[reason] = rejected.get(reason, 0) + 1
            else:
                accepted.append(offer)

    dedup = {}
    for offer in accepted:
        code = offer["product_code"]
        current = dedup.get(code)
        if current is None or offer["price_eur"] < current["price_eur"]:
            dedup[code] = offer

    return list(dedup.values()), {
        "raw_price_cards": raw_cards,
        "accepted_before_dedup": len(accepted),
        "accepted_unique": len(dedup),
        "rejected": rejected,
    }


def ensure_schema_extensions(con):
    columns = {row[1] for row in con.execute("PRAGMA table_info(products_current)")}
    if "variable_weight" not in columns:
        con.execute("ALTER TABLE products_current ADD COLUMN variable_weight INTEGER NOT NULL DEFAULT 0")


def apply_local_offers(offers, flyer_info):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    con = sqlite3.connect(FULL_DB)
    ensure_schema_extensions(con)

    con.execute("DELETE FROM products_current WHERE product_code LIKE 'FLYER:%'")
    con.execute("DELETE FROM price_history WHERE product_code LIKE 'FLYER:%'")

    source_label = "PAC_SUPERSTORE_CAMPANIA:" + Path(urlsplit(flyer_info["url"]).path).name

    for offer in offers:
        row = (
            "Conad", STORE_CODE, STORE_NAME, STORE_ADDRESS,
            offer["product_code"], offer["product_name"], None,
            offer["category1"], "Offerta locale", None,
            offer["quantity_value"], offer["quantity_unit"], offer["price_eur"],
            offer["unit_price"], offer["unit_price_unit"], 0,
            None, source_label, now, int(offer["variable_weight"]),
        )
        con.execute(
            """
            INSERT OR REPLACE INTO products_current(
              supermarket,store_code,store_name,store_address,product_code,product_name,
              brand,category1,category2,category3,quantity_value,quantity_unit,price_eur,
              unit_price,unit_price_unit,bassi_fissi,image_url,source_queries,checked_at,
              variable_weight
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            row,
        )
        con.execute(
            "INSERT INTO price_history(store_code,product_code,price_eur,unit_price,checked_at) VALUES(?,?,?,?,?)",
            (STORE_CODE, offer["product_code"], offer["price_eur"], offer["unit_price"], now),
        )

    con.execute(
        """
        INSERT INTO update_log(checked_at,store_code,query,declared_total,saved_count,status,message)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            now, STORE_CODE, "PAC_SUPERSTORE_CAMPANIA",
            len(offers), len(offers), "OK",
            "Offerte alimentari estratte dal volantino Superstore Campania ufficiale "
            "collegato alla pagina Conad del punto vendita 010548; parser geometrico fail-closed.",
        ),
    )
    con.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", ("local_flyer_url", flyer_info["url"]))
    con.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", ("local_flyer_valid_from", flyer_info["valid_from"]))
    con.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", ("local_flyer_valid_to", flyer_info["valid_to"]))
    con.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", ("local_offer_count", str(len(offers))))
    con.commit()
    con.close()



VISUAL_VERIFIED_OFFERS_V81 = [
    {
        # Volantino ufficiale PAC "Spunta il Risparmio", collegato al PDV 010548.
        # PDF image-only: riga verificata visivamente, NON ottenuta con OCR.
        "product_code": "VISUAL:SPUNTA2026_ACETO_BIANCO_PONTI_1L",
        "product_name": "Aceto di vino bianco Ponti 1 L",
        "brand": "Ponti",
        "category1": "Condimenti e conserve",
        "category2": "Aceti",
        "quantity_value": 1.0,
        "quantity_unit": "L",
        "price_eur": 1.19,
        "unit_price": 1.19,
        "unit_price_unit": "EUR/L",
        "variable_weight": False,
        "valid_from": "2026-08-01",
        "valid_to": "2026-09-30",
        "source_url": "https://www.conad.it/assets/common/volantini/pac/v2026-/2026-4-spunta-campania.pdf",
        "source_label": "OFFICIAL_FLYER_VISUAL_VERIFIED_V81|SPUNTA_RISPARMIO_CAMPANIA|NO_OCR",
    },
]


def apply_visual_verified_offers():
    """
    V81: overlay strettamente limitato a righe di volantini ufficiali image-only
    verificate visivamente. Nessun OCR e nessun prezzo stimato.
    Le righe scadono automaticamente fuori dal periodo ufficiale.
    """
    today = date.today()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    con = sqlite3.connect(FULL_DB)
    ensure_schema_extensions(con)

    con.execute("DELETE FROM products_current WHERE product_code LIKE 'VISUAL:%'")
    con.execute("DELETE FROM price_history WHERE product_code LIKE 'VISUAL:%'")

    inserted = []
    expired = []
    for spec in VISUAL_VERIFIED_OFFERS_V81:
        valid_from = date.fromisoformat(spec["valid_from"])
        valid_to = date.fromisoformat(spec["valid_to"])
        if not (valid_from <= today <= valid_to):
            expired.append({
                "product_code": spec["product_code"],
                "valid_from": spec["valid_from"],
                "valid_to": spec["valid_to"],
            })
            continue

        price = float(spec["price_eur"])
        qty = float(spec["quantity_value"])
        if price <= 0 or qty <= 0:
            con.close()
            raise RuntimeError("Offerta visuale V81 con prezzo/quantita non validi")

        row = (
            "Conad", STORE_CODE, STORE_NAME, STORE_ADDRESS,
            spec["product_code"], spec["product_name"], spec.get("brand"),
            spec["category1"], spec.get("category2"), "Offerta ufficiale verificata visivamente",
            qty, spec["quantity_unit"], round(price, 2),
            float(spec["unit_price"]), spec["unit_price_unit"], 0,
            None, spec["source_label"] + "|" + spec["source_url"], now,
            int(bool(spec.get("variable_weight", False))),
        )
        con.execute(
            """
            INSERT OR REPLACE INTO products_current(
              supermarket,store_code,store_name,store_address,product_code,product_name,
              brand,category1,category2,category3,quantity_value,quantity_unit,price_eur,
              unit_price,unit_price_unit,bassi_fissi,image_url,source_queries,checked_at,
              variable_weight
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            row,
        )
        con.execute(
            "INSERT INTO price_history(store_code,product_code,price_eur,unit_price,checked_at) VALUES(?,?,?,?,?)",
            (STORE_CODE, spec["product_code"], round(price, 2), float(spec["unit_price"]), now),
        )
        inserted.append({
            "product_code": spec["product_code"],
            "product_name": spec["product_name"],
            "price_eur": round(price, 2),
            "quantity_value": qty,
            "quantity_unit": spec["quantity_unit"],
            "valid_from": spec["valid_from"],
            "valid_to": spec["valid_to"],
            "source_scope": "OFFICIAL_FLYER_VISUAL_VERIFIED_V81",
        })

    con.execute(
        """
        INSERT INTO update_log(checked_at,store_code,query,declared_total,saved_count,status,message)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            now, STORE_CODE, "OFFICIAL_FLYER_VISUAL_VERIFIED_V81",
            len(VISUAL_VERIFIED_OFFERS_V81), len(inserted), "OK",
            "V81: sole righe di volantini ufficiali image-only verificate visivamente; "
            "nessun OCR e nessun prezzo stimato; scadenza automatica.",
        ),
    )
    con.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
        ("visual_verified_offer_count", str(len(inserted))),
    )
    con.commit()
    con.close()
    return {"inserted": inserted, "expired": expired}




FALLBACK_SPECS = {
    # V81: riferimenti fissi SOLO per piccoli ingredienti-base che altrimenti
    # bloccano il menu. Non sono prezzi Conad/Capodrise.
    #
    # SOLO questi cinque riferimenti medi sono approvati dall'utente.
    # Non aggiungere altri prezzi fissi senza nuova autorizzazione.
    "aglio": {
        "product_code": "REF:AVG_AGLIO",
        "product_name": "Aglio fresco - prezzo medio di mercato",
        "brand": None,
        "category1": "Frutta e verdura",
        "category2": "Aromi freschi",
        "quantity_value": 0.050,
        "quantity_unit": "KG",
        "price_eur": 0.40,
        "variable_weight": True,
    },
    "cipolla": {
        "product_code": "REF:AVG_CIPOLLA",
        "product_name": "Cipolla - prezzo medio di mercato",
        "brand": None,
        "category1": "Frutta e verdura",
        "category2": "Ortaggi",
        "quantity_value": 0.250,
        "quantity_unit": "KG",
        "price_eur": 0.45,
        "variable_weight": True,
    },
    "prezzemolo": {
        "product_code": "REF:AVG_PREZZEMOLO",
        "product_name": "Prezzemolo fresco - prezzo medio di mercato",
        "brand": None,
        "category1": "Frutta e verdura",
        "category2": "Erbe aromatiche",
        "quantity_value": 0.030,
        "quantity_unit": "KG",
        "price_eur": 0.36,
        "variable_weight": True,
    },
    "rosmarino": {
        "product_code": "REF:AVG_ROSMARINO",
        "product_name": "Rosmarino - prezzo medio di mercato",
        "brand": None,
        "category1": "Condimenti e conserve",
        "category2": "Erbe aromatiche",
        "quantity_value": 0.030,
        "quantity_unit": "KG",
        "price_eur": 1.47,
        "variable_weight": True,
    },
    "peperoncino": {
        "product_code": "REF:AVG_PEPERONCINO",
        "product_name": "Peperoncino - prezzo medio di mercato",
        "brand": None,
        "category1": "Condimenti e conserve",
        "category2": "Spezie",
        "quantity_value": 0.020,
        "quantity_unit": "KG",
        "price_eur": 0.67,
        "variable_weight": True,
    },
}

_STANDALONE_ALLOWED_CATEGORIES = {
    "frutta e verdura",
    "condimenti e conserve",
    "surgelati e gelati",
}

_STANDALONE_EXCLUDES = {
    "aglio": ("senza aglio", "pesto", "spiedini", "gratinat", "sugo", "salsa"),
    "cipolla": ("focaccia", "spianatina", "marinat", "borettane", "agrodolce", "sottolio", "sottaceto"),
    "prezzemolo": ("gratinat", "spiedini", "filetto", "merluzzo"),
    "rosmarino": ("snack", "cracker", "tarall", "patatin", "merluzzo", "hamburger", "costine", "patate al rosmarino"),
    "peperoncino": ("sugo", "salsa", "tarall", "tonno", "sgombro", "formaggio", "salame", "olio aromatizzato", "condimento aromatizzato"),
}


def is_standalone_ingredient_product(product_name, category1, ingredient):
    name = normalized(product_name)
    category = normalized(category1)
    if category not in _STANDALONE_ALLOWED_CATEGORIES:
        return False
    if not re.search(r"\b" + re.escape(ingredient) + r"\b", name):
        return False
    if any(blocked in name for blocked in _STANDALONE_EXCLUDES.get(ingredient, ())):
        return False
    return True


def standalone_ingredient_exists(con, ingredient):
    rows = con.execute(
        "SELECT product_name, category1 FROM products_current WHERE price_eur > 0"
    ).fetchall()
    return any(
        is_standalone_ingredient_product(name, category, ingredient)
        for name, category in rows
    )


def apply_fixed_reference_prices():
    """
    V81: SOLO aglio, cipolla, prezzemolo, rosmarino e peperoncino possono usare
    i riferimenti medi approvati dall'utente. Sono usati solo se non esiste gia' un prodotto
    standalone valido da Capodrise/PAC/Bassi e Fissi.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    con = sqlite3.connect(FULL_DB)
    ensure_schema_extensions(con)

    con.execute("DELETE FROM products_current WHERE product_code LIKE 'REF:%'")
    con.execute("DELETE FROM price_history WHERE product_code LIKE 'REF:%'")

    inserted = []
    skipped = []

    for ingredient, spec in FALLBACK_SPECS.items():
        if standalone_ingredient_exists(con, ingredient):
            skipped.append({
                "ingredient": ingredient,
                "reason": "current_local_or_bassi_fissi_product_exists",
            })
            continue

        price = float(spec["price_eur"])
        qty = float(spec["quantity_value"])
        if price <= 0 or qty <= 0:
            con.close()
            raise RuntimeError(f"Prezzo fisso non valido per {ingredient}")

        unit_price = round(price / qty, 4)
        source_label = "FIXED_MARKET_AVERAGE_V81|LOW_IMPACT_STAPLES|2026-09-19"

        row = (
            "Conad", STORE_CODE, STORE_NAME, STORE_ADDRESS,
            spec["product_code"], spec["product_name"], spec["brand"],
            spec["category1"], spec["category2"], "Prezzo riferimento fisso V81",
            qty, spec["quantity_unit"], round(price, 2),
            unit_price, "EUR/KG", 0,
            None, source_label, now, int(bool(spec.get("variable_weight", False))),
        )
        con.execute(
            """
            INSERT OR REPLACE INTO products_current(
              supermarket,store_code,store_name,store_address,product_code,product_name,
              brand,category1,category2,category3,quantity_value,quantity_unit,price_eur,
              unit_price,unit_price_unit,bassi_fissi,image_url,source_queries,checked_at,
              variable_weight
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            row,
        )
        con.execute(
            "INSERT INTO price_history(store_code,product_code,price_eur,unit_price,checked_at) VALUES(?,?,?,?,?)",
            (STORE_CODE, spec["product_code"], round(price, 2), unit_price, now),
        )
        inserted.append({
            "ingredient": ingredient,
            "product_code": spec["product_code"],
            "product_name": spec["product_name"],
            "price_eur": round(price, 2),
            "quantity_value": qty,
            "quantity_unit": spec["quantity_unit"],
            "reference_scope": "FIXED_MARKET_AVERAGE_V81",
        })

    missing_after = [
        ingredient for ingredient in FALLBACK_SPECS
        if not standalone_ingredient_exists(con, ingredient)
    ]
    if missing_after:
        con.rollback()
        con.close()
        raise RuntimeError(
            "Ingredienti base Conad ancora scoperti dopo i prezzi fissi: "
            + ", ".join(missing_after)
        )

    con.execute(
        """
        INSERT INTO update_log(checked_at,store_code,query,declared_total,saved_count,status,message)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            now, STORE_CODE, "FIXED_MARKET_AVERAGE_V81",
            len(FALLBACK_SPECS), len(inserted), "OK",
            "V81: prezzi medi di mercato fissi approvati dall'utente SOLO per aglio, cipolla, prezzemolo, "
            "rosmarino e peperoncino; non sono prezzi Conad/Capodrise e sono usati solo se le fonti reali non coprono il prodotto.",
        ),
    )
    con.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
        ("reference_fallback_count", str(len(inserted))),
    )
    con.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
        ("reference_fallback_scope", "FIXED_MARKET_AVERAGE_V81"),
    )
    con.execute(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
        ("reference_fallback_policy", "LOW_IMPACT_STAPLES_FIXED_5_APPROVED_2026-09-19"),
    )
    con.commit()
    con.close()

    return {
        "inserted": inserted,
        "skipped": skipped,
        "missing_after": missing_after,
        "scope": "FIXED_MARKET_AVERAGE_V81",
    }

def build_app_db():
    shutil.copy2(FULL_DB, APP_DB)
    con = sqlite3.connect(APP_DB)
    ensure_schema_extensions(con)
    placeholders = ",".join("?" for _ in NON_FOOD_BASE)
    con.execute(
        "DELETE FROM products_current WHERE category1 IN (" + placeholders + ")",
        tuple(sorted(NON_FOOD_BASE)),
    )
    con.commit()
    count = con.execute("SELECT COUNT(*) FROM products_current WHERE price_eur > 0").fetchone()[0]
    local = con.execute("SELECT COUNT(*) FROM products_current WHERE product_code LIKE 'FLYER:%'").fetchone()[0]
    stores = [row[0] for row in con.execute("SELECT DISTINCT store_code FROM products_current")]
    con.close()
    return {"rows": count, "local_offers": local, "stores": stores}


def database_stats(path):
    con = sqlite3.connect(path)
    ensure_schema_extensions(con)
    stats = {
        "rows": con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0],
        "positive": con.execute("SELECT COUNT(*) FROM products_current WHERE price_eur > 0").fetchone()[0],
        "bassi_fissi": con.execute("SELECT COUNT(*) FROM products_current WHERE bassi_fissi=1").fetchone()[0],
        "local_offers": con.execute("SELECT COUNT(*) FROM products_current WHERE product_code LIKE 'FLYER:%'").fetchone()[0],
        "reference_fallbacks": con.execute("SELECT COUNT(*) FROM products_current WHERE product_code LIKE 'REF:%'").fetchone()[0],
        "store_codes": [row[0] for row in con.execute("SELECT DISTINCT store_code FROM products_current")],
    }
    con.close()
    return stats


def main():
    if not FULL_DB.exists():
        raise RuntimeError("Manca prezzi_conad_capodrise.db: eseguire prima CONAD_BASSI_FISSI_ENGINE.py")

    store_check = verify_store()
    pdf_bytes, flyer_info, flyer_attempts = choose_active_flyer()
    offers, parser_audit = parse_flyer(pdf_bytes)

    if len(offers) < 40:
        raise RuntimeError(
            "Parser volantino troppo povero: solo %d offerte affidabili; DB precedente lasciato invariato."
            % len(offers)
        )

    apply_local_offers(offers, flyer_info)
    visual_audit = apply_visual_verified_offers()
    fallback_audit = apply_fixed_reference_prices()
    app_stats = build_app_db()
    full_stats = database_stats(FULL_DB)

    if full_stats["rows"] != full_stats["positive"]:
        raise RuntimeError("DB Conad contiene prezzi non positivi.")
    if full_stats["bassi_fissi"] < 700:
        raise RuntimeError("Copertura Bassi e Fissi insufficiente.")
    if full_stats["local_offers"] < 40:
        raise RuntimeError("Offerte locali insufficienti.")
    if full_stats["store_codes"] != [STORE_CODE]:
        raise RuntimeError("Nel DB è presente uno store_code diverso da 010548.")
    if app_stats["rows"] < 600 or app_stats["local_offers"] < 40:
        raise RuntimeError("DB Android alimentare insufficiente.")

    audit = {
        "status": "OK",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "store_code": STORE_CODE,
        "store_name": STORE_NAME,
        "store_address": STORE_ADDRESS,
        "store_api": store_check,
        "flyer": flyer_info,
        "flyer_candidates": flyer_attempts,
        "parser": parser_audit,
        "visual_verified_offers": visual_audit,
        "fixed_reference_prices": fallback_audit,
        "full_db": full_stats,
        "app_db": app_stats,
        "offer_samples": offers[:30],
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": audit["status"],
        "store_code": STORE_CODE,
        "flyer_valid_from": flyer_info["valid_from"],
        "flyer_valid_to": flyer_info["valid_to"],
        "accepted_local_offers": len(offers),
        "visual_verified_offers": len(visual_audit["inserted"]),
        "reference_fallbacks": full_stats["reference_fallbacks"],
        "full_rows": full_stats["rows"],
        "app_rows": app_stats["rows"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
