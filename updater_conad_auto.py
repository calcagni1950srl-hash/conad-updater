import argparse, asyncio, html as htmlmod, json, math, re, sqlite3, time
from pathlib import Path
from urllib.parse import quote_plus
from playwright.async_api import async_playwright

BASE="https://spesaonline.conad.it"
SEARCH=BASE+"/search?query={query}"
LOADER=BASE+"/search/_jcr_content/root/search.loader.html?query={query}&page={page}"
GENERIC_STORE_CODE="CONAD-GENERICO"

PRODUCT_RE=re.compile(r'data-product="([^"]+)"')
TOTAL_RE=re.compile(r'<b class="results">\s*([\d.]+)\s+risultati')

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

def unit_price(p):
    try:
        q=float(p.get("netQuantity")); price=float(p.get("basePrice"))
    except Exception:
        return None,None
    u=(p.get("netQuantityUm") or "").upper()
    if q<=0: return None,None
    if u=="KG": return round(price/q,4),"EUR/KG"
    if u in ("L","LT"): return round(price/q,4),"EUR/L"
    return None,None

async def accept_cookie_if_present(page):
    b=page.locator("#onetrust-accept-btn-handler")
    try:
        if await b.count() and await b.first.is_visible():
            await b.first.click(force=True,timeout=3000)
            await page.wait_for_timeout(500)
    except Exception:
        pass

async def fetch_text(request, url, referer, retries=3):
    headers={
        "accept":"application/json, text/plain, */*",
        "accept-language":"it-IT",
        "referer":referer,
    }
    last=None
    for attempt in range(1,retries+1):
        try:
            r=await request.get(url,headers=headers,timeout=30000)
            body=await r.text()
            if r.ok:
                return body
            last=f"HTTP {r.status}"
        except Exception as e:
            last=str(e)
        await asyncio.sleep(attempt)
    raise RuntimeError(f"Richiesta fallita dopo {retries} tentativi: {url} — {last}")

async def harvest_query(page, request, query):
    q=quote_plus(query)

    # Solo la prima pagina viene aperta normalmente: serve a inizializzare
    # la sessione e a leggere il totale dichiarato da Conad.
    await page.goto(SEARCH.format(query=q),wait_until="domcontentloaded",timeout=60000)
    await accept_cookie_if_present(page)
    await page.wait_for_timeout(800)
    body=await page.content()

    total=parse_total(body)
    first=parse_products(body)
    if total is None:
        raise RuntimeError(f"Totale risultati non trovato per query {query}.")
    if not first and total:
        raise RuntimeError(f"Pagina 1 senza prodotti per query {query}.")

    allp=dict(first)
    pages=max(1,math.ceil(total/40))

    # Dal test reale del 04/09/2026 l'endpoint loader accetta direttamente
    # query + page e restituisce HTTP 200 con le card prodotto. Nessun click.
    for pageno in range(2,pages+1):
        url=LOADER.format(query=q,page=pageno)
        referer=SEARCH.format(query=q)+f"&page={pageno}"
        body=await fetch_text(request,url,referer)
        pp=parse_products(body)
        if not pp:
            raise RuntimeError(f"Endpoint Conad: pagina {pageno}/{pages} vuota per {query}.")
        before=len(allp)
        allp.update(pp)
        if len(allp)==before:
            raise RuntimeError(f"Endpoint Conad: pagina {pageno}/{pages} duplicata per {query}.")

    # Fail-closed: non salva un catalogo incompleto.
    if len(allp)!=total:
        raise RuntimeError(
            f"Completezza fallita per {query}: Conad dichiara {total}, raccolti {len(allp)}."
        )
    return total,allp

def save_db(results,path):
    now=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
    con=sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS products_current(
      supermarket TEXT NOT NULL, store_code TEXT NOT NULL, store_name TEXT,
      store_address TEXT, product_code TEXT NOT NULL, product_name TEXT NOT NULL,
      brand TEXT, category1 TEXT, category2 TEXT, category3 TEXT,
      quantity_value REAL, quantity_unit TEXT, price_eur REAL NOT NULL,
      unit_price REAL, unit_price_unit TEXT, bassi_fissi INTEGER NOT NULL DEFAULT 0,
      image_url TEXT, source_queries TEXT, checked_at TEXT NOT NULL,
      PRIMARY KEY(store_code,product_code)
    );
    CREATE TABLE IF NOT EXISTS price_history(
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_code TEXT NOT NULL,
      product_code TEXT NOT NULL, price_eur REAL NOT NULL, unit_price REAL,
      checked_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS update_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT NOT NULL,
      store_code TEXT, query TEXT, declared_total INTEGER, saved_count INTEGER,
      status TEXT, message TEXT
    );
    """)
    merged={}; sources={}
    for query,(total,pp) in results.items():
        for code,p in pp.items():
            merged[code]=p; sources.setdefault(code,set()).add(query)
        con.execute("""INSERT INTO update_log
          (checked_at,store_code,query,declared_total,saved_count,status,message)
          VALUES(?,?,?,?,?,?,?)""",
          (now,GENERIC_STORE_CODE,query,total,len(pp),"OK",
           "Catalogo Conad generico via endpoint ufficiale del sito; affidabilità B"))
    con.execute("DELETE FROM products_current WHERE store_code=?",(GENERIC_STORE_CODE,))
    for code,p in merged.items():
        up,upu=unit_price(p)
        row=("Conad",GENERIC_STORE_CODE,"Catalogo Conad generico",None,code,p["nome"],
             p.get("marchio"),p.get("categoriaPrimoLivello"),p.get("categoriaSecondoLivello"),
             p.get("categoriaTerzoLivello"),p.get("netQuantity"),p.get("netQuantityUm"),
             float(p["basePrice"]),up,upu,int(bool(p.get("bassiFissi"))),
             p.get("defaultImgSrc"),",".join(sorted(sources[code])),now)
        con.execute("INSERT OR REPLACE INTO products_current VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",row)
        con.execute("""INSERT INTO price_history
          (store_code,product_code,price_eur,unit_price,checked_at) VALUES(?,?,?,?,?)""",
          (GENERIC_STORE_CODE,code,float(p["basePrice"]),up,now))
    con.commit(); con.close()
    return len(merged)

async def run(args):
    diag=Path(args.diagnostics); diag.mkdir(parents=True,exist_ok=True)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=not args.headful)
        ctx=await browser.new_context(locale="it-IT")
        page=await ctx.new_page()
        try:
            queries=args.query or DEFAULT_QUERIES
            results={}
            for q in queries:
                total,pp=await harvest_query(page,ctx.request,q)
                results[q]=(total,pp)
                print(f"{q}: {len(pp)} / {total}")
            saved=save_db(results,args.db)
            print(json.dumps({
              "store":GENERIC_STORE_CODE,"mode":"generic-direct-endpoint",
              "queries":len(results),"unique_products":saved,
              "reliability":"B","status":"OK"
            },ensure_ascii=False))
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
    ap.add_argument("--query",action="append")
    ap.add_argument("--headful",action="store_true")
    ap.add_argument("--diagnostics",default="diagnostics")
    asyncio.run(run(ap.parse_args()))
