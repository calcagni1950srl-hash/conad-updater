import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

BASE="https://decoacasa.multicedi.it/"
ADDRESS="Via Isonzo 9, 81100 Caserta CE, Italia"
OUT=Path("deco_probe_v3_output"); OUT.mkdir(exist_ok=True)

async def main():
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="it-IT", viewport={"width":1440,"height":1200})
        page=await ctx.new_page()
        net=[]

        async def cap(resp):
            try:
                u=resp.url
                lu=u.lower()
                ct=(resp.headers.get("content-type") or "").lower()
                if any(k in lu for k in ["store","shop","negoz","point","address","postal","delivery",
                                         "ritiro","spesa","catalog","product","search","api"]):
                    net.append({"status":resp.status,"url":u,"content_type":ct})
            except: pass
        page.on("response",lambda r: asyncio.create_task(cap(r)))

        r=await page.goto(BASE,wait_until="domcontentloaded",timeout=60000)
        await page.wait_for_timeout(2500)

        field=page.locator('input[name="addressField1"], #addressField1').first
        if not await field.count():
            raise RuntimeError("Campo ufficiale addressField1 non trovato")

        await field.fill(ADDRESS)
        await page.wait_for_timeout(3000)

        suggestions=await page.locator('.pac-item,[role="option"]').evaluate_all(
            """els=>els.map((e,i)=>({i,text:(e.innerText||e.textContent||'').trim()}))"""
        )
        (OUT/"suggestions.json").write_text(json.dumps(suggestions,ensure_ascii=False,indent=2),encoding="utf-8")

        chosen=None
        for sel in ['.pac-item','[role="option"]']:
            loc=page.locator(sel)
            n=await loc.count()
            for i in range(n):
                txt=(await loc.nth(i).inner_text()).strip()
                if "caserta" in txt.lower():
                    chosen={"selector":sel,"index":i,"text":txt}
                    await loc.nth(i).click()
                    break
            if chosen: break

        if not chosen:
            raise RuntimeError(f"Nessun suggerimento Google Places di Caserta selezionabile. Suggerimenti: {suggestions[:10]}")

        await page.wait_for_timeout(1200)

        # Clicca solo un controllo esplicitamente collegato alla verifica/consegna/ritiro.
        buttons=page.locator("button,input[type=submit]")
        clicked=None
        for i in range(await buttons.count()):
            el=buttons.nth(i)
            txt=((await el.inner_text()) if await el.evaluate("e=>e.tagName==='BUTTON'") else (await el.get_attribute("value") or "")).strip()
            if any(k in txt.lower() for k in ["verifica","continua","conferma","cerca","scopri","spesa","ritiro","consegna"]):
                try:
                    await el.click(timeout=3000)
                    clicked=txt
                    break
                except: pass

        await page.wait_for_timeout(5000)
        html=await page.content()
        text=await page.locator("body").inner_text()
        (OUT/"after_selection.html").write_text(html,encoding="utf-8")
        (OUT/"after_selection.txt").write_text(text,encoding="utf-8")

        links=await page.locator("a").evaluate_all(
            """els=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||''}))
                    .filter(x=>x.href && /spesa|ritiro|consegna|negoz|store|caserta/i.test(x.text+' '+x.href))"""
        )
        refs=[]
        for pat in [r'.{0,150}Caserta.{0,150}',r'.{0,150}Via Isonzo.{0,150}',r'.{0,150}81100.{0,150}']:
            refs += re.findall(pat,html,re.I|re.S)
        refs=[re.sub(r'\s+',' ',x)[:350] for x in refs[:100]]

        cookies=await ctx.cookies()
        safe_cookies=[{"name":c["name"],"domain":c["domain"],"path":c["path"]} for c in cookies]

        result={
            "home_status":r.status if r else None,
            "address_used":ADDRESS,
            "chosen_suggestion":chosen,
            "clicked_control":clicked,
            "final_url":page.url,
            "interesting_links":links[:150],
            "caserta_refs":refs,
            "network_candidates":net[-400:],
            "cookie_names_only":safe_cookies
        }
        # Non dichiara successo catalogo: V3 serve a provare la selezione e scoprire
        # il contesto ufficiale restituito dal sito.
        result["verdict"]="ADDRESS_SELECTED_AND_CONTEXT_CAPTURED" if chosen else "NOT_VALIDATED"
        (OUT/"deco_probe_v3.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result,ensure_ascii=False,indent=2)[:20000],flush=True)
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
