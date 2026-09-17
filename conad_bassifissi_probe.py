import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.conad.it/prodotti-e-marchi/bassi-e-fissi"
OUT = {"requests": [], "responses": [], "clicks": [], "errors": []}

KEYS = ("bassi", "fissi", "product", "prodot", "offer", "offert", "filter", "load", "api", ".json", ".model")
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
                    if any(x in ct for x in ("json", "text", "html")):
                        txt = await resp.text()
                        sample = txt[:2500]
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
            await page.wait_for_timeout(2500)

            initial_text = await page.locator("body").inner_text()
            m = COUNT_RE.search(initial_text)
            OUT["declared_products_initial"] = int(m.group(1)) if m else None
            OUT["initial_price_tokens"] = len(PRICE_RE.findall(initial_text))
            OUT["initial_text_len"] = len(initial_text)

            # The page currently exposes multiple independent "Carica altri" controls.
            # Click every visible one, then repeat until no control remains or nothing changes.
            no_progress = 0
            previous_prices = OUT["initial_price_tokens"]
            for round_no in range(1, 80):
                loc = page.get_by_text("Carica altri", exact=True)
                n = await loc.count()
                visible = []
                for i in range(n):
                    try:
                        if await loc.nth(i).is_visible():
                            visible.append(i)
                    except Exception:
                        pass
                if not visible:
                    break

                clicked = 0
                # reverse order avoids index shifts after DOM mutations
                for i in reversed(visible):
                    try:
                        await loc.nth(i).click(timeout=6000)
                        clicked += 1
                        await page.wait_for_timeout(650)
                    except Exception as e:
                        OUT["errors"].append(f"round {round_no} click {i}: {e!r}")
                await page.wait_for_timeout(1200)
                txt = await page.locator("body").inner_text()
                prices = len(PRICE_RE.findall(txt))
                OUT["clicks"].append({"round": round_no, "visible_before": len(visible), "clicked": clicked, "price_tokens": prices, "text_len": len(txt)})
                if prices <= previous_prices:
                    no_progress += 1
                else:
                    no_progress = 0
                previous_prices = prices
                if no_progress >= 3:
                    break

            final_text = await page.locator("body").inner_text()
            OUT["final_price_tokens"] = len(PRICE_RE.findall(final_text))
            OUT["final_text_len"] = len(final_text)
            OUT["remaining_load_more"] = await page.get_by_text("Carica altri", exact=True).count()
            OUT["body_excerpt"] = final_text[:12000]
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
        "initial_prices": OUT.get("initial_price_tokens"),
        "final_prices": OUT.get("final_price_tokens"),
        "requests": OUT.get("request_count", 0),
        "responses": OUT.get("response_count", 0),
        "click_rounds": len(OUT.get("clicks", [])),
        "errors": OUT.get("errors", [])[-3:],
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
