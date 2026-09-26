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

# Solo reparti che possono servire al menu/spesa alimentare dell'app.
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
        # Example value: {80x80=https://..., 300x300=https://..., 350x350=https://...}
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
    rows = []
    for p in unique.values():
        rows.append((
            p["product_id"], p["product_name"], p.get("brand", ""),
            p.get("category_code", ""), p.get("category_name", ""),
            float(p["price_eur"]), p.get("price_formatted", ""),
            p.get("unit_price"), p.get("unit_price_formatted", ""),
            p.get("unit_price_unit", ""), p.get("availability_raw", ""),
            p.get("product_url", ""), p.get("source_url", ""),
            p.get("base_site_id", site), p.get("store_alias_id", store),
            p.get("detected_at", ""), json.dumps(p.get("raw_json") or {}, ensure_ascii=False, separators=(",", ":")),
        ))
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
    ap.add_argument("--db", default="prezzi_famila.db")
    ap.add_argument("--app-db", default="prezzi_famila_app.db")
    ap.add_argument("--report", default="famila_audit.json")
    ap.add_argument("--site", default="familasud")
    ap.add_argument("--store", default="teverola")
    args = ap.parse_args()

    root = Path(args.input)
    files = sorted(root.rglob("category_*.json"))
    if not files:
        raise SystemExit("Nessun file categoria Famila trovato")

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
            if float(p.get("price_eur") or 0) <= 0:
                continue
            unique[str(p["product_id"])] = p

    missing = sorted(set(EXPECTED_CODES) - set(categories))
    if missing:
        raise SystemExit(f"Categorie Famila mancanti: {missing}")
    if len(unique) < 3000:
        raise SystemExit(f"Catalogo Famila troppo piccolo: {len(unique)} prodotti")

    stamp = datetime.now(timezone.utc).isoformat()
    metadata = {
        "supermarket": "Famila",
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
    if app_total != app_positive or app_total < 6500:
        app_db.unlink(missing_ok=True)
        raise SystemExit(f"Audit DB Android fallito total={app_total} positive={app_positive}")

    report = {
        "version": "Famila matrix updater V5",
        "verdict": "FAMILA_MATRIX_V5_VALIDATED",
        "site": args.site,
        "store": args.store,
        "categories_expected": len(EXPECTED_CODES),
        "categories_downloaded": len(categories),
        "unique_products_positive_price": total,
        "android_food_products_positive_price": app_total,
        "android_food_categories": len(ANDROID_CODES),
        "categories": categories,
        "merged_at": stamp,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
