import argparse
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.conad.it"
PAGE_URL = BASE + "/prodotti-e-marchi/bassi-e-fissi"
LOADER_URL = PAGE_URL + "/_jcr_content/root/rl1_layout/rc152_switch_compone.jloader.loader.json?sliceIndex={index}"
STORE_URL = BASE + "/ricerca-negozi/conad-superstore-via-retella-ex-giarddel-sole-snc-81020-capodrise--010548"

STORE_CODE = "010548"
STORE_NAME = "Conad Superstore Capodrise"
STORE_ADDRESS = "Via Retella Ex Giard.del Sole, SNC, 81020 Capodrise (CE)"
EXPECTED_STORE_COOP = "PAC 2000A"

PRICE_RE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*€")
COUNT_RE = re.compile(r"(\d+)\s+prodotti", re.I)
VALIDITY_RE = re.compile(r"Dal\s+(\d{1,2})/(\d{1,2})\s+al\s+(\d{1,2})/(\d{1,2})", re.I)

# Quantità: prima prova i multipack (es. 3 x 80 g), poi l'ultima quantità esplicita.
MULTIPACK_RE = re.compile(
    r"(?<!\d)(\d{1,3})\s*[xX×]\s*(\d+(?:[.,]\d+)?)\s*(kg|g|l|lt|ml|cl)\b",
    re.I,
)
SIMPLE_QTY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|l|lt|ml|cl)\b", re.I)


def fnum(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def parse_quantity(name: str):
    matches = list(MULTIPACK_RE.finditer(name))
    if matches:
        m = matches[-1]
        count = int(m.group(1))
        each = fnum(m.group(2))
        unit = m.group(3).upper()
        if unit == "LT":
            unit = "L"
        return round(count * each, 6), unit, f"{count}x{m.group(2)} {m.group(3)}"

    matches = list(SIMPLE_QTY_RE.finditer(name))
    if matches:
        m = matches[-1]
        qty = fnum(m.group(1))
        unit = m.group(2).upper()
        if unit == "LT":
            unit = "L"
        return qty, unit, m.group(0)
    return None, None, None


def calc_unit_price(price, qty, unit):
    if price is None or qty is None or not unit or qty <= 0:
        return None, None
    if unit == "G":
        return round(price / (qty / 1000.0), 4), "EUR/KG"
    if unit == "KG":
        return round(price / qty, 4), "EUR/KG"
    if unit == "ML":
        return round(price / (qty / 1000.0), 4), "EUR/L"
    if unit == "CL":
        return round(price / (qty / 100.0), 4), "EUR/L"
    if unit == "L":
        return round(price / qty, 4), "EUR/L"
    return None, None


def parse_card(card):
    code = (card.get("data-code") or card.get("data-ean") or "").strip()
    name = (card.get("data-nome") or "").strip()
    if not code or not name:
        return None

    price_el = card.select_one(".rt213-card-product-flyer__finalPrice")
    validity_el = card.select_one(".rt213-card-product-flyer__validity")
    img_el = card.select_one("img[data-src], img[src]")
    price_text = price_el.get_text(" ", strip=True) if price_el else ""
    m = PRICE_RE.search(price_text)
    if not m:
        return None
    price = fnum(m.group(1))
    if price <= 0:
        return None

    qty, unit, qty_label = parse_quantity(name)
    unit_price, unit_price_unit = calc_unit_price(price, qty, unit)
    image = ""
    if img_el:
        image = (img_el.get("data-src") or img_el.get("src") or "").strip()

    validity = validity_el.get_text(" ", strip=True) if validity_el else ""
    return {
        "code": code,
        "name": name,
        "price": price,
        "validity": validity,
        "quantity_value": qty,
        "quantity_unit": unit,
        "quantity_label": qty_label,
        "unit_price": unit_price,
        "unit_price_unit": unit_price_unit,
        "image_url": image,
    }


def parse_cards(html: str):
    soup = BeautifulSoup(html, "html.parser")
    products = []
    for card in soup.select(".rt213-card-product-flyer"):
        p = parse_card(card)
        if p:
            products.append(p)
    return products


def verify_validity(validities):
    parsed = []
    now = datetime.now()
    for v in sorted(set(x for x in validities if x)):
        m = VALIDITY_RE.search(v)
        if not m:
            continue
        d1, mo1, d2, mo2 = map(int, m.groups())
        start = datetime(now.year, mo1, d1)
        end = datetime(now.year, mo2, d2, 23, 59, 59)
        # Gestione intervallo a cavallo dell'anno.
        if end < start:
            if now < start:
                start = datetime(now.year - 1, mo1, d1)
            else:
                end = datetime(now.year + 1, mo2, d2, 23, 59, 59)
        parsed.append((v, start, end, start <= now <= end))
    if not parsed:
        raise RuntimeError("Validità Bassi e Fissi non riconosciuta.")
    if not any(x[3] for x in parsed):
        raise RuntimeError(f"Nessuna validità Bassi e Fissi attiva oggi: {[x[0] for x in parsed]}")
    return parsed


def create_db(products, path):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    con = sqlite3.connect(path)
    con.executescript("""
    DROP TABLE IF EXISTS products_current;
    DROP TABLE IF EXISTS price_history;
    DROP TABLE IF EXISTS update_log;
    CREATE TABLE products_current(
      supermarket TEXT NOT NULL, store_code TEXT NOT NULL, store_name TEXT,
      store_address TEXT, product_code TEXT NOT NULL, product_name TEXT NOT NULL,
      brand TEXT, category1 TEXT, category2 TEXT, category3 TEXT,
      quantity_value REAL, quantity_unit TEXT, price_eur REAL NOT NULL,
      unit_price REAL, unit_price_unit TEXT, bassi_fissi INTEGER NOT NULL DEFAULT 0,
      image_url TEXT, source_queries TEXT, checked_at TEXT NOT NULL,
      PRIMARY KEY(store_code,product_code)
    );
    CREATE TABLE price_history(
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_code TEXT NOT NULL,
      product_code TEXT NOT NULL, price_eur REAL NOT NULL, unit_price REAL,
      checked_at TEXT NOT NULL
    );
    CREATE TABLE update_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT NOT NULL,
      store_code TEXT, query TEXT, declared_total INTEGER, saved_count INTEGER,
      status TEXT, message TEXT
    );
    CREATE TABLE source_meta(
      key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    """)
    rows = []
    for p in products:
        rows.append((
            "Conad", STORE_CODE, STORE_NAME, STORE_ADDRESS,
            p["code"], p["name"], "CONAD", "Bassi e Fissi", None, None,
            p["quantity_value"], p["quantity_unit"], p["price"],
            p["unit_price"], p["unit_price_unit"], 1,
            p["image_url"], "official_bassi_fissi_current", now,
        ))
    con.executemany("INSERT INTO products_current VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany(
        "INSERT INTO price_history(store_code,product_code,price_eur,unit_price,checked_at) VALUES (?,?,?,?,?)",
        [(STORE_CODE, p["code"], p["price"], p["unit_price"], now) for p in products],
    )
    con.execute(
        "INSERT INTO update_log(checked_at,store_code,query,declared_total,saved_count,status,message) VALUES (?,?,?,?,?,?,?)",
        (now, STORE_CODE, "official_bassi_fissi_current", len(products), len(products), "OK",
         "Fonte pubblica ufficiale Conad Bassi e Fissi; per Capodrise viene usato solo il paniere Bassi e Fissi, non il catalogo completo del punto vendita."),
    )
    meta = {
        "source": PAGE_URL,
        "store_reference": STORE_URL,
        "store_code": STORE_CODE,
        "store_name": STORE_NAME,
        "scope": "BASSI_E_FISSI_ONLY",
        "checked_at": now,
    }
    con.executemany("INSERT INTO source_meta(key,value) VALUES (?,?)", list(meta.items()))
    con.commit()
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="prezzi_conad_capodrise.db")
    ap.add_argument("--audit", default="conad_bassifissi_audit.json")
    ap.add_argument("--expected", type=int, default=796)
    args = ap.parse_args()

    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
        "Referer": PAGE_URL,
    })

    # Verifica pagina del punto vendita: non usa la sessione HeyConad protetta.
    sr = s.get(STORE_URL, timeout=40)
    sr.raise_for_status()
    store_text = BeautifulSoup(sr.text, "html.parser").get_text(" ", strip=True)
    store_ok = (STORE_CODE in sr.text) and ("Capodrise" in store_text)
    if not store_ok:
        raise RuntimeError("Pagina ufficiale del punto vendita 010548 non verificata.")

    # Prima slice: oltre ai prodotti comunica il numero totale di slice e prodotti.
    r0 = s.get(LOADER_URL.format(index=0), timeout=40)
    r0.raise_for_status()
    j0 = r0.json()
    html0 = j0.get("data", {}).get("html", "")
    if not html0:
        raise RuntimeError("Slice 0 Bassi e Fissi vuota.")
    soup0 = BeautifulSoup(html0, "html.parser")
    n_el = soup0.select_one(".rt150-disaggregated-flyer__numberOfSlices")
    c_el = soup0.select_one(".rt150-disaggregated-flyer__productsCount")
    if not n_el or not c_el:
        raise RuntimeError("Contatori Bassi e Fissi non trovati.")
    slices = int(n_el.get_text(strip=True))
    declared = int(c_el.get_text(strip=True))

    by_code = {}
    slice_counts = []
    validity_values = []

    for idx in range(slices):
        if idx == 0:
            html = html0
        else:
            rr = s.get(LOADER_URL.format(index=idx), timeout=40)
            rr.raise_for_status()
            html = rr.json().get("data", {}).get("html", "")
        pp = parse_cards(html)
        slice_counts.append(len(pp))
        if not pp:
            raise RuntimeError(f"Slice {idx}/{slices-1} senza prodotti.")
        for p in pp:
            by_code[p["code"]] = p
            if p["validity"]:
                validity_values.append(p["validity"])
        print(f"slice {idx+1}/{slices}: {len(pp)} prodotti; unici={len(by_code)}", flush=True)

    products = list(by_code.values())
    validity_check = verify_validity(validity_values)

    if declared != len(products):
        raise RuntimeError(f"Completezza fallita: Conad dichiara {declared}, unici estratti {len(products)}, slice={slice_counts}")
    if args.expected and declared != args.expected:
        raise RuntimeError(f"Totale inatteso: attesi {args.expected}, Conad dichiara {declared}.")

    positive = sum(1 for p in products if p["price"] > 0)
    qty_known = sum(1 for p in products if p["quantity_value"] is not None)
    if positive != declared:
        raise RuntimeError(f"Prezzi positivi {positive}/{declared}, aggiornamento rifiutato.")

    create_db(products, args.db)

    audit = {
        "verdict": "CONAD_BASSI_FISSI_CURRENT_VALIDATED",
        "store_code": STORE_CODE,
        "store_name": STORE_NAME,
        "store_page_verified": store_ok,
        "scope": "BASSI_E_FISSI_ONLY",
        "source_url": PAGE_URL,
        "store_url": STORE_URL,
        "declared_products": declared,
        "unique_products": len(products),
        "positive_prices": positive,
        "quantity_parsed": qty_known,
        "slices": slices,
        "slice_counts": slice_counts,
        "validities": [
            {"label": v, "start": a.isoformat(), "end": b.isoformat(), "active": active}
            for v, a, b, active in validity_check
        ],
        "sample": products[:12],
        "note": "Database del paniere ufficiale Conad Bassi e Fissi. Non rappresenta il catalogo completo del punto vendita 010548; eventuali offerte locali PAC saranno aggiunte separatamente solo se verificate.",
    }
    Path(args.audit).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "verdict": audit["verdict"],
        "store": STORE_CODE,
        "declared": declared,
        "saved": len(products),
        "positive": positive,
        "quantity_parsed": qty_known,
        "slices": slices,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
