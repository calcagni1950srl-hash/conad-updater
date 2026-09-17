import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright

TARGET='https://spesaonline.conad.it/'
OUT={'requests':[],'responses':[],'page':{},'errors':[]}

async def main():
  async with async_playwright() as p:
    browser=await p.chromium.launch(headless=True)
    context=await browser.new_context(locale='it-IT',viewport={'width':1440,'height':1100})
    page=await context.new_page()
    def on_request(req):
      u=req.url.lower()
      if any(k in u for k in ['stores.json','set-ecaccess','protection.json','timeslots.json']):
        OUT['requests'].append({'url':req.url,'method':req.method,'post_data':req.post_data,'headers':{k:v for k,v in req.headers.items() if k.lower() in ['content-type','referer','origin']}})
    page.on('request',on_request)
    async def on_response(resp):
      u=resp.url.lower()
      if any(k in u for k in ['stores.json','set-ecaccess','protection.json','timeslots.json']):
        item={'url':resp.url,'status':resp.status}
        try: item['body']=(await resp.text())[:50000]
        except Exception as e: item['error']=repr(e)
        OUT['responses'].append(item)
    page.on('response',lambda r: asyncio.create_task(on_response(r)))
    try:
      await page.goto(TARGET,wait_until='domcontentloaded',timeout=90000)
      await page.wait_for_timeout(3000)
      # accept common cookie dialogs if present
      for txt in ['Accetta tutti','Accetta','Consenti tutti','OK']:
        try:
          b=page.get_by_role('button',name=txt)
          if await b.count(): await b.first.click(timeout=1500); break
        except: pass
      # inspect text inputs
      inputs=page.locator('input')
      meta=[]
      for i in range(min(await inputs.count(),30)):
        el=inputs.nth(i)
        try:
          meta.append({'i':i,'placeholder':await el.get_attribute('placeholder'),'name':await el.get_attribute('name'),'type':await el.get_attribute('type'),'aria':await el.get_attribute('aria-label')})
        except: pass
      OUT['page']['inputs']=meta
      # choose address input heuristically
      chosen=None
      for i,m in enumerate(meta):
        s=' '.join(str(m.get(k) or '') for k in ['placeholder','name','aria']).lower()
        if any(k in s for k in ['indirizzo','address','via mario','dove vuoi']): chosen=inputs.nth(m['i']); break
      if chosen is None and await inputs.count(): chosen=inputs.first
      if chosen is not None:
        await chosen.fill('Via Retella, Capodrise')
        await page.wait_for_timeout(3500)
        # click Google/autocomplete suggestion containing Capodrise/Retella
        for selector in ['.pac-item','[role="option"]','li']:
          opts=page.locator(selector)
          try:
            n=min(await opts.count(),40)
            clicked=False
            for i in range(n):
              t=(await opts.nth(i).inner_text()).strip()
              if 'capodrise' in t.lower() or 'retella' in t.lower():
                await opts.nth(i).click(); clicked=True; break
            if clicked: break
          except: pass
        await page.wait_for_timeout(1500)
        # civic number input if visible
        for i,m in enumerate(meta):
          s=' '.join(str(m.get(k) or '') for k in ['placeholder','name','aria']).lower()
          if any(k in s for k in ['civico','streetnumber','numero']):
            try: await inputs.nth(m['i']).fill('1')
            except: pass
        # click verify/search button
        for pat in ['Verifica','Continua','Cerca','Conferma']:
          try:
            b=page.get_by_role('button',name=pat)
            if await b.count(): await b.first.click(timeout=2500); break
          except: pass
        await page.wait_for_timeout(12000)
      OUT['page']['url']=page.url
      OUT['page']['title']=await page.title()
      OUT['page']['text']=(await page.locator('body').inner_text())[:40000]
      await page.screenshot(path='conad_capodrise_browser.png',full_page=False)
    except Exception as e:
      OUT['errors'].append(repr(e))
    await browser.close()
  Path('conad_capodrise_browser.json').write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({'requests':len(OUT['requests']),'responses':len(OUT['responses']),'errors':OUT['errors'],'url':OUT.get('page',{}).get('url')},ensure_ascii=False,indent=2))

asyncio.run(main())