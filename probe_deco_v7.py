import asyncio, json, re, traceback
from pathlib import Path
from playwright.async_api import async_playwright

BASE="https://decoacasa.multicedi.it/"
ADDRESS="Via Isonzo 9, 81100 Caserta CE, Italia"
TARGET_STORE="San Nicola La Strada"
TARGET_CATEGORY_RE=re.compile(r'/frutta_277(?:[?#]|$)',re.I)
OUT=Path("deco_probe_v7_output"); OUT.mkdir(exist_ok=True)

def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

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
                    if any(k in u for k in ["product","catalog","category","load","page","offset",
                                            "search","api-fe.restore.shopping","frutta"]):
                        res["network"].append({
                            "status":resp.status,"url":resp.url,
                            "method":resp.request.method,
                            "resource_type":resp.request.resource_type,
                            "content_type":resp.headers.get("content-type","")
                        })
                except: pass
            page.on("response",lambda r: asyncio.create_task(cap(r)))

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
            if not best: raise RuntimeError("Card San Nicola non trovata")
            c=containers.nth(best[1])
            await c.get_by_text(re.compile(r"PRENOTA\s+E\s+RITIRA\s+QUI",re.I)).first.click()
            await page.wait_for_timeout(5500)

            links=await page.locator("a").evaluate_all("els=>els.map(e=>e.href).filter(Boolean)")
            frutta=next((u for u in links if TARGET_CATEGORY_RE.search(u)),None)
            if not frutta: raise RuntimeError("Link reale Frutta _277 non trovato")
            rr=await page.goto(frutta,wait_until="domcontentloaded",timeout=60000)
            await page.wait_for_timeout(2500)
            res["category_url"]=page.url
            res["category_status"]=rr.status if rr else None

            async def products():
                return await page.locator('a[href*="/prodotto/"]').evaluate_all(
                    "els=>[...new Set(els.map(e=>e.href))]"
                )

            before=await products()
            res["before_unique"]=len(before)
            (OUT/"before_load.html").write_text(await page.content(),encoding="utf-8")

            load=page.get_by_text(re.compile(r'CARICA\s+ALTRI\s+PRODOTTI',re.I))
            if not await load.count():
                raise RuntimeError("Pulsante CARICA ALTRI PRODOTTI non trovato")

            network_start=len(res["network"])
            await load.first.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            await load.first.click(timeout=5000)

            # Attende davvero la crescita del set prodotti, fino a 12 secondi.
            grown=False
            for _ in range(24):
                await page.wait_for_timeout(500)
                now=await products()
                if len(now)>len(before):
                    grown=True
                    break

            after=await products()
            res["after_unique"]=len(after)
            res["new_unique"]=len(set(after)-set(before))
            res["overlap"]=len(set(after)&set(before))
            res["new_product_urls"]=list(set(after)-set(before))[:100]
            res["load_more_network"]=res["network"][network_start:]
            (OUT/"after_load.html").write_text(await page.content(),encoding="utf-8")
            await page.screenshot(path=str(OUT/"after_load.png"),full_page=True)

            # Verifica se il pulsante resta disponibile per un ulteriore batch.
            res["load_more_still_present"]=await page.get_by_text(
                re.compile(r'CARICA\s+ALTRI\s+PRODOTTI',re.I)
            ).count()>0

            # Candidati endpoint: richieste nate SOLO dal click e non asset statici.
            res["endpoint_candidates"]=[
                x for x in res["load_more_network"]
                if x["resource_type"] in ("xhr","fetch","document") or
                   "json" in x["content_type"].lower()
            ]

            res["verdict"]="LOAD_MORE_VALIDATED" if grown and res["new_unique"]>0 else "LOAD_MORE_NOT_VALIDATED"
            save("deco_probe_v7.json",res)
            print(json.dumps(res,ensure_ascii=False,indent=2)[:30000],flush=True)
            await browser.close()

    except Exception as e:
        res["error"]=f"{type(e).__name__}: {e}"
        res["traceback"]=traceback.format_exc()
        res["verdict"]="FAILED_WITH_DIAGNOSTICS"
        save("deco_probe_v7.json",res)
        print(json.dumps(res,ensure_ascii=False,indent=2)[:30000],flush=True)

if __name__=="__main__":
    asyncio.run(main())
