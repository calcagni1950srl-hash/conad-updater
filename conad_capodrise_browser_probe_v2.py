import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

TARGET='https://spesaonline.conad.it/'
OUT={'requests':[],'responses':[],'page':{},'errors':[]}

STEALTH_JS = r'''
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['it-IT','it','en-US','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
'''

async def main():
  async with async_playwright() as p:
    browser=await p.chromium.launch(
      headless=True,
      args=['--disable-blink-features=AutomationControlled','--no-sandbox']
    )
    context=await browser.new_context(
      locale='it-IT',
      timezone_id='Europe/Rome',
      viewport={'width':1440,'height':1100},
      user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'
    )
    await context.add_init_script(STEALTH_JS)
    page=await context.new_page()

    def on_request(req):
      if '/api/' not in req.url:
        return
      OUT['requests'].append({
        'url':req.url,'method':req.method,'post_data':req.post_data,
        'headers':{k:v for k,v in req.headers.items() if k.lower() in ['content-type','referer','origin','x-requested-with']}
      })
    page.on('request',on_request)

    async def on_response(resp):
      if '/api/' not in resp.url:
        return
      item={'url':resp.url,'status':resp.status}
      try: item['body']=(await resp.text())[:120000]
      except Exception as e: item['error']=repr(e)
      OUT['responses'].append(item)
    page.on('response',lambda r: asyncio.create_task(on_response(r)))

    try:
      await page.goto(TARGET,wait_until='domcontentloaded',timeout=90000)
      await page.wait_for_timeout(2500)

      # Cookie banner
      for sel in ['#onetrust-accept-btn-handler','button:has-text("Accetta tutti")','button:has-text("Accetta")']:
        try:
          loc=page.locator(sel)
          if await loc.count() and await loc.first.is_visible():
            await loc.first.click(timeout=2500)
            break
        except Exception:
          pass

      # Open service/store selector if necessary
      for sel in ['button:has-text("Verifica ora")','text=Verifica ora']:
        try:
          loc=page.locator(sel)
          if await loc.count() and await loc.first.is_visible():
            await loc.first.click(timeout=3000)
            await page.wait_for_timeout(1000)
            break
        except Exception:
          pass

      # Address input
      inputs=page.locator('input:visible')
      chosen=None
      for i in range(await inputs.count()):
        el=inputs.nth(i)
        attrs=' '.join(str(await el.get_attribute(a) or '') for a in ['placeholder','name','aria-label']).lower()
        if any(k in attrs for k in ['indirizzo','address','dove vuoi','via']):
          chosen=el; break
      if chosen is None:
        chosen=inputs.first
      await chosen.fill('Via Retella, Capodrise')
      await page.wait_for_timeout(2500)

      # Click only a visible Google suggestion
      clicked=False
      for sel in ['.pac-item:visible','[role="option"]:visible','.suggestion-item:visible','li:visible']:
        opts=page.locator(sel)
        for i in range(min(await opts.count(),60)):
          try:
            t=(await opts.nth(i).inner_text()).strip()
            if ('capodrise' in t.lower() or 'retella' in t.lower()) and await opts.nth(i).is_visible():
              await opts.nth(i).click(timeout=4000)
              clicked=True
              break
          except Exception:
            pass
        if clicked: break

      if not clicked:
        OUT['errors'].append('No visible Capodrise autocomplete suggestion clicked')

      await page.wait_for_timeout(2500)

      # Submit address lookup / continue
      for pat in ['Verifica','Continua','Cerca','Conferma']:
        try:
          b=page.get_by_role('button',name=re.compile(pat,re.I)).locator(':visible')
          if await b.count():
            await b.first.click(timeout=3000)
            await page.wait_for_timeout(5000)
            break
        except Exception:
          pass

      # Select Capodrise store card if shown
      selected=False
      cards=page.locator('text=VIA RETELLA EX GIARD.DEL SOLE')
      for i in range(await cards.count()):
        try:
          el=cards.nth(i)
          if not await el.is_visible():
            continue
          card=el.locator('xpath=ancestor::*[self::li or contains(@class,"card")][1]')
          if await card.count():
            buttons=card.locator('button:visible')
            if await buttons.count():
              await buttons.first.click(timeout=4000)
            else:
              await card.first.click(timeout=4000)
            selected=True
            break
        except Exception as e:
          OUT['errors'].append('store-click:'+repr(e))

      if not selected:
        # fallback on a visible Conad Superstore result containing Capodrise
        stores=page.locator('text=Conad Superstore')
        for i in range(await stores.count()):
          try:
            el=stores.nth(i)
            if await el.is_visible():
              parent=el.locator('xpath=ancestor::*[self::li or contains(@class,"card")][1]')
              txt=(await parent.inner_text()).lower() if await parent.count() else ''
              if 'capodrise' in txt:
                await parent.click(timeout=4000)
                selected=True
                break
          except Exception:
            pass

      await page.wait_for_timeout(12000)

      # If a service choice appears, choose Ordina e Ritira / Ritira
      for pat in ['Ordina e Ritira','Ritira','Continua','Conferma']:
        try:
          b=page.get_by_role('button',name=re.compile(pat,re.I)).locator(':visible')
          if await b.count():
            await b.first.click(timeout=3000)
            await page.wait_for_timeout(6000)
        except Exception:
          pass

      OUT['page']['url']=page.url
      OUT['page']['title']=await page.title()
      OUT['page']['text']=(await page.locator('body').inner_text())[:50000]
      OUT['page']['storage']=await page.evaluate('''() => ({local:{...localStorage}, session:{...sessionStorage}, pointOfService:window.pointOfService || null, typeOfService:window.typeOfService || null})''')
      await page.screenshot(path='conad_capodrise_browser_v2.png',full_page=False)
    except Exception as e:
      OUT['errors'].append(repr(e))
      try: await page.screenshot(path='conad_capodrise_browser_v2.png',full_page=False)
      except: pass
    await page.wait_for_timeout(1000)
    await browser.close()

  Path('conad_capodrise_browser_v2.json').write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({
    'api_requests':len(OUT['requests']),
    'api_responses':len(OUT['responses']),
    'errors':OUT['errors'],
    'url':OUT.get('page',{}).get('url'),
    'pointOfService':OUT.get('page',{}).get('storage',{}).get('pointOfService')
  },ensure_ascii=False,indent=2))

asyncio.run(main())
