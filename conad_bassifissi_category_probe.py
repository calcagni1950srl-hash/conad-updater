import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

URL='https://www.conad.it/prodotti-e-marchi/bassi-e-fissi'
OUT={'requests':[],'responses':[],'steps':[],'errors':[]}

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(locale='it-IT',viewport={'width':1440,'height':1200})
        page=await ctx.new_page()
        def req(r):
            if r.resource_type in ('xhr','fetch') and 'conad.it' in r.url:
                OUT['requests'].append({'url':r.url,'method':r.method,'post_data':r.post_data})
        async def resp(r):
            if r.request.resource_type in ('xhr','fetch') and 'conad.it' in r.url:
                try:
                    txt=await r.text()
                    OUT['responses'].append({'url':r.url,'status':r.status,'body':txt[:12000]})
                except Exception as e:
                    OUT['responses'].append({'url':r.url,'status':r.status,'error':repr(e)})
        page.on('request',req)
        page.on('response',lambda r: asyncio.create_task(resp(r)))
        try:
            await page.goto(URL,wait_until='domcontentloaded',timeout=90000)
            await page.wait_for_timeout(1500)
            for sel in ('#onetrust-reject-all-handler','#onetrust-accept-btn-handler'):
                try:
                    x=page.locator(sel)
                    if await x.count() and await x.first.is_visible():
                        await x.first.click(force=True,timeout=5000); break
                except: pass
            await page.wait_for_timeout(1000)
            # Open official filter drawer.
            cta=page.locator('.rt152-switch-component__cta')
            OUT['steps'].append({'filter_cta_count':await cta.count()})
            if await cta.count():
                await cta.first.click(force=True,timeout=8000)
                await page.wait_for_timeout(1000)
            OUT['steps'].append({'body_after_open':(await page.locator('body').inner_text())[-12000:]})
            # Find and click the exact Pasta category presented by the site.
            candidates=[page.get_by_text('Pasta',exact=True),page.locator('label:has-text("Pasta")'),page.locator('button:has-text("Pasta")')]
            clicked=False
            for loc in candidates:
                try:
                    for i in range(await loc.count()):
                        el=loc.nth(i)
                        if await el.is_visible():
                            await el.click(force=True,timeout=5000)
                            OUT['steps'].append({'pasta_clicked':True,'tag':await el.evaluate('e=>e.tagName'),'html':(await el.evaluate('e=>e.outerHTML'))[:3000]})
                            clicked=True; break
                    if clicked: break
                except Exception as e:
                    OUT['steps'].append({'candidate_error':repr(e)})
            await page.wait_for_timeout(600)
            # Apply if the drawer requires an explicit action.
            for text in ('Applica','APPLICA'):
                loc=page.get_by_text(text,exact=True)
                done=False
                try:
                    for i in range(await loc.count()):
                        el=loc.nth(i)
                        if await el.is_visible():
                            await el.click(force=True,timeout=6000)
                            OUT['steps'].append({'apply_clicked':True,'text':text})
                            done=True; break
                except: pass
                if done: break
            await page.wait_for_timeout(2500)
            OUT['steps'].append({'final_url':page.url,'final_text':(await page.locator('body').inner_text())[:12000]})
            await page.screenshot(path='conad_bassifissi_category_probe.png',full_page=False)
        except Exception as e:
            OUT['errors'].append(repr(e))
        await browser.close()
    Path('conad_bassifissi_category_probe.json').write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'requests':len(OUT['requests']),'responses':len(OUT['responses']),'steps':OUT['steps'][-4:],'errors':OUT['errors']},ensure_ascii=False))

asyncio.run(main())
