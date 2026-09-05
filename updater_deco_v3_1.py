import asyncio, json, re, sqlite3, traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE="https://decoacasa.multicedi.it/"
ADDRESS="Via Isonzo 9, 81100 Caserta CE, Italia"
TARGET_STORE="San Nicola La Strada"
STORE_SLUG="via-milano-6"
STORE_NAME="San Nicola La Strada - Via Milano 6"
BOOTSTRAP_CATEGORY_ID="277"
BOOTSTRAP_CATEGORY_RE=re.compile(r"/frutta_277(?:[?#]|$)", re.I)

OUT=Path("deco_v3_output")
OUT.mkdir(exist_ok=True)
DB="prezzi_deco.db"

def save_json(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

def money(v):
    if v is None: return None
    m=re.search(r"(\d+(?:[.,]\d+)?)",str(v))
    return float(m.group(1).replace(",",".")) if m else None

def infer_quantity_from_name(name):
    """Recupera la quantità solo quando è esplicitamente scritta nel nome.
    Nessuna conversione o quantità viene inventata."""
    if not name:
        return None
    patterns=[
        r'\b(\d+\s*[xX×]\s*\d+(?:[.,]\d+)?\s*(?:kg|g|gr|l|lt|ml|cl|pz|pezzi))\b',
        r'\b(\d+(?:[.,]\d+)?\s*(?:kg|g|gr|l|lt|ml|cl))\b',
        r'\b(\d+\s*(?:pz|pezzi))\b',
    ]
    for pat in patterns:
        m=re.search(pat,name,re.I)
        if m:
            return re.sub(r'\s+',' ',m.group(1)).strip()
    return None

def product_sections(html):
    soup=BeautifulSoup(html,"html.parser")
    return soup.select("section.product[itemid], section[id^='product_']")

def parse_products(html, category_name, category_url):
    soup=BeautifulSoup(html,"html.parser")
    rows=[]
    for sec in soup.select("section.product[itemid], section[id^='product_']"):
        pid=(sec.get("itemid") or "").strip()
        if not pid:
            m=re.search(r"product_(\d+)",sec.get("id",""))
            pid=m.group(1) if m else ""
        data=sec.find(attrs={"data-id":True})
        name=""
        brand=""
        qty=""
        unit_price=""
        old_price=None
        price=None

        if data:
            name=(data.get("data-name") or "").strip()
            brand=(data.get("data-brand") or "").strip()
            price=money(data.get("data-price"))
            old_price=money(data.get("data-old-price"))
            meta=(data.get("data-meta") or "").strip()
        else:
            meta=""

        if not name:
            nm=sec.find(attrs={"itemprop":"name"})
            name=nm.get_text(" ",strip=True) if nm else ""

        if price is None:
            pm=sec.find("meta",attrs={"itemprop":"price"})
            price=money(pm.get("content")) if pm else None

        size=sec.select_one(".product-meta.size")
        if size:
            qty=size.get_text(" ",strip=True)
        if not qty:
            qty=infer_quantity_from_name(name)

        metas=[x.get_text(" ",strip=True) for x in sec.select(".product-meta") if "size" not in (x.get("class") or [])]
        if metas:
            unit_price=metas[0]
        elif meta:
            unit_price=meta

        a=sec.find("a",href=lambda x:x and "/prodotto/" in x)
        url=urljoin(BASE,a["href"]) if a else category_url

        promo_parts=[]
        if old_price is not None and price is not None and old_price>price:
            promo_parts.append(f"Prezzo precedente {old_price:.2f} €")
        text=sec.get_text(" ",strip=True)
        for pat in [r"fino al\s+\d{1,2}/\d{1,2}/\d{2,4}", r"sconto\s+\d+%"]:
            mm=re.search(pat,text,re.I)
            if mm: promo_parts.append(mm.group(0))
        promotion=" | ".join(dict.fromkeys(promo_parts)) or None

        if not pid:
            # fallback stabile dal finale URL
            mm=re.search(r"-(\d+)(?:[/?#]|$)",url)
            pid=mm.group(1) if mm else url

        rows.append({
            "product_key":pid,
            "name":name,
            "brand":brand or None,
            "category":category_name,
            "quantity_text":qty or None,
            "price_eur":price,
            "unit_price_text":unit_price or None,
            "promotion_text":promotion,
            "old_price_eur":old_price,
            "product_url":url,
            "category_url":category_url,
        })
    # dedup nel singolo frammento
    return list({r["product_key"]:r for r in rows}.values())

def extract_product_list_meta(html):
    soup=BeautifulSoup(html,"html.parser")
    box=soup.select_one(".product-list[data-pagination-url]")
    if not box:
        return None
    return {
        "category_id":box.get("data-category-id",""),
        "special_id":box.get("data-special-id",""),
        "featured_id":box.get("data-featured-id",""),
        "container_id":box.get("data-container-id",""),
        "pagination_url":urljoin(BASE,box.get("data-pagination-url","")),
        "product_per_page":box.get("data-productperpage","40"),
        "is_last_page":(box.get("data-islastpage","").lower()=="true"),
    }

def category_candidates(html):
    soup=BeautifulSoup(html,"html.parser")
    all_cats={}
    for a in soup.find_all("a",href=True):
        u=urljoin(BASE,a["href"]).split("#")[0].split("?")[0]
        if f"/spesa-ritiro-negozio/{STORE_SLUG}/" not in u: continue
        if "/prodotto/" in u or "/ajax/" in u: continue
        if not re.search(r"/[^/]+_\d+$",urlparse(u).path): continue
        all_cats[u]=(a.get_text(" ",strip=True) or urlparse(u).path.rsplit("/",1)[-1])

    # Preferisce le foglie del menu: se un <li> contiene altre categorie discendenti,
    # il suo link principale è un parent e non serve scaricarlo nuovamente.
    parent_urls=set()
    for li in soup.find_all("li"):
        links=[]
        for a in li.find_all("a",href=True):
            u=urljoin(BASE,a["href"]).split("#")[0].split("?")[0]
            if u in all_cats:
                links.append(u)
        if len(set(links))>1:
            first=links[0]
            if any(x!=first for x in links[1:]):
                parent_urls.add(first)

    leaves=[{"name":n,"url":u} for u,n in all_cats.items() if u not in parent_urls]
    # Se l'euristica del menu non trova abbastanza foglie, usa tutte le URL uniche.
    chosen=leaves if len(leaves)>=10 else [{"name":n,"url":u} for u,n in all_cats.items()]
    return chosen, len(all_cats), len(leaves)

def body_to_obj(post_data, content_type):
    if "application/json" in (content_type or "").lower():
        return ("json", json.loads(post_data or "{}"))
    return ("form", dict(parse_qsl(post_data or "", keep_blank_values=True)))

def obj_to_body(kind,obj):
    if kind=="json":
        return json.dumps(obj,separators=(",",":"))
    return urlencode(obj)

def as_number(v):
    try:
        if isinstance(v,bool): return None
        return float(v)
    except: return None

def make_payload(base1, base2, kind, catmeta, ajax_index):
    # ajax_index: 1 = primo "carica altri", 2 = secondo, ecc.
    out=dict(base1)
    # Campi semanticamente riconoscibili
    for k in list(out):
        kl=k.lower().replace("_","")
        if "category" in kl:
            out[k]=catmeta["category_id"]
        elif "special" in kl:
            out[k]=catmeta["special_id"]
        elif "featured" in kl:
            out[k]=catmeta["featured_id"]
        elif "container" in kl:
            out[k]=catmeta["container_id"]
        elif "productperpage" in kl or "pagesize" in kl:
            out[k]=catmeta["product_per_page"]

    # Qualunque contatore numerico che cambi tra click 1 e click 2 viene extrapolato.
    for k,v1 in base1.items():
        if k not in base2: continue
        v2=base2[k]
        n1,n2=as_number(v1),as_number(v2)
        if n1 is not None and n2 is not None and n1!=n2:
            val=n1+(ajax_index-1)*(n2-n1)
            if isinstance(v1,int) or (isinstance(v1,str) and re.fullmatch(r"-?\d+",v1)):
                val=int(round(val))
            out[k]=str(val) if isinstance(v1,str) else val

    # Sostituzione bootstrap category id se presente come valore non riconosciuto
    for k,v in list(out.items()):
        if str(v)==BOOTSTRAP_CATEGORY_ID:
            out[k]=catmeta["category_id"]
    return out

async def select_store(page):
    await page.goto(BASE,wait_until="domcontentloaded",timeout=60000)
    await page.wait_for_timeout(1500)
    for sel in ['button:has-text("Accetta tutto")','button:has-text("Accetta")','#onetrust-accept-btn-handler']:
        if await page.locator(sel).count():
            try:
                await page.locator(sel).first.click(timeout=1500); break
            except: pass
    f=page.locator('input[name="addressField1"],#addressField1').first
    await f.fill(ADDRESS); await page.wait_for_timeout(2300)
    pac=page.locator(".pac-item")
    suggestions=[(await pac.nth(i).inner_text()).strip() for i in range(await pac.count())]
    idx=next(i for i,t in enumerate(suggestions) if "caserta" in t.lower())
    await f.focus()
    for _ in range(idx+1):
        await page.keyboard.press("ArrowDown"); await page.wait_for_timeout(80)
    await page.keyboard.press("Enter"); await page.wait_for_timeout(3800)

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
    await page.wait_for_timeout(4500)
    if STORE_SLUG not in page.url:
        raise RuntimeError(f"Store inatteso: {page.url}")

async def bootstrap_ajax(page):
    # Trova link Frutta reale
    links=await page.locator("a").evaluate_all("els=>els.map(e=>e.href).filter(Boolean)")
    frutta=next((u for u in links if BOOTSTRAP_CATEGORY_RE.search(u)),None)
    if not frutta:
        raise RuntimeError("Categoria bootstrap Frutta _277 non trovata")

    captures=[]
    responses=[]
    current_ids=set()

    async def on_request(req):
        if "productsPagination" in req.url:
            captures.append({
                "url":req.url,
                "method":req.method,
                "post_data":req.post_data or "",
                "headers":await req.all_headers(),
            })
    async def on_response(resp):
        if "productsPagination" in resp.url:
            try:
                responses.append(await resp.text())
            except:
                responses.append("")

    page.on("request",lambda r: asyncio.create_task(on_request(r)))
    page.on("response",lambda r: asyncio.create_task(on_response(r)))

    await page.goto(frutta,wait_until="domcontentloaded",timeout=60000)
    await page.wait_for_timeout(1800)
    initial_html=await page.content()
    initial_ids={r["product_key"] for r in parse_products(initial_html,"Frutta",frutta)}
    current_ids=set(initial_ids)
    new_batches=[]

    for click_no in (1,2):
        loc=page.get_by_text(re.compile(r"CARICA\s+ALTRI\s+PRODOTTI",re.I))
        visible=None
        for i in range(await loc.count()):
            if await loc.nth(i).is_visible():
                visible=loc.nth(i); break
        if visible is None:
            raise RuntimeError(f"Bootstrap: LOAD MORE non visibile al click {click_no}")
        before=set(current_ids)
        await visible.evaluate("(el)=>el.scrollIntoView({block:'center'})")
        await visible.click(timeout=8000)
        for _ in range(30):
            await page.wait_for_timeout(350)
            html=await page.content()
            ids={r["product_key"] for r in parse_products(html,"Frutta",frutta)}
            if len(ids)>len(before):
                current_ids=ids
                new_batches.append(sorted(ids-before))
                break
        else:
            raise RuntimeError(f"Bootstrap: nessuna crescita al click {click_no}")

    await page.wait_for_timeout(1000)
    if len(captures)<2:
        raise RuntimeError(f"Bootstrap: catturate solo {len(captures)} POST AJAX")
    c1,c2=captures[-2],captures[-1]
    ct=c1["headers"].get("content-type","")
    kind,o1=body_to_obj(c1["post_data"],ct)
    kind2,o2=body_to_obj(c2["post_data"],c2["headers"].get("content-type",""))
    if kind!=kind2:
        raise RuntimeError("Bootstrap: content-type payload cambiato tra due click")

    meta=extract_product_list_meta(initial_html)
    if not meta or meta["category_id"]!=BOOTSTRAP_CATEGORY_ID:
        raise RuntimeError("Bootstrap: metadata Frutta non valida")

    return {
        "frutta_url":frutta,
        "initial_html":initial_html,
        "meta":meta,
        "capture1":c1,
        "capture2":c2,
        "payload_kind":kind,
        "payload1":o1,
        "payload2":o2,
        "new_batches":new_batches,
    }

async def api_post(reqctx,url,headers,body):
    # Mantiene soltanto header utili, evitando pseudo-header/browser noise.
    keep={}
    for k,v in headers.items():
        kl=k.lower()
        if kl in ("content-type","x-requested-with","accept","origin"):
            keep[k]=v
    keep["Referer"]=url.replace("/ajax/productsPagination","")
    resp=await reqctx.fetch(url,method="POST",headers=keep,data=body,timeout=60000)
    txt=await resp.text()
    if resp.status!=200:
        raise RuntimeError(f"AJAX HTTP {resp.status}: {url}")
    return txt

def extract_html_from_ajax(text):
    st=text.lstrip()
    if st.startswith("<"):
        return text
    try:
        obj=json.loads(text)
    except:
        return text
    # Cerca ricorsivamente una stringa HTML contenente prodotti
    stack=[obj]
    while stack:
        x=stack.pop()
        if isinstance(x,str) and ("section" in x.lower() or "product_" in x.lower()):
            return x
        if isinstance(x,dict): stack.extend(x.values())
        elif isinstance(x,list): stack.extend(x)
    return text

async def validate_direct_replay(reqctx, boot):
    results=[]
    for idx,(payload,expected) in enumerate([(boot["payload1"],boot["new_batches"][0]),
                                             (boot["payload2"],boot["new_batches"][1])],1):
        raw=obj_to_body(boot["payload_kind"],payload)
        txt=await api_post(reqctx,boot["meta"]["pagination_url"],boot["capture1"]["headers"],raw)
        html=extract_html_from_ajax(txt)
        ids={r["product_key"] for r in parse_products(html,"Frutta",boot["frutta_url"])}
        exp=set(expected)
        overlap=len(ids & exp)
        results.append({"ajax_index":idx,"returned_ids":len(ids),"expected_new_ids":len(exp),"overlap":overlap})
        if not exp or overlap < max(1,int(len(exp)*0.8)):
            raise RuntimeError(f"Replay diretto non validato al batch {idx}: overlap {overlap}/{len(exp)}")
    return results

def init_db():
    con=sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS products(
      product_key TEXT PRIMARY KEY,
      supermarket TEXT NOT NULL,
      store TEXT NOT NULL,
      product_name TEXT NOT NULL,
      brand TEXT,
      category TEXT,
      quantity_text TEXT,
      price_eur REAL NOT NULL,
      unit_price_text TEXT,
      promotion_text TEXT,
      old_price_eur REAL,
      product_url TEXT NOT NULL,
      category_url TEXT,
      source TEXT NOT NULL,
      checked_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS update_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      checked_at TEXT NOT NULL,
      store TEXT NOT NULL,
      categories INTEGER NOT NULL,
      products INTEGER NOT NULL,
      status TEXT NOT NULL,
      note TEXT
    )""")
    con.commit()
    return con

async def main():
    diagnostics={"version":"V3.1","status":"STARTED"}
    now=datetime.now(timezone.utc).isoformat()
    con=init_db()
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            ctx=await browser.new_context(locale="it-IT",viewport={"width":1440,"height":1200})
            page=await ctx.new_page()

            print("1/4 Selezione store ufficiale...",flush=True)
            await select_store(page)
            store_html=await page.content()

            print("2/4 Bootstrap endpoint AJAX con due click reali...",flush=True)
            boot=await bootstrap_ajax(page)
            diagnostics["bootstrap"]={
                "endpoint":boot["meta"]["pagination_url"],
                "payload_kind":boot["payload_kind"],
                "payload1":boot["payload1"],
                "payload2":boot["payload2"],
                "new_batch_sizes":[len(x) for x in boot["new_batches"]],
            }

            print("3/4 Replay diretto dell'endpoint...",flush=True)
            replay=await validate_direct_replay(ctx.request,boot)
            diagnostics["direct_replay"]=replay
            print("Endpoint diretto VALIDATO.",flush=True)

            cats,total_cats,leaf_cats=category_candidates(store_html)
            diagnostics["category_discovery"]={
                "all_unique_category_urls":total_cats,
                "leaf_candidates":leaf_cats,
                "selected_for_download":len(cats),
            }
            save_json("category_list.json",cats)
            print(f"Categorie URL uniche={total_cats}; foglie={leaf_cats}; selezionate={len(cats)}",flush=True)

            all_products={}
            category_stats=[]
            sem=asyncio.Semaphore(5)

            async def process_category(pos,c):
                async with sem:
                    rr=await ctx.request.get(c["url"],timeout=60000)
                    if rr.status!=200:
                        raise RuntimeError(f"HTTP {rr.status} categoria {c['url']}")
                    html=await rr.text()
                    meta=extract_product_list_meta(html)
                    if not meta:
                        # Alcune URL di menu possono essere landing senza lista prodotti.
                        return {"pos":pos,"name":c["name"],"url":c["url"],"landing":True,"products":[],"batches":0}

                    rows=parse_products(html,c["name"],c["url"])
                    seen={r["product_key"] for r in rows}
                    batches=0

                    if not meta["is_last_page"]:
                        for ajax_index in range(1,100):
                            payload=make_payload(boot["payload1"],boot["payload2"],boot["payload_kind"],meta,ajax_index)
                            raw=obj_to_body(boot["payload_kind"],payload)
                            txt=await api_post(ctx.request,meta["pagination_url"],boot["capture1"]["headers"],raw)
                            frag=extract_html_from_ajax(txt)
                            more=parse_products(frag,c["name"],c["url"])
                            ids={r["product_key"] for r in more}
                            new=ids-seen
                            batches+=1

                            # risposta vuota = fine
                            if not ids:
                                break
                            # stessa pagina/nessuna nuova referenza è accettabile solo dopo aver
                            # già ricevuto almeno un batch; evita loop infiniti.
                            if not new:
                                break

                            for r in more:
                                if r["product_key"] in new:
                                    rows.append(r)
                            seen |= new

                            # batch corto: fine naturale
                            try: ppp=int(meta["product_per_page"])
                            except: ppp=40
                            if len(ids)<ppp:
                                break
                        else:
                            raise RuntimeError(f"Troppi batch AJAX: {c['url']}")

                    # Controllo prezzi: ogni prodotto pubblicato nel DOM deve avere prezzo positivo.
                    bad=[r for r in rows if r["price_eur"] is None or r["price_eur"]<=0]
                    if bad:
                        raise RuntimeError(f"{c['name']}: {len(bad)} prodotti senza prezzo valido")
                    return {"pos":pos,"name":c["name"],"url":c["url"],"landing":False,
                            "products":rows,"batches":batches}

            # Lavora a piccoli gruppi paralleli per non sovraccaricare il sito.
            done=0
            for start in range(0,len(cats),5):
                group=cats[start:start+5]
                results=await asyncio.gather(*[
                    process_category(start+i+1,c) for i,c in enumerate(group)
                ])
                for result in results:
                    done+=1
                    rows=result.pop("products")
                    for r in rows:
                        old=all_products.get(r["product_key"])
                        if old is None:
                            all_products[r["product_key"]]=r
                        else:
                            # Mantiene una categoria specifica già acquisita; il prezzo deve coincidere.
                            if abs(old["price_eur"]-r["price_eur"])>0.001:
                                raise RuntimeError(
                                    f"Prezzo discordante stesso prodotto {r['product_key']}: "
                                    f"{old['price_eur']} vs {r['price_eur']}"
                                )
                    result["unique_global_after"]=len(all_products)
                    category_stats.append(result)
                    print(f"[{done}/{len(cats)}] {result['name']}: "
                          f"{'landing' if result['landing'] else str(len(rows))+' prodotti'} | "
                          f"globali {len(all_products)}",flush=True)
                await asyncio.sleep(0.35)

            if not all_products:
                raise RuntimeError("Nessun prodotto raccolto")

            print("4/4 Creazione database...",flush=True)
            con.execute("DELETE FROM products")
            for r in all_products.values():
                con.execute("""INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                    r["product_key"],"Decò",STORE_NAME,r["name"],r["brand"],r["category"],
                    r["quantity_text"],r["price_eur"],r["unit_price_text"],r["promotion_text"],
                    r["old_price_eur"],r["product_url"],r["category_url"],
                    "Decò a Casa / Multicedi",now
                ))
            con.execute("""INSERT INTO update_log
                (checked_at,store,categories,products,status,note)
                VALUES(?,?,?,?,?,?)""",
                (now,STORE_NAME,len(cats),len(all_products),"OK",
                 "V3.1: sessione browser solo bootstrap; catalogo via HTTP/AJAX diretto; "
                 "foglie menu preferite; deduplica product_key; prezzi > 0"))
            con.commit()

            chk=con.execute("""SELECT COUNT(*),COUNT(DISTINCT product_key),
                              SUM(price_eur<=0),MIN(price_eur),MAX(price_eur)
                              FROM products""").fetchone()
            if chk[0]!=chk[1] or (chk[2] or 0)!=0:
                raise RuntimeError(f"Audit DB fallito: {chk}")

            diagnostics["category_stats"]=category_stats
            diagnostics["special_categories"]=[
                x["name"] for x in category_stats
                if re.search(r'(20\d{2}|promo|special|gastronauta)',x["name"],re.I)
            ]
            diagnostics["db_audit"]={
                "rows":chk[0],"unique_keys":chk[1],"invalid_prices":chk[2] or 0,
                "min_price":chk[3],"max_price":chk[4],
            }
            diagnostics["status"]="OK"
            save_json("deco_v3_report.json",diagnostics)
            print(f"DB OK: {chk[0]} prodotti unici. Fine.",flush=True)
            await browser.close()

    except Exception as e:
        con.rollback()
        diagnostics["status"]="FAILED"
        diagnostics["error"]=f"{type(e).__name__}: {e}"
        diagnostics["traceback"]=traceback.format_exc()
        save_json("deco_v3_report.json",diagnostics)
        (OUT/"deco_failure.txt").write_text(
            f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}",
            encoding="utf-8"
        )
        raise
    finally:
        con.close()

if __name__=="__main__":
    asyncio.run(main())
