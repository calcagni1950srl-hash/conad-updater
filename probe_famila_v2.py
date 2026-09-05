import asyncio, json, re, traceback
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

URL="https://www.cosicomodo.it/familasud/global/homedeliverystoreselector?redirectUrl=/familasud"
OUT=Path("famila_probe_v2_output"); OUT.mkdir(exist_ok=True)
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

def inspect_html(html):
    soup=BeautifulSoup(html,"html.parser")
    forms=[]
    for f in soup.find_all("form"):
        forms.append({"action":f.get("action"),"method":f.get("method"),
                      "inputs":[{"name":x.get("name"),"id":x.get("id"),"type":x.get("type"),
                                 "placeholder":x.get("placeholder"),"value":x.get("value")}
                                for x in f.find_all("input")]})
    scripts=[s.get("src") for s in soup.find_all("script") if s.get("src")]
    interesting=[]
    for line in html.splitlines():
        lo=line.lower()
        if any(k in lo for k in ["store","location","address","autocomplete","search"]):
            interesting.append(" ".join(line.strip().split())[:1000])
    return {"title":soup.title.get_text(" ",strip=True) if soup.title else None,
            "forms":forms,"scripts":scripts,"interesting_lines":interesting[:300]}

async def main():
    R={"verdict":"STARTED","http":[],"browser":[]}
    try:
        sess=requests.Session()
        headers={"User-Agent":UA,"Accept-Language":"it-IT,it;q=0.9,en;q=0.7",
                 "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        for url in ["https://www.cosicomodo.it/familasud",URL]:
            try:
                x=sess.get(url,headers=headers,timeout=30,allow_redirects=True)
                R["http"].append({"requested":url,"status":x.status_code,"final_url":x.url,
                                  "length":len(x.text),"server":x.headers.get("server"),
                                  "content_type":x.headers.get("content-type")})
                if x.status_code==200 and len(x.text)>1000:
                    (OUT/"http_page.html").write_text(x.text,encoding="utf-8")
                    save("http_structure.json",inspect_html(x.text))
            except Exception as e:
                R["http"].append({"requested":url,"error":repr(e)})

        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            ctx=await browser.new_context(user_agent=UA,locale="it-IT",viewport={"width":1440,"height":1000})
            page=await ctx.new_page()
            async def cap(resp):
                try:
                    if resp.request.resource_type in ("document","xhr","fetch","script"):
                        R["browser"].append({"status":resp.status,"method":resp.request.method,
                                             "type":resp.request.resource_type,"url":resp.url,
                                             "content_type":resp.headers.get("content-type","")})
                except: pass
            page.on("response",lambda x: asyncio.create_task(cap(x)))
            resp=await page.goto(URL,wait_until="domcontentloaded",timeout=60000)
            await page.wait_for_timeout(3000)
            R["browser_status"]=resp.status if resp else None
            R["browser_final_url"]=page.url
            html=await page.content()
            (OUT/"browser_page.html").write_text(html,encoding="utf-8")
            await page.screenshot(path=str(OUT/"browser_page.png"),full_page=True)
            R["browser_title"]=await page.title()
            R["browser_body_sample"]=(await page.locator("body").inner_text())[:4000]
            R["browser_inputs"]=await page.locator("input").evaluate_all(
                "els=>els.map(e=>({id:e.id,name:e.name,type:e.type,placeholder:e.placeholder,autocomplete:e.autocomplete,cls:e.className}))")
            save("browser_structure.json",inspect_html(html))

            http200=any(x.get("status")==200 for x in R["http"])
            has_input=any(any(k in ((x.get("placeholder") or "")+" "+(x.get("name") or "")+" "+(x.get("id") or "")).lower()
                              for k in ["cap","comune","localit","address","location"])
                          for x in R.get("browser_inputs",[]))
            if R.get("browser_status")==200 and has_input:
                R["verdict"]="OFFICIAL_SELECTOR_BROWSER_VALIDATED"
            elif http200 and R.get("browser_status")==492:
                R["verdict"]="GITHUB_BROWSER_492_HTTP_PUBLIC_OK"
            elif http200:
                R["verdict"]="HTTP_SELECTOR_VALIDATED_BROWSER_NOT_YET"
            else:
                R["verdict"]="OFFICIAL_SELECTOR_NOT_VALIDATED"
            await browser.close()

        save("famila_probe_v2.json",R)
        print(json.dumps(R,ensure_ascii=False,indent=2)[:30000],flush=True)
    except Exception as e:
        R["error"]=f"{type(e).__name__}: {e}"; R["traceback"]=traceback.format_exc()
        R["verdict"]="FAILED_WITH_DIAGNOSTICS"; save("famila_probe_v2.json",R)
        print(json.dumps(R,ensure_ascii=False,indent=2)[:30000],flush=True)

if __name__=="__main__": asyncio.run(main())
