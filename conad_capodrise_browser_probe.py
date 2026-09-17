import asyncio,json,re
from pathlib import Path
from playwright.async_api import async_playwright

TARGET='https://spesaonline.conad.it/'
STORE_ID='010548'
OUT={'requests':[],'responses':[],'page':{},'catalog':{},'errors':[]}

async def main():
  async with async_playwright() as p:
    browser=await p.chromium.launch(headless=True)
    context=await browser.new_context(locale='it-IT',viewport={'width':1440,'height':1100})
    page=await context.new_page()
    def on_request(req):
      u=req.url.lower()
      if '/api/' in u:
        OUT['requests'].append({'url':req.url,'method':req.method,'post_data':req.post_data})
    page.on('request',on_request)
    async def on_response(resp):
      u=resp.url.lower()
      if '/api/' in u:
        item={'url':resp.url,'status':resp.status}
        if any(k in u for k in ['stores.json','set-ecaccess','protection.json','search','products','category','catalog']):
          try: item['body']=(await resp.text())[:80000]
          except Exception as e: item['error']=repr(e)
        OUT['responses'].append(item)
    page.on('response',lambda r: asyncio.create_task(on_response(r)))
    try:
      await page.goto(TARGET,wait_until='domcontentloaded',timeout=90000)
      await page.wait_for_timeout(3000)
      for txt in ['Accetta tutti','Accetta','Consenti tutti','OK']:
        try:
          b=page.get_by_role('button',name=txt)
          if await b.count(): await b.first.click(timeout=1500); break
        except: pass
      addr=page.locator('input[name="googleInputEntrypageLine1"]')
      civ=page.locator('input[name="googleInputEntrypageLine2"]')
      await addr.fill('Via Retella, Capodrise')
      await page.wait_for_timeout(3500)
      # choose autocomplete suggestion
      clicked=False
      for selector in ['.pac-item','[role="option"]','li']:
        opts=page.locator(selector)
        try:
          for i in range(min(await opts.count(),50)):
            t=(await opts.nth(i).inner_text()).strip()
            if 'capodrise' in t.lower() or 'retella' in t.lower():
              await opts.nth(i).click(); clicked=True; break
          if clicked: break
        except: pass
      await page.wait_for_timeout(1200)
      try: await civ.fill('1')
      except: pass
      try: await page.get_by_role('button',name='Verifica').first.click(timeout=3000)
      except: pass
      await page.wait_for_timeout(7000)

      # choose ORDER_AND_COLLECT
      try:
        sec=page.get_by_text('Ordina e ritira',exact=True)
        if await sec.count():
          box=sec.first.locator('xpath=ancestor::*[self::div or self::section][.//button or .//a][1]')
          btn=box.locator('button,a').filter(has_text=re.compile('Seleziona|Continua',re.I))
          if await btn.count(): await btn.first.click(timeout=4000)
      except Exception as e: OUT['errors'].append('service:'+repr(e))
      await page.wait_for_timeout(5000)

      # select Capodrise card/address
      try:
        loc=page.get_by_text(re.compile('RETELLA.*CAPODRISE|CAPODRISE',re.I)).first
        if await loc.count():
          card=loc.locator('xpath=ancestor::*[self::li or contains(@class,"card") or self::div][.//button or .//a][1]')
          btn=card.locator('button,a').filter(has_text=re.compile('Seleziona|Scegli|Continua|Ritira',re.I))
          if await btn.count(): await btn.first.click(timeout=5000)
          else: await loc.click(timeout=4000)
      except Exception as e: OUT['errors'].append('store:'+repr(e))
      await page.wait_for_timeout(12000)

      # if frontend selection did not fire, use the site's own protection function then official set-ecaccess endpoint
      if not any('set-ecaccess' in x['url'].lower() for x in OUT['requests']):
        try:
          result=await page.evaluate('''async () => {
            const token = await window.gpGetProtectionToken("entryaccess");
            const payload = {pointOfServiceId:"010548", typeOfService:"ORDER_AND_COLLECT", protectionToken:token};
            const r = await fetch('/api/ecommerce/it-it.set-ecaccess.json',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
            return {status:r.status,text:await r.text(),payload};
          }''')
          OUT['page']['manual_set_ecaccess']=result
        except Exception as e: OUT['errors'].append('manual-set:'+repr(e))
      await page.wait_for_timeout(5000)

      # open catalog under selected store context
      await page.goto('https://spesaonline.conad.it/tutti-i-prodotti',wait_until='domcontentloaded',timeout=90000)
      await page.wait_for_timeout(12000)
      body=(await page.locator('body').inner_text())
      OUT['catalog']['url']=page.url
      OUT['catalog']['text']=body[:60000]
      OUT['catalog']['euro_tokens']=re.findall(r'\b\d+[,.]\d{2}\s*€|€\s*\d+[,.]\d{2}',body)[:300]
      OUT['catalog']['store_id_seen']=STORE_ID in json.dumps(OUT,ensure_ascii=False)
      OUT['catalog']['cookies']=await context.cookies()
      await page.screenshot(path='conad_capodrise_browser.png',full_page=False)
    except Exception as e:
      OUT['errors'].append(repr(e))
    await browser.close()
  Path('conad_capodrise_browser.json').write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({'requests':len(OUT['requests']),'responses':len(OUT['responses']),'errors':OUT['errors'],'manual':OUT.get('page',{}).get('manual_set_ecaccess'),'euro_tokens':OUT.get('catalog',{}).get('euro_tokens',[])[:20]},ensure_ascii=False,indent=2))

asyncio.run(main())