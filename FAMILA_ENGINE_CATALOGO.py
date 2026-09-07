import argparse
import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.cosicomodo.it/occ/v2"

# Categorie di primo livello osservate direttamente nel __NEXT_DATA__
# ufficiale CosìComodo durante il probe V5.
VERIFIED_CATEGORIES = [
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
    ("10015", "Bambino"),
    ("10001", "Animali"),
    ("10005", "Cura della persona"),
    ("10016", "Tutto per la casa"),
    ("10014", "Tempo libero"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (SmartCampania Famila catalog engine)",
    "Accept": "application/json",
}

def request_json(session, url, params=None, retries=5):
    last = None
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, headers=HEADERS, timeout=60)
            last = r
            if r.status_code == 200:
                return r.json(), r.url
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(30, 2 * (2 ** attempt)))
                continue
            raise RuntimeError(f"HTTP {r.status_code}: {r.url}")
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(min(30, 2 * (2 ** attempt)))
    raise RuntimeError(f"Richiesta fallita: {last.url if last else url}")

def fetch_category(session, site, store, category_code, page, page_size=20):
    url = f"{API}/{site}/stores/{store}/users/anonymous/products/search-by-category"
    params = {
        "categoryCode": category_code,
        "currentPage": page,
        "pageSize": page_size,
        "fields": "FULL",
    }
    return request_json(session, url, params=params)

def num(value):
    try:
        return float(value)
    except Exception:
        return None

def parse_product(product, category_code, category_name, site, store, source_url, stamp):
    code = str(product.get("code") or product.get("ean") or "").strip()
    name = str(product.get("name") or "").strip()
    if not code or not name:
        return None

    price = product.get("price") or {}
    price_value = num(price.get("value")) if isinstance(price, dict) else None
    if price_value is None or price_value <= 0:
        return None

    brand = product.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name") or brand.get("code")
    brand = str(brand or "")

    ref = product.get("referencePrice") or product.get("unitPrice") or {}
    ref_value = num(ref.get("value")) if isinstance(ref, dict) else None
    ref_formatted = str(ref.get("formattedValue") or "") if isinstance(ref, dict) else ""

    ref_unit = ""
    if isinstance(ref, dict):
        for key in ("unit", "unitType", "unitOfMeasure", "referenceUnitMeasure"):
            v = ref.get(key)
            if isinstance(v, dict):
                v = v.get("code") or v.get("name")
            if v:
                ref_unit = str(v)
                break

    stock = product.get("stock") or {}
    stock_status = ""
    if isinstance(stock, dict):
        stock_status = str(stock.get("stockLevelStatus") or "")

    product_url = str(product.get("url") or product.get("productUrl") or "")

    return (
        code,
        name,
        brand,
        category_code,
        category_name,
        price_value,
        str(price.get("formattedValue") or "") if isinstance(price, dict) else "",
        ref_value,
        ref_formatted,
        ref_unit,
        stock_status,
        product_url,
        source_url,
        site,
        store,
        stamp,
        json.dumps(product, ensure_ascii=False, separators=(",", ":")),
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="teverola")
    ap.add_argument("--site", default="familasud")
    ap.add_argument("--db", default="prezzi_famila.db")
    ap.add_argument("--audit", default="famila_audit.json")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).isoformat()
    session = requests.Session()

    audit = {
        "engine": "FAMILA_ENGINE_CATALOGO",
        "store_alias": args.store,
        "base_site_id": args.site,
        "detected_at": stamp,
        "verified_category_count": len(VERIFIED_CATEGORIES),
        "categories": {},
        "errors": [],
    }

    tmp = Path(args.db + ".tmp")
    tmp.unlink(missing_ok=True)

    con = sqlite3.connect(tmp)
    con.executescript("""
    CREATE TABLE products(
        product_id TEXT PRIMARY KEY,
        product_name TEXT NOT NULL,
        brand TEXT,
        category_code TEXT,
        category_name TEXT,
        price_eur REAL NOT NULL,
        price_formatted TEXT,
        unit_price REAL,
        unit_price_formatted TEXT,
        unit_price_unit TEXT,
        stock_status_raw TEXT,
        product_url TEXT,
        source_url TEXT NOT NULL,
        base_site_id TEXT NOT NULL,
        store_alias_id TEXT NOT NULL,
        detected_at TEXT NOT NULL,
        raw_json TEXT NOT NULL
    );

    CREATE TABLE metadata(
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    unique = {}
    grand_received = 0

    for category_code, category_name in VERIFIED_CATEGORIES:
        try:
            first, first_url = fetch_category(
                session, args.site, args.store, category_code, 0
            )

            pagination = first.get("pagination") or {}
            total_results = int(pagination.get("totalResults") or 0)
            total_pages = int(
                pagination.get("totalPages")
                or (math.ceil(total_results / 20) if total_results else 1)
            )

            received = 0
            positive_rows = 0

            for page in range(total_pages):
                if page == 0:
                    data, source_url = first, first_url
                else:
                    time.sleep(0.30)
                    data, source_url = fetch_category(
                        session, args.site, args.store, category_code, page
                    )

                p = data.get("pagination") or {}
                current_page = p.get("currentPage")

                if current_page is None or int(current_page) != page:
                    raise RuntimeError(
                        f"currentPage mismatch: atteso {page}, ricevuto {current_page}"
                    )

                products = data.get("products") or []
                received += len(products)
                grand_received += len(products)

                for product in products:
                    row = parse_product(
                        product,
                        category_code,
                        category_name,
                        args.site,
                        args.store,
                        source_url,
                        stamp,
                    )
                    if row is not None:
                        unique[row[0]] = row
                        positive_rows += 1

            if total_results and received != total_results:
                raise RuntimeError(
                    f"totale dichiarato {total_results}, ricevuto {received}"
                )

            audit["categories"][category_code] = {
                "name": category_name,
                "total_results": total_results,
                "received": received,
                "pages": total_pages,
                "rows_with_positive_price_seen": positive_rows,
            }

        except Exception as exc:
            audit["errors"].append(
                {
                    "category_code": category_code,
                    "category_name": category_name,
                    "error": repr(exc),
                }
            )

    if audit["errors"]:
        con.close()
        tmp.unlink(missing_ok=True)
        audit["verdict"] = "FAIL_CLOSED_DOWNLOAD_ERRORS"
        Path(args.audit).write_text(
            json.dumps(audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise SystemExit("Download incompleto: database non pubblicato.")

    con.executemany(
        "INSERT OR REPLACE INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        unique.values(),
    )

    metadata = {
        "supermarket": "Famila",
        "source": "CosìComodo OCC API",
        "catalog_engine": "FAMILA_ENGINE_CATALOGO",
        "base_site_id": args.site,
        "store_alias_id": args.store,
        "detected_at": stamp,
        "verified_top_level_categories": str(len(VERIFIED_CATEGORIES)),
        "raw_product_cards_received": str(grand_received),
        "unique_products_positive_price": str(len(unique)),
    }

    con.executemany(
        "INSERT INTO metadata(key,value) VALUES(?,?)",
        metadata.items(),
    )
    con.commit()

    db_count = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    positive_count = con.execute(
        "SELECT COUNT(*) FROM products WHERE price_eur > 0"
    ).fetchone()[0]
    duplicate_check = con.execute(
        "SELECT COUNT(*) FROM (SELECT product_id, COUNT(*) c FROM products GROUP BY product_id HAVING c > 1)"
    ).fetchone()[0]
    con.close()

    if db_count <= 0 or positive_count != db_count or duplicate_check != 0:
        tmp.unlink(missing_ok=True)
        audit["verdict"] = "FAIL_CLOSED_FINAL_DB_AUDIT"
        audit["db_count"] = db_count
        audit["positive_count"] = positive_count
        audit["duplicate_check"] = duplicate_check
        Path(args.audit).write_text(
            json.dumps(audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise SystemExit("Audit finale DB fallito.")

    Path(args.db).unlink(missing_ok=True)
    tmp.replace(args.db)

    audit.update(
        {
            "verdict": "FAMILA_ENGINE_CATALOGO_VALIDATED",
            "raw_product_cards_received": grand_received,
            "unique_products_positive_price": db_count,
            "all_db_prices_positive": True,
            "duplicate_product_ids": duplicate_check,
        }
    )

    Path(args.audit).write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(audit, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
