import asyncio, json, re
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright

PAGE='https://www.conad.it/prodotti-e-marchi/bassi-e-fissi'
OUT={}

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(locale='it-IT')
        page=await ctx.new_page()
        await page.goto(PAGE,wait_until='domcontentloaded',timeout=90000)
        await page.wait_for_timeout(2500)
        try:
            b=page.locator('#onetrust-reject-all-handler')
            if await b.count() and await b.first.is_visible():
                await b.first.click(force=True,timeout=5000)
                await page.wait_for_timeout(500)
        except: pass

        blocks=await page.evaluate('''() => Array.from(document.querySelectorAll('.rt072-disaggregated-block')).slice(0,12).map(el=>({id:el.id,loader:el.dataset.loaderEndpoint,max:el.dataset.maxCards,promo:el.dataset.promoName,cards:el.querySelectorAll('.rt072-disaggregated-block__card').length,html:el.outerHTML.slice(0,8000)}))''')
        OUT['blocks']=blocks

        scripts=await page.evaluate('''() => Array.from(document.scripts).map(s=>s.src).filter(Boolean)''')
        OUT['scripts']=scripts
        client=[u for u in scripts if 'clientlib-site' in u]
        js_hits=[]
        if client:
            r=await ctx.request.get(client[0],timeout=60000)
            txt=await r.text()
            OUT['clientlib_status']=r.status
            OUT['clientlib_len']=len(txt)
            needles=['loaderEndpoint','data-loader-endpoint','rt072-disaggregated-block','loadMore','maxCards']
            for needle in needles:
                start=0
                while True:
                    i=txt.find(needle,start)
                    if i<0: break
                    js_hits.append({'needle':needle,'pos':i,'context':txt[max(0,i-1800):i+3500]})
                    start=i+len(needle)
                    if sum(1 for x in js_hits if x['needle']==needle)>=12: break
        OUT['js_hits']=js_hits

        if blocks and blocks[0].get('loader'):
            endpoint=urljoin(PAGE,blocks[0]['loader'])
            tests=[
                endpoint,
                endpoint+'?page=1',
                endpoint+'?page=2',
                endpoint+'?offset=4',
                endpoint+'?start=4',
                endpoint+'?block=0',
                endpoint+'?index=0',
            ]
            res=[]
            for u in tests:
                try:
                    rr=await ctx.request.get(u,headers={'referer':PAGE},timeout=30000)
                    body=await rr.text()
                    res.append({'url':u,'status':rr.status,'len':len(body),'cards':body.count('rt213-card-product-flyer'),'prices':len(re.findall(r'\d{1,3},\d{2}\s*€',body)),'sample':body[:5000]})
                except Exception as e:
                    res.append({'url':u,'error':repr(e)})
            OUT['endpoint_tests']=res
        await browser.close()

    Path('conad_bassifissi_loader_probe.json').write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'blocks':len(OUT.get('blocks',[])),'js_hits':len(OUT.get('js_hits',[])),'endpoint_tests':[(x.get('status'),x.get('len'),x.get('cards')) for x in OUT.get('endpoint_tests',[])]},ensure_ascii=False))

asyncio.run(main())
