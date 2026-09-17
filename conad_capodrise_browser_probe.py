import asyncio,json,re
from pathlib import Path
from playwright.async_api import async_playwright

TARGET='https://spesaonline.conad.it/'
STORE_ID='010548'
OUT={'requests':[],'responses':[],'page':{},'catalog':{},'errors':[]}

async def main():
  async with async_playwright() as p:
    browser=await p.chromium.launch(headless=False,args=['--disable-blink-features=AutomationControlled'])
    context=await browser.new_context(locale='it-IT',viewport={'width':1440,'height':1100},user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36')
    await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    page=await context.new_page()
    def on_request(req):
      if '/api/' in req.url.lower(): OUT['requests'].append({'url':req.url,'method':req.method,'post_data':req.post_data})
    page.on('request',on_request)
    async def on_response(resp):
      if '/api/' in resp.url.lower():
        item={'url':resp.url,'status':resp.status}
        if any(k in resp.url.lower() for k in ['stores.json','set-ecaccess','protection.json','search','products','category','catalog']):
          try: item['body']=(await resp.text())[:80000]
          except Exception as e: item['error']=repr(e)
        OUT['responses'].append(item)
    page.on('response',lambda r: asyncio.create_task(on_response(r)))
    try:
      await page.goto(TARGET,wait_until='domcontentloaded',timeout=90000)
      await page.wait_for_timeout(3500)
      for txt in ['Accetta tutti','Accetta','Consenti tutti','OK']:
        try:
          b=page.get_by_role('button',name=txt)
          if await b.count(): await b.first.click(timeout=2000); break
        except: pass
      addr=page.locator('input[name="googleInputEntrypageLine1"]'); civ=page.locator('input[name="googleInputEntrypageLine2"]')
      await addr.fill('Via Retella, Capodrise'); await page.wait_for_timeout(4000)
      clicked=False
      for selector in ['.pac-item','[role="option"]','li']:
        opts=page.locator(selector)
        try:
          for i in range(min(await opts.count(),60)):
            t=(await opts.nth(i).inner_text()).strip()
            if ('capodrise' in t.lower() or 'retella' in t.lower()) and await opts.nth(i).is_visible():
              await opts.nth(i).click(); clicked=True; break
          if clicked: break
        except: pass
      await page.wait_for_timeout(1200)
      try: await civ.fill('1')
      except: pass
      try: await page.get_by_role('button',name='Verifica').first.click(timeout=3000)
      except: pass
      await page.wait_for_timeout(8000)
      # choose order-and-collect selection button
      try:
        sec=page.get_by_text('Ordina e ritira',exact=True)
        if await sec.count():
          anc=sec.first.locator('xpath=ancestor::div[.//button or .//a][1]')
          btn=anc.locator('button,a').filter(has_text=re.compile('Seleziona|Continua',re.I))
          if await btn.count(): await btn.first.click(timeout=5000)
      except Exception as e: OUT['errors'].append('service:'+repr(e))
      await page.wait_for_timeout(7000)
      # inspect visible text and click the exact Capodrise store card if shown
      try:
        matches=page.get_by_text(re.compile('VIA RETELLA EX GIARD|CAPODRISE',re.I))
        for i in range(await matches.count()):
          loc=matches.nth(i)
          if not await loc.is_visible(): continue
          card=loc.locator('xpath=ancestor::*[self::li or contains(@class,"card") or self::div][.//button or .//a][1]')
          btn=card.locator('button,a').filter(has_text=re.compile('Seleziona|Scegli|Continua|Ritira',re.I))
          if await btn.count(): await btn.first.click(timeout=5000)
          else: await loc.click(timeout=4000)
          break
      except Exception as e: OUT['errors'].append('store:'+repr(e))
      await page.wait_for_timeout(10000)
      if not any('set-ecaccess' in x['url'].lower() for x in OUT['requests']):
        try:
          result=await page.evaluate('''async () => { const token=await window.gpGetProtectionToken("entryaccess"); const payload={pointOfServiceId:"010548",typeOfService:"ORDER_AND_COLLECT",protectionToken:token}; const r=await fetch('/api/ecommerce/it-it.set-ecaccess.json',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); return {status:r.status,text:await r.text(),payload}; }''')
          OUT['page']['manual_set_ecaccess']=result
        except Exception as e: OUT['errors'].append('manual-set:'+repr(e))
      await page.wait_for_timeout(5000)
      await page.goto('https://spesaonline.conad.it/tutti-i-prodotti',wait_until='domcontentloaded',timeout=90000); await page.wait_for_timeout(12000)
      body=await page.locator('body').inner_text()
      OUT['catalog']['url']=page.url; OUT['catalog']['text']=body[:80000]
      OUT['catalog']['euro_tokens']=re.findall(r'\b\d+[,.]\d{2}\s*€|€\s*\d+[,.]\d{2}',body)[:500]
      OUT['catalog']['cookies']=await context.cookies()
      OUT['catalog']['store_selected']=any('set-ecaccess' in x['url'].lower() and x['method']=='POST' for x in OUT['requests'])
      await page.screenshot(path='conad_capodrise_browser.png',full_page=False)
    except Exception as e: OUT['errors'].append(repr(e))
    await browser.close()
  Path('conad_capodrise_browser.json').write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({'requests':len(OUT['requests']),'errors':OUT['errors'],'manual':OUT.get('page',{}).get('manual_set_ecaccess'),'selected':OUT.get('catalog',{}).get('store_selected'),'euro':OUT.get('catalog',{}).get('euro_tokens',[])[:20]},ensure_ascii=False,indent=2))

asyncio.run(main())