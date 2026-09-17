import asyncio,json,re,html as htmlmod
from pathlib import Path
from playwright.async_api import async_playwright

TARGET='https://spesaonline.conad.it/tutti-i-prodotti'
PRODUCT_RE=re.compile(r'data-product="([^"]+)"')
TOTAL_RE=re.compile(r'<b class="results">\s*([\d.]+)\s+risultat(?:o|i)')
OUT={'requests':[],'responses':[],'initial':{},'errors':[]}

def parse_products(body):
    out=[]
    for raw in PRODUCT_RE.findall(body):
        try: out.append(json.loads(htmlmod.unescape(raw)))
        except: pass
    return out

def parse_total(body):
    m=TOTAL_RE.search(body)
    return int(m.group(1).replace('.','')) if m else None

async def main():
  async with async_playwright() as p:
    browser=await p.chromium.launch(headless=True)
    ctx=await browser.new_context(locale='it-IT',viewport={'width':1440,'height':1000})
    page=await ctx.new_page()
    def on_req(req):
      u=req.url
      if ('loader' in u or 'tutti-i-prodotti' in u) and req.resource_type in ('xhr','fetch','document'):
        OUT['requests'].append({'url':u,'method':req.method,'post_data':req.post_data,'resource_type':req.resource_type})
    page.on('request',on_req)
    async def on_resp(resp):
      u=resp.url
      if 'loader' in u:
        try:
          body=await resp.text()
          OUT['responses'].append({'url':u,'status':resp.status,'len':len(body),'products':len(parse_products(body)),'total':parse_total(body),'sample':body[:500]})
        except Exception as e:
          OUT['responses'].append({'url':u,'status':resp.status,'error':repr(e)})
    page.on('response',lambda r: asyncio.create_task(on_resp(r)))
    try:
      await page.goto(TARGET,wait_until='domcontentloaded',timeout=90000)
      try:
        b=page.locator('#onetrust-accept-btn-handler')
        if await b.count(): await b.first.click(force=True,timeout=3000)
      except: pass
      await page.wait_for_timeout(1500)
      body=await page.content()
      ps=parse_products(body)
      OUT['initial']={'url':page.url,'total':parse_total(body),'products':len(ps),'positive':sum(1 for x in ps if float(x.get('basePrice') or 0)>0),'sample':ps[:3]}
      for target in (2,3):
        link=page.locator(f'a[data-page="{target}"]')
        if not await link.count():
          link=page.locator('a[aria-label="Pagina Successiva"]')
        if await link.count():
          await link.first.click(force=True,timeout=6000)
          await page.wait_for_timeout(1800)
      await page.screenshot(path='conad_all_products_probe.png',full_page=False)
    except Exception as e:
      OUT['errors'].append(repr(e))
    await browser.close()
  Path('conad_all_products_probe.json').write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({'initial':OUT['initial'],'requests':len(OUT['requests']),'responses':len(OUT['responses']),'errors':OUT['errors']},ensure_ascii=False))

asyncio.run(main())