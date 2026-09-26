#!/usr/bin/env python3
import argparse
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
import urllib.request
from pathlib import Path

SOURCES = [
    ("Piccolo", "https://raw.githubusercontent.com/calcagni1950srl-hash/piccolo-updater/main/prezzi.db"),
    ("Decò", "https://raw.githubusercontent.com/calcagni1950srl-hash/conad-updater/main/prezzi_deco.db"),
    ("Famila", "https://raw.githubusercontent.com/calcagni1950srl-hash/conad-updater/main/prezzi_famila_app.db"),
    ("Sole365", "https://raw.githubusercontent.com/calcagni1950srl-hash/conad-updater/main/prezzi_sole365_app.db"),
    ("Conad", "https://raw.githubusercontent.com/calcagni1950srl-hash/conad-updater/probe-conad-full-catalog/prezzi_conad_capodrise_stable_full.db"),
    ("Lidl", "https://raw.githubusercontent.com/calcagni1950srl-hash/conad-updater/probe-lidl-catalog/prezzi_lidl_everli.db"),
]

DEFAULT_STORE = {
    "Piccolo": "Piccolo",
    "Decò": "San Nicola La Strada - Via Milano 6",
    "Famila": "Teverola",
    "Sole365": "Marcianise Aurno",
    "Conad": "Conad Superstore Capodrise",
    "Lidl": "Caserta - Via Paolo Borsellino 4",
}

TOKEN_ALIASES = {
    "patate": "patata", "patata": "patata",
    "zucchine": "zucchina", "zucchina": "zucchina",
    "pomodori": "pomodoro", "pomodoro": "pomodoro",
    "melanzane": "melanzana", "melanzana": "melanzana",
    "peperoni": "peperone", "peperone": "peperone",
    "cipolle": "cipolla", "cipolla": "cipolla",
    "carote": "carota", "carota": "carota",
    "mele": "mela", "mela": "mela",
    "banane": "banana", "banana": "banana",
    "penne": "penne", "spaghetti": "spaghetti", "rigatoni": "rigatoni", "fusilli": "fusilli",
    "detersivi": "detersivo", "detergenti": "detersivo", "detergente": "detersivo",
}

STOP = {
    "di", "da", "dal", "dalla", "dello", "della", "dei", "degli", "delle",
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "e", "con", "per",
    "al", "alla", "allo", "ai", "alle", "del", "dell", "in", "x"
}

PASTA_FORMATS = {"penne", "spaghetti", "rigatoni", "fusilli", "farfalle", "linguine", "bucatini", "paccheri", "mezze", "ziti", "tortiglioni"}

def norm(s):
    s = s or ""
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"\s+", " ", s)

def tokens(name, brand, category):
    raw = norm(f"{name or ''} {brand or ''} {category or ''}")
    out = set()
    for t in raw.split():
        if len(t) < 2 or t in STOP or t.replace('.', '', 1).isdigit():
            continue
        c = TOKEN_ALIASES.get(t, t)
        out.add(c)
        if t in PASTA_FORMATS:
            out.add("pasta")
    words = raw.split()
    if "coca" in words and "cola" in words:
        out.update(("coca", "cola", "cocacola"))
    if "cocacola" in raw.replace(" ", ""):
        out.update(("coca", "cola", "cocacola"))
    return sorted(out)

def download(url, target, retries=3):
    target = Path(target)
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LaMiaSpesa-FastDB/1.0"})
            with urllib.request.urlopen(req, timeout=45) as r, open(target, "wb") as f:
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            with open(target, "rb") as f:
                if f.read(16) != b"SQLite format 3\x00":
                    raise RuntimeError(f"{url}: file scaricato non SQLite")
            return
        except Exception as e:
            last = e
            if target.exists():
                target.unlink()
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Download fallito: {url}: {last}")

def tables(c):
    return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}

def q(name):
    return '"' + str(name).replace('"', '""') + '"'

def columns(c, table):
    return {r[1] for r in c.execute(f"pragma table_info({q(table)})")}

def first(cols, *names):
    for n in names:
        if n in cols:
            return n
    return None

def select_expr(col):
    return q(col) if col else "NULL"

def safe_float(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None

def safe_int(v):
    try:
        return int(v) if v is not None else 0
    except Exception:
        return 0

def rows_piccolo(c):
    ts = tables(c)
    table = "products_certified" if "products_certified" in ts else "products_current" if "products_current" in ts else None
    if not table:
        return
    cols = columns(c, table)
    name = first(cols, "name", "product_name")
    price = first(cols, "price_eur")
    if not name or not price:
        return
    mkt = first(cols, "supermarket"); store = first(cols, "store_code", "store")
    cat = first(cols, "category", "category_name")
    qv = first(cols, "quantity_value"); qu = first(cols, "quantity_unit")
    up = first(cols, "unit_price_eur", "unit_price"); upu = first(cols, "unit_price_unit")
    var = first(cols, "variable_weight"); src = first(cols, "source_url")
    checked = first(cols, "checked_at", "last_seen"); audit = first(cols, "audit_status")
    where = f"{q(price)} > 0"
    if audit:
        where += f" AND ({q(audit)} IS NULL OR UPPER({q(audit)}) IN ('VALID','OK','CERTIFIED'))"
    sql = f"SELECT {','.join(select_expr(x) for x in [mkt,store,cat,name,qv,qu,price,up,upu,var,src,checked])} FROM {q(table)} WHERE {where}"
    for r in c.execute(sql):
        nm = r[3] or ""
        yield dict(market=r[0] or "Piccolo", key="|".join(str(x or "") for x in [r[0],r[1],r[2],nm]), store=r[1], name=nm,
                   brand=None, category=r[2], quantity_text=nm, quantity_value=r[4], quantity_unit=r[5], price=r[6], unit_price=r[7], unit_price_unit=r[8], variable=safe_int(r[9]), source=r[10], checked=r[11])

def rows_deco(c):
    if "products" not in tables(c): return
    cols = columns(c, "products")
    required = {"product_key","supermarket","store","product_name","brand","category","quantity_text","price_eur"}
    if not required.issubset(cols): return
    sql = "SELECT product_key,supermarket,store,product_name,brand,category,quantity_text,price_eur,unit_price_text,product_url,checked_at FROM products WHERE price_eur > 0"
    for r in c.execute(sql):
        yield dict(market=r[1] or "Decò", key=r[0], store=r[2], name=r[3] or "", brand=r[4], category=r[5], quantity_text=r[6], quantity_value=None, quantity_unit=None,
                   price=r[7], unit_price=None, unit_price_unit=None, variable=0, source=r[9], checked=r[10])

def rows_cosi(c, market):
    if "products" not in tables(c): return
    cols = columns(c, "products")
    req = {"product_id","product_name","brand","category_name","price_eur","unit_price","unit_price_unit"}
    if not req.issubset(cols): return
    optional = [first(cols,"product_url"), first(cols,"source_url"), first(cols,"store_alias_id"), first(cols,"detected_at")]
    sql = f"SELECT product_id,product_name,brand,category_name,price_eur,unit_price,unit_price_unit,{','.join(select_expr(x) for x in optional)} FROM products WHERE price_eur > 0"
    for r in c.execute(sql):
        yield dict(market=market, key=f"{market}:{r[0]}", store=r[9] or DEFAULT_STORE[market], name=r[1] or "", brand=r[2], category=r[3], quantity_text=r[1] or "", quantity_value=None, quantity_unit=None,
                   price=r[4], unit_price=r[5], unit_price_unit=r[6], variable=0, source=r[8] or r[7], checked=r[10])

def rows_conad(c):
    if "products_current" not in tables(c): return
    cols = columns(c, "products_current")
    if not {"product_name","price_eur"}.issubset(cols): return
    names = [first(cols,"supermarket"), first(cols,"store_code"), first(cols,"store_name"), first(cols,"product_code"), first(cols,"product_name"), first(cols,"brand"),
             first(cols,"category1"), first(cols,"category2"), first(cols,"category3"), first(cols,"quantity_value"), first(cols,"quantity_unit"), first(cols,"price_eur"),
             first(cols,"unit_price"), first(cols,"unit_price_unit"), first(cols,"checked_at"), first(cols,"variable_weight")]
    sql = f"SELECT {','.join(select_expr(x) for x in names)} FROM products_current WHERE {q(first(cols,'price_eur'))} > 0"
    for r in c.execute(sql):
        category = " > ".join(str(x) for x in r[6:9] if x)
        yield dict(market=r[0] or "Conad", key=f"CONAD:{r[1] or ''}:{r[3] or ''}", store=r[2] or r[1] or DEFAULT_STORE["Conad"], name=r[4] or "", brand=r[5], category=category or None,
                   quantity_text=r[4] or "", quantity_value=r[9], quantity_unit=r[10], price=r[11], unit_price=r[12], unit_price_unit=r[13], variable=safe_int(r[15]), source=None, checked=r[14])

def rows_lidl(c):
    ts = tables(c); table = "products_android" if "products_android" in ts else "products" if "products" in ts else None
    if not table: return
    cols = columns(c, table)
    pid = first(cols,"product_id","id","sku"); name = first(cols,"product_name","name"); brand=first(cols,"brand"); cat=first(cols,"category_name","category"); price=first(cols,"price_eur","price")
    if not pid or not name or not price: return
    qv=first(cols,"quantity_value"); qu=first(cols,"quantity_unit_raw","quantity_unit"); qt=first(cols,"quantity_text"); store=first(cols,"store_id","store"); src=first(cols,"product_url","source_url")
    sql=f"SELECT {','.join(select_expr(x) for x in [pid,name,brand,cat,price,qv,qu,qt,store,src])} FROM {q(table)} WHERE {q(price)} > 0"
    for r in c.execute(sql):
        yield dict(market="Lidl", key=f"LIDL:{r[0]}", store=r[8] or DEFAULT_STORE["Lidl"], name=r[1] or "", brand=r[2], category=r[3], quantity_text=f"{r[1] or ''} {r[7] or ''}".strip(), quantity_value=r[5], quantity_unit=r[6], price=r[4], unit_price=None, unit_price_unit=None, variable=0, source=r[9], checked=None)

def iter_rows(market, path):
    c = sqlite3.connect(path)
    try:
        if market == "Piccolo": yield from rows_piccolo(c)
        elif market == "Decò": yield from rows_deco(c)
        elif market in ("Famila","Sole365"): yield from rows_cosi(c, market)
        elif market == "Conad": yield from rows_conad(c)
        elif market == "Lidl": yield from rows_lidl(c)
    finally:
        c.close()

def build(output):
    output = Path(output)
    tmpout = output.with_suffix(output.suffix + ".tmp")
    if tmpout.exists(): tmpout.unlink()
    con = sqlite3.connect(tmpout)
    con.executescript("""
    PRAGMA journal_mode=OFF;
    PRAGMA synchronous=OFF;
    PRAGMA temp_store=MEMORY;
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE products(
      id INTEGER PRIMARY KEY, market TEXT NOT NULL, product_key TEXT NOT NULL, store TEXT,
      name TEXT NOT NULL, norm_name TEXT NOT NULL, brand TEXT, norm_brand TEXT,
      category TEXT, norm_category TEXT, quantity_text TEXT, quantity_value REAL,
      quantity_unit TEXT, price_eur REAL NOT NULL, unit_price_eur REAL, unit_price_unit TEXT,
      variable_weight INTEGER NOT NULL DEFAULT 0, source_url TEXT, checked_at TEXT,
      UNIQUE(market, product_key)
    );
    CREATE TABLE product_tokens(
      token TEXT NOT NULL, product_id INTEGER NOT NULL,
      PRIMARY KEY(token, product_id)
    ) WITHOUT ROWID;
    """)
    counts = {}
    with tempfile.TemporaryDirectory(prefix="fastcompare_") as td:
        for market, url in SOURCES:
            local = Path(td) / (market.replace("ò","o") + ".db")
            print(f"Download {market}...")
            download(url, local)
            n=0
            for p in iter_rows(market, local):
                price=safe_float(p.get("price"))
                if not p.get("name") or not price or price <= 0: continue
                cur=con.execute("""INSERT OR IGNORE INTO products(market,product_key,store,name,norm_name,brand,norm_brand,category,norm_category,quantity_text,quantity_value,quantity_unit,price_eur,unit_price_eur,unit_price_unit,variable_weight,source_url,checked_at)
                                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (market,str(p.get("key") or ""),p.get("store"),p["name"],norm(p["name"]),p.get("brand"),norm(p.get("brand")),p.get("category"),norm(p.get("category")),p.get("quantity_text"),safe_float(p.get("quantity_value")),p.get("quantity_unit"),price,safe_float(p.get("unit_price")),p.get("unit_price_unit"),safe_int(p.get("variable")),p.get("source"),p.get("checked")))
                if cur.rowcount == 0: continue
                pid=cur.lastrowid
                con.executemany("INSERT OR IGNORE INTO product_tokens(token,product_id) VALUES(?,?)", [(t,pid) for t in tokens(p["name"],p.get("brand"),p.get("category"))])
                n += 1
            counts[market]=n
            con.commit()
            print(f"{market}: {n} prodotti")
    con.executescript("""
    CREATE INDEX idx_products_market ON products(market);
    CREATE INDEX idx_products_market_name ON products(market, norm_name);
    CREATE INDEX idx_products_market_brand ON products(market, norm_brand);
    CREATE INDEX idx_tokens_product ON product_tokens(product_id);
    INSERT INTO meta(key,value) VALUES('schema_version','1');
    """)
    con.execute("INSERT INTO meta(key,value) VALUES('built_at',datetime('now'))")
    con.execute("INSERT INTO meta(key,value) VALUES('markets',?)", (','.join(m for m,_ in SOURCES),))
    for m,n in counts.items(): con.execute("INSERT INTO meta(key,value) VALUES(?,?)", (f"count_{m}",str(n)))
    con.commit()
    con.execute("VACUUM")
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    total = con.execute("SELECT count(*) FROM products").fetchone()[0]
    con.close()
    if ok != "ok" or total == 0:
        raise RuntimeError(f"DB finale non valido: integrity={ok}, products={total}")
    os.replace(tmpout, output)
    print(f"Creato {output} - {total} prodotti - {output.stat().st_size/1024/1024:.2f} MB")
    print("Conteggi:", counts)

if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",default="prezzi_confronto_fast.db")
    args=ap.parse_args()
    build(args.output)
