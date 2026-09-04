import asyncio, json, re, traceback
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from playwright.async_api import async_playwright

BASE="https://decoacasa.multicedi.it/"
ADDRESS="Via Isonzo 9, 81100 Caserta CE, Italia"
TARGET_STORE="San Nicola La Strada"
OUT=Path("deco_probe_v6_output"); OUT.mkdir(exist_ok=True)

def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

def with_page(url,n):
    p=urlparse(url); q=parse_qs(p.query)
    q["page"]=[str(n)]
    return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),p.fragment))

async def main():
    res={"steps":[],"network":[],"error":None}
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            ctx=await browser.new_context(locale="it-IT",viewport={"width":1440,"height":1200})
            page=await ctx.new_page()

            async def cap(resp):
                try:
                    u=resp.url.lower()
                    if any(k in u for k in ["category","catalog","product","search","page","api-fe.restore.shopping"]):
                        res["network"].append({"status":resp.status,"url":resp.url,
                                               "content_type":resp.headers.get("content-type","")})
                except: pass
            page.on("response",lambda r: asyncio.create_task(cap(r)))

            await page.goto(BASE,wait_until="domcontentloaded",timeout=60000)
            await page.wait_for_timeout(2000)
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
                await page.keyboard.press("ArrowDown"); await page.wait_for_timeout(120)
            await page.keyboard.press("Enter"); await page.wait_for_timeout(4500)

            # card target
            containers=page.locator("div,li,article,section")
            best=None
            for i in range(await containers.count()):
                try: txt=(await containers.nth(i).inner_text()).strip()
                except: continue
                lo=txt.lower()
                if TARGET_STORE.lower() in lo and "prenota e ritira qui" in lo:
                    if best is None or len(txt)<best[0]: best=(len(txt),i)
            if not best: raise RuntimeError("Card San Nicola non trovata")
            c=containers.nth(best[1])
            await c.get_by_text(re.compile(r"PRENOTA\s+E\s+RITIRA\s+QUI",re.I)).first.click()
            await page.wait_for_timeout(6000)
            res["steps"].append({"store_url":page.url})

            # prende una categoria reale che contenga link prodotto, senza inventare slug
            links=await page.locator("a").evaluate_all(
                """els=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||''}))
                   .filter(x=>x.href)"""
            )
            cats=[]
            for x in links:
                if re.search(r'/[^/]+_[0-9]+(?:[?#]|$)',x["href"]) and "/prodotto/" not in x["href"]:
                    if x["href"] not in [c["href"] for c in cats]: cats.append(x)
            save("category_candidates.json",cats[:150])

            chosen=None
            for cand in cats[:30]:
                rr=await page.goto(cand["href"],wait_until="domcontentloaded",timeout=60000)
                await page.wait_for_timeout(1800)
                pc=await page.locator('a[href*="/prodotto/"]').count()
                if pc>=5:
                    chosen={"text":cand["text"],"url":page.url,"status":rr.status if rr else None}
                    break
            if not chosen: raise RuntimeError("Nessuna categoria reale con >=5 link prodotto trovata")
            res["category"]=chosen

            # Analizza pagina 1: prodotti unici, testi che sembrano totali, controlli pagina/load more.
            async def inspect_current(n):
                text=await page.locator("body").inner_text()
                html=await page.content()
                prods=await page.locator('a[href*="/prodotto/"]').evaluate_all(
                    "els=>[...new Set(els.map(e=>e.href))]"
                )
                totals=[]
                for pat in [r'(\d+)\s+prodott[io]',r'prodott[io]\s*\(?\s*(\d+)\s*\)?',
                            r'(\d+)\s+risultat[oi]',r'risultat[oi]\s*\(?\s*(\d+)\s*\)?']:
                    totals += re.findall(pat,text,re.I)
                controls=await page.locator(
                    'a[href*="page="],button:has-text("Carica"),a:has-text("Carica"),'
                    'button:has-text("Mostra"),a:has-text("Mostra"),'
                    'button:has-text("Successiv"),a:has-text("Successiv")'
                ).evaluate_all("els=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||null}))")
                (OUT/f"category_page_{n}.html").write_text(html,encoding="utf-8")
                return {"page":n,"url":page.url,"products":len(prods),"product_urls":prods,
                        "declared_candidates":list(dict.fromkeys(totals))[:30],
                        "controls":controls[:50]}

            p1=await inspect_current(1)
            pages=[p1]

            # Preferisce link di paginazione realmente esposti. Se non esistono, prova
            # SOLO il parametro page=2 come diagnostica e verifica che cambi il set prodotti.
            href2=None
            for ctrl in p1["controls"]:
                h=ctrl.get("href")
                if h and re.search(r'[?&]page=2(?:&|$)',h):
                    href2=h; break

            if href2:
                await page.goto(href2,wait_until="domcontentloaded",timeout=60000)
                await page.wait_for_timeout(1800)
                pages.append(await inspect_current(2))
                res["pagination_mode"]="EXPOSED_PAGE_LINK"
            else:
                test2=with_page(chosen["url"],2)
                await page.goto(test2,wait_until="domcontentloaded",timeout=60000)
                await page.wait_for_timeout(1800)
                p2=await inspect_current(2)
                pages.append(p2)
                res["pagination_mode"]="DIAGNOSTIC_PAGE_PARAMETER"

            set1=set(p1["product_urls"]); set2=set(pages[1]["product_urls"])
            res["pages"]= [{k:v for k,v in x.items() if k!="product_urls"} for x in pages]
            res["page_overlap"]=len(set1 & set2)
            res["page2_new_products"]=len(set2-set1)
            res["network_tail"]=res["network"][-250:]

            # V6 valida paginazione solo se pagina 2 contiene prodotti nuovi.
            res["verdict"]="CATEGORY_PAGINATION_VALIDATED" if len(set2-set1)>0 else "PAGINATION_NOT_VALIDATED"
            save("deco_probe_v6.json",res)
            print(json.dumps(res,ensure_ascii=False,indent=2)[:30000],flush=True)
            await browser.close()
    except Exception as e:
        res["error"]=f"{type(e).__name__}: {e}"
        res["traceback"]=traceback.format_exc()
        res["verdict"]="FAILED_WITH_DIAGNOSTICS"
        save("deco_probe_v6.json",res)
        print(json.dumps(res,ensure_ascii=False,indent=2)[:30000],flush=True)

if __name__=="__main__":
    asyncio.run(main())
