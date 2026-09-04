import argparse, asyncio, html as htmlmod, json, re, sqlite3, time
from pathlib import Path
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

EXPECTED_STORE="010548"  # legacy: usato solo dai test/store-specific helpers
GENERIC_STORE_CODE="CONAD-GENERICO"
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
    Accetta il consenso tramite il normale pulsante OneTrust.
    La diagnostica V10 mostra che #onetrust-consent-sdk può non risultare
    'visible' a Playwright anche quando il banner figlio è visibile.
    """
    accept = page.locator("#onetrust-accept-btn-handler")
    banner = page.locator("#onetrust-banner-sdk")

    for _ in range(3):
        try:
            button_visible = await accept.count() and await accept.first.is_visible()
        except Exception:
            button_visible = False
        try:
            banner_visible = await banner.count() and await banner.first.is_visible()
        except Exception:
            banner_visible = False

        if not button_visible and not banner_visible:
            return True

        if await accept.count():
            try:
                await accept.first.click(force=True, timeout=3000)
            except Exception:
                try:
                    await page.evaluate("""
                        () => document.querySelector('#onetrust-accept-btn-handler')?.click()
                    """)
                except Exception:
                    pass

        await page.wait_for_timeout(800)

    # Final check on the actual elements that can block pointer events.
    blocked = await page.evaluate("""
        () => {
            const selectors = [
                '#onetrust-banner-sdk',
                '#onetrust-pc-sdk',
                '.onetrust-pc-dark-filter'
            ];
            return selectors.some(sel => {
                const el=document.querySelector(sel);
                if (!el) return false;
                const st=getComputedStyle(el), r=el.getBoundingClientRect();
                return st.display !== 'none' &&
                       st.visibility !== 'hidden' &&
                       st.pointerEvents !== 'none' &&
                       r.width > 0 && r.height > 0;
            });
        }
    """)
    if blocked:
        raise RuntimeError("Banner cookie OneTrust ancora visibile dopo ACCETTA TUTTI I COOKIE.")
    return True

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

    # OneTrust può comparire dopo autocomplete.
    await accept_cookie_if_present(page)

    # La diagnostica V10 mostra che, in alcuni casi, Conad ha già aperto
    # "Come vuoi fare la spesa?" (step 1 di 2) mentre il click Verifica
    # risulta ancora in attesa. Se lo step servizio è già visibile, non
    # clicchiamo nuovamente Verifica.
    service_step = page.locator("body").filter(
        has_text=re.compile(r"Come vuoi fare la spesa", re.I)
    )
    pickup_now = page.locator(
        'button[onclick*="GoogleUtils.loadStores"][onclick*="ORDER_AND_COLLECT"]'
    )
    already_advanced = False
    try:
        already_advanced = (
            (await pickup_now.count() and await pickup_now.first.is_visible())
        )
    except Exception:
        already_advanced = False

    if not already_advanced:
        await verify.first.click(timeout=5000, force=True)
        await page.wait_for_timeout(2500)


async def write_store_stage(page, name, diagnostics="diagnostics"):
    try:
        d=Path(diagnostics)
        d.mkdir(parents=True,exist_ok=True)
        state=await page.evaluate("""
            () => {
                const card=document.querySelector('.component-card-negozio[data-pos-id="010548"]');
                const confirm=[...document.querySelectorAll('.btn-conferma-pdv button')]
                    .find(x => {
                        const s=getComputedStyle(x), r=x.getBoundingClientRect();
                        return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0;
                    });
                return {
                    href: location.href,
                    pointOfService: window.pointOfService || null,
                    typeOfService: window.typeOfService || null,
                    selectedAddress: window.selectedAddress || null,
                    cardClass: card ? card.className : null,
                    cardOnclick: card ? card.getAttribute('onclick') : null,
                    confirmVisible: !!confirm,
                    localStorageKeys: Object.keys(localStorage),
                    sessionStorageKeys: Object.keys(sessionStorage)
                };
            }
        """)
        cookies=await page.context.cookies()
        state["cookieNames"]=sorted({c.get("name","") for c in cookies})
        (d/f"{name}.json").write_text(
            json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8"
        )
        await page.screenshot(path=str(d/f"{name}.png"),full_page=True)
    except Exception:
        pass

async def select_store(page):
    await accept_cookie_if_present(page)
    await fill_address(page)
    await accept_cookie_if_present(page)

    pickup=page.locator(
        'button[onclick*="GoogleUtils.loadStores"][onclick*="ORDER_AND_COLLECT"]'
    )
    visible_pickup=None
    for i in range(await pickup.count()):
        try:
            if await pickup.nth(i).is_visible():
                visible_pickup=pickup.nth(i)
                break
        except Exception:
            pass
    if visible_pickup is None:
        raise RuntimeError("Pulsante visibile ORDER_AND_COLLECT non trovato.")

    await visible_pickup.click(timeout=5000,force=True)

    target=page.locator('.component-card-negozio[data-pos-id="010548"]')
    await target.first.wait_for(state="visible",timeout=18000)
    await write_store_stage(page,"stage_1_store_list")

    # Execute the exact two functions declared by Conad in the card onclick.
    # This avoids any ambiguity caused by overlays/scrolling while still using
    # the site's own normal selection logic.
    result=await page.evaluate("""
        () => {
            const el=document.querySelector('.component-card-negozio[data-pos-id="010548"]');
            if (!el) return {ok:false,reason:'card missing'};
            if (!window.OnboardingManager || !window.GoogleUtils)
                return {ok:false,reason:'Conad JS managers missing'};
            try {
                OnboardingManager.confirmStore(el);
                GoogleUtils.clickStoreToList(el,0,'ORDER_AND_COLLECT');
                return {ok:true};
            } catch(e) {
                return {ok:false,reason:String(e)};
            }
        }
    """)
    if not result.get("ok"):
        raise RuntimeError(f"Selezione store 010548 fallita: {result.get('reason')}")

    await page.wait_for_timeout(1200)
    await write_store_stage(page,"stage_2_store_selected")

    confirm=page.locator(".btn-conferma-pdv button")
    visible_confirm=None
    for i in range(await confirm.count()):
        try:
            if await confirm.nth(i).is_visible():
                visible_confirm=confirm.nth(i)
                break
        except Exception:
            pass
    if visible_confirm is None:
        raise RuntimeError("Pulsante visibile 'Conferma il negozio' non trovato.")

    await accept_cookie_if_present(page)

    # Normal click on the actual visible confirmation control.
    await visible_confirm.click(timeout=5000,force=True)
    await page.wait_for_timeout(2500)
    await write_store_stage(page,"stage_3_after_confirm")

    # Give Conad's asynchronous persistence request enough time to complete.
    for _ in range(12):
        body=await page.content()
        st=parse_store(body)
        if st and str(st.get("name"))==EXPECTED_STORE:
            return
        try:
            live=await page.evaluate("""
                () => window.pointOfService ? String(window.pointOfService.name || '') : ''
            """)
            if live==EXPECTED_STORE:
                return
        except Exception:
            pass
        await page.wait_for_timeout(750)

    await write_store_stage(page,"stage_4_before_verify")

async def verify_store(page):
    body=await page.content()
    st=parse_store(body)
    if st and str(st.get("name"))==EXPECTED_STORE:
        return st

    await write_store_stage(page,"stage_5_verify_before_reload")

    await page.goto(
        "https://spesaonline.conad.it/search?query=latte",
        wait_until="domcontentloaded"
    )
    await page.wait_for_timeout(1800)
    body=await page.content()
    st=parse_store(body)

    if not st or str(st.get("name"))!=EXPECTED_STORE:
        got=None if not st else st.get("name")
        total=parse_total(body)
        await write_store_stage(page,"stage_6_verify_failed")
        raise RuntimeError(
            f"Store non verificato. Atteso {EXPECTED_STORE}, ottenuto {got}; "
            f"risultati latte={total}. Diagnostica V15 salvata per ogni fase."
        )
    return st

async def harvest_query(page, query):
    """Raccoglie il catalogo Conad generico senza selezione punto vendita."""
    url=f"https://spesaonline.conad.it/search?query={quote_plus(query)}"
    await page.goto(url,wait_until="domcontentloaded",timeout=60000)
    await accept_cookie_if_present(page)
    await page.wait_for_timeout(1000)
    body=await page.content()

    total=parse_total(body)
    last=parse_last_page(body)
    allp=parse_products(body)

    if not allp:
        raise RuntimeError(f"Nessun prodotto trovato per query {query} nel catalogo Conad generico.")

    if total is not None and len(allp)>=total:
        return total,allp

    # Paginazione ufficiale del sito.
    # Diagnostica V17: il browser era fermo realmente a pagina 3 quando il
    # codice pensava di aver aperto pagina 4. Da ora non consideriamo mai
    # riuscito un click finché la paginazione non marca la pagina richiesta
    # come `uk-active`.
    for pageno in range(2,last+1):
        before=set(allp)
        opened=False

        for attempt in range(1,4):
            await accept_cookie_if_present(page)

            # Da pagina N-1 usiamo il vero controllo "Pagina Successiva":
            # è univoco, mentre data-page=N può comparire sia sul numero sia
            # sulla freccia e in passato ha prodotto click ambigui.
            next_link=page.locator(
                'a[aria-label="Pagina Successiva"]'
            )

            if not await next_link.count():
                # Fallback al numero pagina esplicito.
                next_link=page.locator(
                    f'a[title="Pagina {pageno}"][data-page="{pageno}"]'
                )

            if not await next_link.count():
                raise RuntimeError(
                    f"Controllo paginazione verso pagina {pageno} non trovato "
                    f"per query {query}."
                )

            try:
                await next_link.first.scroll_into_view_if_needed()
            except Exception:
                pass

            try:
                await next_link.first.click(timeout=5000, force=True)
            except Exception:
                try:
                    await next_link.first.evaluate("(el) => el.click()")
                except Exception:
                    pass

            # Verifica REALE del cambio pagina.
            try:
                await page.wait_for_function(
                    """(n) => {
                        const a=document.querySelector(
                            `.component-Pagination li.uk-active a[data-page="${n}"]`
                        );
                        return !!a;
                    }""",
                    pageno,
                    timeout=7000
                )
                opened=True
                break
            except Exception:
                await page.wait_for_timeout(700)

        if not opened:
            active=await page.evaluate("""
                () => {
                    const a=document.querySelector(
                        '.component-Pagination li.uk-active a[data-page]'
                    );
                    return a ? a.getAttribute('data-page') : null;
                }
            """)
            raise RuntimeError(
                f"Pagina {pageno} non aperta realmente per query {query}; "
                f"pagina attiva rimasta {active}."
            )

        # Ora che la UI certifica la pagina giusta, aspettiamo il contenuto.
        await page.wait_for_timeout(1000)
        await accept_cookie_if_present(page)

        body=await page.content()
        pp=parse_products(body)

        if not pp:
            raise RuntimeError(
                f"Nessun prodotto alla pagina {pageno} per query {query}."
            )

        previous_count=len(allp)
        allp.update(pp)

        if len(allp)==previous_count:
            # Un ultimo retry di lettura: su pagina 4 Conad può aggiornare la
            # paginazione prima delle card prodotto.
            await page.wait_for_timeout(1800)
            body=await page.content()
            pp=parse_products(body)
            allp.update(pp)

        if len(allp)==previous_count:
            active=await page.evaluate("""
                () => {
                    const a=document.querySelector(
                        '.component-Pagination li.uk-active a[data-page]'
                    );
                    return a ? a.getAttribute('data-page') : null;
                }
            """)
            raise RuntimeError(
                f"Pagina {pageno} è attiva ({active}) ma non contiene nuovi "
                f"articoli per query {query}."
            )

    if total is not None and len(allp)!=total:
        raise RuntimeError(
            f"Completezza fallita per {query}: dichiarati {total}, raccolti {len(allp)}."
        )
    return total,allp

def save_db(products_by_query, path):
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
        con.execute(
            "INSERT INTO update_log(checked_at,store_code,query,declared_total,saved_count,status,message) VALUES(?,?,?,?,?,?,?)",
            (now,GENERIC_STORE_CODE,query,total,len(pp),"OK",
             "Catalogo Conad generico; prezzo indicativo, promozioni/disponibilità possono variare per punto vendita")
        )

    # Sostituisce lo snapshot corrente generico senza toccare eventuali store specifici.
    con.execute("DELETE FROM products_current WHERE store_code=?",(GENERIC_STORE_CODE,))

    for code,p in merged.items():
        up,upu=unit_price(p)
        row=(
            "Conad",GENERIC_STORE_CODE,"Catalogo Conad generico",None,code,p["nome"],p.get("marchio"),
            p.get("categoriaPrimoLivello"),p.get("categoriaSecondoLivello"),p.get("categoriaTerzoLivello"),
            p.get("netQuantity"),p.get("netQuantityUm"),float(p["basePrice"]),up,upu,
            int(bool(p.get("bassiFissi"))),p.get("defaultImgSrc"),
            ",".join(sorted(sources[code])),now
        )
        con.execute("INSERT OR REPLACE INTO products_current VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",row)
        con.execute(
            "INSERT INTO price_history(store_code,product_code,price_eur,unit_price,checked_at) VALUES(?,?,?,?,?)",
            (GENERIC_STORE_CODE,code,float(p["basePrice"]),up,now)
        )
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
            # Nessun onboarding, indirizzo o punto vendita.
            # Usiamo direttamente il catalogo pubblico/generico Conad.
            queries=args.query or DEFAULT_QUERIES
            results={}
            for q in queries:
                total,pp=await harvest_query(page,q)
                results[q]=(total,pp)
                print(f"{q}: {len(pp)} / {total if total is not None else '?'}")

            saved=save_db(results,args.db)
            print(json.dumps({
                "store":GENERIC_STORE_CODE,
                "mode":"generic",
                "queries":len(results),
                "unique_products":saved,
                "reliability":"B",
                "status":"OK"
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
    ap.add_argument("--query",action="append",help="Ripetibile. Se omesso usa il set ingredienti predefinito.")
    ap.add_argument("--headful",action="store_true")
    ap.add_argument("--diagnostics",default="diagnostics")
    args=ap.parse_args()
    asyncio.run(run(args))
