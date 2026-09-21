import asyncio, html, json, re
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE="https://spesaonline.conad.it"
PAGE=BASE+"/tutti-i-prodotti"
LOADER=BASE+"/tutti-i-prodotti/_jcr_content/root/search.loader.html?page=2"
UA="Mozilla/5.0 (SmartCampania Conad full catalog probe)"

def extract_products(body):
    out=[]
    for raw in re.findall(r'data-product="([^"]+)"', body):
        try:
            obj=json.loads(html.unescape(raw))
            out.append(obj)
        except Exception:
            pass
    return out

def scan_http():
    s=requests.Session()
    s.headers.update({"User-Agent":UA,"Accept-Language":"it-IT,it;q=0.9"})
    r=s.get(PAGE,timeout=60)
    r.raise_for_status()
    p1=extract_products(r.text)
    rr=s.get(LOADER,headers={"Referer":PAGE},timeout=60)
    loader_status=rr.status_code
    p2=extract_products(rr.text) if rr.ok else []
    text=r.text
    urls=sorted(set(re.findall(r'https?://[^"\'<> ]+', text)))
    interesting=[u for u in urls if any(k in u.lower() for k in ("store","shop","search","product","service","point"))]
    return {
        "page_status":r.status_code,
        "page_bytes":len(r.content),
        "page1_products":len(p1),
        "first_product":p1[0] if p1 else None,
        "loader_status":loader_status,
        "loader_bytes":len(rr.content),
        "page2_products":len(p2),
        "second_page_first_product":p2[0] if p2 else None,
        "interesting_urls":interesting[:200],
        "html_markers":{
            "anacan": "anacan" in text.lower(),
            "pointofservice": "pointofservice" in text.lower(),
            "storecode": "storecode" in text.lower(),
            "selectedstore": "selectedstore" in text.lower(),
            "retailstore": "retailstore" in text.lower(),
        }
    }

async def browser_probe():
    captured=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="it-IT")
        page=await ctx.new_page()
        page.on("request", lambda req: captured.append(req.url) if any(k in req.url.lower() for k in ("loader","store","pointofservice","product","search","service")) else None)
        await page.goto(PAGE,wait_until="networkidle",timeout=120000)
        cookies=await ctx.cookies()
        storage=await page.evaluate("""() => ({
          local: Object.fromEntries(Object.entries(localStorage)),
          session: Object.fromEntries(Object.entries(sessionStorage))
        })""")
        await browser.close()
    return {
        "cookies":[{"name":c["name"],"domain":c["domain"],"path":c["path"],"value_preview":c["value"][:120]} for c in cookies],
        "storage":storage,
        "captured_urls":sorted(set(captured))[:500],
    }

async def main():
    out={"http":scan_http(),"browser":await browser_probe()}
    Path("probe_full_catalog.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":
    asyncio.run(main())

# trigger probe after workflow creation
