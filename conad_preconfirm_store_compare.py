import asyncio
import html as htmlmod
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright

BASE = 'https://spesaonline.conad.it'
ADDRESS = 'Via Retella, 81020 Capodrise CE'
STORES = {
    'capodrise': {'id': '010548', 'text': 'VIA RETELLA EX GIARD.DEL SOLE'},
    'smcv': {'id': '009919', 'text': 'VIA GRAN BRETAGNA'},
}
QUERIES = ['latte', 'pasta', 'uova']
PRODUCT_RE = re.compile(r'data-product="([^"]+)"')
OUT = {'stores': {}, 'errors': []}


def parse_products(body):
    out = []
    for raw in PRODUCT_RE.findall(body):
        try:
            p = json.loads(htmlmod.unescape(raw))
        except Exception:
            continue
        name = p.get('nome') or p.get('name') or ''
        code = str(p.get('code') or '')
        try:
            price = float(p.get('basePrice') or 0)
        except Exception:
            price = 0.0
        if code or name:
            out.append({
                'code': code,
                'name': name,
                'price': price,
                'qty': p.get('netQuantity'),
                'unit': p.get('netQuantityUm'),
            })
    return out


async def click_visible(page, selector, settle=1200):
    loc = page.locator(selector)
    for i in range(await loc.count() - 1, -1, -1):
        el = loc.nth(i)
        try:
            if await el.is_visible():
                await el.click(timeout=7000)
                await page.wait_for_timeout(settle)
                return True
        except Exception:
            pass
    return False


async def prepare_store(ctx, key, store):
    page = await ctx.new_page()
    result = {'id': store['id'], 'text': store['text'], 'queries': {}, 'steps': []}
    try:
        await page.goto(BASE + '/', wait_until='domcontentloaded', timeout=90000)
        await page.wait_for_timeout(1800)
        await click_visible(page, '#onetrust-accept-btn-handler', 500)
        inp = page.locator('input[name="googleInputEntrypageLine1"]').first
        await inp.fill(ADDRESS)
        result['steps'].append('address_filled')
        await page.wait_for_timeout(2200)
        if not await click_visible(page, '.pac-item:has-text("Capodrise")', 1000):
            raise RuntimeError('address autocomplete failed')
        civic = page.locator('input[name="googleInputEntrypageLine2"]').first
        if await civic.count() and await civic.is_visible():
            await civic.fill('1')
        body = (await page.locator('body').inner_text()).lower()
        if 'come vuoi fare la spesa' not in body:
            await click_visible(page, 'button:has-text("Verifica")', 2200)
        if not await click_visible(page, 'button:has-text("Seleziona")', 2200):
            raise RuntimeError('pickup selection failed')
        hit = page.get_by_text(store['text'], exact=False).first
        if not await hit.count() or not await hit.is_visible():
            raise RuntimeError(f"store card not visible: {store['text']}")
        await hit.click(timeout=7000)
        await page.wait_for_timeout(1200)
        result['steps'].append('store_card_opened')

        snap = await page.evaluate("() => ({local:{...localStorage}, session:{...sessionStorage}})")
        selected = snap.get('local', {}).get('storeSelected', '')
        result['storeSelected'] = selected
        result['storeSelected_has_expected_id'] = store['id'] in selected

        # IMPORTANT: do not click the protected confirmation button.
        for query in QUERIES:
            catalog = await ctx.new_page()
            await catalog.goto(BASE + '/search?query=' + query, wait_until='domcontentloaded', timeout=90000)
            await catalog.wait_for_timeout(4000)
            products = parse_products(await catalog.content())
            result['queries'][query] = {
                'products_found': len(products),
                'positive_prices': sum(1 for p in products if p['price'] > 0),
                'products': products,
            }
            await catalog.close()
        await page.close()
        return result
    except Exception as exc:
        result['error'] = repr(exc)
        try:
            await page.close()
        except Exception:
            pass
        return result


def compare_store_results(a, b):
    comparison = {}
    for query in QUERIES:
        pa = {p['code']: p for p in a.get('queries', {}).get(query, {}).get('products', []) if p.get('code')}
        pb = {p['code']: p for p in b.get('queries', {}).get(query, {}).get('products', []) if p.get('code')}
        common = sorted(set(pa) & set(pb))
        changed = []
        same_positive = []
        for code in common:
            xa, xb = pa[code], pb[code]
            if xa['price'] != xb['price']:
                changed.append({'code': code, 'name': xa['name'], 'a': xa['price'], 'b': xb['price']})
            elif xa['price'] > 0:
                same_positive.append({'code': code, 'name': xa['name'], 'price': xa['price']})
        comparison[query] = {
            'common_products': len(common),
            'changed_prices': len(changed),
            'changed_examples': changed[:20],
            'same_positive_examples': same_positive[:10],
        }
    return comparison


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            # Separate contexts to prevent state leaking between store tests.
            results = {}
            for key, store in STORES.items():
                ctx = await browser.new_context(locale='it-IT', timezone_id='Europe/Rome', viewport={'width': 1440, 'height': 1100})
                results[key] = await prepare_store(ctx, key, store)
                await ctx.close()
            OUT['stores'] = results
            OUT['comparison'] = compare_store_results(results['capodrise'], results['smcv'])
            OUT['verdict'] = 'STORE_SPECIFIC_PRECONFIRM' if any(
                OUT['comparison'][q]['changed_prices'] > 0 for q in QUERIES
            ) else 'NO_STORE_PRICE_DIFFERENCE_PRECONFIRM'
        finally:
            await browser.close()

    Path('conad_preconfirm_store_compare.json').write_text(json.dumps(OUT, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {
        'stores': {
            k: {
                'id': v.get('id'),
                'storeSelected_has_expected_id': v.get('storeSelected_has_expected_id'),
                'error': v.get('error'),
                'positive': {q: v.get('queries', {}).get(q, {}).get('positive_prices') for q in QUERIES},
            } for k, v in OUT['stores'].items()
        },
        'comparison': OUT.get('comparison'),
        'verdict': OUT.get('verdict'),
        'errors': OUT['errors'],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


asyncio.run(main())
