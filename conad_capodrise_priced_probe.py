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
STORE_LABEL = "Conad Superstore Capodrise"
ADDRESS_QUERY = "Via Retella, 81020 Capodrise CE"
STORE_TEXT = "VIA RETELLA EX GIARD.DEL SOLE"
PRODUCT_RE = re.compile(r'data-product="([^"]+)"')
EURO_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{2}))\s*€")


def first_value(data, paths):
    for path in paths:
        cur = data
        try:
            for key in path:
                cur = cur[key]
            if cur not in (None, ""):
                return cur
        except Exception:
            pass
    return None


def product_summary(body):
    products, seen = [], set()
    for raw in PRODUCT_RE.findall(body):
        try:
            data = json.loads(htmlmod.unescape(raw))
        except Exception:
            continue
        name = first_value(data, [("name",), ("nome",), ("productName",)]) or ""
        code = first_value(data, [("code",), ("productCode",), ("sku",)]) or ""
        value = first_value(data, [("basePrice",), ("price", "value"), ("priceData", "value"), ("price",), ("sellingPrice",)])
        try:
            price = float(str(value).replace(",", "."))
            if price <= 0:
                price = 0.0
        except Exception:
            price = 0.0
        key = (str(code), str(name), price)
        if key in seen:
            continue
        seen.add(key)
        products.append({"code": str(code), "name": str(name), "price": price})
    positive = [p for p in products if p["price"] > 0]
    euros = []
    for raw in EURO_RE.findall(body):
        try:
            value = float(raw.replace(",", "."))
            if value > 0 and value not in euros:
                euros.append(value)
        except Exception:
            pass
    return {
        "products_found": len(products),
        "positive_prices": len(positive),
        "examples": positive[:10],
        "visible_euro_values_sample": euros[:10],
    }


def request_fields(req):
    raw = req.post_data or ""
    try:
        x = json.loads(raw)
        if isinstance(x, dict):
            return x
    except Exception:
        pass
    parsed = parse_qs(raw, keep_blank_values=True)
    return {k: (v[0] if isinstance(v, list) and v else v) for k, v in parsed.items()}


def sanitise_selection_request(req):
    if "set-ecaccess" not in req.url:
        return None
    data = request_fields(req)
    store = data.get("pointOfServiceId") or data.get("pointofserviceid")
    token = data.get("protectionToken") or data.get("protectiontoken") or ""
    return {
        "url": req.url,
        "method": req.method,
        "pointOfServiceId": str(store) if store is not None else None,
        "has_protection_token": bool(token),
        "protection_token_length": len(str(token)) if token else 0,
    }


async def snap(page):
    return await page.evaluate(r"""() => {
      const visible = el => { const s=getComputedStyle(el), r=el.getBoundingClientRect(); return s.visibility!=='hidden' && s.display!=='none' && r.width>0 && r.height>0; };
      const txt = el => (el.innerText||el.textContent||'').trim().replace(/\s+/g,' ').slice(0,500);
      return {
        url: location.href,
        buttons: [...document.querySelectorAll('button,[role="button"],a')].filter(visible).map(txt).filter(Boolean).slice(0,180),
        body_text: (document.body.innerText||'').trim().replace(/\s+/g,' ').slice(0,14000)
      };
    }""")


async def save(page, out, name):
    try:
        out["snapshots"][name] = await snap(page)
    except Exception as exc:
        out["steps"].append({"step": "snapshot:" + name, "ok": False, "error": str(exc)[:300]})


async def click_visible(page, selector, out, step, settle=1200):
    loc = page.locator(selector)
    for i in range(await loc.count() - 1, -1, -1):
        item = loc.nth(i)
        try:
            if await item.is_visible():
                await item.click(timeout=6000)
                out["steps"].append({"step": step, "ok": True, "selector": selector, "index": i})
                await page.wait_for_timeout(settle)
                return True
        except Exception:
            pass
    out["steps"].append({"step": step, "ok": False, "selector": selector})
    return False


async def service_panel_visible(page):
    try:
        t = (await page.locator("body").inner_text()).lower()
        return "come vuoi fare la spesa" in t and "ordina e ritira" in t
    except Exception:
        return False


async def click_capodrise_card(page, out):
    for phrase in [STORE_TEXT, "Conad Superstore VIA RETELLA", "CAPODRISE, 81020"]:
        try:
            hits = page.get_by_text(phrase, exact=False)
            for i in range(await hits.count()):
                hit = hits.nth(i)
                if not await hit.is_visible():
                    continue
                try:
                    await hit.click(timeout=6000)
                    out["steps"].append({"step": "select_store_card", "ok": True, "phrase": phrase, "mode": "text_click", "index": i})
                    await page.wait_for_timeout(1600)
                    return True
                except Exception:
                    pass
                node = hit
                for depth in range(1, 8):
                    try:
                        node = node.locator("..")
                        tag = await node.evaluate("el => el.tagName.toLowerCase()")
                        role = await node.get_attribute("role")
                        tabindex = await node.get_attribute("tabindex")
                        onclick = await node.get_attribute("onclick")
                        if tag in ("button", "a") or role in ("button", "link") or tabindex is not None or onclick is not None:
                            await node.click(timeout=6000)
                            out["steps"].append({"step": "select_store_card", "ok": True, "phrase": phrase, "mode": "ancestor_click", "depth": depth, "tag": tag, "role": role})
                            await page.wait_for_timeout(1600)
                            return True
                    except Exception:
                        continue
        except Exception:
            continue
    out["steps"].append({"step": "select_store_card", "ok": False})
    return False


async def ui_select(page, out):
    inp = page.locator('input[name="googleInputEntrypageLine1"]').first
    if not await inp.count() or not await inp.is_visible():
        return False
    await inp.fill(ADDRESS_QUERY)
    out["steps"].append({"step": "fill_address", "ok": True})
    await page.wait_for_timeout(2200)
    if not await click_visible(page, '.pac-item:has-text("Capodrise")', out, "select_address", 1000):
        return False

    civic = page.locator('input[name="googleInputEntrypageLine2"]').first
    if await civic.count() and await civic.is_visible():
        await civic.fill("1")
        out["steps"].append({"step": "fill_civic", "ok": True})

    if not await service_panel_visible(page):
        await click_visible(page, 'button:has-text("Verifica")', out, "verify_address", 2500)
    panel = await service_panel_visible(page)
    out["steps"].append({"step": "service_panel", "ok": panel})
    if not panel:
        return False

    if not await click_visible(page, 'button:has-text("Seleziona")', out, "select_pickup", 2500):
        return False
    await save(page, out, "pickup_store_list")

    if not await click_capodrise_card(page, out):
        await save(page, out, "store_card_not_opened")
        return False
    await save(page, out, "after_store_card_click")

    # Opening the card only expands its details. Conad then exposes the real
    # visible confirmation control; this click is what should trigger its
    # official set-ecaccess request and generate the enterprise protection token.
    confirmed = await click_visible(
        page,
        'button:has-text("Conferma il negozio")',
        out,
        "confirm_store",
        4500,
    )
    await save(page, out, "after_store_confirm")
    return confirmed


async def search_check(page, query):
    best = None
    for url in [BASE + "/search?query=" + quote_plus(query), BASE + "/cerca?text=" + quote_plus(query)]:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(1800)
            info = product_summary(await page.content())
            info["url"] = page.url
            rank = (info["positive_prices"], info["products_found"], len(info["visible_euro_values_sample"]))
            if best is None or rank > best[0]:
                best = (rank, info)
            if info["positive_prices"] > 0:
                break
        except Exception as exc:
            if best is None:
                best = ((0, 0, 0), {"url": url, "products_found": 0, "positive_prices": 0, "examples": [], "error": str(exc)[:300]})
    return best[1] if best else {"products_found": 0, "positive_prices": 0, "examples": []}


async def main():
    out = {
        "store_id": STORE_ID,
        "store_label": STORE_LABEL,
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
            try:
                item = sanitise_selection_request(req)
                if item:
                    out["selection_requests"].append(item)
            except Exception as exc:
                out["errors"].append("request_listener: " + str(exc)[:300])

        async def on_response(resp):
            try:
                if "set-ecaccess" in resp.url:
                    out["selection_responses"].append({"url": resp.url, "status": resp.status})
            except Exception as exc:
                out["errors"].append("response_listener: " + str(exc)[:300])

        page.on("request", on_request)
        page.on("response", on_response)
        try:
            await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(1600)
            await click_visible(page, "#onetrust-accept-btn-handler", out, "cookies", 700)
            clicked = await ui_select(page, out)
            out["steps"].append({"step": "store_ui_clicked", "ok": clicked})
            await page.wait_for_timeout(2500)

            request = next((x for x in reversed(out["selection_requests"]) if x.get("pointOfServiceId") == STORE_ID), None)
            response = out["selection_responses"][-1] if out["selection_responses"] else None
            out["selection_request_seen"] = bool(request)
            out["selection_store_id"] = request.get("pointOfServiceId") if request else None
            out["selection_has_protection_token"] = bool(request and request.get("has_protection_token"))
            out["selection_status"] = response.get("status") if response else None

            selection_ok = bool(
                request and response
                and request.get("pointOfServiceId") == STORE_ID
                and request.get("has_protection_token")
                and 200 <= response["status"] < 300
            )
            if selection_ok:
                for query in ["latte", "pasta", "uova"]:
                    out["queries"][query] = await search_check(page, query)
            else:
                out["steps"].append({"step": "catalog_checks", "ok": False, "note": "store 010548 not yet confirmed by Conad set-ecaccess"})

            all_priced = selection_ok and all(
                out["queries"].get(q, {}).get("positive_prices", 0) > 0
                for q in ["latte", "pasta", "uova"]
            )
            out["verdict"] = "CAPODRISE_PRICED_VALIDATED" if all_priced else "CAPODRISE_NOT_VALIDATED"
        except Exception as exc:
            out["errors"].append(repr(exc))
            out["verdict"] = "ERROR"
        finally:
            await save(page, out, "final")
            try:
                await page.screenshot(path="conad_capodrise_priced_probe.png", full_page=True)
            except Exception as exc:
                out["errors"].append("screenshot: " + str(exc)[:300])
            await browser.close()

    Path("conad_capodrise_priced_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out["verdict"] == "CAPODRISE_PRICED_VALIDATED"


sys.exit(0 if asyncio.run(main()) else 1)
