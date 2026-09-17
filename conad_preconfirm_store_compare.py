import asyncio
import html as htmlmod
import json
import re
from pathlib import Path
from urllib.parse import quote_plus
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
        await page.wait_for_timeout(1500)
        result['steps'].append('store_card_opened')

        state = await page.evaluate("""() => {
            const local = {...localStorage};
            const session = {...sessionStorage};
            const interesting = {};
            for (const [area, data] of Object.entries({local, session})) {
                for (const [k, v] of Object.entries(data)) {
                    if (/store|point|pos|ecaccess/i.test(k) || /010548|009919/.test(String(v))) {
                        interesting[area + ':' + k] = String(v).slice(0, 1200);
                    }
                }
            }
            return {local, session, interesting, url: location.href};
        }""")
        selected = state.get('local', {}).get('storeSelected', '')
        result['storeSelected'] = selected
        result['storeSelected_has_expected_id'] = store['id'] in selected
        result['interesting_state'] = state.get('interesting', {})
        result['card_page_url'] = state.get('url')
        result['confirm_visible'] = await page.locator('button:has-text("Conferma il negozio")').count() > 0

        # IMPORTANT: do not click the protected confirmation button.
        # The purpose is to establish whether merely opening a store card changes
        # the public catalog. If it does, we can use the public state without
        # bypassing Conad's anti-bot protection.
        for query in QUERIES:
            catalog = await ctx.new_page()
            await catalog.goto(BASE + '/search?query=' + quote_plus(query), wait_until='domcontentloaded', timeout=90000)
            await catalog.wait_for_timeout(4000)
            products = parse_products(await catalog.content())
            result['queries'][query] = {
                'url': catalog.url,
                'products_found': len(products),
                'positive_prices': sum(1 for p in products if p['price'] > 0),
                'signature': sorted((p['code'], p['price']) for p in products if p.get('code')),
                'products': products,
            }
            await catalog.close()

        await page.screenshot(path=f'conad_preconfirm_{key}.png', full_page=False)
        await page.close()
        return result
    except Exception as exc:
        result['error'] = repr(exc)
        try:
            await page.screenshot(path=f'conad_preconfirm_{key}_error.png', full_page=False)
        except Exception:
            pass
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
        only_a = sorted(set(pa) - set(pb))
        only_b = sorted(set(pb) - set(pa))
        same_positive = []
        for code in common:
            xa, xb = pa[code], pb[code]
            if xa['price'] != xb['price']:
                changed.append({'code': code, 'name': xa['name'], 'a': xa['price'], 'b': xb['price']})
            elif xa['price'] > 0:
                same_positive.append({'code': code, 'name': xa['name'], 'price': xa['price']})
        comparison[query] = {
            'a_products': len(pa),
            'b_products': len(pb),
            'common_products': len(common),
            'only_a': len(only_a),
            'only_b': len(only_b),
            'changed_prices': len(changed),
            'changed_examples': changed[:20],
            'same_positive_examples': same_positive[:10],
        }
    return comparison


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            # Separate contexts prevent state leaking between the two store tests.
            results = {}
            for key, store in STORES.items():
                ctx = await browser.new_context(
                    locale='it-IT', timezone_id='Europe/Rome',
                    viewport={'width': 1440, 'height': 1100}
                )
                results[key] = await prepare_store(ctx, key, store)
                await ctx.close()
            OUT['stores'] = results
            OUT['comparison'] = compare_store_results(results['capodrise'], results['smcv'])
            different = any(
                OUT['comparison'][q]['changed_prices'] > 0
                or OUT['comparison'][q]['only_a'] > 0
                or OUT['comparison'][q]['only_b'] > 0
                for q in QUERIES
            )
            OUT['verdict'] = 'STORE_SPECIFIC_PRECONFIRM' if different else 'NO_STORE_DIFFERENCE_PRECONFIRM'
        finally:
            await browser.close()

    Path('conad_preconfirm_store_compare.json').write_text(
        json.dumps(OUT, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    summary = {
        'stores': {
            k: {
                'id': v.get('id'),
                'storeSelected_has_expected_id': v.get('storeSelected_has_expected_id'),
                'interesting_state': v.get('interesting_state'),
                'confirm_visible': v.get('confirm_visible'),
                'error': v.get('error'),
                'positive': {
                    q: v.get('queries', {}).get(q, {}).get('positive_prices')
                    for q in QUERIES
                },
            } for k, v in OUT['stores'].items()
        },
        'comparison': OUT.get('comparison'),
        'verdict': OUT.get('verdict'),
        'errors': OUT['errors'],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


asyncio.run(main())
