import asyncio, json, re, traceback
from pathlib import Path
from playwright.async_api import async_playwright

START="https://www.cosicomodo.it/familasud/global/homedeliverystoreselector?redirectUrl=/familasud"
OUT=Path("famila_probe_v3_output"); OUT.mkdir(exist_ok=True)

def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

async def main():
    R={"verdict":"STARTED","network":[]}
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            ctx=await browser.new_context(locale="it-IT",viewport={"width":1440,"height":1100})
            page=await ctx.new_page()

            async def cap(resp):
                try:
                    u=resp.url
                    if resp.request.resource_type in ("xhr","fetch","document") or any(k in u.lower() for k in ["store","search","product","category","autocomplete","location","address","cap","comune"]):
                        entry={"status":resp.status,"method":resp.request.method,"type":resp.request.resource_type,"url":u}
                        try:
                            pd=resp.request.post_data
                            if pd and len(pd)<4000: entry["post_data"]=pd
                        except: pass
                        try:
                            ct=resp.headers.get("content-type","")
                            entry["content_type"]=ct
                            if ("json" in ct or "text" in ct) and resp.status==200:
                                txt=await resp.text()
                                if len(txt)<20000: entry["body_sample"]=txt[:12000]
                        except: pass
                        R["network"].append(entry)
                except: pass
            page.on("response",lambda x: asyncio.create_task(cap(x)))

            resp=await page.goto(START,wait_until="domcontentloaded",timeout=60000)
            R["selector_status"]=resp.status if resp else None
            await page.wait_for_timeout(2500)

            for sel in ['#onetrust-accept-btn-handler','button:has-text("Accetta tutto")','button:has-text("Accetta")']:
                if await page.locator(sel).count():
                    try:
                        if await page.locator(sel).first.is_visible():
                            await page.locator(sel).first.click(timeout=1500)
                            break
                    except: pass

            field=page.locator("#capComuneInput")
            if not await field.count():
                field=page.locator('input[name*="cap" i], input[placeholder*="CAP" i], input[placeholder*="comune" i]').first
            if not await field.count():
                raise RuntimeError("Campo CAP/comune non trovato")

            await field.fill("Caserta")
            await page.wait_for_timeout(3000)
            await page.screenshot(path=str(OUT/"01_autocomplete.png"),full_page=True)
            (OUT/"01_autocomplete.html").write_text(await page.content(),encoding="utf-8")

            # Raccoglie tutte le suggestion visibili e seleziona quella più corta con Caserta.
            suggestions=[]
            candidates=page.locator("li,button,a,div,span")
            for i in range(min(await candidates.count(),1000)):
                el=candidates.nth(i)
                try:
                    if not await el.is_visible(): continue
                    t=" ".join((await el.inner_text()).split())
                except: continue
                if t and "caserta" in t.lower() and len(t)<=180:
                    suggestions.append(t)
            R["caserta_suggestions"]=list(dict.fromkeys(suggestions))[:100]

            chosen=None
            for sel in ["li","button","a","div","span"]:
                loc=page.locator(sel)
                best=None
                for i in range(min(await loc.count(),1000)):
                    el=loc.nth(i)
                    try:
                        if not await el.is_visible(): continue
                        t=" ".join((await el.inner_text()).split())
                    except: continue
                    if "caserta" in t.lower() and 1<=len(t)<=180:
                        if best is None or len(t)<best[0]: best=(len(t),el,t)
                if best:
                    try:
                        await best[1].click(timeout=2500)
                        chosen=best[2]
                        break
                    except: pass
            if not chosen:
                await field.focus()
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")
                chosen="keyboard-first-suggestion"
            R["selected_location"]=chosen
            await page.wait_for_timeout(4000)

            # Premi un eventuale Cerca/Continua.
            for pattern in [r"^Cerca$",r"Continua",r"Conferma",r"Visualizza"]:
                loc=page.get_by_text(re.compile(pattern,re.I))
                done=False
                for i in range(await loc.count()):
                    try:
                        if await loc.nth(i).is_visible():
                            await loc.nth(i).click(timeout=2000)
                            await page.wait_for_timeout(3500)
                            done=True; break
                    except: pass
                if done: break

            await page.screenshot(path=str(OUT/"02_stores.png"),full_page=True)
            stores_html=await page.content()
            (OUT/"02_stores.html").write_text(stores_html,encoding="utf-8")

            # Estrae card candidate dei negozi.
            store_cards=[]
            nodes=page.locator("article,li,section,div")
            for i in range(min(await nodes.count(),2000)):
                el=nodes.nth(i)
                try:
                    if not await el.is_visible(): continue
                    t=" ".join((await el.inner_text()).split())
                except: continue
                lo=t.lower()
                if ("famila" in lo or "borsellino" in lo or "carlo iii" in lo) and 20<=len(t)<=700:
                    hrefs=await el.locator("a").evaluate_all("els=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||''}))")
                    btns=await el.locator("button").evaluate_all("els=>els.map(e=>({text:(e.innerText||'').trim(),id:e.id||'',cls:e.className||''}))")
                    store_cards.append({"text":t,"links":hrefs,"buttons":btns})
            # dedup by text
            uniq=[]
            seen=set()
            for x in store_cards:
                if x["text"] not in seen:
                    seen.add(x["text"]); uniq.append(x)
            R["store_cards"]=uniq[:100]

            # Preferisci Borsellino, altrimenti primo Famila Caserta.
            target=None
            for i in range(min(await nodes.count(),2000)):
                el=nodes.nth(i)
                try:
                    if not await el.is_visible(): continue
                    t=" ".join((await el.inner_text()).split())
                except: continue
                lo=t.lower()
                if "borsellino" in lo and "caserta" in lo and 20<=len(t)<=700:
                    if target is None or len(t)<target[0]: target=(len(t),el,t)
            if target is None:
                for i in range(min(await nodes.count(),2000)):
                    el=nodes.nth(i)
                    try:
                        if not await el.is_visible(): continue
                        t=" ".join((await el.inner_text()).split())
                    except: continue
                    lo=t.lower()
                    if "famila" in lo and "caserta" in lo and 20<=len(t)<=700:
                        if target is None or len(t)<target[0]: target=(len(t),el,t)

            if target:
                R["target_store_text"]=target[2]
                controls=target[1].locator("a,button")
                for i in range(await controls.count()):
                    el=controls.nth(i)
                    try:
                        if not await el.is_visible(): continue
                        t=" ".join((await el.inner_text()).split())
                    except: continue
                    if re.search(r"(scegli|seleziona|ritira|spesa|entra|continua|prenota)",t,re.I):
                        await el.click(timeout=3500)
                        R["clicked_store_control"]=t
                        await page.wait_for_timeout(5000)
                        break

            R["final_url"]=page.url
            final_html=await page.content()
            (OUT/"03_final.html").write_text(final_html,encoding="utf-8")
            await page.screenshot(path=str(OUT/"03_final.png"),full_page=True)

            links=await page.locator("a").evaluate_all("els=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||''})).filter(x=>x.href)")
            body=await page.locator("body").inner_text()

            product_links=[x for x in links if any(k in x["href"].lower() for k in ["/product","/prodot","sku="])]
            category_links=[x for x in links if any(k in x["href"].lower() for k in ["/category","/categoria","/repart","/catalog"])]
            prices=re.findall(r"(?:€\s*)?\d{1,4}[,.]\d{2}(?:\s*€)?",body)

            R["product_links_count"]=len({x["href"] for x in product_links})
            R["product_links_sample"]=product_links[:50]
            R["category_links_count"]=len({x["href"] for x in category_links})
            R["category_links_sample"]=category_links[:80]
            R["price_occurrences"]=len(prices)
            R["price_sample"]=prices[:40]
            R["network_tail"]=R["network"][-400:]

            has_store=bool(R.get("target_store_text"))
            has_catalog=R["product_links_count"]>0 and R["price_occurrences"]>0
            if has_store and has_catalog:
                R["verdict"]="STORE_AND_CATALOG_VALIDATED"
            elif has_store:
                R["verdict"]="STORE_VALIDATED_CATALOG_NOT_YET"
            else:
                R["verdict"]="LOCATION_FLOW_VALIDATED_STORE_NOT_YET"

            save("famila_probe_v3.json",R)
            print(json.dumps(R,ensure_ascii=False,indent=2)[:50000],flush=True)
            await browser.close()
    except Exception as e:
        R["error"]=f"{type(e).__name__}: {e}"
        R["traceback"]=traceback.format_exc()
        R["verdict"]="FAILED_WITH_DIAGNOSTICS"
        save("famila_probe_v3.json",R)
        print(json.dumps(R,ensure_ascii=False,indent=2)[:50000],flush=True)

if __name__=="__main__":
    asyncio.run(main())
