import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

URL='https://www.conad.it/ricerca-negozi/conad-superstore-via-retella-ex-giarddel-sole-snc-81020-capodrise--010548'
STORE_ID='010548'
KEYS=('offer','offert','promo','flyer','volantin','product','prodot','catalog','store','negoz','010548')
OUT={'requests':[],'responses':[],'console':[],'page_errors':[],'snapshot':{},'errors':[]}

def interesting(url, body=''):
    s=(url+' '+body[:3000]).lower()
    return any(k in s for k in KEYS)

def on_req(req):
    if 'conad.it' not in req.url:
        return
    data=req.post_data or ''
    if interesting(req.url,data):
        OUT['requests'].append({'method':req.method,'url':req.url,'post_data':data[:12000],
                                'contains_store_id':STORE_ID in (req.url+' '+data)})

async def on_resp(resp):
    if 'conad.it' not in resp.url:
        return
    try:
        body=await resp.text()
    except Exception:
        body=''
    if interesting(resp.url,body):
        OUT['responses'].append({'status':resp.status,'url':resp.url,'body':body[:50000],
                                 'contains_store_id':STORE_ID in (resp.url+' '+body)})

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(locale='it-IT',viewport={'width':1440,'height':1000})
        page=await ctx.new_page()
        page.on('request',on_req)
        page.on('response',lambda r: asyncio.create_task(on_resp(r)))
        page.on('console',lambda m: OUT['console'].append({'type':m.type,'text':m.text[:1000]}) if len(OUT['console'])<200 else None)
        page.on('pageerror',lambda e: OUT['page_errors'].append(str(e)[:1500]))
        try:
            await page.goto(URL,wait_until='domcontentloaded',timeout=90000)
            await page.wait_for_timeout(2500)
            b=page.locator('#onetrust-accept-btn-handler')
            if await b.count() and await b.first.is_visible():
                await b.first.click(timeout=5000)
            await page.wait_for_timeout(8000)
            text=(await page.locator('body').inner_text()).replace('\n',' ')
            OUT['snapshot']={
                'url':page.url,'title':await page.title(),'body':re.sub(r'\s+',' ',text)[:25000],
                'store_id_in_html':STORE_ID in await page.content(),
                'offer_links':await page.locator('a').evaluate_all("els => els.map(a => ({t:(a.innerText||'').trim(),h:a.href})).filter(x => /offert|volantin/i.test(x.t+' '+x.h)).slice(0,100)")
            }
            await page.screenshot(path='conad_capodrise_offers_probe.png',full_page=True)
        except Exception as exc:
            OUT['errors'].append(repr(exc))
        finally:
            await page.wait_for_timeout(500)
            await browser.close()
    Path('conad_capodrise_offers_probe.json').write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({
      'requests':len(OUT['requests']),'responses':len(OUT['responses']),
      'store_requests':sum(x['contains_store_id'] for x in OUT['requests']),
      'store_responses':sum(x['contains_store_id'] for x in OUT['responses']),
      'offer_links':OUT.get('snapshot',{}).get('offer_links',[]),'errors':OUT['errors']
    },ensure_ascii=False,indent=2))

asyncio.run(main())
