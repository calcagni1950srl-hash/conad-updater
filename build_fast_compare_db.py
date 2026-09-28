#!/usr/bin/env python3
import argparse
import json
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
import urllib.request
from pathlib import Path

CONAD_OVERLAY_URL = "https://raw.githubusercontent.com/calcagni1950srl-hash/conad-updater/probe-conad-full-catalog/conad_capodrise_search_overlay.json"

SOURCES = [
    ("Piccolo", "https://raw.githubusercontent.com/calcagni1950srl-hash/piccolo-updater/main/prezzi.db"),
    ("Decò", "https://raw.githubusercontent.com/calcagni1950srl-hash/conad-updater/main/prezzi_deco.db"),
    ("Famila", "https://raw.githubusercontent.com/calcagni1950srl-hash/conad-updater/main/prezzi_famila_all_app.db"),
    ("Sole365", "https://raw.githubusercontent.com/calcagni1950srl-hash/conad-updater/main/prezzi_sole365_all_app.db"),
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


# Tassonomia canonica usata dall'app. Le regole vengono applicate a TUTTI i prodotti.
CANONICAL_RULES = [
    ("CARTA_IGIENICA", "carta_igienica", ["carta igienica", "carta wc", "toilet paper", "rotoli igienici"]),
    ("PROSCIUTTO_CRUDO", "prosciutto_crudo", ["prosciutto crudo", "prosciutto di parma", "san daniele", "crudo stagionato"]),
    ("PROSCIUTTO_COTTO", "prosciutto_cotto", ["prosciutto cotto"]),
    ("MORTADELLA", "mortadella", ["mortadella"]),
    ("SALAME", "salame", ["salame", "soppressata"]),
    ("ACQUA", "acqua", ["acqua minerale", "acqua naturale", "acqua frizzante", "acqua effervescente", "acqua"]),
    ("COLA", "cola", ["coca cola", "coca-cola", "pepsi", "cola"]),
    ("PASTA_SECCA", "pasta_secca", ["spaghetti", "penne", "rigatoni", "fusilli", "farfalle", "linguine", "bucatini", "paccheri", "ziti", "tortiglioni", "pasta di semola"]),
    ("PASTA_FRESCA_RIPIENA", "pasta_fresca_ripiena", ["tortellini", "tortelloni", "ravioli", "cappelletti", "agnolotti"]),
    ("GNOCCHI", "gnocchi", ["gnocchi"]),
    ("PANE", "pane", ["pane", "panini", "baguette", "rosetta"]),
    ("PIZZA_FOCACCIA", "pizza_focaccia", ["pizza", "focaccia"]),
    ("LATTE", "latte", ["latte"]),
    ("YOGURT", "yogurt", ["yogurt"]),
    ("MOZZARELLA", "mozzarella", ["mozzarella", "fiordilatte", "fior di latte"]),
    ("FORMAGGIO", "formaggio", ["parmigiano", "grana", "pecorino", "provolone", "emmental", "formaggio"]),
    ("UOVA", "uova", ["uova", "uovo"]),
    ("BURRO", "burro", ["burro"]),
    ("OLIO", "olio", ["olio extravergine", "olio evo", "olio di oliva", "olio oliva", "olio di semi"]),
    ("FARINA", "farina", ["farina"]),
    ("ZUCCHERO", "zucchero", ["zucchero"]),
    ("SALE", "sale", ["sale fino", "sale grosso", "sale marino", "sale"]),
    ("RISO", "riso", ["riso"]),
    ("PASSATA", "passata_pomodoro", ["passata di pomodoro", "passata pomodoro"]),
    ("PELATI", "pomodori_pelati", ["pomodori pelati", "pelati"]),
    ("TONNO_CONSERVA", "tonno_conserva", ["tonno sott olio", "tonno sott'olio", "tonno al naturale", "tonno in scatola"]),
    ("LEGUMI", "legumi", ["fagioli", "ceci", "lenticchie", "piselli", "legumi"]),
    ("BISCOTTI", "biscotti", ["biscotti"]),
    ("CEREALI_COLAZIONE", "cereali_colazione", ["cereali colazione", "corn flakes", "muesli"]),
    ("CAFFE", "caffe", ["caffe", "caffè"]),
    ("SUCCO", "succo", ["succo", "nettare"]),
    ("BIRRA", "birra", ["birra"]),
    ("VINO", "vino", ["vino"]),
    ("DETERSIVO_LAVATRICE", "detersivo_lavatrice", ["detersivo lavatrice", "capsule lavatrice", "pods lavatrice"]),
    ("AMMORBIDENTE", "ammorbidente", ["ammorbidente"]),
    ("DETERSIVO_PIATTI", "detersivo_piatti", ["detersivo piatti", "detergente piatti"]),
    ("LAVASTOVIGLIE", "lavastoviglie", ["lavastoviglie", "tabs lavastoviglie", "pastiglie lavastoviglie"]),
    ("SGRASSATORE", "sgrassatore", ["sgrassatore"]),
    ("CANDEGGINA", "candeggina", ["candeggina"]),
    ("SHAMPOO", "shampoo", ["shampoo"]),
    ("BAGNOSCHIUMA", "bagnoschiuma", ["bagnoschiuma", "docciaschiuma"]),
    ("DENTIFRICIO", "dentifricio", ["dentifricio"]),
    ("DEODORANTE", "deodorante", ["deodorante"]),
    ("FAZZOLETTI", "fazzoletti", ["fazzoletti"]),
    ("TOVAGLIOLI", "tovaglioli", ["tovaglioli"]),
    ("CARTA_CUCINA", "carta_cucina", ["carta cucina", "rotoloni cucina", "asciugatutto", "scottex"]),
    ("PELLICOLA", "pellicola", ["pellicola"]),
    ("ALLUMINIO", "alluminio", ["alluminio"]),
    ("PANNOLINI", "pannolini", ["pannolini"]),
    ("CIBO_CANE", "cibo_cane", ["crocchette cane", "cibo cane", "dog food"]),
    ("CIBO_GATTO", "cibo_gatto", ["crocchette gatto", "cibo gatto", "cat food"]),
    ("PATATE_FRESCHE", "patate", ["patate", "patata"]),
    ("ZUCCHINE_FRESCHE", "zucchine", ["zucchine", "zucchina"]),
    ("POMODORI_FRESCHI", "pomodori", ["pomodori", "pomodoro"]),
    ("MELANZANE_FRESCHE", "melanzane", ["melanzane", "melanzana"]),
    ("PEPERONI_FRESCHI", "peperoni", ["peperoni", "peperone"]),
    ("CIPOLLE_FRESCHE", "cipolle", ["cipolle", "cipolla"]),
    ("CAROTE_FRESCHE", "carote", ["carote", "carota"]),
    ("MELE_FRESCHE", "mele", ["mele", "mela"]),
    ("BANANE_FRESCHE", "banane", ["banane", "banana"]),
    ("INSALATA_FRESCA", "insalata", ["lattuga", "insalata"]),
]

CATEGORY_HINTS = [
    ("CARTA_MONOUSO", ["carta e plastica", "carta monouso", "igiene casa"]),
    ("SALUMI", ["salumi", "affettati", "carne e salumi"]),
    ("BEVANDE", ["acqua, bevande, vino e alcolici", "acqua bevande vino e alcolici"]),
    ("BIBITE", ["bibite", "bevande analcoliche"]),
    ("PASTA", ["pasta e riso", "pasta pane", "pasta secca"]),
    ("PASTA_FRESCA", ["pasta fresca", "gastronomia e pasta fresca", "ravioli e tortellini"]),
    ("LATTICINI", ["latticini", "latte e derivati", "latte burro uova e yogurt", "formaggi latte e uova"]),
    ("FORMAGGI", ["formaggi"]),
    ("ORTOFRUTTA", ["ortofrutta", "frutta e verdura", "verdura fresca", "frutta fresca", "frutta", "verdura legumi e cereali", "verdura legumi cereali", "altra frutta"]),
    ("CARNE", ["carne"]),
    ("PESCE", ["pesce", "pesce scatola"]),
    ("PESCE_SURGELATO", ["pesce surgelato"]),
    ("SURGELATI", ["surgelati", "gelati e surgelati"]),
    ("GELATI", ["gelati"]),
    ("COLAZIONE_DOLCI", ["colazione merenda e dolci", "dolci", "biscotti cereali e dolci", "yogurt e dessert"]),
    ("SNACK", ["snack dolci e salati", "snack e patatine", "panetteria e snack salati"]),
    ("PANETTERIA", ["pane e pasticceria", "panetteria", "pasticceria"]),
    ("CONDIMENTI", ["condimenti", "olio aceto", "sale aromi e spezie"]),
    ("SUGHI", ["sughi", "derivati del pomodoro"]),
    ("CONSERVE", ["conserve", "sottoli", "sottaceti"]),
    ("DISPENSA", ["dispensa", "prodotti alimentari"]),
    ("CAFFE_INFUSI", ["caffe ed infusi", "caffe e infusi", "te infuso", "tisane"]),
    ("VINO", ["prosecco", "spumanti", "champagne"]),
    ("ALCOLICI", ["aperitivi e birra"]),
    ("ALCOLICI", ["vino amari e distillati", "liquori", "distillati"]),
    ("CASA_PULIZIA", ["pulizia", "cura casa", "detersivi", "lavastoviglie", "bucato"]),
    ("IGIENE_PERSONALE", ["cura del corpo", "igiene personale", "capelli", "igiene orale"]),
    ("INFANZIA", ["infanzia", "tutto per l infanzia"]),
    ("ANIMALI", ["animali", "pet"]),
    ("GASTRONOMIA", ["gastronomia", "piatti pronti"]),
    ("INTEGRATORI", ["integratori", "alimentazione sportiva", "prodotti dietetici"]),
]

NEGATIVE_BY_CANONICAL = {
    "PROSCIUTTO_CRUDO": ["tortellini", "tortelloni", "ravioli", "cappelletti", "sfoglia", "pizza", "panino", "sandwich", "stick", "snack"],
    "PROSCIUTTO_COTTO": ["tortellini", "tortelloni", "ravioli", "cappelletti", "pizza", "panino", "sandwich", "snack"],
    "PATATE_FRESCHE": ["gnocchi", "chips", "patatine", "pure", "purè", "surgelat", "fritte", "crocchette"],
    "ZUCCHINE_FRESCHE": ["tortino", "burger", "grigliat", "surgelat", "ripien", "minestrone", "vellutata"],
    "CARTA_IGIENICA": ["salviette", "umidificata"],
}

def precise_type(name, brand, category):
    n = norm(name)
    b = norm(brand)
    c = norm(category)

    # Carta igienica: mai dedotta dalla sola categoria generica "carta e plastica".
    if (
        "carta igienica" in n or
        re.search(r"(^| )c igienica( |$)", n) or
        ("igienica" in n and ("rotol" in n or "carta" in n)) or
        "carta igienica" in c or "toilet paper" in n or "toilet paper" in c
    ) and not any(x in n for x in ["salviette", "umidificata"]):
        return ("CARTA_IGIENICA", "carta_igienica", 100)

    # Prosciutto: richiede contesto salumi oppure nome che identifica chiaramente il prodotto.
    salumi_ctx = any(x in c for x in ["salumi", "affettati", "prosciutto crudo"])
    pasta_ctx = any(x in c for x in ["pasta", "ravioli", "tortellini", "gastronomia"])
    if (
        ("prosciutto crudo" in n or "prosciutto di parma" in n or "san daniele" in n) and
        not pasta_ctx and
        not any(x in n for x in ["tortell", "raviol", "cappellett", "sfoglia", "pizza", "panino", "sandwich", "stick"])
    ):
        return ("PROSCIUTTO_CRUDO", "prosciutto_crudo", 100)
    if ("prosciutto cotto" in n and not pasta_ctx and not any(x in n for x in ["tortell", "raviol", "pizza", "panino"])):
        return ("PROSCIUTTO_COTTO", "prosciutto_cotto", 100)

    # Acqua: deve essere davvero una bevanda/acqua, non "pesce di acqua dolce" o "tonno in acqua".
    water_cat = (
        c == "acqua" or
        (c.startswith("acqua ") and "bevande" not in c and "vino" not in c and "alcol" not in c) or
        " > acqua > " in (" " + c + " ") or
        "bevande e preparati acqua" in c
    )
    water_name = n.startswith("acqua ") or " acqua minerale " in (" " + n + " ")
    beverage_ctx = any(x in c for x in ["acqua", "bevande", "water"]) and not any(x in c for x in ["viso", "tonici", "cosmesi", "cura persona", "igiene", "pesce", "tonno"])
    if (water_cat or (water_name and beverage_ctx)) and not any(x in c for x in ["pesce", "tonno"]) and not any(x in n for x in ["tonno", "filetti", "pesce", "micellare", "profumo", "profum"]):
        return ("ACQUA", "acqua", 100)

    # Cola: solo bibita/cola, mai caramelle o dolci al gusto cola.
    cola_ctx = c == "cola" or "bibite" in c or "bevande gassate" in c
    cola_name = (
        "coca cola" in n or "coca-cola" in n or "pepsi" in n or
        n.startswith("cola ") or n == "cola" or
        b in {"coca cola", "coca-cola", "pepsi"}
    )
    if (cola_ctx or cola_name) and not any(x in c for x in ["caramelle", "dolci", "pasticceria"]) and not "caramell" in n:
        return ("COLA", "cola", 100)

    return None

def canonicalize(name, brand, category):
    n = norm(name)
    c = norm(category)
    whole = f" {n} "
    # prima regole forti sul nome
    for canonical, subtype, aliases in CANONICAL_RULES:
        matched = None
        for alias in aliases:
            a = norm(alias)
            if re.search(r"(^| )" + re.escape(a) + r"( |$)", n):
                matched = a
                break
        if not matched:
            continue
        negatives = NEGATIVE_BY_CANONICAL.get(canonical, [])
        if any(norm(x) in n for x in negatives):
            continue
        # ortofrutta: richiede categoria fresca oppure nome che inizia/è quasi puro
        if canonical.endswith("_FRESCHE") or canonical == "INSALATA_FRESCA":
            fresh_cat = any(x in c for x in ["ortofrutta", "frutta", "verdura", "vegetali", "ortaggi", "fresco"])
            words = n.split()
            aliases_norm = {norm(x) for x in aliases}
            first_match = next((i for i,w in enumerate(words) if w in aliases_norm), -1)
            if not fresh_cat and first_match > 1:
                continue
        return canonical, subtype, 100

    # poi fallback controllato dalla categoria sorgente
    for canonical, hints in CATEGORY_HINTS:
        def hint_match(h):
            if h.startswith("="):
                return c == norm(h[1:])
            if h.startswith("^"):
                return c.startswith(norm(h[1:]))
            return norm(h) in c
        if any(hint_match(h) for h in hints):
            return canonical, None, 70

    return "ALTRO", None, 30


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
    qv=first(cols,"quantity_value"); qu=first(cols,"quantity_unit_raw","quantity_unit"); qt=first(cols,"quantity_text")
    up=first(cols,"unit_price"); upu=first(cols,"unit_price_unit"); var=first(cols,"variable_weight")
    minq=first(cols,"minimum_quantity"); store=first(cols,"store_id","store"); src=first(cols,"product_url","source_url")
    sql=f"SELECT {','.join(select_expr(x) for x in [pid,name,brand,cat,price,qv,qu,qt,up,upu,var,minq,store,src])} FROM {q(table)} WHERE {q(price)} > 0"
    for r in c.execute(sql):
        extra = f" min_qty {r[11]}" if r[11] not in (None,0,0.0,"") else ""
        yield dict(market="Lidl", key=f"LIDL:{r[0]}", store=r[12] or DEFAULT_STORE["Lidl"], name=r[1] or "", brand=r[2], category=r[3], quantity_text=f"{r[1] or ''} {r[7] or ''}{extra}".strip(), quantity_value=r[5], quantity_unit=r[6], price=r[4], unit_price=r[8], unit_price_unit=r[9], variable=safe_int(r[10]), source=r[13], checked=None)

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


def load_conad_overlay():
    req = urllib.request.Request(CONAD_OVERLAY_URL, headers={"User-Agent":"LaMiaSpesa-FastDB/1.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.load(r)
    if not data.get("store_selected_verified") or str(data.get("store_code")) != "010548":
        raise RuntimeError("Overlay Conad non verificato per Capodrise 010548")
    for x in data.get("products", []):
        price = safe_float(x.get("price_eur"))
        if not x.get("name") or not price or price <= 0:
            continue
        yield dict(
            market="Conad",
            key=f"CONAD:010548:overlay:{x.get('code') or norm(x.get('name'))}",
            store=data.get("store_name") or DEFAULT_STORE["Conad"],
            name=x.get("name") or "",
            brand="Conad" if "conad" in norm(x.get("name")) else None,
            category=x.get("category"),
            quantity_text=x.get("name") or "",
            quantity_value=x.get("quantity_value"),
            quantity_unit=x.get("quantity_unit"),
            price=price,
            unit_price=None,
            unit_price_unit=None,
            variable=0,
            source="Conad Capodrise verified search overlay",
            checked=None,
        )

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
      canonical_category TEXT NOT NULL, product_type TEXT, classification_confidence INTEGER NOT NULL,
      UNIQUE(market, product_key)
    );
    CREATE TABLE product_tokens(
      token TEXT NOT NULL, product_id INTEGER NOT NULL,
      PRIMARY KEY(token, product_id)
    ) WITHOUT ROWID;
    CREATE TABLE canonical_aliases(
      alias_norm TEXT PRIMARY KEY,
      canonical_category TEXT NOT NULL,
      product_type TEXT
    ) WITHOUT ROWID;
    """)
    # Dizionario piccolo che l'app usa per interpretare nomi alternativi.
    alias_rows = {}
    for canonical, subtype, aliases in CANONICAL_RULES:
        for alias in aliases:
            alias_rows[norm(alias)] = (canonical, subtype)
    # sinonimi d'uso comune non necessariamente presenti nei nomi catalogo
    alias_rows.update({
        "carta wc": ("CARTA_IGIENICA", "carta_igienica"),
        "carta toilette": ("CARTA_IGIENICA", "carta_igienica"),
        "rotoli bagno": ("CARTA_IGIENICA", "carta_igienica"),
        "rotoloni bagno": ("CARTA_IGIENICA", "carta_igienica"),
        "crudo": ("PROSCIUTTO_CRUDO", "prosciutto_crudo"),
        "cotto": ("PROSCIUTTO_COTTO", "prosciutto_cotto"),
        "acqua minerale": ("ACQUA", "acqua"),
        "acqua naturale": ("ACQUA", "acqua"),
        "acqua frizzante": ("ACQUA", "acqua"),
        "acqua effervescente": ("ACQUA", "acqua"),
        "coca cola": ("COLA", "cola"),
        "coca-cola": ("COLA", "cola"),
    })
    con.executemany("INSERT OR REPLACE INTO canonical_aliases(alias_norm,canonical_category,product_type) VALUES(?,?,?)",
                    [(a,v[0],v[1]) for a,v in alias_rows.items()])
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
                canonical, product_type, confidence = canonicalize(p["name"], p.get("brand"), p.get("category"))
                precise = precise_type(p["name"], p.get("brand"), p.get("category"))
                if precise is not None:
                    canonical, product_type, confidence = precise
                elif product_type in {"carta_igienica","prosciutto_crudo","prosciutto_cotto","acqua","cola"}:
                    # Per i tipi più sensibili non manteniamo un subtype ricavato da una sola parola.
                    product_type = None
                cur=con.execute("""INSERT OR IGNORE INTO products(market,product_key,store,name,norm_name,brand,norm_brand,category,norm_category,quantity_text,quantity_value,quantity_unit,price_eur,unit_price_eur,unit_price_unit,variable_weight,source_url,checked_at,canonical_category,product_type,classification_confidence)
                                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (market,str(p.get("key") or ""),p.get("store"),p["name"],norm(p["name"]),p.get("brand"),norm(p.get("brand")),p.get("category"),norm(p.get("category")),p.get("quantity_text"),safe_float(p.get("quantity_value")),p.get("quantity_unit"),price,safe_float(p.get("unit_price")),p.get("unit_price_unit"),safe_int(p.get("variable")),p.get("source"),p.get("checked"),canonical,product_type,confidence))
                if cur.rowcount == 0: continue
                pid=cur.lastrowid
                con.executemany("INSERT OR IGNORE INTO product_tokens(token,product_id) VALUES(?,?)", [(t,pid) for t in tokens(p["name"],p.get("brand"),p.get("category"))])
                n += 1
            counts[market]=n
            con.commit()
            print(f"{market}: {n} prodotti")

        # Overlay verificato sul punto vendita Conad Capodrise 010548.
        overlay_n = 0
        for p in load_conad_overlay():
            price=safe_float(p.get("price"))
            canonical, product_type, confidence = canonicalize(p["name"], p.get("brand"), p.get("category"))
            precise = precise_type(p["name"], p.get("brand"), p.get("category"))
            if precise is not None:
                canonical, product_type, confidence = precise
            cur=con.execute("""INSERT OR REPLACE INTO products(market,product_key,store,name,norm_name,brand,norm_brand,category,norm_category,quantity_text,quantity_value,quantity_unit,price_eur,unit_price_eur,unit_price_unit,variable_weight,source_url,checked_at,canonical_category,product_type,classification_confidence)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            ("Conad",str(p.get("key") or ""),p.get("store"),p["name"],norm(p["name"]),p.get("brand"),norm(p.get("brand")),p.get("category"),norm(p.get("category")),p.get("quantity_text"),safe_float(p.get("quantity_value")),p.get("quantity_unit"),price,None,None,0,p.get("source"),None,canonical,product_type,confidence))
            pid=cur.lastrowid
            if pid:
                con.executemany("INSERT OR IGNORE INTO product_tokens(token,product_id) VALUES(?,?)", [(t,pid) for t in tokens(p["name"],p.get("brand"),p.get("category"))])
                overlay_n += 1
        counts["Conad"] = counts.get("Conad",0) + overlay_n
        con.commit()
        print(f"Conad overlay: {overlay_n} prodotti")
    con.executescript("""
    CREATE INDEX idx_products_market ON products(market);
    CREATE INDEX idx_products_market_name ON products(market, norm_name);
    CREATE INDEX idx_products_market_brand ON products(market, norm_brand);
    CREATE INDEX idx_tokens_product ON product_tokens(product_id);
    CREATE INDEX idx_products_canonical_market ON products(canonical_category, market);
    CREATE INDEX idx_products_type_market ON products(product_type, market);
    INSERT INTO meta(key,value) VALUES('schema_version','3');
    """)
    con.execute("INSERT INTO meta(key,value) VALUES('built_at',datetime('now'))")
    con.execute("INSERT INTO meta(key,value) VALUES('markets',?)", (','.join(m for m,_ in SOURCES),))
    for m,n in counts.items(): con.execute("INSERT INTO meta(key,value) VALUES(?,?)", (f"count_{m}",str(n)))
    con.commit()
    con.execute("VACUUM")
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    total = con.execute("SELECT count(*) FROM products").fetchone()[0]
    strong = con.execute("SELECT count(*) FROM products WHERE classification_confidence >= 70").fetchone()[0]
    exact = con.execute("SELECT count(*) FROM products WHERE classification_confidence = 100").fetchone()[0]
    other = con.execute("SELECT count(*) FROM products WHERE canonical_category='ALTRO'").fetchone()[0]
    print(f"CLASSIFICAZIONE: forte={strong}/{total} ({strong*100.0/total:.1f}%) esatta={exact}/{total} ({exact*100.0/total:.1f}%) altro={other}")
    print("TOP categorie:", con.execute("SELECT canonical_category,count(*) FROM products GROUP BY canonical_category ORDER BY count(*) DESC LIMIT 25").fetchall())
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
