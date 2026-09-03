import argparse, asyncio, html as htmlmod, json, re, sqlite3, time
from pathlib import Path
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

EXPECTED_STORE="010548"
STORE_TEXT="VIA RETELLA EX GIARD.DEL SOLE"
ADDRESS_QUERY="81020 CAPODRISE"

PRODUCT_RE=re.compile(r'data-product="([^"]+)"')
TOTAL_RE=re.compile(r'<b class="results">\s*([\d.]+)\s+risultati')
STORE_RE=re.compile(r'var pointOfService = (\{.*?\});\s*var user', re.S)
PAGE_RE=re.compile(r'data-page="(\d+)"')

DEFAULT_QUERIES=[
    "pasta","riso","pane","farina","latte","uova","formaggio","mozzarella","provola",
    "pomodoro","passata","pelati","olio","sale","pepe","aglio","cipolla","patate",
    "zucchine","melanzane","peperoni","scarola","friarielli","piselli","fagioli",
    "ceci","carne","manzo","pollo","salsiccia","prosciutto","salame","tonno",
    "alici","baccalà","cozze","polpo","pangrattato","capperi","olive","basilico",
    "prezzemolo","origano","lievito","burro"
]

def parse_products(body):
    out={}
    for raw in PRODUCT_RE.findall(body):
        try:
            p=json.loads(htmlmod.unescape(raw))
        except Exception:
            continue
        if p.get("code") and p.get("nome") and p.get("basePrice") is not None:
            out[str(p["code"])]=p
    return out

def parse_total(body):
    m=TOTAL_RE.search(body)
    return int(m.group(1).replace(".","")) if m else None

def parse_store(body):
    m=STORE_RE.search(body)
    return json.loads(m.group(1)) if m else None

def parse_last_page(body):
    vals=[int(x) for x in PAGE_RE.findall(body)]
    return max(vals) if vals else 1

def unit_price(p):
    try:
        q=float(p.get("netQuantity"))
        price=float(p.get("basePrice"))
    except Exception:
        return None,None
    u=(p.get("netQuantityUm") or "").upper()
    if q<=0: return None,None
    if u=="KG": return round(price/q,4),"EUR/KG"
    if u in ("L","LT"): return round(price/q,4),"EUR/L"
    return None,None

async def click_text_any(page, patterns, timeout=2500):
    for pat in patterns:
        for locator in (
            page.get_by_role("button", name=re.compile(pat,re.I)),
            page.get_by_role("link", name=re.compile(pat,re.I)),
            page.get_by_text(re.compile(pat,re.I), exact=False),
        ):
            try:
                if await locator.count():
                    await locator.first.click(timeout=timeout)
                    return True
            except Exception:
                pass
    return False

async def accept_cookie_if_present(page):
    await click_text_any(page, [r"accetta tutto",r"accetta",r"consenti tutto"], timeout=1500)

async def fill_address(page):
    # Several fallbacks because Conad changes placeholder/labels over time.
    candidates=[
        page.get_by_placeholder(re.compile(r"indirizzo|cap|dove",re.I)),
        page.get_by_label(re.compile(r"indirizzo|cap|dove",re.I)),
        page.locator('input[type="text"]'),
        page.locator('input[type="search"]'),
    ]
    target=None
    for loc in candidates:
        try:
            n=await loc.count()
            for i in range(n):
                el=loc.nth(i)
                if await el.is_visible():
                    target=el; break
            if target: break
        except Exception:
            pass
    if not target:
        raise RuntimeError("Campo indirizzo non trovato.")
    await target.fill(ADDRESS_QUERY)
    await page.wait_for_timeout(1800)

    # Prefer autocomplete suggestion containing CAPODRISE.
    try:
        cand=page.get_by_text(re.compile(r"CAPODRISE",re.I), exact=False)
        if await cand.count():
            await cand.last.click(timeout=3000)
            return
    except Exception:
        pass
    await target.press("Enter")

async def select_store(page):
    # Enter onboarding if page landed in generic catalog.
    await click_text_any(page,[r"inizia.*spesa",r"seleziona.*servizio",r"modifica"],timeout=2500)
    await page.wait_for_timeout(800)

    # Address first when requested.
    body=(await page.locator("body").inner_text()).lower()
    if "indirizzo" in body or "cap" in body or "dove vuoi" in body:
        try:
            await fill_address(page)
            await page.wait_for_timeout(1600)
        except Exception:
            # Continue: some sessions ask service before address.
            pass

    # Choose pickup / order-and-collect.
    await click_text_any(page,[r"ritiro",r"ordina e ritira",r"ritira la tua spesa"],timeout=3500)
    await page.wait_for_timeout(1800)

    # If address is now requested, fill it.
    body=(await page.locator("body").inner_text()).lower()
    if "capodrise" not in body and ("indirizzo" in body or "cap" in body):
        await fill_address(page)
        await page.wait_for_timeout(1800)

    # Select exact store by address text.
    store_loc=page.get_by_text(re.compile(r"VIA RETELLA.*GIARD",re.I), exact=False)
    if await store_loc.count():
        await store_loc.first.click(timeout=4000)
    else:
        # Search broader visible text and click a nearby button.
        card=page.locator("body").filter(has_text=re.compile(r"VIA RETELLA.*GIARD",re.I))
        if not await card.count():
            raise RuntimeError("Punto vendita Via Retella non trovato nella selezione negozio.")
        await click_text_any(page,[r"seleziona",r"scegli",r"ritiro"],timeout=2500)

    await page.wait_for_timeout(2500)

async def verify_store(page):
    body=await page.content()
    s=parse_store(body)
    if not s:
        # Navigate to a search page where the global store object is normally present.
        await page.goto("https://spesaonline.conad.it/search?query=latte",wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        body=await page.content()
        s=parse_store(body)
    if not s or str(s.get("name"))!=EXPECTED_STORE:
        got=None if not s else s.get("name")
        raise RuntimeError(f"Store non verificato. Atteso {EXPECTED_STORE}, ottenuto {got}.")
    return s

async def harvest_query(page, query):
    url=f"https://spesaonline.conad.it/search?query={quote_plus(query)}"
    await page.goto(url,wait_until="domcontentloaded")
    await page.wait_for_timeout(1000)
    body=await page.content()
    s=parse_store(body)
    if not s or str(s.get("name"))!=EXPECTED_STORE:
        raise RuntimeError(f"Sessione persa durante query {query}: store non è {EXPECTED_STORE}.")
    total=parse_total(body)
    last=parse_last_page(body)
    allp=parse_products(body)

    if total is not None and len(allp)>=total:
        return total,allp

    # Use the site's own pagination clicks, not guessed private parameters.
    for pageno in range(2,last+1):
        link=page.locator(f'a[data-page="{pageno}"]')
        if not await link.count():
            raise RuntimeError(f"Link pagina {pageno} non trovato per query {query}.")
        before=set(allp)
        await link.first.click()
        await page.wait_for_timeout(1100)
        body=await page.content()
        pp=parse_products(body)
        if not pp:
            raise RuntimeError(f"Nessun prodotto alla pagina {pageno} per query {query}.")
        allp.update(pp)
        if set(allp)==before:
            raise RuntimeError(f"Pagina {pageno} non ha prodotto nuovi articoli per query {query}.")

    if total is not None and len(allp)!=total:
        raise RuntimeError(f"Completezza fallita per {query}: dichiarati {total}, raccolti {len(allp)}.")
    return total,allp

def save_db(store, products_by_query, path):
    now=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
    con=sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS products_current(
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
      PRIMARY KEY(store_code,product_code)
    );
    CREATE TABLE IF NOT EXISTS price_history(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      store_code TEXT NOT NULL,
      product_code TEXT NOT NULL,
      price_eur REAL NOT NULL,
      unit_price REAL,
      checked_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS update_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      checked_at TEXT NOT NULL,
      store_code TEXT,
      query TEXT,
      declared_total INTEGER,
      saved_count INTEGER,
      status TEXT,
      message TEXT
    );
    """)

    merged={}
    sources={}
    for query,(total,pp) in products_by_query.items():
        for code,p in pp.items():
            merged[code]=p
            sources.setdefault(code,set()).add(query)
        con.execute("INSERT INTO update_log(checked_at,store_code,query,declared_total,saved_count,status,message) VALUES(?,?,?,?,?,?,?)",
                    (now,EXPECTED_STORE,query,total,len(pp),"OK","browser auto-session; completezza query verificata"))

    addr=(store.get("address") or {}).get("formattedAddress")
    for code,p in merged.items():
        up,upu=unit_price(p)
        row=("Conad",EXPECTED_STORE,store.get("storeType"),addr,code,p["nome"],p.get("marchio"),
             p.get("categoriaPrimoLivello"),p.get("categoriaSecondoLivello"),p.get("categoriaTerzoLivello"),
             p.get("netQuantity"),p.get("netQuantityUm"),float(p["basePrice"]),up,upu,
             int(bool(p.get("bassiFissi"))),p.get("defaultImgSrc"),",".join(sorted(sources[code])),now)
        con.execute("INSERT OR REPLACE INTO products_current VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",row)
        con.execute("INSERT INTO price_history(store_code,product_code,price_eur,unit_price,checked_at) VALUES(?,?,?,?,?)",
                    (EXPECTED_STORE,code,float(p["basePrice"]),up,now))
    con.commit()
    con.close()
    return len(merged)

async def run(args):
    diag=Path(args.diagnostics)
    diag.mkdir(parents=True,exist_ok=True)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=not args.headful)
        ctx=await browser.new_context(locale="it-IT")
        page=await ctx.new_page()
        try:
            await page.goto("https://spesaonline.conad.it/entry",wait_until="domcontentloaded",timeout=60000)
            await accept_cookie_if_present(page)
            await select_store(page)
            store=await verify_store(page)

            queries=args.query or DEFAULT_QUERIES
            results={}
            for q in queries:
                total,pp=await harvest_query(page,q)
                results[q]=(total,pp)
                print(f"{q}: {len(pp)} / {total if total is not None else '?'}")
            saved=save_db(store,results,args.db)
            print(json.dumps({"store":EXPECTED_STORE,"queries":len(results),"unique_products":saved,"status":"OK"},ensure_ascii=False))
        except Exception as e:
            try:
                await page.screenshot(path=str(diag/"failure.png"),full_page=True)
                (diag/"failure.html").write_text(await page.content(),encoding="utf-8")
                (diag/"failure.txt").write_text(str(e),encoding="utf-8")
            except Exception:
                pass
            raise
        finally:
            await browser.close()

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",default="prezzi_conad.db")
    ap.add_argument("--query",action="append",help="Ripetibile. Se omesso usa il set ingredienti predefinito.")
    ap.add_argument("--headful",action="store_true")
    ap.add_argument("--diagnostics",default="diagnostics")
    args=ap.parse_args()
    asyncio.run(run(args))
