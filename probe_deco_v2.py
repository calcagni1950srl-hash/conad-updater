import asyncio, json, re
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright

BASE="https://decoacasa.multicedi.it/"
OUT=Path("deco_probe_output")
OUT.mkdir(exist_ok=True)

async def main():
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="it-IT", viewport={"width":1440,"height":1200})
        page=await ctx.new_page()
        network=[]

        async def capture_response(resp):
            try:
                ct=(resp.headers.get("content-type") or "").lower()
                u=resp.url.lower()
                if any(x in u for x in ["api","store","negoz","shop","point","postal","cap","search","delivery","ritiro","catalog","product"]):
                    network.append({"status":resp.status,"url":resp.url,"content_type":ct})
            except: pass

        page.on("response", lambda r: asyncio.create_task(capture_response(r)))

        print("Apro home ufficiale Decò a Casa", flush=True)
        r=await page.goto(BASE,wait_until="domcontentloaded",timeout=60000)
        await page.wait_for_timeout(3000)
        home_html=await page.content()
        home_text=await page.locator("body").inner_text()
        (OUT/"home.html").write_text(home_html,encoding="utf-8")
        (OUT/"home.txt").write_text(home_text,encoding="utf-8")

        links=await page.locator("a").evaluate_all("""
            els => els.map(e => ({text:(e.innerText||'').trim(), href:e.href||''}))
                      .filter(x => x.href)
        """)
        interesting=[x for x in links if any(k in (x["text"]+" "+x["href"]).lower()
                    for k in ["spesa","ritiro","consegna","negoz","punto vendita","caserta","store"])]

        inputs=await page.locator("input").evaluate_all("""
            els => els.map(e => ({
              type:e.type, name:e.name, id:e.id, placeholder:e.placeholder,
              autocomplete:e.autocomplete, value:e.value
            }))
        """)
        buttons=await page.locator("button").evaluate_all("""
            els => els.map(e => ({text:(e.innerText||'').trim(), id:e.id, name:e.name, type:e.type}))
        """)

        # Prova non distruttiva: cerca un campo località/CAP e inserisce "Caserta".
        candidate_selectors=[
            'input[placeholder*="indirizzo" i]','input[placeholder*="cap" i]',
            'input[placeholder*="citt" i]','input[placeholder*="localit" i]',
            'input[name*="address" i]','input[name*="postal" i]',
            'input[id*="address" i]','input[id*="postal" i]'
        ]
        interaction={"attempted":False}
        field=None
        for sel in candidate_selectors:
            loc=page.locator(sel)
            if await loc.count():
                field=loc.first
                break

        if field:
            interaction["attempted"]=True
            interaction["selector_found"]=True
            try:
                await field.fill("Caserta")
                await page.wait_for_timeout(2500)
                suggestions=await page.locator(
                    '[role="option"], .pac-item, li, .autocomplete-suggestion'
                ).evaluate_all("""
                    els => els.map(e => (e.innerText||'').trim())
                              .filter(t => /caserta/i.test(t)).slice(0,30)
                """)
                interaction["suggestions"]=suggestions
            except Exception as e:
                interaction["error"]=str(e)
        else:
            interaction["selector_found"]=False

        # Cerca riferimenti Caserta/81020/81100 nel markup/script.
        refs=[]
        for pat in [r'.{0,120}Caserta.{0,120}', r'.{0,120}81100.{0,120}', r'.{0,120}81020.{0,120}']:
            refs += re.findall(pat, home_html, re.I|re.S)
        refs=[re.sub(r'\s+',' ',x)[:300] for x in refs[:50]]

        scripts=await page.locator("script[src]").evaluate_all("els=>els.map(e=>e.src)")
        result={
            "home_status":r.status if r else None,
            "home_url":page.url,
            "interesting_links":interesting[:100],
            "inputs":inputs,
            "buttons":buttons[:100],
            "interaction":interaction,
            "caserta_refs":refs,
            "script_srcs":scripts,
            "network_candidates":network[-300:],
        }
        (OUT/"deco_probe_v2.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result,ensure_ascii=False,indent=2)[:15000],flush=True)
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
