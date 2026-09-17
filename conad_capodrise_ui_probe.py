import asyncio
import html as htmlmod
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote_plus

from playwright.async_api import async_playwright

BASE = "https://spesaonline.conad.it"
STORE_ID = "010548"
ADDRESS = "Via Retella, 81020 Capodrise CE"
PRODUCT_RE = re.compile(r'data-product="([^"]+)"')


def safe_request(req):
    if "set-ecaccess" not in req.url:
        return None
    form = parse_qs(req.post_data or "", keep_blank_values=True)
    store = (form.get("pointOfServiceId") or [None])[0]
    token = (form.get("protectionToken") or [""])[0]
    return {
        "url": req.url,
        "method": req.method,
        "pointOfServiceId": store,
        "has_protection_token": bool(token),
        "protection_token_length": len(token),
    }


def price_of(data):
    values = [
        data.get("basePrice"),
        data.get("sellingPrice"),
        (data.get("price") or {}).get("value") if isinstance(data.get("price"), dict) else data.get("price"),
        (data.get("priceData") or {}).get("value") if isinstance(data.get("priceData"), dict) else None,
    ]
    for value in values:
        try:
            p = float(str(value).replace(",", "."))
            if p > 0:
                return p
        except Exception:
            pass
    return 0.0


def products(body):
    out, seen = [], set()
    for raw in PRODUCT_RE.findall(body):
        try:
            data = json.loads(htmlmod.unescape(raw))
        except Exception:
            continue
        name = data.get("name") or data.get("nome") or data.get("productName") or ""
        code = data.get("code") or data.get("productCode") or data.get("sku") or ""
        price = price_of(data)
        key = (str(code), str(name), price)
        if key in seen:
            continue
        seen.add(key)
        out.append({"code": str(code), "name": str(name), "price": price})
    return out


async def shot(page, name):
    try:
        await page.screenshot(path=f"conad_{name}.png", full_page=False)
    except Exception:
        pass


async def visible_text(page):
    return await page.evaluate("""() => ({
      url: location.href,
      title: document.title,
      text: (document.body.innerText || '').replace(/\s+/g,' ').slice(0,10000),
      inputs: [...document.querySelectorAll('input')].filter(el => {
        const r=el.getBoundingClientRect(), s=getComputedStyle(el);
        return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
      }).map(el => ({name:el.name, placeholder:el.placeholder, value:(el.value||'').slice(0,150)}))
    })""")


async def click_last_visible(locator, timeout=6000):
    count = await locator.count()
    for i in range(count - 1, -1, -1):
        item = locator.nth(i)
        try:
            if await item.is_visible():
                await item.click(timeout=timeout)
                return True
        except Exception:
            pass
    return False


async def query_check(page, q):
    best = {"products_found": 0, "positive_prices": 0, "examples": []}
    for url in [BASE + "/search?query=" + quote_plus(q), BASE + "/cerca?text=" + quote_plus(q)]:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(1800)
            arr = products(await page.content())
            pos = [p for p in arr if p["price"] > 0]
            cur = {"url": page.url, "products_found": len(arr), "positive_prices": len(pos), "examples": pos[:8]}
            if (cur["positive_prices"], cur["products_found"]) > (best["positive_prices"], best["products_found"]):
                best = cur
            if pos:
                break
        except Exception as exc:
            best["error"] = str(exc)[:400]
    return best


async def main():
    out = {
        "store_id": STORE_ID,
        "address": ADDRESS,
        "method": "normal_public_ui_only",
        "steps": [],
        "snapshots": {},
        "selection_requests": [],
        "selection_responses": [],
        "queries": {},
        "errors": [],
        "verdict": "NOT_RUN",
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(locale="it-IT", viewport={"width": 1440, "height": 1000})
        page = await context.new_page()

        def on_request(req):
            item = safe_request(req)
            if item:
                out["selection_requests"].append(item)

        async def on_response(resp):
            if "set-ecaccess" in resp.url:
                out["selection_responses"].append({"url": resp.url, "status": resp.status})

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(1500)
            try:
                btn = page.locator("#onetrust-accept-btn-handler")
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=5000)
                    out["steps"].append({"step": "cookies", "ok": True})
            except Exception:
                pass

            address = page.locator('input[name="googleInputEntrypageLine1"]')
            await address.fill(ADDRESS)
            out["steps"].append({"step": "fill_address", "ok": True})
            await page.wait_for_timeout(2200)

            suggestion = page.locator('.pac-item:has-text("Capodrise")')
            if not await click_last_visible(suggestion):
                raise RuntimeError("Capodrise autocomplete suggestion not selectable")
            out["steps"].append({"step": "address_suggestion", "ok": True})
            await page.wait_for_timeout(700)

            civic = page.locator('input[name="googleInputEntrypageLine2"]')
            if await civic.count() and await civic.first.is_visible():
                await civic.first.fill("1")
                out["steps"].append({"step": "civic", "ok": True, "value": "1"})

            out["snapshots"]["before_verify"] = await visible_text(page)
            await shot(page, "before_verify")

            verify = page.get_by_text("Verifica", exact=True)
            if not await click_last_visible(verify):
                raise RuntimeError("Visible Verifica control not clickable")
            out["steps"].append({"step": "verify", "ok": True})
            await page.wait_for_timeout(3500)
            out["snapshots"]["after_verify"] = await visible_text(page)
            await shot(page, "after_verify")

            pickup = page.get_by_text("Ordina e ritira", exact=True)
            if not await click_last_visible(pickup):
                raise RuntimeError("Ordina e ritira option not clickable")
            out["steps"].append({"step": "pickup", "ok": True})
            await page.wait_for_timeout(3500)
            out["snapshots"]["store_list"] = await visible_text(page)
            await shot(page, "store_list")

            # Find the Capodrise/Via Retella store card and click its normal UI control.
            hit = page.get_by_text("Via Retella", exact=False)
            selected = False
            for i in range(min(await hit.count(), 20)):
                node = hit.nth(i)
                try:
                    if not await node.is_visible():
                        continue
                    parent = node
                    for _ in range(8):
                        for label in ["Seleziona", "Conferma il negozio", "Scegli", "Conferma"]:
                            control = parent.get_by_text(label, exact=True)
                            if await click_last_visible(control, timeout=5000):
                                selected = True
                                out["steps"].append({"step": "select_store", "ok": True, "near": "Via Retella", "control": label})
                                break
                        if selected:
                            break
                        parent = parent.locator("..")
                    if selected:
                        break
                except Exception:
                    continue

            if not selected:
                cap = page.get_by_text("Capodrise", exact=False)
                if await click_last_visible(cap):
                    selected = True
                    out["steps"].append({"step": "select_store_text", "ok": True})

            await page.wait_for_timeout(5000)
            out["snapshots"]["after_store_click"] = await visible_text(page)
            await shot(page, "after_store_click")

            req = next((r for r in reversed(out["selection_requests"]) if r.get("pointOfServiceId") == STORE_ID), None)
            resp = out["selection_responses"][-1] if out["selection_responses"] else None
            out["selection_request_seen"] = bool(req)
            out["selection_store_id"] = req.get("pointOfServiceId") if req else None
            out["selection_has_protection_token"] = bool(req and req.get("has_protection_token"))
            out["selection_status"] = resp.get("status") if resp else None

            selection_ok = bool(req and resp and req.get("has_protection_token") and 200 <= resp["status"] < 300)
            if selection_ok:
                for q in ["latte", "pasta", "uova"]:
                    out["queries"][q] = await query_check(page, q)

            priced = selection_ok and all(out["queries"].get(q, {}).get("positive_prices", 0) > 0 for q in ["latte", "pasta", "uova"])
            out["verdict"] = "CAPODRISE_PRICED_VALIDATED" if priced else "CAPODRISE_NOT_VALIDATED"
        except Exception as exc:
            out["errors"].append(repr(exc))
            out["verdict"] = "ERROR"
        finally:
            try:
                out["snapshots"]["final"] = await visible_text(page)
                await shot(page, "final")
            except Exception:
                pass
            await browser.close()

    Path("conad_capodrise_priced_probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out["verdict"] == "CAPODRISE_PRICED_VALIDATED"


ok = asyncio.run(main())
sys.exit(0 if ok else 1)
