import asyncio,json,re
from pathlib import Path
from playwright.async_api import async_playwright

URL="https://www.conad.it/ricerca-negozi/conad-superstore-via-retella-ex-giarddel-sole-snc-81020-capodrise--010548"
OUT={}

async def main():
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
    await page.wait_for_timeout(800)
    OUT["matches"]=await page.evaluate("""() => {
      const all=[...document.querySelectorAll('*')];
      const hits=all.filter(e => /In offerta nel negozio/i.test((e.innerText||'').trim()));
      return hits.slice(0,30).map((e,i)=>{
        let chain=[]; let n=e;
        for(let d=0; n && d<6; d++,n=n.parentElement){
          chain.push({
            tag:n.tagName,id:n.id||'',cls:n.className||'',
            text:(n.innerText||'').trim().replace(/\s+/g,' ').slice(0,800),
            attrs:Object.fromEntries([...n.attributes].filter(a=>a.name.startsWith('data-')).map(a=>[a.name,a.value])),
            links:[...n.querySelectorAll(':scope > a, :scope a')].slice(0,20).map(a=>({text:(a.innerText||'').trim(),href:a.href||'',cls:a.className||'',outer:a.outerHTML.slice(0,1500)})),
            buttons:[...n.querySelectorAll(':scope > button, :scope button')].slice(0,20).map(b=>({text:(b.innerText||'').trim(),cls:b.className||'',outer:b.outerHTML.slice(0,1500)}))
          });
        }
        return {i,chain};
      });
    }""")
    Path("conad_exact_offer_component_probe.json").write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(OUT,ensure_ascii=False))
    await browser.close()
asyncio.run(main())
