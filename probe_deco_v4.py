import asyncio, json, traceback
from pathlib import Path
from playwright.async_api import async_playwright

BASE="https://decoacasa.multicedi.it/"
ADDRESS="Via Isonzo 9, 81100 Caserta CE, Italia"
OUT=Path("deco_probe_v4_output"); OUT.mkdir(exist_ok=True)

def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

async def main():
    result={"address":ADDRESS,"steps":[],"network":[],"error":None}
    browser=None
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            ctx=await browser.new_context(locale="it-IT",viewport={"width":1440,"height":1200})
            page=await ctx.new_page()

            async def cap(resp):
                try:
                    u=resp.url.lower()
                    if any(k in u for k in ["store","shop","negoz","point","address","postal","delivery",
                                            "ritiro","spesa","catalog","product","search","api"]):
                        result["network"].append({
                            "status":resp.status,"url":resp.url,
                            "content_type":resp.headers.get("content-type","")
                        })
                except: pass
            page.on("response",lambda r: asyncio.create_task(cap(r)))

            r=await page.goto(BASE,wait_until="domcontentloaded",timeout=60000)
            result["steps"].append({"step":"home","status":r.status if r else None,"url":page.url})
            await page.wait_for_timeout(2500)

            field=page.locator('input[name="addressField1"],#addressField1').first
            if not await field.count(): raise RuntimeError("addressField1 non trovato")
            await field.fill(ADDRESS)
            await page.wait_for_timeout(3000)
            result["steps"].append({"step":"address_filled"})

            pac=page.locator(".pac-item")
            suggestions=[]
            for i in range(await pac.count()):
                suggestions.append((await pac.nth(i).inner_text()).strip())
            save("suggestions.json",suggestions)

            idx=next((i for i,t in enumerate(suggestions) if "caserta" in t.lower()),None)
            if idx is None: raise RuntimeError("Suggerimento Caserta non trovato")

            # V4: non usa click Playwright sulla voce Google. Il widget Places
            # spesso rimuove il nodo durante il click in headless. Usiamo tastiera:
            # ArrowDown fino alla voce corretta + Enter, che attiva l'evento Places.
            await field.focus()
            for _ in range(idx+1):
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(150)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)
            result["steps"].append({"step":"place_selected_keyboard","text":suggestions[idx],"url":page.url})

            # salva SEMPRE lo stato immediatamente dopo la selezione
            (OUT/"after_place.html").write_text(await page.content(),encoding="utf-8")
            (OUT/"after_place.txt").write_text(await page.locator("body").inner_text(),encoding="utf-8")
            await page.screenshot(path=str(OUT/"after_place.png"),full_page=True)

            # Cerca il pulsante pertinente dopo che Places ha valorizzato il campo.
            candidates=[]
            controls=page.locator("button,input[type=submit],a")
            for i in range(await controls.count()):
                el=controls.nth(i)
                try:
                    txt=((await el.inner_text()) or (await el.get_attribute("value")) or "").strip()
                    href=await el.get_attribute("href")
                    if txt and any(k in txt.lower() for k in ["verifica","continua","conferma","cerca",
                                                               "scopri","spesa","ritiro","consegna"]):
                        candidates.append({"i":i,"text":txt,"href":href})
                except: pass
            save("controls.json",candidates)

            clicked=None
            for c in candidates:
                try:
                    el=controls.nth(c["i"])
                    await el.click(timeout=3000)
                    clicked=c
                    break
                except: pass
            result["steps"].append({"step":"post_place_control","clicked":clicked})
            await page.wait_for_timeout(5000)

            result["final_url"]=page.url
            (OUT/"final.html").write_text(await page.content(),encoding="utf-8")
            (OUT/"final.txt").write_text(await page.locator("body").inner_text(),encoding="utf-8")
            await page.screenshot(path=str(OUT/"final.png"),full_page=True)

            links=await page.locator("a").evaluate_all(
                """els=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||''}))
                    .filter(x=>x.href && /spesa|ritiro|consegna|negoz|store|caserta|isonzo/i.test(x.text+' '+x.href))"""
            )
            result["interesting_links"]=links[:200]
            result["verdict"]="PLACE_EVENT_COMPLETED"
            await browser.close()
    except Exception as e:
        result["error"]=f"{type(e).__name__}: {e}"
        result["traceback"]=traceback.format_exc()
        result["verdict"]="FAILED_WITH_DIAGNOSTICS"
    finally:
        save("deco_probe_v4.json",result)
        print(json.dumps(result,ensure_ascii=False,indent=2)[:25000],flush=True)

if __name__=="__main__":
    asyncio.run(main())
