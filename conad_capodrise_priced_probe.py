import asyncio, json, re, html as htmlmod, sys
from pathlib import Path
from urllib.parse import quote_plus
from playwright.async_api import async_playwright

BASE='https://spesaonline.conad.it'
STORE_ID='010548'
STORE_LAT=41.04177275840482
STORE_LON=14.319815401607025
ADDRESS='VIA RETELLA EX GIARD.DEL SOLE, SNC, 81020 CAPODRISE CE'
PRODUCT_RE=re.compile(r'data-product="([^"]+)"')
TOTAL_RE=re.compile(r'<b class="results">\s*([\d.]+)\s+risultat(?:o|i)')


def parse_products(body):
    out=[]
    for raw in PRODUCT_RE.findall(body):
        try:
            p=json.loads(htmlmod.unescape(raw))
        except Exception:
            continue
        if p.get('code') and p.get('nome'):
            out.append(p)
    return out


def product_sample(body):
    arr=parse_products(body)
    sample=[]
    positive=0
    for p in arr[:80]:
        try: price=float(p.get('basePrice') or 0)
        except Exception: price=0
        if price>0: positive+=1
        if len(sample)<12:
            sample.append({
                'code':p.get('code'),'name':p.get('nome'),'price':p.get('basePrice'),
                'qty':p.get('netQuantity'),'unit':p.get('netQuantityUm')
            })
    return {'count':len(arr),'positive_first80':positive,'sample':sample}


async def accept_cookies(page, out):
    selectors=[
        '#onetrust-accept-btn-handler',
        'button:has-text("ACCETTA TUTTI I COOKIE")',
        'button:has-text("Accetta tutti i cookie")',
        'button:has-text("Accetta tutti")',
        '[role="button"]:has-text("ACCETTA TUTTI I COOKIE")',
    ]
    for sel in selectors:
        try:
            loc=page.locator(sel)
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click(force=True, timeout=5000)
                out['steps'].append({'step':'cookies','ok':True,'selector':sel})
                await page.wait_for_timeout(1500)
                return True
        except Exception as e:
            out['steps'].append({'step':'cookies_try','selector':sel,'error':str(e)[:300]})
    out['steps'].append({'step':'cookies','ok':False})
    return False


async def get_protection_token(page, out):
    try:
        await page.wait_for_function("typeof window.gpGetProtectionToken === 'function'", timeout=30000)
    except Exception as e:
        out['steps'].append({'step':'protection_function_wait','ok':False,'error':str(e)[:500]})
        return None

    state=await page.evaluate("""() => ({
      gp: typeof window.gpGetProtectionToken,
      grecaptcha: typeof window.grecaptcha,
      enterprise: !!(window.grecaptcha && window.grecaptcha.enterprise)
    })""")
    out['steps'].append({'step':'protection_state','value':state})

    # Give reCAPTCHA Enterprise time to initialise after cookie consent.
    await page.wait_for_timeout(3500)
    last=None
    for attempt in range(1,5):
        try:
            token=await page.evaluate("""async () => {
              if (typeof window.gpGetProtectionToken !== 'function') return null;
              return await window.gpGetProtectionToken('entryaccess');
            }""")
            if token:
                out['steps'].append({'step':'token','ok':True,'attempt':attempt,'length':len(token)})
                return token
            last='empty token'
        except Exception as e:
            last=str(e)
            out['steps'].append({'step':'token_try','attempt':attempt,'ok':False,'error':last[:800]})
        await page.wait_for_timeout(3000 * attempt)
    out['steps'].append({'step':'token','ok':False,'error':(last or '')[:1000]})
    return None


async def main():
    out={'store':STORE_ID,'steps':[],'queries':{},'errors':[]}
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        ctx=await browser.new_context(locale='it-IT', viewport={'width':1280,'height':720})
        page=await ctx.new_page()
        try:
            await page.goto(BASE+'/',wait_until='domcontentloaded',timeout=90000)
            await page.wait_for_timeout(1500)
            await accept_cookies(page,out)

            token=await get_protection_token(page,out)
            if not token:
                raise RuntimeError('Protection token non ottenuto')

            payload={
                'pointOfServiceId':STORE_ID,
                'becommerce':'sap',
                'typeOfService':'ORDER_AND_COLLECT',
                'deliveryAddress':ADDRESS,
                'completeAddress':ADDRESS,
                'latitudine':STORE_LAT,
                'longitudine':STORE_LON,
                'nStoresFound':6,
                'protectionToken':token,
            }
            r=await ctx.request.post(
                BASE+'/api/ecommerce/it-it.set-ecaccess.json',
                data=payload,
                headers={'referer':BASE+'/','origin':BASE,'accept':'application/json, text/plain, */*'}
            )
            txt=await r.text()
            out['steps'].append({'step':'set-ecaccess','status':r.status,'ok':r.ok,'body':txt[:3000]})
            if not r.ok:
                raise RuntimeError(f'set-ecaccess HTTP {r.status}')

            await page.goto(BASE+'/',wait_until='domcontentloaded',timeout=90000)
            await page.wait_for_timeout(1200)
            globals_info=await page.evaluate("""() => ({
                store: window.pointOfService ? window.pointOfService.name : null,
                storeType: window.pointOfService ? window.pointOfService.storeType : null,
                service: window.typeOfService || null
            })""")
            out['steps'].append({'step':'globals','value':globals_info})

            for q in ['latte','pasta','uova']:
                url=BASE+'/search?query='+quote_plus(q)
                await page.goto(url,wait_until='domcontentloaded',timeout=90000)
                await page.wait_for_timeout(900)
                body=await page.content()
                info=product_sample(body)
                m=TOTAL_RE.search(body)
                info['declared_total']=int(m.group(1).replace('.','')) if m else None
                info['url']=page.url
                out['queries'][q]=info

            priced=sum(v.get('positive_first80',0) for v in out['queries'].values())
            selected=(globals_info or {}).get('store')==STORE_ID
            out['verdict']='CAPODRISE_PRICED_VALIDATED' if selected and priced>0 else 'CAPODRISE_NOT_VALIDATED'
        except Exception as e:
            out['errors'].append(repr(e))
            out['verdict']='ERROR'
        finally:
            try: await page.screenshot(path='conad_capodrise_priced_probe.png',full_page=False)
            except Exception: pass
            await browser.close()

    Path('conad_capodrise_priced_probe.json').write_text(
        json.dumps(out,ensure_ascii=False,indent=2), encoding='utf-8'
    )
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return out['verdict']=='CAPODRISE_PRICED_VALIDATED'

ok=asyncio.run(main())
if not ok:
    sys.exit(1)
