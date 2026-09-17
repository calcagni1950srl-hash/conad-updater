import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

TARGET = 'https://spesaonline.conad.it/'
STORE_TEXT = 'VIA RETELLA EX GIARD.DEL SOLE'
OUT = {
    'requests': [], 'responses': [], 'console': [], 'page_errors': [],
    'snapshots': {}, 'page': {}, 'errors': []
}

STEALTH_JS = r'''
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['it-IT','it','en-US','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || { runtime: {} };
'''


def add_request(req):
    if '/api/' not in req.url:
        return
    OUT['requests'].append({
        'url': req.url,
        'method': req.method,
        'resource_type': req.resource_type,
        'post_data': (req.post_data or '')[:20000],
        'headers': {k: v for k, v in req.headers.items()
                    if k.lower() in ['content-type','referer','origin','x-requested-with']}
    })


async def add_response(resp):
    if '/api/' not in resp.url:
        return
    item = {'url': resp.url, 'status': resp.status}
    try:
        item['body'] = (await resp.text())[:50000]
    except Exception as exc:
        item['body_error'] = repr(exc)
    OUT['responses'].append(item)


async def snapshot(page, name):
    try:
        OUT['snapshots'][name] = await page.evaluate(r'''() => ({
          url: location.href,
          body: (document.body.innerText || '').replace(/\s+/g,' ').slice(0,18000),
          buttons: [...document.querySelectorAll('button,[role="button"],a')]
            .filter(el => {const s=getComputedStyle(el),r=el.getBoundingClientRect(); return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0;})
            .map(el => (el.innerText||el.textContent||'').trim().replace(/\s+/g,' ')).filter(Boolean).slice(0,220),
          local: {...localStorage},
          session: {...sessionStorage},
          globals: {
            pointOfService: window.pointOfService || null,
            typeOfService: window.typeOfService || null,
            gpGetProtectionToken: typeof window.gpGetProtectionToken,
            grecaptcha: typeof window.grecaptcha,
            grecaptchaEnterprise: typeof (window.grecaptcha && window.grecaptcha.enterprise)
          }
        })''')
    except Exception as exc:
        OUT['errors'].append('snapshot '+name+': '+repr(exc))


async def click_visible(page, selector, step, settle=1200):
    loc = page.locator(selector)
    for i in range(await loc.count() - 1, -1, -1):
        try:
            el = loc.nth(i)
            if await el.is_visible():
                await el.click(timeout=7000)
                OUT.setdefault('steps', []).append({'step': step, 'ok': True, 'selector': selector, 'index': i})
                await page.wait_for_timeout(settle)
                return True
        except Exception as exc:
            OUT.setdefault('steps', []).append({'step': step+'_attempt', 'ok': False, 'error': str(exc)[:500]})
    OUT.setdefault('steps', []).append({'step': step, 'ok': False, 'selector': selector})
    return False


async def main():
    OUT['steps'] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled','--no-sandbox']
        )
        context = await browser.new_context(
            locale='it-IT', timezone_id='Europe/Rome',
            viewport={'width':1440,'height':1100},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'
        )
        await context.add_init_script(STEALTH_JS)
        page = await context.new_page()
        page.on('request', add_request)
        page.on('response', lambda r: asyncio.create_task(add_response(r)))
        page.on('console', lambda m: OUT['console'].append({'type': m.type, 'text': m.text[:1500]}) if len(OUT['console']) < 500 else None)
        page.on('pageerror', lambda e: OUT['page_errors'].append(str(e)[:2000]) if len(OUT['page_errors']) < 100 else None)

        try:
            await page.goto(TARGET, wait_until='domcontentloaded', timeout=90000)
            await page.wait_for_timeout(1800)
            await click_visible(page, '#onetrust-accept-btn-handler', 'cookies', 600)
            await snapshot(page, 'initial')

            inp = page.locator('input[name="googleInputEntrypageLine1"]').first
            await inp.fill('Via Retella, 81020 Capodrise CE')
            OUT['steps'].append({'step':'fill_address','ok':True})
            await page.wait_for_timeout(2200)
            if not await click_visible(page, '.pac-item:has-text("Capodrise")', 'select_address', 1200):
                raise RuntimeError('Capodrise autocomplete not selected')

            civic = page.locator('input[name="googleInputEntrypageLine2"]').first
            if await civic.count() and await civic.is_visible():
                await civic.fill('1')
                OUT['steps'].append({'step':'fill_civic','ok':True})

            await snapshot(page, 'after_address')
            body = (await page.locator('body').inner_text()).lower()
            if 'come vuoi fare la spesa' not in body:
                await click_visible(page, 'button:has-text("Verifica")', 'verify_address', 2500)
            await snapshot(page, 'service_panel')

            if not await click_visible(page, 'button:has-text("Seleziona")', 'select_pickup', 2500):
                raise RuntimeError('Pickup selection failed')
            await snapshot(page, 'pickup_store_list')

            hit = page.get_by_text(STORE_TEXT, exact=False).first
            if not await hit.count() or not await hit.is_visible():
                raise RuntimeError('Capodrise card not visible')
            await hit.click(timeout=7000)
            OUT['steps'].append({'step':'open_capodrise_card','ok':True})
            await page.wait_for_timeout(1600)
            await snapshot(page, 'card_open')

            before_req = len(OUT['requests'])
            before_resp = len(OUT['responses'])
            confirm = page.locator('button:has-text("Conferma il negozio")').first
            if not await confirm.count() or not await confirm.is_visible():
                raise RuntimeError('Confirm store button not visible')
            await confirm.click(timeout=7000)
            OUT['steps'].append({'step':'confirm_store','ok':True})
            await page.wait_for_timeout(12000)
            await snapshot(page, 'after_confirm_12s')
            OUT['confirm_delta'] = {
                'api_requests': len(OUT['requests']) - before_req,
                'api_responses': len(OUT['responses']) - before_resp,
            }

            OUT['page']['url'] = page.url
            OUT['page']['title'] = await page.title()
            OUT['page']['cookies'] = [
                {'name': c['name'], 'domain': c['domain'], 'path': c['path']}
                for c in await context.cookies()
            ]
            await page.screenshot(path='conad_capodrise_browser_v2.png', full_page=True)
        except Exception as exc:
            OUT['errors'].append(repr(exc))
            try:
                await snapshot(page, 'exception')
                await page.screenshot(path='conad_capodrise_browser_v2.png', full_page=True)
            except Exception:
                pass
        finally:
            await page.wait_for_timeout(500)
            await browser.close()

    Path('conad_capodrise_browser_v2.json').write_text(
        json.dumps(OUT, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(json.dumps({
        'steps': OUT.get('steps'),
        'api_requests': len(OUT['requests']),
        'api_responses': len(OUT['responses']),
        'confirm_delta': OUT.get('confirm_delta'),
        'page_errors': OUT['page_errors'][-10:],
        'errors': OUT['errors'],
    }, ensure_ascii=False, indent=2))


asyncio.run(main())
