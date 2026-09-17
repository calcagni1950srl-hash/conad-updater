import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.conad.it/prodotti-e-marchi/bassi-e-fissi"
OUT = {"requests": [], "responses": [], "clicks": [], "errors": []}

KEYS = ("bassi", "fissi", "product", "prodot", "offer", "offert", "filter", "load", "api", ".json", ".model", "offset", "limit", "page")
PRICE_RE = re.compile(r"\b\d{1,3},\d{2}\s*€")
COUNT_RE = re.compile(r"(\d+)\s+prodotti", re.I)


def interesting(url: str) -> bool:
    u = url.lower()
    return any(k in u for k in KEYS)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="it-IT", viewport={"width": 1440, "height": 1200})
        page = await ctx.new_page()

        def on_req(req):
            if req.resource_type in ("xhr", "fetch") and interesting(req.url):
                OUT["requests"].append({
                    "url": req.url,
                    "method": req.method,
                    "post_data": req.post_data,
                    "resource_type": req.resource_type,
                })

        async def on_resp(resp):
            if interesting(resp.url):
                try:
                    ct = (resp.headers.get("content-type") or "").lower()
                    sample = ""
                    if any(x in ct for x in ("json", "text", "html", "javascript")):
                        txt = await resp.text()
                        sample = txt[:3000]
                    OUT["responses"].append({
                        "url": resp.url,
                        "status": resp.status,
                        "content_type": ct,
                        "sample": sample,
                    })
                except Exception as e:
                    OUT["responses"].append({"url": resp.url, "status": resp.status, "error": repr(e)})

        page.on("request", on_req)
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=90000)
            try:
                c = page.locator("#onetrust-accept-btn-handler")
                if await c.count() and await c.first.is_visible():
                    await c.first.click(timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            # Trigger lazy components without interacting with protected services.
            for y in range(0, 12000, 900):
                await page.evaluate("y => window.scrollTo(0, y)", y)
                await page.wait_for_timeout(120)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(1000)

            initial_text = await page.locator("body").inner_text()
            m = COUNT_RE.search(initial_text)
            OUT["declared_products_initial"] = int(m.group(1)) if m else None
            OUT["initial_price_tokens"] = len(PRICE_RE.findall(initial_text))
            OUT["initial_text_len"] = len(initial_text)

            # Inspect loader-related DOM configuration and inline scripts.
            OUT["dom_loader_candidates"] = await page.evaluate("""
                () => {
                  const rx = /(carica|load|bassi|fissi|product|prodot|offert|filter|api|json|offset|limit|page)/i;
                  const out = [];
                  for (const el of document.querySelectorAll('*')) {
                    const attrs = Array.from(el.attributes || []).map(a => [a.name, a.value]);
                    const attrText = attrs.map(x => x.join('=')).join(' ');
                    const txt = (el.innerText || '').trim().replace(/\s+/g,' ');
                    if (rx.test(attrText) || /carica\s+altri/i.test(txt)) {
                      out.push({tag: el.tagName, id: el.id || '', cls: el.className || '', attrs, text: txt.slice(0,300)});
                    }
                    if (out.length >= 500) break;
                  }
                  return out;
                }
            """)
            OUT["scripts"] = await page.evaluate("""
                () => Array.from(document.scripts).map(s => ({src:s.src || '', text:(s.textContent||'').slice(0,6000)}))
                    .filter(x => /(bassi|fissi|product|prodot|load|filter|offset|limit|api|json)/i.test(x.src+' '+x.text))
                    .slice(0,120)
            """)
            OUT["links"] = await page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]')).map(a => ({text:(a.innerText||'').trim(), href:a.href, cls:a.className||''}))
                    .filter(x => /(bassi|fissi|product|prodot|load|filter|offset|limit|api|json|page)/i.test(x.href+' '+x.text+' '+x.cls))
                    .slice(0,300)
            """)

            # Search broadly for any element that actually contains the visible label.
            labels = await page.evaluate("""
                () => Array.from(document.querySelectorAll('*')).filter(el => /carica\s+altri/i.test((el.innerText||'').trim()))
                    .map(el => ({tag:el.tagName,id:el.id||'',cls:el.className||'',html:el.outerHTML.slice(0,2000)})).slice(0,80)
            """)
            OUT["load_more_labels"] = labels

            # Try semantic and broad selectors after lazy scrolling.
            selectors = [
                'text=Carica altri',
                'button:has-text("Carica")',
                'a:has-text("Carica")',
                '[class*="load"]',
                '[class*="more"]',
                '[data-*]'
            ]
            OUT["selector_counts"] = {}
            for s in selectors[:-1]:
                try:
                    OUT["selector_counts"][s] = await page.locator(s).count()
                except Exception as e:
                    OUT["selector_counts"][s] = f"ERR {e!r}"

            Path("conad_bassifissi_page.html").write_text(await page.content(), encoding="utf-8")
            final_text = await page.locator("body").inner_text()
            OUT["final_price_tokens"] = len(PRICE_RE.findall(final_text))
            OUT["final_text_len"] = len(final_text)
            OUT["body_excerpt"] = final_text[:16000]
            OUT["request_count"] = len(OUT["requests"])
            OUT["response_count"] = len(OUT["responses"])
            await page.screenshot(path="conad_bassifissi_probe.png", full_page=False)
        except Exception as e:
            OUT["errors"].append(repr(e))
        finally:
            await browser.close()

    Path("conad_bassifissi_probe.json").write_text(json.dumps(OUT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "declared": OUT.get("declared_products_initial"),
        "prices": OUT.get("final_price_tokens"),
        "requests": OUT.get("request_count", 0),
        "dom_candidates": len(OUT.get("dom_loader_candidates", [])),
        "load_labels": len(OUT.get("load_more_labels", [])),
        "selector_counts": OUT.get("selector_counts"),
        "errors": OUT.get("errors", [])[-3:],
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
