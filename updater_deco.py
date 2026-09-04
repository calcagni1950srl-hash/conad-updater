import asyncio, re, sqlite3, json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

BASE="https://decoacasa.multicedi.it/"
ADDRESS="Via Isonzo 9, 81100 Caserta CE, Italia"
TARGET_STORE="San Nicola La Strada"
DB="prezzi_deco.db"

def euro(s):
    if not s: return None
    m=re.search(r'(\d{1,4}[,.]\d{2})\s*€',s)
    return float(m.group(1).replace(",",".")) if m else None

def init_db():
    con=sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS products(
      product_key TEXT PRIMARY KEY, supermarket TEXT NOT NULL, store TEXT NOT NULL,
      name TEXT NOT NULL, brand TEXT, category TEXT, quantity_text TEXT,
      price_eur REAL NOT NULL, unit_price_text TEXT, promotion_text TEXT,
      product_url TEXT NOT NULL, source TEXT NOT NULL, checked_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS update_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT NOT NULL,
      store TEXT NOT NULL, categories INTEGER NOT NULL, products INTEGER NOT NULL,
      status TEXT NOT NULL, note TEXT
    )""")
    con.commit(); return con

async def choose_store(page):
    await page.goto(BASE,wait_until="domcontentloaded",timeout=60000)
    await page.wait_for_timeout(1800)
    for sel in ['button:has-text("Accetta tutto")','button:has-text("Accetta")','#onetrust-accept-btn-handler']:
        if await page.locator(sel).count():
            try: await page.locator(sel).first.click(timeout=1500); break
            except: pass
    f=page.locator('input[name="addressField1"],#addressField1').first
    await f.fill(ADDRESS); await page.wait_for_timeout(2500)
    pac=page.locator(".pac-item")
    ss=[(await pac.nth(i).inner_text()).strip() for i in range(await pac.count())]
    idx=next(i for i,t in enumerate(ss) if "caserta" in t.lower())
    await f.focus()
    for _ in range(idx+1):
        await page.keyboard.press("ArrowDown"); await page.wait_for_timeout(100)
    await page.keyboard.press("Enter"); await page.wait_for_timeout(4200)

    containers=page.locator("div,li,article,section")
    best=None
    for i in range(await containers.count()):
        try: txt=(await containers.nth(i).inner_text()).strip()
        except: continue
        lo=txt.lower()
        if TARGET_STORE.lower() in lo and "prenota e ritira qui" in lo:
            if best is None or len(txt)<best[0]: best=(len(txt),i)
    if not best: raise RuntimeError("Card San Nicola La Strada non trovata")
    c=containers.nth(best[1])
    await c.get_by_text(re.compile(r"PRENOTA\s+E\s+RITIRA\s+QUI",re.I)).first.click()
    await page.wait_for_timeout(5500)
    if "via-milano-6" not in page.url:
        raise RuntimeError(f"Store inatteso dopo selezione: {page.url}")

async def category_links(page):
    raw=await page.locator("a").evaluate_all(
      """els=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||''})).filter(x=>x.href)"""
    )
    seen=set(); cats=[]
    for x in raw:
        u=x["href"]
        if "/prodotto/" in u: continue
        if not re.search(r'/[^/]+_[0-9]+(?:[?#]|$)',u): continue
        if "via-milano-6" not in u: continue
        clean=u.split("?")[0].split("#")[0]
        if clean in seen: continue
        seen.add(clean); cats.append({"name":x["text"].strip() or clean.rsplit("/",1)[-1],"url":clean})
    return cats

async def visible_load_more(page):
    loc=page.get_by_text(re.compile(r'CARICA\s+ALTRI\s+PRODOTTI',re.I))
    for i in range(await loc.count()):
        el=loc.nth(i)
        try:
            if await el.is_visible():
                return el
        except:
            pass
    return None

async def load_all(page):
    last=-1
    for n in range(1,80):
        urls=await page.locator('a[href*="/prodotto/"]').evaluate_all(
            "els=>[...new Set(els.map(e=>e.href))]"
        )
        cur=len(urls)
        if cur==last:
            raise RuntimeError(f"Nessuna crescita dopo CARICA ALTRI: {page.url}")
        last=cur
        btn=await visible_load_more(page)
        if btn is None:
            return urls
        # Il DOM Decò contiene anche copie nascoste del pulsante.
        # V1 usava .first e poteva scegliere quella invisibile.
        await btn.evaluate("(el)=>el.scrollIntoView({block:'center'})")
        try:
            await btn.click(timeout=8000)
        except Exception:
            # Fallback sullo stesso elemento già verificato come visibile.
            await btn.evaluate("(el)=>el.click()")
        grew=False
        for _ in range(30):
            await page.wait_for_timeout(400)
            now=await page.locator('a[href*="/prodotto/"]').evaluate_all(
                "els=>[...new Set(els.map(e=>e.href))]"
            )
            if len(now)>cur:
                grew=True; break
        if not grew:
            # Un ultimo controllo: se il pulsante è sparito, il batch può essere finale.
            if await visible_load_more(page) is None:
                return now
            raise RuntimeError(f"CARICA ALTRI non ha aggiunto prodotti: {page.url}")
        await page.wait_for_timeout(600)
    raise RuntimeError("Troppi batch: limite di sicurezza raggiunto")

async def parse_visible_cards(page, category):
    # Usa il DOM già validato; ogni URL prodotto è la chiave stabile.
    links=await page.locator('a[href*="/prodotto/"]').evaluate_all(
        """els=>[...new Map(els.map(e=>[e.href,e])).values()].map(e=>e.href)"""
    )
    rows=[]
    for u in links:
        a=page.locator(f'a[href="{u}"]').first
        # risale al contenitore più piccolo che contiene un prezzo
        node=a
        text=""
        for _ in range(7):
            try: text=(await node.inner_text()).strip()
            except: text=""
            if euro(text) is not None and len(text)<2500: break
            node=node.locator("..")
        price=euro(text)
        if price is None: continue
        lines=[x.strip() for x in text.splitlines() if x.strip()]
        name=lines[0] if lines else urlparse(u).path.rsplit("/",1)[-1]
        # Preferisci una riga descrittiva più lunga se la prima è un'etichetta.
        for x in lines:
            if len(x)>len(name) and "€" not in x and not re.fullmatch(r'\d+',x):
                name=x
        qty=None
        qm=re.search(r'(\d+(?:[,.]\d+)?)\s*(kg|lt|l|pz|gr|g|ml)\b',text,re.I)
        if qm: qty=f"{qm.group(1)} {qm.group(2)}"
        up=None
        um=re.search(r'(\d+(?:[,.]\d+)?)\s*€\s*/\s*(kg|l|lt|pz)',text,re.I)
        if um: up=f"{um.group(1)} €/{um.group(2)}"
        promo="\n".join(x for x in lines if any(k in x.lower() for k in ["promo","sconto","offerta"])) or None
        key=u.rstrip("/").rsplit("-",1)[-1]
        rows.append((key,name,None,category,qty,price,up,promo,u))
    return rows

async def main():
    now=datetime.now(timezone.utc).isoformat()
    con=init_db()
    current_category="inizializzazione"
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            ctx=await browser.new_context(locale="it-IT",viewport={"width":1440,"height":1200})
            page=await ctx.new_page()
            await choose_store(page)
            cats=await category_links(page)
            if not cats: raise RuntimeError("Nessuna categoria trovata")
            print(f"Categorie candidate: {len(cats)}",flush=True)

            all_rows={}
            completed=0
            for i,c in enumerate(cats,1):
                current_category=f"{c['name']} | {c['url']}"
                print(f"[{i}/{len(cats)}] {c['name']} -> {c['url']}",flush=True)
                r=await page.goto(c["url"],wait_until="domcontentloaded",timeout=60000)
                if not r or r.status!=200: raise RuntimeError(f"HTTP categoria {r.status if r else 'none'}")
                await page.wait_for_timeout(1300)
                urls=await load_all(page)
                rows=await parse_visible_cards(page,c["name"])
                row_urls={x[-1] for x in rows}
                if len(row_urls)!=len(urls):
                    raise RuntimeError(
                        f"Parser incompleto in {c['name']}: DOM {len(urls)} prodotti, prezzi parsati {len(row_urls)}"
                    )
                for row in rows: all_rows[row[0]]=row
                completed+=1
                print(f"  completi {len(urls)}; unici globali {len(all_rows)}",flush=True)
                await page.wait_for_timeout(800)

            if completed!=len(cats): raise RuntimeError("Non tutte le categorie completate")
            if not all_rows: raise RuntimeError("Database vuoto")

            con.execute("DELETE FROM products")
            for key,name,brand,cat,qty,price,up,promo,url in all_rows.values():
                if price<=0: raise RuntimeError(f"Prezzo non valido {key}: {price}")
                con.execute("""INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (key,"Decò","San Nicola La Strada - Via Milano 6",name,brand,cat,qty,
                     price,up,promo,url,"Decò a Casa / Multicedi",now))
            con.execute("""INSERT INTO update_log(checked_at,store,categories,products,status,note)
                           VALUES(?,?,?,?,?,?)""",
                        (now,"San Nicola La Strada - Via Milano 6",completed,len(all_rows),"OK",
                         "Tutte le categorie visitate; load-more esaurito; prodotti deduplicati per URL/id; prezzi > 0"))
            con.commit()
            check=con.execute("SELECT COUNT(*),COUNT(DISTINCT product_key),MIN(price_eur),MAX(price_eur) FROM products").fetchone()
            print(f"DB OK: righe={check[0]}, unici={check[1]}, min={check[2]}, max={check[3]}",flush=True)
            if check[0]!=check[1]: raise RuntimeError("Duplicati nel DB")
            await browser.close()
    except Exception as e:
        con.rollback()
        Path("deco_failure.txt").write_text(
            f"Categoria corrente: {current_category}\n"
            f"Errore: {type(e).__name__}: {e}\n",
            encoding="utf-8"
        )
        raise
    finally:
        con.close()

if __name__=="__main__":
    asyncio.run(main())
