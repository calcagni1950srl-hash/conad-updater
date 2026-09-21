import argparse
import json
import sqlite3
import time
from pathlib import Path

STORE_CODE = "010548"
STORE_NAME = "Conad Superstore Capodrise"
STORE_ADDRESS = "Via Retella ex Giard. del Sole, SNC, 81020 Capodrise (CE)"
SOURCE_SCOPE = "CONAD_TUTTI_PRODOTTI_CAPODRISE_STABLE"
FULL_SCHEMA = "SMART_CAMPANIA_CONAD_STABLE_BROWSER_EXPORT_V1"
CONT_SCHEMA = "SMART_CAMPANIA_CONAD_STABLE_BROWSER_CONTINUATION_V1"

NON_FOOD_BASE = {
    "Animali domestici",
    "Articoli per la casa",
    "Cancelleria e cartoleria",
    "Cura persona",
    "Prima infanzia",
    "Tempo libero e outdoor",
}


def _float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def current_general_promo(product):
    """Return a real, currently displayed GENERAL promo price for this stable SKU.

    The product remains part of the stable catalog independently of this overlay.
    A promo may change price, but never product/recipe availability.
    """
    base = _float(product.get("basePrice"))
    best = None
    for promo in product.get("promo") or []:
        if not isinstance(promo, dict):
            continue
        if str(promo.get("customerType") or "").upper() != "GENERAL":
            continue
        if _float(promo.get("quantityThreshold"), 0.0) != 0.0:
            continue
        final = _float(promo.get("finalPrice"))
        if final <= 0:
            continue
        if base > 0 and final > base:
            continue
        if best is None or final < best:
            best = final
    return best


def normalized_quantity(product, current_price):
    """Normalize real Conad pack/minimum-purchase quantity.

    For variable-weight products Conad's increment.minWeight is authoritative even
    when netQuantity says 1 KG. Example: 100 g minimum at EUR 1.19 = EUR 11.90/kg.
    """
    q = None
    unit = None
    variable = False

    inc = product.get("increment") if isinstance(product.get("increment"), dict) else None
    if inc:
        min_weight = _float(inc.get("minWeight"))
        inc_unit = str(inc.get("unitOfMeasure") or "").upper()
        if min_weight > 0 and inc_unit in ("G", "GR"):
            q, unit, variable = min_weight, "GR", True
        elif min_weight > 0 and inc_unit == "KG":
            q, unit, variable = min_weight, "KG", True

    if q is None:
        raw_q = product.get("netQuantity")
        try:
            q = float(raw_q) if raw_q is not None else None
        except Exception:
            q = None
        unit = str(product.get("netQuantityUm") or "").upper() or None

    unit_price = None
    unit_price_unit = None
    if q and q > 0 and current_price > 0:
        if unit == "KG":
            unit_price, unit_price_unit = current_price / q, "EUR/KG"
        elif unit in ("G", "GR"):
            unit_price, unit_price_unit = current_price / (q / 1000.0), "EUR/KG"
        elif unit in ("L", "LT"):
            unit_price, unit_price_unit = current_price / q, "EUR/L"
        elif unit == "ML":
            unit_price, unit_price_unit = current_price / (q / 1000.0), "EUR/L"
        elif unit == "CL":
            unit_price, unit_price_unit = current_price / (q / 100.0), "EUR/L"
        elif unit in ("PZ", "PEZZO", "PEZZI"):
            unit_price, unit_price_unit = current_price / q, "EUR/PZ"

    return q, unit, (round(unit_price, 6) if unit_price is not None else None), unit_price_unit, variable


def validate_export(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    schema = data.get("schema")
    if schema not in (FULL_SCHEMA, CONT_SCHEMA):
        raise RuntimeError(f"{path}: schema Conad non riconosciuto: {schema!r}")

    store = data.get("store") or {}
    if str(store.get("code") or "") != STORE_CODE:
        raise RuntimeError(f"{path}: punto vendita errato: {store.get('code')!r}")

    policy = data.get("policy") or {}
    if policy.get("flyerProductsUnlockRecipes") is not False:
        raise RuntimeError(f"{path}: policy stable-only non confermata.")

    products = data.get("products")
    if not isinstance(products, list) or not products:
        raise RuntimeError(f"{path}: export senza prodotti.")

    return data, products


def merge_exports(paths):
    merged = {}
    declared_total = None
    expected_pages = None
    max_page = 0
    captured_at = None

    for path in paths:
        data, products = validate_export(path)
        stats = data.get("stats") or {}
        declared = stats.get("declaredTotal")
        pages = stats.get("expectedPages")
        last_page = stats.get("lastPage") or stats.get("pagesScanned") or 0

        if declared:
            declared = int(declared)
            if declared_total not in (None, declared):
                raise RuntimeError("Export Conad con totali dichiarati discordanti.")
            declared_total = declared
        if pages:
            pages = int(pages)
            if expected_pages not in (None, pages):
                raise RuntimeError("Export Conad con numero pagine discordante.")
            expected_pages = pages
        max_page = max(max_page, int(last_page or 0))

        stamp = data.get("capturedAt")
        if stamp and (captured_at is None or stamp > captured_at):
            captured_at = stamp

        for p in products:
            code = str(p.get("code") or "").strip()
            if not code:
                continue
            old = merged.get(code)
            if old is None:
                merged[code] = p
            else:
                old_price = _float(old.get("basePrice"))
                new_price = _float(p.get("basePrice"))
                if old_price <= 0 < new_price:
                    merged[code] = p

    return merged, declared_total, expected_pages, max_page, captured_at


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
      regular_price_eur REAL,
      promo_price_eur REAL,
      promo_active_general INTEGER NOT NULL DEFAULT 0,
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


def build_db(paths, out_db, audit_path, allow_incomplete=False):
    products, declared_total, expected_pages, max_page, captured_at = merge_exports(paths)

    if declared_total is not None and len(products) != declared_total and not allow_incomplete:
        raise RuntimeError(
            f"Catalogo incompleto: Conad dichiara {declared_total}, "
            f"ma gli export contengono {len(products)} codici unici."
        )

    checked_at = captured_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    catalog_complete = declared_total is not None and len(products) == declared_total

    food = {}
    excluded_non_food = {}
    zero_price = 0
    invalid = 0

    for code, p in products.items():
        name = str(p.get("nome") or p.get("title") or "").strip()
        category1 = str(p.get("categoriaPrimoLivello") or "").strip()
        base = _float(p.get("basePrice"))
        if not code or not name:
            invalid += 1
            continue
        if category1 in NON_FOOD_BASE:
            excluded_non_food[category1] = excluded_non_food.get(category1, 0) + 1
            continue
        if base <= 0:
            zero_price += 1
            continue
        food[code] = p

    if not food:
        raise RuntimeError("Nessun prodotto alimentare Conad con prezzo positivo.")

    con = init_db(out_db)
    variable_rows = 0
    promo_rows = 0

    try:
        for code, p in food.items():
            regular_price = _float(p.get("basePrice"))
            promo_price = current_general_promo(p)
            current_price = promo_price if promo_price is not None else regular_price
            qty, qty_unit, up, upu, variable = normalized_quantity(p, current_price)

            if variable:
                variable_rows += 1
            if promo_price is not None:
                promo_rows += 1

            row = (
                "Conad", STORE_CODE, STORE_NAME, STORE_ADDRESS,
                code, p.get("nome") or p.get("title"), p.get("marchio") or p.get("brand"),
                p.get("categoriaPrimoLivello"), p.get("categoriaSecondoLivello"), p.get("categoriaTerzoLivello"),
                qty, qty_unit,
                current_price, up, upu,
                1 if p.get("bassiFissi") in (True, 1) else 0,
                p.get("defaultImgSrc"), SOURCE_SCOPE, checked_at, int(variable),
                regular_price, promo_price, int(promo_price is not None),
            )
            con.execute(
                "INSERT INTO products_current VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row
            )
            con.execute(
                "INSERT INTO price_history(store_code,product_code,price_eur,unit_price,checked_at) VALUES(?,?,?,?,?)",
                (STORE_CODE, code, current_price, up, checked_at)
            )

        meta = {
            "supermarket": "Conad",
            "store_code": STORE_CODE,
            "store_name": STORE_NAME,
            "store_address": STORE_ADDRESS,
            "source_scope": SOURCE_SCOPE,
            "catalog_policy": "STABLE_PRODUCT_IDENTITY_PROMO_PRICE_OVERLAY_NO_FLYER_UNLOCK",
            "captured_at": checked_at,
            "declared_total": str(declared_total or ""),
            "pages": str(expected_pages or max_page or ""),
            "unique_products_source": str(len(products)),
            "saved_food_products": str(len(food)),
            "excluded_non_food": str(sum(excluded_non_food.values())),
            "excluded_non_food_by_category": json.dumps(excluded_non_food, ensure_ascii=False, sort_keys=True),
            "excluded_zero_price": str(zero_price),
            "excluded_invalid": str(invalid),
            "variable_weight_rows": str(variable_rows),
            "general_promo_rows": str(promo_rows),
            "catalog_complete": str(catalog_complete).lower(),
            "flyer_rows": "0",
            "visual_rows": "0",
        }
        con.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", meta.items())
        con.execute(
            "INSERT INTO update_log(checked_at,store_code,query,declared_total,saved_count,status,message) VALUES(?,?,?,?,?,?,?)",
            (
                checked_at, STORE_CODE, "tutti-i-prodotti stable browser export",
                declared_total, len(food), "OK" if catalog_complete else "PARTIAL",
                "Identità prodotto dal catalogo stabile Tutti i prodotti; "
                "eventuale promo GENERAL sovrappone solo il prezzo dello stesso SKU; "
                "nessun FLYER/VISUAL può sbloccare ricette."
            )
        )
        con.commit()
    finally:
        con.close()

    audit = {
        "status": "OK" if catalog_complete else "PARTIAL",
        "store_code": STORE_CODE,
        "declared_total": declared_total,
        "expected_pages": expected_pages,
        "last_page_seen": max_page,
        "catalog_complete": catalog_complete,
        "source_unique": len(products),
        "saved_food_products": len(food),
        "excluded_non_food": excluded_non_food,
        "excluded_zero_price": zero_price,
        "excluded_invalid": invalid,
        "variable_weight_rows": variable_rows,
        "general_promo_price_overlays": promo_rows,
        "flyer_or_visual_rows": 0,
        "output_db": str(out_db),
    }
    Path(audit_path).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("exports", nargs="+", help="Uno o più export JSON dello stesso catalogo Capodrise")
    parser.add_argument("--output", default="prezzi_conad_capodrise_stable_full.db")
    parser.add_argument("--audit", default="conad_stable_import_audit.json")
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()

    build_db(
        [Path(x) for x in args.exports],
        Path(args.output),
        Path(args.audit),
        allow_incomplete=args.allow_incomplete,
    )


if __name__ == "__main__":
    main()
