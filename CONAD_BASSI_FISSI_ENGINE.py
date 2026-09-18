import asyncio, html as htmlmod, json, math, re, sqlite3, time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "https://spesaonline.conad.it"
PAGE = BASE + "/bassi-e-fissi"
LOADER = BASE + "/bassi-e-fissi/_jcr_content/root/search.loader.html?page={page}"
STORE_CODE = "010548"
STORE_NAME = "Conad Superstore Capodrise"
STORE_ADDRESS = "Via Retella ex Giard. del Sole, SNC, 81020 Capodrise (CE)"
SOURCE_SCOPE = "CONAD_BASSI_E_FISSI_UFFICIALE"

PRODUCT_RE = re.compile(r'data-product="([^"]+)"')
TOTAL_RE = re.compile(r'<b class="results">\s*([\d.]+)\s+risultat(?:o|i)', re.I)

def parse_products(body):
    out = {}
    for raw in PRODUCT_RE.findall(body):
        try:
            p = json.loads(htmlmod.unescape(raw))
        except Exception:
            continue
        code = str(p.get("code") or "").strip()
        name = str(p.get("nome") or "").strip()
        try:
            price = float(p.get("basePrice") or 0)
        except Exception:
            price = 0
        if code and name and price > 0:
            out[code] = p
    return out

def parse_total(body):
    m = TOTAL_RE.search(body)
    return int(m.group(1).replace(".", "")) if m else None

def unit_price(p):
    try:
        q = float(p.get("netQuantity"))
        price = float(p.get("basePrice"))
    except Exception:
        return None, None
    unit = str(p.get("netQuantityUm") or "").upper()
    if q <= 0:
        return None, None
    if unit == "KG":
        return round(price / q, 4), "EUR/KG"
    if unit in ("L", "LT"):
        return round(price / q, 4), "EUR/L"
    return None, None

async def dismiss_cookie(page):
    for sel in ("#onetrust-reject-all-handler", "#onetrust-accept-btn-handler"):
        try:
            loc = page.locator(sel)
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click(force=True, timeout=3000)
                await page.wait_for_timeout(400)
                return
        except Exception:
            pass

async def fetch_page(request, url, referer, retries=6):
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = await request.get(url, headers={
                "accept": "text/html, */*;q=0.8",
                "accept-language": "it-IT,it;q=0.9",
                "referer": referer,
            }, timeout=30000)
            body = await r.text()
            if r.ok:
                return body
            last = f"HTTP {r.status}"
            if r.status == 429:
                ra = r.headers.get("retry-after")
                try:
                    wait = max(20, int(ra)) if ra else min(120, 20 * attempt)
                except Exception:
                    wait = min(120, 20 * attempt)
                print(f"HTTP 429 su {url}: attendo {wait}s (tentativo {attempt}/{retries})", flush=True)
                await asyncio.sleep(wait)
                continue
        except Exception as e:
            last = repr(e)
        await asyncio.sleep(min(12, attempt * 3))
    raise RuntimeError(f"Download fallito: {url} - {last}")

def init_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
    DROP TABLE IF EXISTS products_current;
    DROP TABLE IF EXISTS price_history;
    DROP TABLE IF EXISTS update_log;
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
      bassi_fissi INTEGER NOT NULL DEFAULT 1,
      image_url TEXT,
      source_queries TEXT,
      checked_at TEXT NOT NULL,
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

def save_db(products, declared_total, card_count, pages, path):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    con = init_db(path)
    for code, p in products.items():
        up, upu = unit_price(p)
        row = (
            "Conad", STORE_CODE, STORE_NAME, STORE_ADDRESS,
            code, p.get("nome"), p.get("marchio"),
            p.get("categoriaPrimoLivello"), p.get("categoriaSecondoLivello"), p.get("categoriaTerzoLivello"),
            p.get("netQuantity"), p.get("netQuantityUm"),
            float(p.get("basePrice")), up, upu, 1,
            p.get("defaultImgSrc"), SOURCE_SCOPE, now
        )
        con.execute("INSERT INTO products_current VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        con.execute(
            "INSERT INTO price_history(store_code,product_code,price_eur,unit_price,checked_at) VALUES(?,?,?,?,?)",
            (STORE_CODE, code, float(p.get("basePrice")), up, now)
        )
    meta = {
        "supermarket": "Conad",
        "store_code": STORE_CODE,
        "store_name": STORE_NAME,
        "store_address": STORE_ADDRESS,
        "source_scope": SOURCE_SCOPE,
        "declared_total": str(declared_total),
        "card_count": str(card_count),
        "unique_products": str(len(products)),
        "pages": str(pages),
        "checked_at": now,
    }
    con.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", meta.items())
    con.execute(
        "INSERT INTO update_log(checked_at,store_code,query,declared_total,saved_count,status,message) VALUES(?,?,?,?,?,?,?)",
        (now, STORE_CODE, "bassi-e-fissi", declared_total, len(products), "OK",
         f"Fonte ufficiale Conad Bassi e Fissi; {card_count}/{declared_total} card ricevute su {pages} pagine; prezzi positivi.")
    )
    con.commit()
    con.close()

async def main():
    out = {"status": "START"}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="it-IT")
        page = await ctx.new_page()
        try:
            await page.goto(PAGE, wait_until="domcontentloaded", timeout=90000)
            await dismiss_cookie(page)
            await page.wait_for_timeout(700)
            body = await page.content()
            declared = parse_total(body)
            first = parse_products(body)
            if declared is None or declared <= 0:
                raise RuntimeError("Totale Bassi e Fissi non trovato.")
            if not first:
                raise RuntimeError("Pagina 1 senza prodotti con prezzo positivo.")
            pages = math.ceil(declared / 30)
            merged = dict(first)
            card_count = len(first)
            page_counts = {1: len(first)}
            seen = {code: [1] for code in first}

            for n in range(2, pages + 1):
                body_n = await fetch_page(ctx.request, LOADER.format(page=n), PAGE + f"?page={n}")
                pp = parse_products(body_n)
                if not pp:
                    raise RuntimeError(f"Pagina {n}/{pages} senza prodotti.")
                page_counts[n] = len(pp)
                card_count += len(pp)
                for code in pp:
                    seen.setdefault(code, []).append(n)
                merged.update(pp)
                # Ritmo prudente: il sito consente la paginazione pubblica ma applica rate limiting.
                await asyncio.sleep(4)

            if card_count != declared:
                raise RuntimeError(f"Completezza fallita: dichiarati {declared}, ricevute {card_count} card.")
            if any(float(p.get("basePrice") or 0) <= 0 for p in merged.values()):
                raise RuntimeError("Sono presenti prezzi non positivi.")

            save_db(merged, declared, card_count, pages, "prezzi_conad_capodrise.db")
            duplicates = {c: ps for c, ps in seen.items() if len(ps) > 1}
            out = {
                "status": "OK",
                "store_code": STORE_CODE,
                "source_scope": SOURCE_SCOPE,
                "declared_total": declared,
                "card_count": card_count,
                "unique_products": len(merged),
                "duplicates": len(duplicates),
                "pages": pages,
                "page_counts": page_counts,
                "min_price": min(float(p["basePrice"]) for p in merged.values()),
                "max_price": max(float(p["basePrice"]) for p in merged.values()),
            }
            Path("conad_bassifissi_audit.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(out, ensure_ascii=False))
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
