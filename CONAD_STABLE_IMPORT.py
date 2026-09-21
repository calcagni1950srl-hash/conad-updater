import json, math, sqlite3, sys, time
from pathlib import Path

STORE_CODE = "010548"
STORE_NAME = "Conad Superstore Capodrise"
STORE_ADDRESS = "Via Retella ex Giard. del Sole, SNC, 81020 Capodrise (CE)"
SOURCE_SCOPE = "CONAD_TUTTI_PRODOTTI_CAPODRISE_STABLE"
NON_FOOD_BASE = {
    "Cura persona", "Articoli per la casa", "Prima infanzia", "Animali domestici"
}

def normalized_quantity_and_price(p):
    """Return real purchasable/reference quantity from Conad product metadata.

    For fixed packs use netQuantity/netQuantityUm.
    For variable-weight cards Conad exposes increment.minWeight; basePrice is the
    price of that minimum purchasable weight (e.g. 100 g meat, 500 g produce).
    """
    try:
        price = float(p.get("basePrice") or 0)
    except Exception:
        price = 0.0
    q = p.get("netQuantity")
    unit = str(p.get("netQuantityUm") or "").upper()
    try:
        q = float(q) if q is not None else None
    except Exception:
        q = None

    variable = False
    inc = p.get("increment") if isinstance(p.get("increment"), dict) else None
    if (q is None or q <= 0) and inc:
        try:
            min_weight = float(inc.get("minWeight") or 0)
        except Exception:
            min_weight = 0.0
        inc_unit = str(inc.get("unitOfMeasure") or "").upper()
        if min_weight > 0 and inc_unit in ("G", "GR"):
            q, unit, variable = min_weight, "GR", True
        elif min_weight > 0 and inc_unit == "KG":
            q, unit, variable = min_weight, "KG", True

    up = None
    upu = None
    if q and q > 0 and price > 0:
        if unit == "KG":
            up, upu = price / q, "EUR/KG"
        elif unit in ("G", "GR"):
            up, upu = price / (q / 1000.0), "EUR/KG"
        elif unit in ("L", "LT"):
            up, upu = price / q, "EUR/L"
        elif unit == "ML":
            up, upu = price / (q / 1000.0), "EUR/L"
        elif unit in ("PZ", "PEZZO", "PEZZI"):
            up, upu = price / q, "EUR/PZ"

    return q, unit or None, (round(up, 4) if up is not None else None), upu, variable

def init_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
    DROP TABLE IF EXISTS products_current;
    DROP TABLE IF EXISTS price_history;
    DROP TABLE IF EXISTS update_log;
    DROP TABLE IF EXISTS metadata;
    CREATE TABLE products_current(
      supermarket TEXT NOT NULL,
      store_code TEXT NOT NULL,
      store_name TEXT,
      store_address TEXT,
      product_code TEXT NOT NULL,
      product_name TEXT NOT NULL,
      brand TEXT,
      category1 TEXT,
      category2 TEXT,
      category3 TEXT,
      quantity_value REAL,
      quantity_unit TEXT,
      price_eur REAL NOT NULL,
      unit_price REAL,
      unit_price_unit TEXT,
      bassi_fissi INTEGER NOT NULL DEFAULT 0,
      image_url TEXT,
      source_queries TEXT,
      checked_at TEXT NOT NULL,
      variable_weight INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(store_code, product_code)
    );
    CREATE TABLE price_history(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      store_code TEXT NOT NULL,
      product_code TEXT NOT NULL,
      price_eur REAL NOT NULL,
      unit_price REAL,
      checked_at TEXT NOT NULL
    );
    CREATE TABLE update_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      checked_at TEXT NOT NULL,
      store_code TEXT,
      query TEXT,
      declared_total INTEGER,
      saved_count INTEGER,
      status TEXT,
      message TEXT
    );
    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    return con

def load_export(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != "SMART_CAMPANIA_CONAD_STABLE_BROWSER_EXPORT_V1":
        raise RuntimeError("Schema export Conad non riconosciuto.")
    store = data.get("store") or {}
    if str(store.get("code") or "") != STORE_CODE:
        raise RuntimeError(f"Export del punto vendita errato: {store.get('code')!r}")
    policy = data.get("policy") or {}
    if policy.get("flyerProductsUnlockRecipes") is not False:
        raise RuntimeError("Policy stable-only non confermata nell'export.")
    products = data.get("products")
    if not isinstance(products, list) or not products:
        raise RuntimeError("Export senza prodotti.")
    return data, products

def main():
    if len(sys.argv) < 2:
        raise SystemExit("Uso: python CONAD_STABLE_IMPORT.py <export.json> [output.db]")
    src = Path(sys.argv[1])
    out_db = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("prezzi_conad_capodrise_stable_full.db")

    data, products = load_export(src)
    checked_at = data.get("capturedAt") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    by_code = {}
    zero_price = 0
    non_food = 0
    invalid = 0
    promo_metadata = 0
    for p in products:
        code = str(p.get("code") or "").strip()
        name = str(p.get("nome") or p.get("title") or "").strip()
        category1 = str(p.get("categoriaPrimoLivello") or "").strip()
        try:
            price = float(p.get("basePrice") or 0)
        except Exception:
            price = 0
        if not code or not name:
            invalid += 1
            continue
        if category1 in NON_FOOD_BASE:
            non_food += 1
            continue
        if price <= 0:
            zero_price += 1
            continue
        if isinstance(p.get("promo"), list) and p.get("promo"):
            promo_metadata += 1
        old = by_code.get(code)
        if old is None or float(old.get("basePrice") or 0) <= 0:
            by_code[code] = p

    if not by_code:
        raise RuntimeError("Nessun prodotto alimentare con prezzo positivo.")

    con = init_db(out_db)
    try:
        variable_rows = 0
        for code, p in by_code.items():
            qty, qty_unit, up, upu, variable = normalized_quantity_and_price(p)
            if variable:
                variable_rows += 1
            price = float(p.get("basePrice"))
            row = (
                "Conad", STORE_CODE, STORE_NAME, STORE_ADDRESS,
                code, p.get("nome") or p.get("title"), p.get("marchio") or p.get("brand"),
                p.get("categoriaPrimoLivello"), p.get("categoriaSecondoLivello"), p.get("categoriaTerzoLivello"),
                qty, qty_unit,
                price, up, upu, 1 if p.get("bassiFissi") in (True, 1) else 0,
                p.get("defaultImgSrc"), SOURCE_SCOPE, checked_at, int(variable)
            )
            con.execute("INSERT INTO products_current VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            con.execute(
                "INSERT INTO price_history(store_code,product_code,price_eur,unit_price,checked_at) VALUES(?,?,?,?,?)",
                (STORE_CODE, code, price, up, checked_at)
            )

        meta = {
            "supermarket": "Conad",
            "store_code": STORE_CODE,
            "store_name": STORE_NAME,
            "store_address": STORE_ADDRESS,
            "source_scope": SOURCE_SCOPE,
            "catalog_policy": "STABLE_TUTTI_I_PRODOTTI_NO_FLYER_UNLOCK",
            "browser_export_schema": data.get("schema", ""),
            "captured_at": checked_at,
            "export_products": str(len(products)),
            "saved_positive_food_products": str(len(by_code)),
            "excluded_zero_price": str(zero_price),
            "excluded_non_food": str(non_food),
            "excluded_invalid": str(invalid),
            "products_with_promo_metadata": str(promo_metadata),
            "variable_weight_rows": str(variable_rows),
            "catalog_complete": "false" if (
                int((data.get("stats") or {}).get("pagesScanned") or 0) == 250
                and len(products) == 7500
                and not (data.get("stats") or {}).get("declaredTotal")
            ) else "unknown",
        }
        con.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", meta.items())
        con.execute(
            "INSERT INTO update_log(checked_at,store_code,query,declared_total,saved_count,status,message) VALUES(?,?,?,?,?,?,?)",
            (
                checked_at, STORE_CODE, "tutti-i-prodotti stable browser export",
                len(products), len(by_code), "OK",
                "Catalogo stabile Conad Capodrise; solo prodotti del catalogo Tutti i prodotti con basePrice positivo; nessun FLYER/VISUAL."
            )
        )
        con.commit()
    finally:
        con.close()

    audit = {
        "status": "OK",
        "store_code": STORE_CODE,
        "source_scope": SOURCE_SCOPE,
        "input_products": len(products),
        "saved_positive_food_products": len(by_code),
        "excluded_zero_price": zero_price,
        "excluded_non_food": non_food,
        "excluded_invalid": invalid,
        "products_with_promo_metadata": promo_metadata,
        "variable_weight_rows": variable_rows,
        "catalog_complete": False if (
            int((data.get("stats") or {}).get("pagesScanned") or 0) == 250
            and len(products) == 7500
            and not (data.get("stats") or {}).get("declaredTotal")
        ) else None,
        "min_base_price": min(float(p.get("basePrice")) for p in by_code.values()),
        "max_base_price": max(float(p.get("basePrice")) for p in by_code.values()),
        "output_db": str(out_db),
    }
    Path("conad_stable_import_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False))

if __name__ == "__main__":
    main()
