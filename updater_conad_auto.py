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
    """
    Chiude il banner OneTrust in modo deterministico e verifica che l'overlay
    non intercetti più i click. Nessun bypass: viene premuto il normale
    pulsante "Accetta tutti i cookie" della pagina.
    """
    sdk = page.locator("#onetrust-consent-sdk")
    accept = page.locator("#onetrust-accept-btn-handler")

    # OneTrust può comparire con un piccolo ritardo.
    try:
        await page.wait_for_timeout(500)
    except Exception:
        pass

    # Se il banner non è presente/visibile non c'è nulla da fare.
    try:
        visible = await sdk.count() and await sdk.first.is_visible()
    except Exception:
        visible = False

    if not visible:
        return True

    clicked = False

    # 1) Click Playwright forzato sul pulsante ufficiale.
    try:
        if await accept.count():
            await accept.first.click(timeout=5000, force=True)
            clicked = True
    except Exception:
        pass

    # 2) Fallback DOM click sullo stesso pulsante ufficiale.
    if not clicked:
        try:
            clicked = bool(await page.evaluate("""
                () => {
                    const b = document.querySelector('#onetrust-accept-btn-handler');
                    if (!b) return false;
                    b.click();
                    return true;
                }
            """))
        except Exception:
            pass

    # 3) Fallback testuale, sempre sul consenso standard.
    if not clicked:
        clicked = await click_text_any(
            page,
            [r"accetta tutti i cookie", r"accetta tutti", r"consenti tutti"],
            timeout=3000
        )

    # Attende che OneTrust sparisca davvero.
    try:
        await page.wait_for_function("""
            () => {
                const sdk = document.querySelector('#onetrust-consent-sdk');
                if (!sdk) return true;
                const st = getComputedStyle(sdk);
                const rect = sdk.getBoundingClientRect();
                return st.display === 'none' ||
                       st.visibility === 'hidden' ||
                       st.pointerEvents === 'none' ||
                       rect.width === 0 ||
                       rect.height === 0;
            }
        """, timeout=8000)
        return True
    except Exception:
        pass

    # Alcune configurazioni OneTrust lasciano il contenitore nel DOM ma
    # nascondono banner e dark-filter. Controlliamo quindi i due overlay reali.
    try:
        blocked = await page.evaluate("""
            () => {
                const ids = ['onetrust-banner-sdk','onetrust-pc-sdk'];
                for (const id of ids) {
                    const el = document.getElementById(id);
                    if (el) {
                        const st = getComputedStyle(el);
                        const r = el.getBoundingClientRect();
                        if (st.display !== 'none' &&
                            st.visibility !== 'hidden' &&
                            st.pointerEvents !== 'none' &&
                            r.width > 0 && r.height > 0) {
                            return true;
                        }
                    }
                }
                const dark = document.querySelector('.onetrust-pc-dark-filter');
                if (dark) {
                    const st = getComputedStyle(dark);
                    const r = dark.getBoundingClientRect();
                    if (st.display !== 'none' &&
                        st.visibility !== 'hidden' &&
                        st.pointerEvents !== 'none' &&
                        r.width > 0 && r.height > 0) {
                        return true;
                    }
                }
                return false;
            }
        """)
        if not blocked:
            return True
    except Exception:
        pass

    raise RuntimeError("Banner cookie OneTrust ancora attivo dopo il consenso.")

async def fill_address(page):
    # Campo visibile della pagina /entry; onboarding solo come fallback se visibile.
    entry = page.locator("#googleInputEntrypageLine1")
    onboarding = page.locator("#googleInputOnboardingStep0Line1")

    field = None
    mode = None

    if await entry.count() and await entry.first.is_visible():
        field = entry.first
        mode = "entry"
    elif await onboarding.count() and await onboarding.first.is_visible():
        field = onboarding.first
        mode = "onboarding"
    else:
        raise RuntimeError("Nessun campo indirizzo Conad visibile.")

    await field.fill("Via Retella, Capodrise CE")
    await page.wait_for_timeout(2200)

    suggestions = [
        page.locator(".pac-container-custom .pac-item").filter(
            has_text=re.compile(r"Retella|Capodrise", re.I)
        ),
        page.locator(".pac-container .pac-item").filter(
            has_text=re.compile(r"Retella|Capodrise", re.I)
        ),
        page.locator(".pac-item").filter(
            has_text=re.compile(r"Capodrise", re.I)
        ),
    ]

    chosen = False
    for loc in suggestions:
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click(timeout=5000)
                chosen = True
                break
        except Exception:
            pass

    if not chosen:
        # Fallback tastiera per l'autocomplete Google Places.
        try:
            await field.press("ArrowDown")
            await field.press("Enter")
            await page.wait_for_timeout(900)
        except Exception:
            pass

    if mode == "entry":
        # Il pulsante Verifica è nello stesso blocco/form del campo entry.
        form = field.locator("xpath=ancestor::form[1]")
        verify = form.locator("button.submitButton")
        if not await verify.count() or not await verify.first.is_visible():
            # Fallback al primo pulsante submit visibile vicino al campo.
            parent = field.locator("xpath=ancestor::*[self::form or contains(@class,'entry')][1]")
            verify = parent.locator("button[type='submit'], button.submitButton")
    else:
        verify = page.locator("#verificaButton")

    if not await verify.count() or not await verify.first.is_visible():
        raise RuntimeError("Pulsante Verifica indirizzo Conad non visibile.")

    # OneTrust può riapparire dopo il caricamento/autocomplete: chiudilo prima del click.
    await accept_cookie_if_present(page)
    await verify.first.click(timeout=5000)
    await page.wait_for_timeout(2500)

async def select_store(page):
    await accept_cookie_if_present(page)

    # Always establish a valid address through the real onboarding.
    await fill_address(page)
    await accept_cookie_if_present(page)

    # Select Conad's explicit pickup service.
    pickup=page.locator('button[onclick*="GoogleUtils.loadStores"][onclick*="ORDER_AND_COLLECT"]')
    visible_pickup=None
    for i in range(await pickup.count()):
        if await pickup.nth(i).is_visible():
            visible_pickup=pickup.nth(i)
            break
    if visible_pickup is None:
        # Text fallback limited to the Ordina e ritira card.
        card=page.locator("#ordina-e-ritira")
        if await card.count():
            btn=card.locator("button").filter(has_text=re.compile(r"Seleziona",re.I))
            if await btn.count() and await btn.first.is_visible():
                visible_pickup=btn.first
    if visible_pickup is None:
        raise RuntimeError("Pulsante visibile ORDER_AND_COLLECT non trovato dopo verifica indirizzo.")

    await visible_pickup.click(timeout=5000)

    # Wait for real store results.
    try:
        await page.wait_for_function(
            """() => {
              const root=document.querySelector('#ordina-ritira-scelta-pdv');
              if (!root) return false;
              const txt=(root.innerText||'').toLowerCase();
              return txt.includes('retella') || txt.includes('010548') ||
                     root.querySelectorAll('li, .store, .card').length > 0;
            }""",
            timeout=18000
        )
    except Exception:
        pass

    # Find target only inside pickup-store modal/section.
    root=page.locator("#ordina-ritira-scelta-pdv")
    candidates=[
        root.get_by_text(re.compile(r"VIA RETELLA.*GIARD",re.I), exact=False),
        root.get_by_text(re.compile(r"RETELLA",re.I), exact=False),
        root.get_by_text(re.compile(r"010548",re.I), exact=False),
    ]
    target=None
    for loc in candidates:
        try:
            if await loc.count() and await loc.first.is_visible():
                target=loc.first
                break
        except Exception:
            pass
    if target is None:
        txt=""
        try: txt=(await root.inner_text())[:2000]
        except Exception: pass
        raise RuntimeError("Store 010548/Via Retella non presente nella lista ritiro. Lista: "+txt)

    # Click store card or its explicit select/confirm control.
    clicked=False
    for ancestor in ("xpath=ancestor::li[1]","xpath=ancestor::*[contains(@class,'card')][1]"):
        try:
            box=target.locator(ancestor)
            if await box.count():
                controls=box.locator("button, a").filter(has_text=re.compile(r"seleziona|scegli|conferma",re.I))
                if await controls.count() and await controls.first.is_visible():
                    await controls.first.click(timeout=5000); clicked=True; break
        except Exception:
            pass
    if not clicked:
        await target.click(timeout=5000)

    await page.wait_for_timeout(1200)
    # Some Conad flows require a separate confirmation button.
    await click_text_any(page,[r"conferma il negozio",r"conferma"],timeout=2500)
    await page.wait_for_timeout(2500)

async def verify_store(page):
    # Give Conad time to persist the anonymous cart/store session.
    for _ in range(8):
        body=await page.content()
        s=parse_store(body)
        if s and str(s.get("name"))==EXPECTED_STORE:
            return s
        await page.wait_for_timeout(700)

    # Search page should reflect the persisted selected store.
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
