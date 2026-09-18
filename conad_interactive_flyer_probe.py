import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

URLS = [
    "https://www.conad.it/volantini/Integrativa19-Campania?anacanId=010548",
    "https://www.conad.it/volantini/04-Spunta-Risparmio-Campania?anacanId=010548",
    "https://www.conad.it/volantini/Offerte19-Superstore-Campania?anacanId=010548",
]

OUT = {"pages": []}

KEYWORDS = (
    "volantin", "flyer", "leaflet", "catalog", "product", "offer", "offert",
    "json", "api", "viewer", "publication", "page", "article", "item", "campaign"
)

def interesting(url, content_type=""):
    u = (url or "").lower()
    c = (content_type or "").lower()
    return "json" in c or any(k in u for k in KEYWORDS)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for url in URLS:
            ctx = await browser.new_context(
                locale="it-IT",
                viewport={"width": 1440, "height": 1100},
            )
            page = await ctx.new_page()
            item = {
                "url": url,
                "final_url": None,
                "title": None,
                "requests": [],
                "responses": [],
                "iframes": [],
                "scripts": [],
                "links": [],
                "body_excerpt": "",
                "errors": [],
            }

            def on_req(req):
                if interesting(req.url):
                    item["requests"].append({
                        "url": req.url,
                        "method": req.method,
                        "type": req.resource_type,
                        "post_data": req.post_data,
                    })

            async def on_resp(resp):
                try:
                    ct = resp.headers.get("content-type") or ""
                    if not interesting(resp.url, ct):
                        return
                    row = {
                        "url": resp.url,
                        "status": resp.status,
                        "content_type": ct,
                    }
                    if any(x in ct.lower() for x in ("json", "text", "html", "javascript", "xml")):
                        txt = await resp.text()
                        row["sample"] = txt[:40000]
                    item["responses"].append(row)
                except Exception as e:
                    item["responses"].append({
                        "url": resp.url,
                        "status": resp.status,
                        "error": repr(e),
                    })

            page.on("request", on_req)
            page.on("response", lambda r: asyncio.create_task(on_resp(r)))

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(2500)

                for sel in ("#onetrust-reject-all-handler", "#onetrust-accept-btn-handler"):
                    try:
                        loc = page.locator(sel)
                        if await loc.count() and await loc.first.is_visible():
                            await loc.first.click(force=True, timeout=3000)
                            await page.wait_for_timeout(700)
                            break
                    except Exception:
                        pass

                for y in range(0, 12000, 700):
                    await page.evaluate("y => window.scrollTo(0, y)", y)
                    await page.wait_for_timeout(70)

                item["final_url"] = page.url
                item["title"] = await page.title()
                item["iframes"] = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('iframe'))
                        .map(x => ({
                            src: x.src || x.getAttribute('src') || '',
                            title: x.getAttribute('title') || '',
                            id: x.id || '',
                            cls: x.className || ''
                        }))
                """)
                item["scripts"] = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('script[src]'))
                        .map(x => x.src)
                        .filter(Boolean)
                """)
                item["links"] = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('a[href]'))
                        .map(a => ({
                            text: (a.innerText || '').trim().slice(0, 180),
                            href: a.href || ''
                        }))
                        .filter(x => /volantin|offert|catalog|product|viewer|flyer/i.test(x.text + ' ' + x.href))
                        .slice(0, 200)
                """)
                item["body_excerpt"] = (await page.locator("body").inner_text())[:30000]

                await page.screenshot(
                    path=f"interactive_flyer_{len(OUT['pages'])+1}.png",
                    full_page=False
                )
            except Exception as e:
                item["errors"].append(repr(e))

            OUT["pages"].append(item)
            await ctx.close()

        await browser.close()

    Path("conad_interactive_flyer_probe.json").write_text(
        json.dumps(OUT, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = []
    for item in OUT["pages"]:
        summary.append({
            "url": item["url"],
            "final_url": item["final_url"],
            "requests": len(item["requests"]),
            "responses": len(item["responses"]),
            "iframes": len(item["iframes"]),
            "scripts": len(item["scripts"]),
            "errors": item["errors"],
        })
    print(json.dumps(summary, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
