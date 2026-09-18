import asyncio, json, re, html as htmlmod
from pathlib import Path
from playwright.async_api import async_playwright

URL="https://spesaonline.conad.it/bassi-e-fissi"
PRODUCT_RE=re.compile(r'data-product="([^"]+)"')
TOTAL_RE=re.compile(r'<b class="results">\s*([\d.]+)\s+risultat(?:o|i)',re.I)
OUT={"requests":[],"responses":[],"clicks":[],"errors":[]}

def products(body):
    out=[]
    for raw in PRODUCT_RE.findall(body):
        try:
            out.append(json.loads(htmlmod.unescape(raw)))
        except Exception:
            pass
    return out

def total(body):
    m=TOTAL_RE.search(body)
    return int(m.group(1).replace(".","")) if m else None

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="it-IT",viewport={"width":1440,"height":1200})
        page=await ctx.new_page()

        def on_req(req):
            if req.resource_type in ("xhr","fetch","document") and ("loader" in req.url or "bassi-e-fissi" in req.url):
                OUT["requests"].append({"url":req.url,"method":req.method,"post_data":req.post_data,"type":req.resource_type})
        async def on_resp(resp):
            if "loader" in resp.url or "bassi-e-fissi" in resp.url:
                try:
                    txt=await resp.text()
                    OUT["responses"].append({"url":resp.url,"status":resp.status,"len":len(txt),"products":len(products(txt)),"total":total(txt),"sample":txt[:800]})
                except Exception as e:
                    OUT["responses"].append({"url":resp.url,"status":resp.status,"error":repr(e)})
        page.on("request",on_req)
        page.on("response",lambda r: asyncio.create_task(on_resp(r)))

        try:
            await page.goto(URL,wait_until="domcontentloaded",timeout=90000)
            await page.wait_for_timeout(1200)
            for sel in ["#onetrust-reject-all-handler","#onetrust-accept-btn-handler"]:
                try:
                    loc=page.locator(sel)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click(force=True,timeout=3000)
                        break
                except Exception:
                    pass
            await page.wait_for_timeout(800)
            body=await page.content()
            OUT["initial"]={"url":page.url,"total":total(body),"products":len(products(body)),"positive":sum(1 for x in products(body) if float(x.get("basePrice") or 0)>0)}
            OUT["pagination"]=await page.evaluate("""() => Array.from(document.querySelectorAll('a,button')).map((e,i)=>({i,tag:e.tagName,text:(e.innerText||'').trim(),href:e.getAttribute('href'),dataPage:e.getAttribute('data-page'),aria:e.getAttribute('aria-label'),cls:e.className||''})).filter(x=>x.dataPage||/successiva|next|pagina/i.test((x.text||'')+' '+(x.aria||''))||((x.href||'').includes('page='))).slice(0,100)""")

            clicked=False
            loc=page.locator('a[data-page="2"]')
            if await loc.count():
                await loc.first.click(force=True,timeout=6000); clicked=True
                OUT["clicks"].append("a[data-page=2]")
            if not clicked:
                loc=page.locator('a[aria-label*="Successiva" i], button[aria-label*="Successiva" i]')
                if await loc.count():
                    await loc.first.click(force=True,timeout=6000); clicked=True
                    OUT["clicks"].append("aria-successiva")
            if not clicked:
                loc=page.locator('a[href*="page=2"]')
                if await loc.count():
                    await loc.first.click(force=True,timeout=6000); clicked=True
                    OUT["clicks"].append("href-page2")
            await page.wait_for_timeout(1800)
            body2=await page.content()
            OUT["after"]={"url":page.url,"total":total(body2),"products":len(products(body2)),"positive":sum(1 for x in products(body2) if float(x.get("basePrice") or 0)>0)}
            await page.screenshot(path="conad_bassifissi_spesa_probe.png",full_page=False)
        except Exception as e:
            OUT["errors"].append(repr(e))
        await browser.close()
    Path("conad_bassifissi_spesa_probe.json").write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"initial":OUT.get("initial"),"after":OUT.get("after"),"clicks":OUT.get("clicks"),"requests":len(OUT.get("requests",[])),"responses":len(OUT.get("responses",[])),"errors":OUT.get("errors")},ensure_ascii=False))

asyncio.run(main())
