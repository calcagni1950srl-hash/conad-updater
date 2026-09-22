import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.lidl.it"
OUT_DB = "prezzi_lidl_probe.db"
OUT_JSON = "lidl_probe_audit.json"

CATEGORY_URLS = [
    "https://www.lidl.it/h/frutta-e-verdura/h10071012",
    "https://www.lidl.it/h/carne-e-pollame/h10095752",
    "https://www.lidl.it/h/pesce-e-frutti-di-mare/h10095756",
    "https://www.lidl.it/h/formaggi-latticini-e-uova/h10095761",
    "https://www.lidl.it/h/panetteria/h10095920",
    "https://www.lidl.it/h/dispensa/h10096095",
    "https://www.lidl.it/h/olio-salse-e-condimenti/h10096110",
    "https://www.lidl.it/h/piatti-pronti/h10096112",
    "https://www.lidl.it/h/cereali-e-creme-spalmabili/h10096114",
    "https://www.lidl.it/h/surgelati/h10096116",
    "https://www.lidl.it/h/dolciumi-e-snack/h10096118",
    "https://www.lidl.it/h/bevande/h10096120",
    "https://www.lidl.it/h/caffe-te-e-cacao/h10096128",
]

UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
}

PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{2}))\s*€")
QTY_RE = re.compile(
    r"(?i)(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*"
    r"(kg|g|gr|l|lt|ml|cl)\b"
)
UNIT_PRICE_RE = re.compile(
    r"(?i)1\s*(kg|l|lt)\s*=\s*(\d+(?:[.,]\d+)?)\s*€"
)

session = requests.Session()
session.headers.update(UA)

def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def parse_number(s):
    return float(s.replace(".", "").replace(",", ".")) if s else None

def canonical_unit(u):
    u = (u or "").lower()
    if u in {"g", "gr"}:
        return "g"
    if u == "kg":
        return "kg"
    if u in {"l", "lt"}:
        return "l"
    if u == "ml":
        return "ml"
    if u == "cl":
        return "cl"
    return u

def fetch(url):
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def discover_product_links():
    links = set()
    category_stats = {}
    for url in CATEGORY_URLS:
        html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        found = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/p/" in href:
                full = urljoin(BASE, href.split("?")[0])
                found.add(full)
                links.add(full)
        category_stats[url] = len(found)
        print(url, len(found))
        time.sleep(0.3)
    return sorted(links), category_stats

def parse_jsonld(soup):
    objs = []
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        txt = node.string or node.get_text(" ", strip=True)
        if not txt:
            continue
        try:
            data = json.loads(txt)
        except Exception:
            continue
        if isinstance(data, list):
            objs.extend(data)
        else:
            objs.append(data)
    return objs

def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)

def parse_product(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    text = clean(soup.get_text(" ", strip=True))

    h1 = soup.find("h1")
    name = clean(h1.get_text(" ", strip=True) if h1 else "")
    if not name:
        title = soup.find("title")
        name = clean(title.get_text(" ", strip=True) if title else "").split("|")[0].strip()

    brand = None
    price = None
    jsonld = parse_jsonld(soup)
    for root in jsonld:
        for obj in walk(root):
            typ = obj.get("@type")
            if typ == "Product" or (isinstance(typ, list) and "Product" in typ):
                name = clean(obj.get("name")) or name
                b = obj.get("brand")
                if isinstance(b, dict):
                    brand = clean(b.get("name"))
                elif isinstance(b, str):
                    brand = clean(b)
                offers = obj.get("offers")
                if isinstance(offers, dict):
                    try:
                        p = float(str(offers.get("price")).replace(",", "."))
                        if p > 0:
                            price = p
                    except Exception:
                        pass

    prices = [parse_number(m.group(1)) for m in PRICE_RE.finditer(text)]
    prices = [p for p in prices if p and 0.01 <= p <= 500]
    if price is None and prices:
        # prefer the smallest plausible current selling price; crossed-out old prices
        # often coexist in the page text.
        price = min(prices)

    qty = None
    qm = QTY_RE.search(text)
    if qm:
        mult = int(qm.group(1) or 1)
        val = parse_number(qm.group(2))
        unit = canonical_unit(qm.group(3))
        if val and val > 0:
            qty = (mult * val, unit, clean(qm.group(0)))

    unit_price = None
    um = UNIT_PRICE_RE.search(text)
    if um:
        unit_price = (parse_number(um.group(2)), canonical_unit(um.group(1)))

    piece = bool(re.search(r"(?i)\bal\s+pezzo\b", text))
    variable_kg = bool(re.search(r"(?i)\b(?:sfuso|sfusi|sfuse|al)\s+(?:al\s+)?kg\b", text))

    return {
        "url": url,
        "name": name,
        "brand": brand,
        "price_eur": price,
        "quantity_value": qty[0] if qty else (1.0 if piece else None),
        "quantity_unit": qty[1] if qty else ("pz" if piece else None),
        "quantity_text": qty[2] if qty else ("Al pezzo" if piece else ("Al kg" if variable_kg else None)),
        "unit_price_eur": unit_price[0] if unit_price else (price if variable_kg else None),
        "unit_price_unit": unit_price[1] if unit_price else ("kg" if variable_kg else None),
        "variable_weight": 1 if variable_kg else 0,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

def main():
    links, category_stats = discover_product_links()
    print("discovered", len(links), "product links")

    products = []
    errors = []
    for i, url in enumerate(links, 1):
        try:
            p = parse_product(url)
            if p["name"] and p["price_eur"] and p["price_eur"] > 0:
                products.append(p)
            else:
                errors.append({"url": url, "reason": "missing name or positive price"})
        except Exception as e:
            errors.append({"url": url, "reason": repr(e)})
        if i % 25 == 0:
            print(i, "/", len(links), "valid", len(products), "errors", len(errors))
        time.sleep(0.15)

    con = sqlite3.connect(OUT_DB)
    cur = con.cursor()
    cur.executescript("""
    DROP TABLE IF EXISTS products;
    CREATE TABLE products (
        product_key TEXT PRIMARY KEY,
        supermarket TEXT NOT NULL,
        store TEXT,
        product_name TEXT NOT NULL,
        brand TEXT,
        category TEXT,
        quantity_text TEXT,
        quantity_value REAL,
        quantity_unit TEXT,
        price_eur REAL NOT NULL,
        unit_price REAL,
        unit_price_unit TEXT,
        variable_weight INTEGER NOT NULL DEFAULT 0,
        product_url TEXT,
        checked_at TEXT
    );
    """)
    for p in products:
        key = re.sub(r"[^a-z0-9]+", "-", p["url"].lower()).strip("-")
        cur.execute("""
        INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            key, "Lidl", "Caserta - Via Paolo Borsellino 4",
            p["name"], p["brand"], None, p["quantity_text"],
            p["quantity_value"], p["quantity_unit"], p["price_eur"],
            p["unit_price_eur"], p["unit_price_unit"], p["variable_weight"],
            p["url"], p["checked_at"]
        ))
    con.commit()
    con.close()

    audit = {
        "verdict": "PROBE",
        "discovered_links": len(links),
        "valid_positive_price_products": len(products),
        "errors": len(errors),
        "category_link_counts": category_stats,
        "store_reference": "Lidl Caserta - Via Paolo Borsellino 4",
        "note": "Probe catalog from public Lidl Italia food pages. Do not use in app until coverage and freshness audit pass.",
        "error_samples": errors[:30],
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)

    print(json.dumps(audit, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
