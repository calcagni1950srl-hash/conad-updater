import asyncio,json,re
from pathlib import Path
from playwright.async_api import async_playwright

URL="https://www.conad.it/ricerca-negozi/conad-superstore-via-retella-ex-giarddel-sole-snc-81020-capodrise--010548"
OUT={"before":{},"after":{},"requests":[],"responses":[],"errors":[]}

def interesting(url, body=""):
    s=(url+" "+body[:5000]).lower()
    return any(k in s for k in ("010548","offer","offert","promo","product","prodot","loader","store"))

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="it-IT",viewport={"width":1440,"height":1200})
        page=await ctx.new_page()

        def on_req(req):
            data=req.post_data or ""
            if "conad.it" in req.url and interesting(req.url,data):
                OUT["requests"].append({"method":req.method,"url":req.url,"type":req.resource_type,"post_data":data[:12000]})

        async def on_resp(resp):
            if "conad.it" not in resp.url:
                return
            try:
                ct=resp.headers.get("content-type") or ""
                body=""
                if any(x in ct.lower() for x in ("json","text","html","javascript")):
                    body=await resp.text()
                if interesting(resp.url,body):
                    OUT["responses"].append({"status":resp.status,"url":resp.url,"content_type":ct,"body":body[:40000]})
            except Exception as e:
                OUT["responses"].append({"status":resp.status,"url":resp.url,"error":repr(e)})

        page.on("request",on_req)
        page.on("response",lambda r: asyncio.create_task(on_resp(r)))

        try:
            await page.goto(URL,wait_until="domcontentloaded",timeout=90000)
            await page.wait_for_timeout(2200)
            for sel in ("#onetrust-reject-all-handler","#onetrust-accept-btn-handler"):
                try:
                    loc=page.locator(sel)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click(force=True,timeout=3000)
                        await page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            comp=page.locator('.rt074-in-this-store').first
            if not await comp.count():
                raise RuntimeError("rt074-in-this-store non trovato")
            await comp.scroll_into_view_if_needed()
            OUT["before"]=await comp.evaluate("""e=>({
                html:e.outerHTML,
                text:(e.innerText||'').trim(),
                links:Array.from(e.querySelectorAll('a')).map(a=>({text:(a.innerText||'').trim(),href:a.href||'',outer:a.outerHTML})),
                buttons:Array.from(e.querySelectorAll('button')).map(b=>({text:(b.innerText||'').trim(),outer:b.outerHTML}))
            })""")
            before_r=len(OUT["requests"]); before_s=len(OUT["responses"])

            clicked=False
            target=comp.get_by_text("Scopri di più",exact=False)
            for i in range(await target.count()):
                el=target.nth(i)
                try:
                    if await el.is_visible():
                        await el.click(force=True,timeout=5000)
                        clicked=True
                        OUT["clicked"]={"mode":"text","index":i}
                        break
                except Exception:
                    pass
            if not clicked:
                links=comp.locator("a")
                for i in range(await links.count()):
                    el=links.nth(i)
                    try:
                        if await el.is_visible():
                            OUT["clicked"]={"mode":"link","index":i,"href":await el.get_attribute("href")}
                            await el.click(force=True,timeout=5000)
                            clicked=True
                            break
                    except Exception:
                        pass
            if not clicked:
                buttons=comp.locator("button")
                for i in range(await buttons.count()):
                    el=buttons.nth(i)
                    try:
                        if await el.is_visible():
                            OUT["clicked"]={"mode":"button","index":i}
                            await el.click(force=True,timeout=5000)
                            clicked=True
                            break
                    except Exception:
                        pass

            await page.wait_for_timeout(4500)
            OUT["after"]={
                "url":page.url,
                "body":re.sub(r"\s+"," ",(await page.locator("body").inner_text()))[:30000],
                "request_delta":OUT["requests"][before_r:],
                "response_delta":OUT["responses"][before_s:],
            }
            await page.screenshot(path="conad_store_offers_click_probe.png",full_page=False)
        except Exception as e:
            OUT["errors"].append(repr(e))
        finally:
            await browser.close()

    Path("conad_store_offers_click_probe.json").write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "clicked":OUT.get("clicked"),
        "before_links":OUT.get("before",{}).get("links"),
        "before_buttons":OUT.get("before",{}).get("buttons"),
        "after_url":OUT.get("after",{}).get("url"),
        "request_delta":OUT.get("after",{}).get("request_delta"),
        "response_delta_count":len(OUT.get("after",{}).get("response_delta",[])),
        "errors":OUT["errors"]
    },ensure_ascii=False))

asyncio.run(main())
