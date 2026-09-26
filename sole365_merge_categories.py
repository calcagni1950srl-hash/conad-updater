import argparse, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_CODES = {
    "10006": "Frutta e verdura",
    "10013": "Salumi e formaggi",
    "10007": "Gastronomia e pasta fresca",
    "10009": "Latte, burro, uova e yogurt",
    "10003": "Carne",
    "10011": "Pesce",
    "10004": "Colazione, merenda e dolci",
    "10012": "Prodotti alimentari",
    "10010": "Pane e pasticceria",
    "10008": "Gelati e surgelati",
    "10002": "Acqua, bevande, vino e alcolici",
    "10015": "Tutto per il bambino",
    "10001": "Amici animali",
    "10005": "Cura della persona",
    "10016": "Tutto per la casa",
    "10014": "Tempo libero",
}
ANDROID_CODES = {
    "10002", "10003", "10004", "10006", "10007", "10008",
    "10009", "10010", "10011", "10012", "10013",
}


def extract_image_url(raw):
    """Return one real product image URL from CosìComodo OCC raw JSON."""
    if not isinstance(raw, dict):
        return ""
    images = raw.get("productImages") or []
    if not isinstance(images, list):
        return ""
    for item in images:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "")
        for size in ("300x300", "350x350", "150x150", "80x80"):
            marker = size + "="
            pos = value.find(marker)
            if pos < 0:
                continue
            url = value[pos + len(marker):]
            url = url.split(",", 1)[0].split("}", 1)[0].strip()
            if url.startswith("https://") or url.startswith("http://"):
                return url
    return ""


def write_full_db(path, unique, metadata, site, store):
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
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
      availability_raw TEXT,
      product_url TEXT,
      source_url TEXT NOT NULL,
      base_site_id TEXT NOT NULL,
      store_alias_id TEXT NOT NULL,
      detected_at TEXT NOT NULL,
      raw_json TEXT NOT NULL
    );
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    """)
    rows = [(
        p["product_id"], p["product_name"], p.get("brand", ""),
        p.get("category_code", ""), p.get("category_name", ""),
        float(p["price_eur"]), p.get("price_formatted", ""),
        p.get("unit_price"), p.get("unit_price_formatted", ""),
        p.get("unit_price_unit", ""), p.get("availability_raw", ""),
        p.get("product_url", ""), p.get("source_url", ""),
        p.get("base_site_id", site), p.get("store_alias_id", store),
        p.get("detected_at", ""), json.dumps(p.get("raw_json") or {}, ensure_ascii=False, separators=(",", ":")),
    ) for p in unique.values()]
    con.executemany("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", metadata.items())
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    positive = con.execute("SELECT COUNT(*) FROM products WHERE price_eur > 0").fetchone()[0]
    con.close()
    return total, positive


def write_android_db(path, unique, metadata):
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE products(
      product_id TEXT PRIMARY KEY,
      product_name TEXT NOT NULL,
      brand TEXT,
      category_code TEXT NOT NULL,
      category_name TEXT NOT NULL,
      price_eur REAL NOT NULL,
      unit_price REAL,
      unit_price_unit TEXT,
      product_url TEXT,
      source_url TEXT,
      store_alias_id TEXT,
      detected_at TEXT,
      image_url TEXT
    );
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    """)
    rows = []
    for p in unique.values():
        code = str(p.get("category_code") or "")
        if code not in ANDROID_CODES:
            continue
        rows.append((
            str(p["product_id"]), str(p["product_name"]), str(p.get("brand") or ""),
            code, str(p.get("category_name") or EXPECTED_CODES.get(code, "")),
            float(p["price_eur"]), p.get("unit_price"), str(p.get("unit_price_unit") or ""),
            str(p.get("product_url") or ""), str(p.get("source_url") or ""),
            str(p.get("store_alias_id") or metadata["store_alias_id"]), str(p.get("detected_at") or ""),
            extract_image_url(p.get("raw_json") or {}),
        ))
    con.executemany("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    android_metadata = dict(metadata)
    android_metadata.update({
        "variant": "android-food-images-v2",
        "food_categories": str(len(ANDROID_CODES)),
        "products_positive_price": str(len(rows)),
    })
    con.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", android_metadata.items())
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    positive = con.execute("SELECT COUNT(*) FROM products WHERE price_eur > 0").fetchone()[0]
    con.execute("VACUUM")
    con.close()
    return total, positive


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--db", default="prezzi_sole365.db")
    ap.add_argument("--app-db", default="prezzi_sole365_app.db")
    ap.add_argument("--report", default="sole365_audit.json")
    ap.add_argument("--site", default="sole365")
    ap.add_argument("--store", default="marcianise-aurno")
    args = ap.parse_args()

    root = Path(args.input)
    files = sorted(root.rglob("category_*.json"))
    if not files:
        raise SystemExit("Nessun file categoria Sole365 trovato")

    categories = {}
    unique = {}
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        code = str(data.get("category_code") or "")
        if code not in EXPECTED_CODES:
            raise SystemExit(f"Categoria inattesa: {code}")
        if int(data.get("received") or 0) != int(data.get("total_results") or 0):
            raise SystemExit(f"Categoria incompleta: {code}")
        categories[code] = {
            "name": EXPECTED_CODES[code],
            "totalResults": int(data.get("total_results") or 0),
            "received": int(data.get("received") or 0),
            "positive_price_products": int(data.get("positive_price_products") or 0),
        }
        for p in data.get("products") or []:
            if float(p.get("price_eur") or 0) > 0:
                unique[str(p["product_id"])] = p

    missing = sorted(set(EXPECTED_CODES) - set(categories))
    if missing:
        raise SystemExit(f"Categorie Sole365 mancanti: {missing}")
    if len(unique) < 8000:
        raise SystemExit(f"Catalogo Sole365 troppo piccolo: {len(unique)} prodotti")

    stamp = datetime.now(timezone.utc).isoformat()
    metadata = {
        "supermarket": "Sole365",
        "source": "CosìComodo official OCC API",
        "base_site_id": args.site,
        "store_alias_id": args.store,
        "merged_at": stamp,
        "categories": str(len(categories)),
        "products_positive_price": str(len(unique)),
    }

    db = Path(args.db)
    total, positive = write_full_db(db, unique, metadata, args.site, args.store)
    if total != positive or total != len(unique):
        db.unlink(missing_ok=True)
        raise SystemExit(f"Audit DB fallito total={total} positive={positive} unique={len(unique)}")

    app_db = Path(args.app_db)
    app_total, app_positive = write_android_db(app_db, unique, metadata)
    if app_total != app_positive or app_total < 6000:
        app_db.unlink(missing_ok=True)
        raise SystemExit(f"Audit DB Android fallito total={app_total} positive={app_positive}")

    # I prodotti sfusi Sole365 devono conservare unit_price €/KG, altrimenti
    # l'app rischierebbe di interpretare 0,00198 €/g come prezzo confezione.
    con = sqlite3.connect(app_db)
    low_price_count = con.execute("SELECT COUNT(*) FROM products WHERE price_eur BETWEEN 0.0001 AND 0.1999").fetchone()[0]
    low_price_with_kg = con.execute("SELECT COUNT(*) FROM products WHERE price_eur BETWEEN 0.0001 AND 0.1999 AND UPPER(COALESCE(unit_price_unit,''))='KG' AND unit_price>0").fetchone()[0]
    con.close()
    if low_price_count > 0 and low_price_with_kg < int(low_price_count * 0.80):
        app_db.unlink(missing_ok=True)
        db.unlink(missing_ok=True)
        raise SystemExit(f"Audit peso variabile fallito low={low_price_count} withKG={low_price_with_kg}")

    report = {
        "version": "Sole365 matrix updater V1",
        "verdict": "SOLE365_MATRIX_V1_VALIDATED",
        "site": args.site,
        "store": args.store,
        "categories_expected": len(EXPECTED_CODES),
        "categories_downloaded": len(categories),
        "unique_products_positive_price": total,
        "android_food_products_positive_price": app_total,
        "android_food_categories": len(ANDROID_CODES),
        "android_low_price_rows": low_price_count,
        "android_low_price_rows_with_kg_reference": low_price_with_kg,
        "categories": categories,
        "merged_at": stamp,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
