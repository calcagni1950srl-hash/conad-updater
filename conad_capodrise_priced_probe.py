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
        except: price=0
        if price>0: positive+=1
        if len(sample)<12:
            sample.append({
                'code':p.get('code'),'name':p.get('nome'),'price':p.get('basePrice'),
                'qty':p.get('netQuantity'),'unit':p.get('netQuantityUm')
            })
    return {'count':len(arr),'positive_first80':positive,'sample':sample}

async def main():
    out={'store':STORE_ID,'steps':[],'queries':{},'errors':[]}
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        ctx=await browser.new_context(locale='it-IT')
        page=await ctx.new_page()
        try:
            await page.goto(BASE+'/',wait_until='domcontentloaded',timeout=90000)
            try:
                b=page.locator('#onetrust-accept-btn-handler')
                if await b.count() and await b.first.is_visible():
                    await b.first.click(force=True,timeout=3000)
            except: pass
            await page.wait_for_timeout(1500)

            token=await page.evaluate("""async () => {
              if (typeof window.gpGetProtectionToken !== 'function') return null;
              return await window.gpGetProtectionToken('entryaccess');
            }""")
            out['steps'].append({'step':'token','ok':bool(token),'length':len(token or '')})
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
            r=await ctx.request.post(BASE+'/api/ecommerce/it-it.set-ecaccess.json',
                data=payload,
                headers={'referer':BASE+'/','origin':BASE,'accept':'application/json, text/plain, */*'})
            txt=await r.text()
            out['steps'].append({'step':'set-ecaccess','status':r.status,'ok':r.ok,'body':txt[:3000]})
            if not r.ok:
                raise RuntimeError(f'set-ecaccess HTTP {r.status}')

            await page.goto(BASE+'/',wait_until='domcontentloaded',timeout=90000)
            await page.wait_for_timeout(1000)
            globals_info=await page.evaluate("""() => ({
                store: window.pointOfService ? window.pointOfService.name : null,
                storeType: window.pointOfService ? window.pointOfService.storeType : null,
                service: window.typeOfService || null
            })""")
            out['steps'].append({'step':'globals','value':globals_info})

            for q in ['latte','pasta','uova']:
                url=BASE+'/search?query='+quote_plus(q)
                await page.goto(url,wait_until='domcontentloaded',timeout=90000)
                await page.wait_for_timeout(700)
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
            try: await page.screenshot(path='conad_capodrise_priced_probe.png',full_page=False)
            except: pass
        await browser.close()
    Path('conad_capodrise_priced_probe.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return out['verdict']=='CAPODRISE_PRICED_VALIDATED'

ok=asyncio.run(main())
if not ok:
    sys.exit(1)
