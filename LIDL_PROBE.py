import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://www.lidl.it"
OUT_DB = "prezzi_lidl_probe.db"
OUT_JSON = "lidl_probe_audit.json"

CATEGORY_URLS = [
    "https://www.lidl.it/h/frutta-e-verdura/h10071012",
    "https://www.lidl.it/h/carne-e-pollame/h10095752",
    "https://www.lidl.it/h/formaggi-latticini-e-uova/h10095761",
    "https://www.lidl.it/h/dispensa/h10096095",
    "https://www.lidl.it/h/surgelati/h10071049",
    "https://www.lidl.it/h/piatti-pronti/h10071020",
    "https://www.lidl.it/c/cibo-e-bevande/s10068374",
]

PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{2}))\s*€")
QTY_RE = re.compile(
    r"(?i)(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*"
    r"(kg|g|gr|l|lt|ml|cl)\b"
)
UNIT_PRICE_RE = re.compile(
    r"(?i)1\s*(kg|l|lt)\s*=\s*(\d+(?:[.,]\d+)?)\s*€"
)

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

def expand_category(page, url):
    print("OPEN", url)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)

    # Accept cookies if shown.
    for label in ["Accetta tutti", "Accetta", "Consenti tutti"]:
        try:
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            if btn.count():
                btn.first.click(timeout=1500)
                page.wait_for_timeout(500)
                break
        except Exception:
            pass

    # Repeatedly click load-more if present and scroll.
    for _ in range(25):
        clicked = False
        for text in ["Visualizza altri prodotti", "Mostra altri prodotti", "Carica altri"]:
            try:
                btn = page.get_by_text(text, exact=False)
                if btn.count():
                    btn.last.scroll_into_view_if_needed()
                    btn.last.click(timeout=2500)
                    page.wait_for_timeout(1200)
                    clicked = True
                    break
            except Exception:
                pass
        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(500)
        if not clicked:
            # one extra scroll cycle can still lazy-load cards
            page.mouse.wheel(0, 8000)
            page.wait_for_timeout(900)
            break

    hrefs = page.eval_on_selector_all(
        'a[href*="/p/"]',
        "els => els.map(e => e.href)"
    )
    clean_links = sorted({h.split("?")[0] for h in hrefs if "/p/" in h})
    print("FOUND", len(clean_links), "product links")
    return clean_links

def parse_product(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)
    text = clean(page.locator("body").inner_text(timeout=15000))
    title = ""
    try:
        title = clean(page.locator("h1").first.inner_text(timeout=4000))
    except Exception:
        pass
    if not title:
        title = clean(page.title()).split("|")[0].strip()

    brand = None
    # Lidl product pages commonly place brand immediately above product title.
    try:
        h1 = page.locator("h1").first
        parent_text = clean(h1.locator("xpath=..").inner_text(timeout=3000))
        bits = [clean(x) for x in parent_text.split("\n") if clean(x)]
        if bits and bits[0].lower() != title.lower() and len(bits[0]) <= 60:
            brand = bits[0]
    except Exception:
        pass

    prices = [parse_number(m.group(1)) for m in PRICE_RE.finditer(text)]
    prices = [p for p in prices if p and 0.01 <= p <= 500]
    # Current Lidl selling price appears after old crossed-out price; choosing the
    # smallest positive value is conservative for promo pages and is rechecked by
    # unit price where available.
    price = min(prices) if prices else None

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
    variable_kg = bool(re.search(r"(?i)\b(?:sfuse?|sfusi|al\s+kg)\b", text))

    if price is None:
        return None

    return {
        "url": url,
        "name": title,
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
    all_links = set()
    category_stats = {}
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="it-IT",
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
        )
        page = context.new_page()

        for url in CATEGORY_URLS:
            try:
                links = expand_category(page, url)
                category_stats[url] = len(links)
                all_links.update(links)
            except Exception as e:
                category_stats[url] = 0
                errors.append({"url": url, "reason": "category: " + repr(e)})

        links = sorted(all_links)
        print("DISCOVERED", len(links), "unique product links")

        products = []
        for i, url in enumerate(links, 1):
            try:
                prod = parse_product(page, url)
                if prod and prod["name"] and prod["price_eur"] > 0:
                    products.append(prod)
                else:
                    errors.append({"url": url, "reason": "missing name or positive price"})
            except Exception as e:
                errors.append({"url": url, "reason": "product: " + repr(e)})
            if i % 25 == 0:
                print(i, "/", len(links), "valid", len(products), "errors", len(errors))

        browser.close()

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
        "note": "Probe catalog rendered with Chromium. Do not use in app until recipe coverage and price/quantity audit pass.",
        "error_samples": errors[:40],
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)
    print(json.dumps(audit, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
