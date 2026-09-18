import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright

URL="https://www.conad.it/ricerca-negozi/conad-superstore-via-retella-ex-giarddel-sole-snc-81020-capodrise--010548"

async def main():
  out={}
  async with async_playwright() as p:
    browser=await p.chromium.launch(headless=True)
    page=await browser.new_page(locale="it-IT",viewport={"width":1440,"height":1200})
    await page.goto(URL,wait_until="domcontentloaded",timeout=90000)
    await page.wait_for_timeout(2500)
    for sel in ("#onetrust-reject-all-handler","#onetrust-accept-btn-handler"):
      try:
        loc=page.locator(sel)
        if await loc.count() and await loc.first.is_visible():
          await loc.first.click(force=True,timeout=3000); break
      except: pass
    await page.wait_for_timeout(1200)
    out["elements"]=await page.evaluate("""() => Array.from(document.querySelectorAll('*')).map((e,i)=>({
      i,
      tag:e.tagName,
      cls:e.className||'',
      id:e.id||'',
      text:(e.innerText||'').trim().replace(/\s+/g,' ').slice(0,500),
      loader:e.getAttribute('data-loader-endpoint'),
      promo:e.getAttribute('data-promo-name'),
      store:e.getAttribute('data-store-id'),
      max:e.getAttribute('data-max-cards'),
      ds:Object.fromEntries(Array.from(e.attributes).filter(a=>a.name.startsWith('data-')).map(a=>[a.name,a.value]))
    })).filter(x=>x.loader||x.promo||x.store||/rt222-card-promotional/.test(x.cls)||/in offerta nel negozio/i.test(x.text)).slice(0,300)""")
    out["html_hits"]=await page.evaluate("""() => {
      const h=document.documentElement.outerHTML;
      const keys=['loader-endpoint','promo-name','rt222-card-promotional','promozioni-nazionali','promozioni-locali'];
      const o={};
      for(const k of keys){
        const i=h.indexOf(k); o[k]=i>=0?h.slice(Math.max(0,i-2500),i+5000):null;
      }
      return o;
    }""")
    await browser.close()
  Path("conad_dom_promo_loader_probe.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
  print(json.dumps({"elements":out["elements"]},ensure_ascii=False))
asyncio.run(main())
