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


def parse_price(data):
    value = first_value(data, [
        ("basePrice",), ("price", "value"), ("priceData", "value"),
        ("price",), ("sellingPrice",),
    ])
    try:
        price = float(str(value).replace(",", "."))
        return price if price > 0 else 0.0
    except Exception:
        return 0.0


def product_summary(body):
    products, seen = [], set()
    for raw in PRODUCT_RE.findall(body):
        try:
            data = json.loads(htmlmod.unescape(raw))
        except Exception:
            continue
        name = first_value(data, [("name",), ("nome",), ("productName",)]) or ""
        code = first_value(data, [("code",), ("productCode",), ("sku",)]) or ""
        price = parse_price(data)
        key = (str(code), str(name), price)
        if key in seen:
            continue
        seen.add(key)
        products.append({
            "code": str(code), "name": str(name), "price": price,
            "qty": first_value(data, [("netQuantity",), ("quantity",)]),
            "unit": first_value(data, [("netQuantityUm",), ("unit",)]),
        })
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


def sanitise_selection_request(req):
    if "set-ecaccess" not in req.url:
        return None
    parsed = parse_qs(req.post_data or "", keep_blank_values=True)
    store = (parsed.get("pointOfServiceId") or parsed.get("pointofserviceid") or [None])[0]
    token = (parsed.get("protectionToken") or parsed.get("protectiontoken") or [""])[0]
    return {
        "url": req.url,
        "method": req.method,
        "pointOfServiceId": store,
        "has_protection_token": bool(token),
        "protection_token_length": len(token),
    }


async def snapshot(page):
    return await page.evaluate("""() => {
      const visible = el => {
        const s=getComputedStyle(el), r=el.getBoundingClientRect();
        return s.visibility!=='hidden' && s.display!=='none' && r.width>0 && r.height>0;
      };
      const txt = el => (el.innerText||el.textContent||'').trim().replace(/\s+/g,' ').slice(0,240);
      return {
        url: location.href,
        title: document.title,
        inputs: [...document.querySelectorAll('input')].filter(visible).slice(0,30).map(el => ({
          placeholder: el.getAttribute('placeholder'), aria: el.getAttribute('aria-label'),
          name: el.getAttribute('name'), type: el.getAttribute('type')
        })),
        buttons: [...document.querySelectorAll('button,[role="button"],a')].filter(visible)
          .map(txt).filter(Boolean).slice(0,100),
        body_text: (document.body.innerText||'').trim().replace(/\s+/g,' ').slice(0,6000)
      };
    }""")


async def save_snapshot(page, out, name):
    try:
        out["snapshots"][name] = await snapshot(page)
    except Exception as exc:
        out["steps"].append({"step": "snapshot:" + name, "ok": False, "error": str(exc)[:400]})


async def click_first(page, selectors, out, step, timeout=5000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=timeout)
                out["steps"].append({"step": step, "ok": True, "selector": sel})
                await page.wait_for_timeout(1200)
                return True
        except Exception:
            pass
    out["steps"].append({"step": step, "ok": False})
    return False


async def accept_cookies(page, out):
    return await click_first(page, [
        "#onetrust-accept-btn-handler",
        'button:has-text("ACCETTA TUTTI I COOKIE")',
        'button:has-text("Accetta tutti i cookie")',
        'button:has-text("Accetta tutti")',
        'button:has-text("Accetta")',
    ], out, "cookies")


async def address_input(page):
    for sel in [
        'input[placeholder*="Via Mario Rossi" i]',
        'input[placeholder*="indirizzo" i]',
        'input[aria-label*="indirizzo" i]',
        'input[name*="address" i]',
        'input[name*="indirizzo" i]',
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                return loc, sel
        except Exception:
            pass
    return None, None


async def expose_store_flow(page, out):
    inp, sel = await address_input(page)
    if inp:
        return inp, sel
    await click_first(page, [
        'button:has-text("Inizia la spesa")', 'a:has-text("Inizia la spesa")',
        'button:has-text("Modifica negozio")', 'a:has-text("Modifica negozio")',
        'button:has-text("Scegli il negozio")', 'a:has-text("Scegli il negozio")',
        '[aria-label*="negozio" i]', '[aria-label*="punto vendita" i]',
    ], out, "open_store_flow")
    return await address_input(page)


async def choose_address(page, out):
    for sel in [
        '[role="option"]:has-text("Capodrise")',
        'li:has-text("Capodrise")',
        '.pac-item:has-text("Capodrise")',
    ]:
        if await click_first(page, [sel], out, "select_address_suggestion"):
            return True
    try:
        inp, _ = await address_input(page)
        if inp:
            await inp.press("ArrowDown")
            await inp.press("Enter")
            await page.wait_for_timeout(1000)
            out["steps"].append({"step": "select_address_suggestion_keyboard", "ok": True})
            return True
    except Exception as exc:
        out["steps"].append({"step": "select_address_suggestion_keyboard", "ok": False, "error": str(exc)[:300]})
    return False


async def fill_civic(page, out):
    for sel in ['input[placeholder="es. 10"]', 'input[aria-label*="civico" i]', 'input[name*="civic" i]']:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                if not (await loc.input_value()).strip():
                    await loc.fill("1")
                out["steps"].append({"step": "fill_civic", "ok": True, "selector": sel})
                return True
        except Exception:
            pass
    out["steps"].append({"step": "fill_civic", "ok": False, "note": "not requested"})
    return False


async def select_pickup(page, out):
    # Prefer a Seleziona button inside the visible container that actually says Ordina e ritira.
    try:
        cards = page.locator('div,section,article,li')
        for i in range(min(await cards.count(), 700)):
            card = cards.nth(i)
            try:
                if not await card.is_visible():
                    continue
                text = (await card.inner_text()).strip()
                if "Ordina e ritira" not in text or len(text) > 900:
                    continue
                btn = card.locator('button:has-text("Seleziona")').first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=5000)
                    out["steps"].append({"step": "select_pickup", "ok": True, "text": text[:250]})
                    await page.wait_for_timeout(2200)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    # Safe fallback only if exactly one Seleziona is visible.
    try:
        loc = page.locator('button:has-text("Seleziona")')
        visible = [loc.nth(i) for i in range(await loc.count()) if await loc.nth(i).is_visible()]
        if len(visible) == 1:
            await visible[0].click(timeout=5000)
            out["steps"].append({"step": "select_pickup_single_button", "ok": True})
            await page.wait_for_timeout(2200)
            return True
    except Exception:
        pass
    out["steps"].append({"step": "select_pickup", "ok": False})
    return False


async def select_store(page, out):
    # Locate the requested public store and click its normal confirmation control.
    for term in ["Via Retella", "Capodrise", STORE_ID]:
        try:
            matches = page.get_by_text(term, exact=False)
            for i in range(min(await matches.count(), 30)):
                hit = matches.nth(i)
                if not await hit.is_visible():
                    continue
                node = hit
                for _ in range(7):
                    for bsel in [
                        'button:has-text("Conferma il negozio")',
                        'button:has-text("Seleziona")',
                        'button:has-text("Scegli")',
                        'button:has-text("Conferma")',
                    ]:
                        btn = node.locator(bsel).first
                        if await btn.count() and await btn.is_visible():
                            await btn.click(timeout=6000)
                            out["steps"].append({"step": "select_store", "ok": True, "term": term, "button": bsel})
                            await page.wait_for_timeout(4000)
                            return True
                    node = node.locator("..")
        except Exception:
            pass
    return await click_first(page, [
        'button:has-text("Capodrise")', 'a:has-text("Capodrise")',
        '[role="button"]:has-text("Capodrise")',
    ], out, "select_store_card", timeout=6000)


async def select_capodrise_via_ui(page, out):
    inp, selector = await expose_store_flow(page, out)
    if not inp:
        out["steps"].append({"step": "address_input", "ok": False})
        return False
    await inp.fill(ADDRESS_QUERY)
    out["steps"].append({"step": "fill_address", "ok": True, "selector": selector, "value": ADDRESS_QUERY})
    await page.wait_for_timeout(2200)
    await choose_address(page, out)
    await fill_civic(page, out)
    await click_first(page, [
        'button:has-text("Verifica")', 'button:has-text("Continua")', 'button:has-text("Avanti")'
    ], out, "verify_address", timeout=7000)
    await page.wait_for_timeout(2500)
    await save_snapshot(page, out, "after_address_verify")
    await select_pickup(page, out)
    await save_snapshot(page, out, "after_pickup")
    selected = await select_store(page, out)
    await save_snapshot(page, out, "after_store_selection")
    return selected


async def search_check(page, query):
    best = None
    for url in [BASE + "/search?query=" + quote_plus(query), BASE + "/cerca?text=" + quote_plus(query)]:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(1600)
            info = product_summary(await page.content())
            info["url"] = page.url
            info["title"] = await page.title()
            rank = (info["positive_prices"], info["products_found"], len(info["visible_euro_values_sample"]))
            if best is None or rank > best[0]:
                best = (rank, info)
            if info["positive_prices"] > 0:
                break
        except Exception as exc:
            if best is None:
                best = ((0, 0, 0), {"url": url, "products_found": 0, "positive_prices": 0, "examples": [], "error": str(exc)[:400]})
    return best[1] if best else {"products_found": 0, "positive_prices": 0, "examples": []}


async def main():
    out = {
        "store_id": STORE_ID, "store_label": STORE_LABEL, "address_query": ADDRESS_QUERY,
        "method": "normal_public_ui_only", "steps": [], "snapshots": {},
        "selection_requests": [], "selection_responses": [], "queries": {}, "errors": [],
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
            await page.wait_for_timeout(1800)
            await accept_cookies(page, out)
            await save_snapshot(page, out, "initial")
            clicked = await select_capodrise_via_ui(page, out)
            out["steps"].append({"step": "store_ui_clicked", "ok": clicked})

            request = next((x for x in reversed(out["selection_requests"]) if x.get("pointOfServiceId") == STORE_ID), None)
            response = out["selection_responses"][-1] if out["selection_responses"] else None
            out["selection_request_seen"] = bool(request)
            out["selection_store_id"] = request.get("pointOfServiceId") if request else None
            out["selection_has_protection_token"] = bool(request and request.get("has_protection_token"))
            out["selection_status"] = response.get("status") if response else None

            selection_ok = bool(
                request and response and request.get("pointOfServiceId") == STORE_ID
                and request.get("has_protection_token") and 200 <= response["status"] < 300
            )
            if selection_ok:
                for query in ["latte", "pasta", "uova"]:
                    out["queries"][query] = await search_check(page, query)
            else:
                out["steps"].append({
                    "step": "catalog_checks", "ok": False,
                    "note": "skipped: Conad did not confirm store 010548 through its own set-ecaccess request"
                })

            all_priced = selection_ok and all(out["queries"].get(q, {}).get("positive_prices", 0) > 0 for q in ["latte", "pasta", "uova"])
            out["verdict"] = "CAPODRISE_PRICED_VALIDATED" if all_priced else "CAPODRISE_NOT_VALIDATED"
        except Exception as exc:
            out["errors"].append(repr(exc))
            out["verdict"] = "ERROR"
        finally:
            await save_snapshot(page, out, "final")
            try:
                await page.screenshot(path="conad_capodrise_priced_probe.png", full_page=True)
            except Exception as exc:
                out["errors"].append("screenshot: " + str(exc)[:300])
            await browser.close()

    Path("conad_capodrise_priced_probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out["verdict"] == "CAPODRISE_PRICED_VALIDATED"


ok = asyncio.run(main())
sys.exit(0 if ok else 1)
