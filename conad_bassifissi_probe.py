import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.conad.it/prodotti-e-marchi/bassi-e-fissi"
OUT = {"requests": [], "responses": [], "clicks": [], "errors": []}

PRICE_RE = re.compile(r"\b\d{1,3},\d{2}\s*€")
COUNT_RE = re.compile(r"(\d+)\s+prodotti", re.I)


def keep_request(req) -> bool:
    if req.resource_type not in ("xhr", "fetch"):
        return False
    u = req.url.lower()
    return "conad.it" in u or "conad" in u


async def dismiss_cookie(page):
    candidates = [
        "#onetrust-reject-all-handler",
        "#onetrust-accept-btn-handler",
        'button:has-text("RIFIUTA TUTTI I COOKIE")',
        'button:has-text("ACCETTA TUTTI I COOKIE")',
        'text=RIFIUTA TUTTI I COOKIE',
        'text=ACCETTA TUTTI I COOKIE',
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel)
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible():
                    await el.click(force=True, timeout=5000)
                    await page.wait_for_timeout(800)
                    OUT["cookie"] = {"dismissed": True, "selector": sel, "index": i}
                    return True
        except Exception as e:
            OUT.setdefault("cookie_attempt_errors", []).append({"selector": sel, "error": repr(e)})
    OUT["cookie"] = {"dismissed": False}
    return False


async def visible_load_more(page):
    selectors = [
        'button:has-text("Carica altri")',
        'a:has-text("Carica altri")',
        'text=Carica altri',
        '[class*="load-more"]',
        '[class*="loadMore"]',
        '[data-action*="load"]',
    ]
    found = []
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(await loc.count()):
                el = loc.nth(i)
                try:
                    if await el.is_visible():
                        found.append((sel, i, el))
                except Exception:
                    pass
        except Exception:
            pass
    return found


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="it-IT", viewport={"width": 1440, "height": 1200})
        page = await ctx.new_page()

        def on_req(req):
            if keep_request(req):
                OUT["requests"].append({
                    "url": req.url,
                    "method": req.method,
                    "post_data": req.post_data,
                    "resource_type": req.resource_type,
                })

        async def on_resp(resp):
            req = resp.request
            if not keep_request(req):
                return
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                sample = ""
                if any(x in ct for x in ("json", "text", "html", "javascript")):
                    txt = await resp.text()
                    sample = txt[:12000]
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
            await page.wait_for_timeout(1800)
            await dismiss_cookie(page)
            await page.wait_for_timeout(1200)

            # Scroll through the list so the real loader is rendered.
            for y in range(0, 30000, 900):
                await page.evaluate("y => window.scrollTo(0, y)", y)
                await page.wait_for_timeout(80)

            initial_text = await page.locator("body").inner_text()
            m = COUNT_RE.search(initial_text)
            OUT["declared_products_initial"] = int(m.group(1)) if m else None
            OUT["initial_price_tokens"] = len(PRICE_RE.findall(initial_text))
            OUT["initial_text_len"] = len(initial_text)

            # Capture exact DOM candidates before clicking.
            OUT["load_more_labels_before"] = await page.evaluate("""
                () => Array.from(document.querySelectorAll('*'))
                    .filter(el => /carica\s+altri/i.test((el.innerText||'').trim()) || /(load.more|load-more|loadmore)/i.test((el.className||'')+' '+Array.from(el.attributes||[]).map(a=>a.name+'='+a.value).join(' ')))
                    .map(el => ({tag:el.tagName,id:el.id||'',cls:el.className||'',attrs:Array.from(el.attributes||[]).map(a=>[a.name,a.value]),text:(el.innerText||'').trim().slice(0,300),html:el.outerHTML.slice(0,3000)}))
                    .slice(0,120)
            """)

            # Click the visible public loader repeatedly and record each network delta.
            for round_no in range(1, 40):
                before_text = await page.locator("body").inner_text()
                before_prices = len(PRICE_RE.findall(before_text))
                before_req = len(OUT["requests"])
                before_resp = len(OUT["responses"])

                found = await visible_load_more(page)
                if not found:
                    OUT["clicks"].append({"round": round_no, "result": "no_visible_loader", "prices": before_prices})
                    break

                sel, idx, el = found[-1]
                try:
                    await el.scroll_into_view_if_needed(timeout=5000)
                    await el.click(force=True, timeout=8000)
                    await page.wait_for_timeout(1600)
                    after_text = await page.locator("body").inner_text()
                    after_prices = len(PRICE_RE.findall(after_text))
                    OUT["clicks"].append({
                        "round": round_no,
                        "selector": sel,
                        "index": idx,
                        "before_prices": before_prices,
                        "after_prices": after_prices,
                        "request_delta": OUT["requests"][before_req:],
                        "response_delta": OUT["responses"][before_resp:],
                    })
                    if after_prices <= before_prices:
                        break
                except Exception as e:
                    OUT["clicks"].append({"round": round_no, "selector": sel, "index": idx, "error": repr(e)})
                    break

            final_text = await page.locator("body").inner_text()
            OUT["final_price_tokens"] = len(PRICE_RE.findall(final_text))
            OUT["final_text_len"] = len(final_text)
            OUT["request_count"] = len(OUT["requests"])
            OUT["response_count"] = len(OUT["responses"])
            OUT["body_excerpt"] = final_text[:24000]
            OUT["load_more_labels_after"] = await page.evaluate("""
                () => Array.from(document.querySelectorAll('*'))
                    .filter(el => /carica\s+altri/i.test((el.innerText||'').trim()))
                    .map(el => ({tag:el.tagName,id:el.id||'',cls:el.className||'',text:(el.innerText||'').trim().slice(0,300),html:el.outerHTML.slice(0,2500)})).slice(0,80)
            """)

            Path("conad_bassifissi_page.html").write_text(await page.content(), encoding="utf-8")
            await page.screenshot(path="conad_bassifissi_probe.png", full_page=False)
        except Exception as e:
            OUT["errors"].append(repr(e))
        finally:
            await browser.close()

    Path("conad_bassifissi_probe.json").write_text(json.dumps(OUT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "declared": OUT.get("declared_products_initial"),
        "cookie": OUT.get("cookie"),
        "initial_prices": OUT.get("initial_price_tokens"),
        "final_prices": OUT.get("final_price_tokens"),
        "click_rounds": len(OUT.get("clicks", [])),
        "requests": OUT.get("request_count", 0),
        "responses": OUT.get("response_count", 0),
        "errors": OUT.get("errors", [])[-3:],
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
