import json
import math
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_WEB = "https://www.cosicomodo.it"
API_BASE = "https://api.cosicomodo.it/occ/v2"
SITE = "familasud"
STORE_ALIAS = "teverola"
STORE_LABEL = "Famila - Teverola"
USER_ID = "anonymous"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"
DB_PATH = Path("prezzi_famila.db")
REPORT_PATH = Path("famila_audit_report.json")

# Reparti principali Famila Sud Teverola, verificati sulla pagina ufficiale.
CATEGORIES = [
    ("10006", "Frutta e verdura"),
    ("10013", "Salumi e formaggi"),
    ("10007", "Gastronomia e pasta fresca"),
    ("10009", "Latte, burro, uova e yogurt"),
    ("10003", "Carne"),
    ("10011", "Pesce"),
    ("10004", "Colazione, merenda e dolci"),
    ("10012", "Prodotti alimentari"),
    ("10010", "Pane e pasticceria"),
    ("10008", "Gelati e surgelati"),
    ("10002", "Acqua, bevande, vino e alcolici"),
    ("10015", "Tutto per il bambino"),
    ("10001", "Amici animali"),
    ("10005", "Cura della persona"),
    ("10016", "Tutto per la casa"),
    ("10014", "Tempo libero"),
]


def clean_text(value):
    if value is None:
        return None
    if isinstance(value, dict):
        # Campi marca/descrizione possono essere oggetti localizzati.
        for key in ("name", "value", "formattedValue", "description", "code"):
            if value.get(key):
                return clean_text(value.get(key))
        return None
    if isinstance(value, list):
        vals = [clean_text(x) for x in value]
        vals = [x for x in vals if x]
        return ", ".join(vals) if vals else None
    text = " ".join(str(value).replace("\u00a0", " ").split()).strip()
    return text or None


def fnum(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            x = float(value)
            return x if math.isfinite(x) else None
        except Exception:
            return None
    text = clean_text(value)
    if not text:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except Exception:
        return None


def get_price(product):
    candidates = []
    for key in ("bestPrice", "price", "sellingPrice"):
        obj = product.get(key)
        if isinstance(obj, dict):
            candidates.append(obj)
    for obj in candidates:
        value = fnum(obj.get("value"))
        if value is not None and value > 0:
            return value, obj
    for key in ("priceValue", "sellingPriceValue"):
        value = fnum(product.get(key))
        if value is not None and value > 0:
            return value, {}
    return None, {}


def get_unit_price(product, price_obj):
    value = fnum(price_obj.get("priceReferenceUnit")) if isinstance(price_obj, dict) else None
    unit = clean_text(price_obj.get("referenceUnitMeasure")) if isinstance(price_obj, dict) else None
    if value is None:
        for key in ("unitPrice", "pricePerUnit", "referencePrice"):
            obj = product.get(key)
            if isinstance(obj, dict):
                value = fnum(obj.get("value"))
                unit = unit or clean_text(obj.get("unit") or obj.get("unitMeasure") or obj.get("referenceUnitMeasure"))
                if value is not None:
                    break
            else:
                value = fnum(obj)
                if value is not None:
                    break
    if value is None or value <= 0:
        return None
    if unit:
        return f"{value:.6f} EUR/{unit}"
    return f"{value:.6f} EUR"


def explicit_quantity_text(product):
    # Solo campi espliciti del catalogo. Nessuna quantità viene inventata dal nome.
    scalar_keys = (
        "quantityText", "quantity", "packageSize", "packSize", "format",
        "formattedQuantity", "grammatura", "weight", "netWeight", "content",
    )
    for key in scalar_keys:
        value = product.get(key)
        text = clean_text(value)
        if text and text.lower() not in {"0", "0.0", "none", "null"}:
            return text

    # Classificazioni Selex: cerca una grammatura/quantità dichiarata.
    for key in ("classifications", "features", "variantOptions"):
        value = product.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            label = clean_text(item.get("name") or item.get("code") or item.get("featureUnit")) or ""
            if any(token in label.lower() for token in ("gramm", "peso", "contenuto", "formato", "quant")):
                candidate = clean_text(item.get("value") or item.get("featureValues") or item.get("values"))
                if candidate:
                    return candidate
    return None


def product_url(product):
    url = clean_text(product.get("url"))
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if not url.startswith("/"):
        url = "/" + url
    return BASE_WEB + url


def normalize_product(raw, fallback_category, checked_at):
    if not isinstance(raw, dict):
        return None
    code = clean_text(raw.get("code") or raw.get("ean") or raw.get("eanCode"))
    name = clean_text(raw.get("name") or raw.get("description"))
    price, price_obj = get_price(raw)
    if not code or not name or price is None or price <= 0:
        return None

    category = clean_text(raw.get("leafCategoryName") or raw.get("categoryName") or fallback_category)
    brand = clean_text(raw.get("marca") or raw.get("brand"))
    quantity_text = explicit_quantity_text(raw)
    unit_price_text = get_unit_price(raw, price_obj)
    url = product_url(raw)

    saleable = raw.get("saleable")
    stock = raw.get("stock")
    stock_level = None
    if isinstance(stock, dict):
        stock_level = clean_text(stock.get("stockLevelStatus") or stock.get("stockLevel"))

    return {
        "product_key": code,
        "supermarket": "Famila",
        "store": STORE_LABEL,
        "product_name": name,
        "brand": brand,
        "category": category,
        "quantity_text": quantity_text,
        "price_eur": float(price),
        "unit_price_text": unit_price_text,
        "product_url": url,
        "checked_at": checked_at,
        "saleable": None if saleable is None else int(bool(saleable)),
        "stock_status": stock_level,
        "raw_json": json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
    }


def session_for_store():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
    })
    # Questo passaggio è necessario: imposta i cookie reali del PDV Teverola.
    url = f"{BASE_WEB}/{SITE}/{STORE_ALIAS}/reparti/prodotti-alimentari/c/10012"
    r = s.get(url, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }, timeout=60)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    node = soup.find("script", id="__NEXT_DATA__")
    if not node or not node.string:
        raise RuntimeError("Famila __NEXT_DATA__ non trovato")
    pp = ((json.loads(node.string).get("props") or {}).get("pageProps") or {})
    point = pp.get("pointOfService") or {}
    pdv_name = clean_text(point.get("name")) if isinstance(point, dict) else None
    if pdv_name != "MEGAMARK_FAMILA_163711":
        raise RuntimeError(f"Punto vendita Famila inatteso: {pdv_name!r}")
    required_cookies = {"familasud_anonymous_preferred_base_store", "pointOfService"}
    if not required_cookies.issubset(set(s.cookies.keys())):
        raise RuntimeError(f"Cookie PDV Famila mancanti: {required_cookies - set(s.cookies.keys())}")
    return s, point


def fetch_page(session, category_code, page, retries=4):
    endpoint = f"{API_BASE}/{SITE}/stores/{STORE_ALIAS}/users/{USER_ID}/products/search-by-category"
    params = {
        "categoryCode": category_code,
        "currentPage": page,
        "pageSize": 20,
        "fields": "FULL",
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": BASE_WEB,
        "Referer": f"{BASE_WEB}/{SITE}/{STORE_ALIAS}/reparti/",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    last = None
    for attempt in range(retries):
        try:
            r = session.get(endpoint, params=params, headers=headers, timeout=60)
            last = r
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    return data
            if r.status_code in {429, 500, 502, 503, 504}:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Famila HTTP {r.status_code}: {r.text[:500]}")
        except (requests.RequestException, ValueError) as e:
            if attempt == retries - 1:
                raise RuntimeError(f"Errore Famila categoria={category_code} page={page}: {e}") from e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Famila fetch fallito: {last.status_code if last else 'no response'}")


def build_db(products):
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("""
            CREATE TABLE products (
                product_key TEXT PRIMARY KEY,
                supermarket TEXT NOT NULL,
                store TEXT NOT NULL,
                product_name TEXT NOT NULL,
                brand TEXT,
                category TEXT,
                quantity_text TEXT,
                price_eur REAL NOT NULL,
                unit_price_text TEXT,
                product_url TEXT,
                checked_at TEXT NOT NULL,
                saleable INTEGER,
                stock_status TEXT,
                raw_json TEXT
            )
        """)
        rows = [(
            p["product_key"], p["supermarket"], p["store"], p["product_name"], p["brand"],
            p["category"], p["quantity_text"], p["price_eur"], p["unit_price_text"],
            p["product_url"], p["checked_at"], p["saleable"], p["stock_status"], p["raw_json"],
        ) for p in products.values()]
        con.executemany("""
            INSERT INTO products (
                product_key, supermarket, store, product_name, brand, category,
                quantity_text, price_eur, unit_price_text, product_url, checked_at,
                saleable, stock_status, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        con.execute("CREATE INDEX idx_famila_product_name ON products(product_name)")
        con.execute("CREATE INDEX idx_famila_category ON products(category)")
        con.commit()
    finally:
        con.close()


def audit_db(category_stats, point_of_service):
    con = sqlite3.connect(DB_PATH)
    try:
        total = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        bad_price = con.execute("SELECT COUNT(*) FROM products WHERE price_eur IS NULL OR price_eur <= 0").fetchone()[0]
        empty_name = con.execute("SELECT COUNT(*) FROM products WHERE TRIM(COALESCE(product_name,'')) = ''").fetchone()[0]
        dup = con.execute("SELECT COUNT(*) FROM (SELECT product_key, COUNT(*) c FROM products GROUP BY product_key HAVING c > 1)").fetchone()[0]
        null_qty = con.execute("SELECT COUNT(*) FROM products WHERE TRIM(COALESCE(quantity_text,'')) = ''").fetchone()[0]
        unit_prices = con.execute("SELECT COUNT(*) FROM products WHERE TRIM(COALESCE(unit_price_text,'')) <> ''").fetchone()[0]
        saleable_false = con.execute("SELECT COUNT(*) FROM products WHERE saleable = 0").fetchone()[0]
        categories = con.execute("SELECT COUNT(DISTINCT category) FROM products").fetchone()[0]
    finally:
        con.close()

    total_reported = sum(x.get("totalResults", 0) or 0 for x in category_stats)
    total_fetched_rows = sum(x.get("rowsFetched", 0) or 0 for x in category_stats)
    failed_pages = sum(len(x.get("failedPages", [])) for x in category_stats)

    # Il totale deduplicato può essere inferiore alla somma dei reparti se Famila espone lo stesso
    # prodotto in più classificazioni. Le pagine, invece, devono essere tutte percorse.
    valid = (
        total >= 2500 and
        bad_price == 0 and
        empty_name == 0 and
        dup == 0 and
        failed_pages == 0 and
        total_fetched_rows >= int(total_reported * 0.98)
    )
    report = {
        "status": "VALID" if valid else "INVALID",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "store": {
            "site": SITE,
            "alias": STORE_ALIAS,
            "name": point_of_service.get("name") if isinstance(point_of_service, dict) else None,
            "displayName": point_of_service.get("displayName") if isinstance(point_of_service, dict) else None,
            "selexCode": point_of_service.get("selexCode") if isinstance(point_of_service, dict) else None,
        },
        "database": str(DB_PATH),
        "unique_products": total,
        "sum_category_total_results": total_reported,
        "fetched_rows_before_dedup": total_fetched_rows,
        "bad_price": bad_price,
        "empty_name": empty_name,
        "duplicate_product_key": dup,
        "quantity_text_missing": null_qty,
        "unit_price_present": unit_prices,
        "saleable_false": saleable_false,
        "distinct_categories": categories,
        "failed_pages": failed_pages,
        "category_stats": category_stats,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not valid:
        raise SystemExit("FAMILA_AUDIT_INVALID")


def main():
    checked_at = datetime.now(timezone.utc).isoformat()
    session, point = session_for_store()
    products = {}
    category_stats = []

    for idx, (code, label) in enumerate(CATEGORIES, start=1):
        first = fetch_page(session, code, 0)
        pag = first.get("pagination") or {}
        total_pages = int(pag.get("totalPages") or 0)
        total_results = int(pag.get("totalResults") or 0)
        if total_pages <= 0 and total_results > 0:
            total_pages = math.ceil(total_results / 20)
        if total_pages <= 0:
            raise RuntimeError(f"Famila categoria {code} senza paginazione valida")

        rows_fetched = 0
        failed_pages = []
        page_data = first
        for page in range(total_pages):
            if page > 0:
                try:
                    page_data = fetch_page(session, code, page)
                except Exception as e:
                    print(f"WARN categoria={code} pagina={page}: {e}")
                    failed_pages.append(page)
                    continue
            page_products = page_data.get("products") or []
            rows_fetched += len(page_products)
            for raw in page_products:
                p = normalize_product(raw, label, checked_at)
                if not p:
                    continue
                old = products.get(p["product_key"])
                # A parità di EAN conserva la scheda più informativa, mai un prezzo nullo.
                if old is None:
                    products[p["product_key"]] = p
                else:
                    old_score = sum(bool(old.get(k)) for k in ("brand", "category", "quantity_text", "unit_price_text", "product_url"))
                    new_score = sum(bool(p.get(k)) for k in ("brand", "category", "quantity_text", "unit_price_text", "product_url"))
                    if new_score > old_score:
                        products[p["product_key"]] = p
            if page % 10 == 0 or page == total_pages - 1:
                print(f"[{idx}/{len(CATEGORIES)}] {code} {label}: page {page + 1}/{total_pages}, rows={rows_fetched}, unique={len(products)}")
            time.sleep(0.05)

        category_stats.append({
            "code": code,
            "label": label,
            "totalPages": total_pages,
            "totalResults": total_results,
            "rowsFetched": rows_fetched,
            "failedPages": failed_pages,
        })

    build_db(products)
    audit_db(category_stats, point)
    print("FAMILA_FULL_DB_OK", len(products), DB_PATH.stat().st_size)


if __name__ == "__main__":
    main()
