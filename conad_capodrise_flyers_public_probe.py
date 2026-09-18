import asyncio, json, re
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright

URL="https://www.conad.it/ricerca-negozi/conad-superstore-via-retella-ex-giarddel-sole-snc-81020-capodrise--010548"
OUT={"requests":[],"responses":[],"dom_links":[],"regex_urls":[],"errors":[]}

KEYS=("volantin","flyer","leaflet","010548","assets/common/volantini","offert")

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="it-IT",viewport={"width":1440,"height":1200})
        page=await ctx.new_page()

        def interesting(url):
            u=url.lower()
            return any(k in u for k in KEYS)

        def on_req(req):
            if interesting(req.url):
                OUT["requests"].append({"url":req.url,"method":req.method,"type":req.resource_type,"post_data":req.post_data})
        async def on_resp(resp):
            if interesting(resp.url):
                row={"url":resp.url,"status":resp.status}
                try:
                    ct=(resp.headers.get("content-type") or "").lower()
                    row["content_type"]=ct
                    if any(x in ct for x in ("json","text","html","javascript")):
                        txt=await resp.text()
                        row["sample"]=txt[:12000]
                except Exception as e:
                    row["error"]=repr(e)
                OUT["responses"].append(row)

        page.on("request",on_req)
        page.on("response",lambda r: asyncio.create_task(on_resp(r)))

        try:
            await page.goto(URL,wait_until="domcontentloaded",timeout=90000)
            await page.wait_for_timeout(1800)
            for sel in ("#onetrust-reject-all-handler","#onetrust-accept-btn-handler"):
                try:
                    loc=page.locator(sel)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click(force=True,timeout=3000)
                        await page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            for y in range(0,18000,900):
                await page.evaluate("y=>window.scrollTo(0,y)",y)
                await page.wait_for_timeout(80)

            OUT["dom_links"]=await page.evaluate("""() => Array.from(document.querySelectorAll('a')).map(a=>({text:(a.innerText||'').trim().slice(0,300),href:a.href||'',title:a.getAttribute('title')||'',aria:a.getAttribute('aria-label')||'',cls:a.className||''})).filter(x=>/volantin|offert|flyer|leaflet|pdf/i.test((x.text||'')+' '+(x.href||'')+' '+(x.title||'')+' '+(x.aria||''))).slice(0,250)""")
            html=await page.content()
            pats=[
              r'https?://[^"\'<>\s]+\.pdf(?:\?[^"\'<>\s]*)?',
              r'/assets/common/volantini/[^"\'<>\s]+',
              r'https?://[^"\'<>\s]*(?:volantin|flyer|leaflet)[^"\'<>\s]*'
            ]
            urls=[]
            for pat in pats:
                for m in re.findall(pat,html,re.I):
                    u=urljoin(URL,m)
                    if u not in urls: urls.append(u)
            OUT["regex_urls"]=urls[:300]
            OUT["body_excerpt"]=(await page.locator("body").inner_text())[:30000]
            OUT["html_markers"]={
              "010548": html.lower().count("010548"),
              "volantini": html.lower().count("volantin"),
              "pdf": html.lower().count(".pdf"),
              "pac": html.lower().count("pac"),
            }
            await page.screenshot(path="conad_capodrise_flyers_public_probe.png",full_page=False)
        except Exception as e:
            OUT["errors"].append(repr(e))
        await browser.close()

    Path("conad_capodrise_flyers_public_probe.json").write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
      "dom_links":len(OUT.get("dom_links",[])),
      "regex_urls":len(OUT.get("regex_urls",[])),
      "requests":len(OUT.get("requests",[])),
      "responses":len(OUT.get("responses",[])),
      "markers":OUT.get("html_markers"),
      "errors":OUT.get("errors")
    },ensure_ascii=False))

asyncio.run(main())
