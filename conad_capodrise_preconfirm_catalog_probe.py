import asyncio
import html as htmlmod
import json
import re
import time
from pathlib import Path
from playwright.async_api import async_playwright

BASE='https://spesaonline.conad.it'
STORE_ID='010548'
STORE_TEXT='VIA RETELLA EX GIARD.DEL SOLE'
PRODUCT_RE=re.compile(r'data-product="([^"]+)"')
OUT={'steps':[],'requests':[],'responses':[],'snapshots':{},'catalog':{},'errors':[]}

def parse_products(body):
    out=[]
    for raw in PRODUCT_RE.findall(body):
        try:
            p=json.loads(htmlmod.unescape(raw))
        except Exception:
            continue
        name=p.get('nome') or p.get('name') or ''
        code=str(p.get('code') or '')
        price=p.get('basePrice')
        try: price=float(price)
        except Exception: price=None
        if code or name:
            out.append({'code':code,'name':name,'price':price,'qty':p.get('netQuantity'),'unit':p.get('netQuantityUm')})
    return out

def on_request(req):
    if 'spesaonline.conad.it' not in req.url:
        return
    if any(x in req.url for x in ['/api/','search.loader','/search?','/c/']):
        OUT['requests'].append({
            'url':req.url,'method':req.method,'resource_type':req.resource_type,
            'contains_store_id':STORE_ID in (req.url + ' ' + (req.post_data or '')),
            'post_data':(req.post_data or '')[:12000],
            'headers':{k:v for k,v in req.headers.items() if k.lower() in ['content-type','referer','origin','x-requested-with']}
        })

async def on_response(resp):
    if 'spesaonline.conad.it' not in resp.url:
        return
    if any(x in resp.url for x in ['/api/','search.loader','/search?','/c/']):
        item={'url':resp.url,'status':resp.status,'contains_store_id':STORE_ID in resp.url}
        try:
            body=await resp.text()
            item['body_contains_store_id']=STORE_ID in body
            item['body_sample']=body[:1000]
            if 'data-product=' in body:
                pp=parse_products(body)
                item['products']=len(pp)
                item['sample_products']=pp[:5]
        except Exception as exc:
            item['body_error']=repr(exc)
        OUT['responses'].append(item)

async def snapshot(page,name):
    try:
        OUT['snapshots'][name]=await page.evaluate(r'''() => ({
          url: location.href,
          body:(document.body.innerText||'').replace(/\s+/g,' ').slice(0,12000),
          local:{...localStorage},
          session:{...sessionStorage}
        })''')
    except Exception as exc:
        OUT['errors'].append('snapshot '+name+': '+repr(exc))

async def click_visible(page,selector,step,settle=1200):
    loc=page.locator(selector)
    for i in range(await loc.count()-1,-1,-1):
        el=loc.nth(i)
        try:
            if await el.is_visible():
                await el.click(timeout=7000)
                OUT['steps'].append({'step':step,'ok':True,'selector':selector,'index':i})
                await page.wait_for_timeout(settle)
                return True
        except Exception:
            pass
    OUT['steps'].append({'step':step,'ok':False,'selector':selector})
    return False

async def find_visible_button(page,text,timeout_ms=15000):
    deadline=time.monotonic()+timeout_ms/1000
    needle=text.lower()
    while time.monotonic()<deadline:
        buttons=page.locator('button')
        for i in range(await buttons.count()):
            el=buttons.nth(i)
            try:
                label=' '.join((await el.inner_text()).split())
                if needle in label.lower() and await el.is_visible():
                    OUT['steps'].append({'step':'confirm_visible','ok':True,'index':i})
                    return el
            except Exception:
                pass
        await page.wait_for_timeout(250)
    OUT['steps'].append({'step':'confirm_visible','ok':False})
    return None

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=False,args=['--disable-blink-features=AutomationControlled','--no-sandbox'])
        ctx=await browser.new_context(locale='it-IT',timezone_id='Europe/Rome',viewport={'width':1440,'height':1100})
        page=await ctx.new_page()
        page.on('request',on_request)
        page.on('response',lambda r: asyncio.create_task(on_response(r)))
        try:
            await page.goto(BASE+'/',wait_until='domcontentloaded',timeout=90000)
            await page.wait_for_timeout(1800)
            await click_visible(page,'#onetrust-accept-btn-handler','cookies',600)
            inp=page.locator('input[name="googleInputEntrypageLine1"]').first
            await inp.fill('Via Retella, 81020 Capodrise CE')
            OUT['steps'].append({'step':'fill_address','ok':True})
            await page.wait_for_timeout(2200)
            if not await click_visible(page,'.pac-item:has-text("Capodrise")','select_address',1200):
                raise RuntimeError('Capodrise autocomplete not selected')
            civic=page.locator('input[name="googleInputEntrypageLine2"]').first
            if await civic.count() and await civic.is_visible():
                await civic.fill('1')
            body=(await page.locator('body').inner_text()).lower()
            if 'come vuoi fare la spesa' not in body:
                await click_visible(page,'button:has-text("Verifica")','verify_address',2500)
            if not await click_visible(page,'button:has-text("Seleziona")','select_pickup',2500):
                raise RuntimeError('Pickup selection failed')
            hit=page.get_by_text(STORE_TEXT,exact=False).first
            if not await hit.count() or not await hit.is_visible():
                raise RuntimeError('Capodrise card not visible')
            await hit.click(timeout=7000)
            OUT['steps'].append({'step':'open_capodrise_card','ok':True})
            confirm=await find_visible_button(page,'Conferma il negozio')
            if confirm is None:
                raise RuntimeError('Visible confirm button not found')
            await snapshot(page,'preconfirm')
            store_selected=OUT['snapshots']['preconfirm'].get('local',{}).get('storeSelected','')
            OUT['store_selected_contains_010548']=STORE_ID in store_selected

            # Do not click the protected confirmation. Open catalog in the same browser context.
            catalog=await ctx.new_page()
            catalog.on('request',on_request)
            catalog.on('response',lambda r: asyncio.create_task(on_response(r)))
            await catalog.goto(BASE+'/search?query=latte',wait_until='domcontentloaded',timeout=90000)
            await catalog.wait_for_timeout(5000)
            await snapshot(catalog,'catalog_latte')
            html=await catalog.content()
            pp=parse_products(html)
            OUT['catalog']={
                'url':catalog.url,'products_found':len(pp),
                'positive_prices':sum(1 for x in pp if isinstance(x.get('price'),(int,float)) and x['price']>0),
                'sample':pp[:12],
                'html_contains_store_id':STORE_ID in html,
                'body_contains_store_text':STORE_TEXT.lower() in (await catalog.locator('body').inner_text()).lower(),
            }
            OUT['network_mentions_store_id']=[x for x in OUT['requests'] if x.get('contains_store_id')]
            OUT['response_mentions_store_id']=[x for x in OUT['responses'] if x.get('body_contains_store_id') or x.get('contains_store_id')]
            await catalog.screenshot(path='conad_capodrise_preconfirm_catalog.png',full_page=True)
        except Exception as exc:
            OUT['errors'].append(repr(exc))
            try: await page.screenshot(path='conad_capodrise_preconfirm_catalog.png',full_page=True)
            except Exception: pass
        finally:
            await browser.close()
    Path('conad_capodrise_preconfirm_catalog.json').write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({
        'steps':OUT['steps'],'store_selected_contains_010548':OUT.get('store_selected_contains_010548'),
        'catalog':OUT.get('catalog'),'network_store_mentions':len(OUT.get('network_mentions_store_id',[])),
        'response_store_mentions':len(OUT.get('response_mentions_store_id',[])),'errors':OUT['errors']
    },ensure_ascii=False,indent=2))

asyncio.run(main())
