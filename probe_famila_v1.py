import asyncio, json, re, traceback
from pathlib import Path
from playwright.async_api import async_playwright

BASE="https://www.cosicomodo.it/familasud"
TARGET_CITY="Caserta"
TARGET_HINTS=["borsellino","13","15","17","19"]
OUT=Path("famila_probe_v1_output"); OUT.mkdir(exist_ok=True)

def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

async def main():
    r={"verdict":"STARTED","network":[]}
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            ctx=await browser.new_context(locale="it-IT",viewport={"width":1440,"height":1200})
            page=await ctx.new_page()

            async def cap(resp):
                try:
                    u=resp.url
                    if any(k in u.lower() for k in ["store","product","search","category","repart","selector","ajax","api"]):
                        r["network"].append({"status":resp.status,"url":u,
                                             "method":resp.request.method,
                                             "type":resp.request.resource_type,
                                             "content_type":resp.headers.get("content-type","")})
                except: pass
            page.on("response",lambda x: asyncio.create_task(cap(x)))

            resp=await page.goto(BASE,wait_until="domcontentloaded",timeout=60000)
            await page.wait_for_timeout(2500)
            r["home_status"]=resp.status if resp else None

            # cookie: soltanto consensi espliciti
            for sel in ['#onetrust-accept-btn-handler','button:has-text("Accetta tutto")',
                        'button:has-text("Accetta")']:
                if await page.locator(sel).count():
                    try: await page.locator(sel).first.click(timeout=1200); break
                    except: pass

            # Individua il campo località/CAP dal testo ufficiale.
            inputs=page.locator("input")
            candidates=[]
            for i in range(await inputs.count()):
                el=inputs.nth(i)
                candidates.append({
                    "i":i,
                    "id":await el.get_attribute("id"),
                    "name":await el.get_attribute("name"),
                    "placeholder":await el.get_attribute("placeholder"),
                    "type":await el.get_attribute("type")
                })
            save("inputs.json",candidates)

            target=None
            for i,x in enumerate(candidates):
                blob=" ".join(str(x.get(k) or "") for k in ("id","name","placeholder")).lower()
                if any(k in blob for k in ["cap","comune","localit","location","postal"]):
                    target=inputs.nth(i); break
            if target is None:
                # fallback: primo input testuale visibile
                for i in range(await inputs.count()):
                    el=inputs.nth(i)
                    if await el.is_visible() and (await el.get_attribute("type") or "text") in ("text","search"):
                        target=el; break
            if target is None: raise RuntimeError("Campo CAP/località non trovato")

            await target.fill(TARGET_CITY)
            await page.wait_for_timeout(2500)
            (OUT/"after_city.html").write_text(await page.content(),encoding="utf-8")
            await page.screenshot(path=str(OUT/"after_city.png"),full_page=True)

            # Registra tutte le opzioni visibili che citano Caserta/Famila/Borsellino.
            texts=[]
            for sel in ["li","button","a","div"]:
                loc=page.locator(sel)
                for i in range(min(await loc.count(),800)):
                    el=loc.nth(i)
                    try:
                        if not await el.is_visible(): continue
                        t=" ".join((await el.inner_text()).split())
                    except: continue
                    lo=t.lower()
                    if t and len(t)<500 and any(k in lo for k in ["caserta","borsellino","famila"]):
                        texts.append(t)
            texts=list(dict.fromkeys(texts))
            save("visible_candidates.json",texts[:300])

            # Prova prima una suggestion Caserta, senza indovinare endpoint.
            clicked=False
            for sel in ["li","button","a","div"]:
                loc=page.locator(sel)
                best=None
                for i in range(min(await loc.count(),800)):
                    el=loc.nth(i)
                    try:
                        if not await el.is_visible(): continue
                        t=" ".join((await el.inner_text()).split())
                    except: continue
                    if "caserta" in t.lower() and 1<=len(t)<=150:
                        if best is None or len(t)<best[0]: best=(len(t),el,t)
                if best:
                    try:
                        await best[1].click(timeout=2500)
                        clicked=True; r["city_choice"]=best[2]; break
                    except: pass

            if not clicked:
                # Alcuni autocomplete accettano tastiera.
                await target.focus(); await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter")
            await page.wait_for_timeout(3500)

            # Se esiste un bottone Cerca, lo usa.
            for txt in [re.compile(r"^Cerca$",re.I),re.compile(r"Ritiro in negozio",re.I)]:
                loc=page.get_by_text(txt)
                for i in range(await loc.count()):
                    try:
                        if await loc.nth(i).is_visible():
                            await loc.nth(i).click(timeout=2000); await page.wait_for_timeout(3000); break
                    except: pass

            (OUT/"stores.html").write_text(await page.content(),encoding="utf-8")
            await page.screenshot(path=str(OUT/"stores.png"),full_page=True)

            # Cerca il contenitore più piccolo che cita Borsellino.
            nodes=page.locator("div,li,article,section")
            best=None
            for i in range(await nodes.count()):
                el=nodes.nth(i)
                try:
                    if not await el.is_visible(): continue
                    t=" ".join((await el.inner_text()).split())
                except: continue
                lo=t.lower()
                if "borsellino" in lo and "caserta" in lo:
                    if best is None or len(t)<best[0]: best=(len(t),el,t)
            if best:
                r["target_store_text"]=best[2]
                # salva attributi/link utili del contenitore
                links=await best[1].locator("a").evaluate_all(
                    "els=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||'',id:e.id||'',cls:e.className||''}))")
                buttons=await best[1].locator("button").evaluate_all(
                    "els=>els.map(e=>({text:(e.innerText||'').trim(),id:e.id||'',cls:e.className||''}))")
                r["target_store_links"]=links; r["target_store_buttons"]=buttons

                # Clicca solo un controllo interno della card target che sembri selezione/ritiro.
                controls=best[1].locator("a,button")
                chosen=None
                for i in range(await controls.count()):
                    el=controls.nth(i)
                    try:
                        if not await el.is_visible(): continue
                        t=" ".join((await el.inner_text()).split())
                    except: continue
                    if re.search(r"(scegli|seleziona|ritira|spesa|entra|continua)",t,re.I):
                        chosen=(el,t); break
                if chosen:
                    await chosen[0].click(timeout=4000)
                    r["store_control_clicked"]=chosen[1]
                    await page.wait_for_timeout(5000)

            r["final_url"]=page.url
            (OUT/"final.html").write_text(await page.content(),encoding="utf-8")
            await page.screenshot(path=str(OUT/"final.png"),full_page=True)

            # Analizza ciò che è realmente apparso dopo la selezione.
            links=await page.locator("a").evaluate_all(
                """els=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||''})).filter(x=>x.href)""")
            product=[x for x in links if "/p/" in x["href"] or "/prodotto/" in x["href"].lower()]
            categories=[x for x in links if any(k in x["href"].lower() for k in ["/repart","category","/c/","ricerca?q="])]
            body=await page.locator("body").inner_text()
            prices=re.findall(r"\b\d{1,4}[,.]\d{2}\s*€|€\s*\d{1,4}[,.]\d{2}\b",body)
            units=re.findall(r"\b\d+(?:[,.]\d+)?\s*(?:kg|g|gr|l|lt|ml|cl|pz)\b",body,re.I)

            r["product_links_count"]=len({x["href"] for x in product})
            r["product_links_sample"]=product[:30]
            r["category_links_count"]=len({x["href"] for x in categories})
            r["category_links_sample"]=categories[:50]
            r["price_occurrences"]=len(prices)
            r["price_sample"]=prices[:30]
            r["unit_occurrences"]=len(units)
            r["network_tail"]=r["network"][-250:]

            store_ok=("borsellino" in body.lower() or "borsellino" in page.url.lower()
                      or bool(r.get("target_store_text")))
            catalog_ok=r["product_links_count"]>0 and r["price_occurrences"]>0
            if store_ok and catalog_ok:
                r["verdict"]="STORE_AND_CATALOG_CONTEXT_VALIDATED"
            elif store_ok:
                r["verdict"]="STORE_FOUND_CATALOG_NOT_YET_VALIDATED"
            else:
                r["verdict"]="STORE_NOT_VALIDATED"

            save("famila_probe_v1.json",r)
            print(json.dumps(r,ensure_ascii=False,indent=2)[:30000],flush=True)
            await browser.close()
    except Exception as e:
        r["error"]=f"{type(e).__name__}: {e}"
        r["traceback"]=traceback.format_exc()
        r["verdict"]="FAILED_WITH_DIAGNOSTICS"
        save("famila_probe_v1.json",r)
        print(json.dumps(r,ensure_ascii=False,indent=2)[:30000],flush=True)

if __name__=="__main__":
    asyncio.run(main())
