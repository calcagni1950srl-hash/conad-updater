import asyncio, json, random, re, html as htmlmod
from pathlib import Path
from urllib.parse import quote_plus
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from playwright.async_api import async_playwright

BASE="https://spesaonline.conad.it"
SEARCH=BASE+"/search?query={query}"
LOADER=BASE+"/search/_jcr_content/root/search.loader.html?query={query}&page={page}"
PRODUCT_RE=re.compile(r'data-product="([^"]+)"')
TOTAL_RE=re.compile(r'<b class="results">\s*([\d.]+)\s+risultati')

def products(body):
    out={}
    for raw in PRODUCT_RE.findall(body):
        try: p=json.loads(htmlmod.unescape(raw))
        except Exception: continue
        if p.get("code") and p.get("basePrice") is not None:
            out[str(p["code"])]=p
    return out

def total(body):
    m=TOTAL_RE.search(body)
    return int(m.group(1).replace(".","")) if m else None

def retry_after_seconds(value):
    if not value: return None
    try: return max(0,int(value))
    except Exception: pass
    try:
        dt=parsedate_to_datetime(value)
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return max(0,int((dt-datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        return None

async def main():
    out=Path("rate_probe_output"); out.mkdir(exist_ok=True)
    log=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="it-IT")
        page=await ctx.new_page()
        q=quote_plus("latte")
        await page.goto(SEARCH.format(query=q),wait_until="domcontentloaded",timeout=60000)
        await page.wait_for_timeout(1000)
        body=await page.content()
        declared=total(body); allp=products(body)
        log.append({"page":1,"status":200,"products":len(allp),"declared_total":declared})

        pages=(declared+39)//40
        for pageno in range(2,pages+1):
            # Deliberatamente lento: 12-16 s fra richieste.
            await asyncio.sleep(random.uniform(12,16))
            url=LOADER.format(query=q,page=pageno)
            ok=False
            for attempt in range(1,5):
                r=await ctx.request.get(url,headers={
                    "accept":"application/json, text/plain, */*",
                    "accept-language":"it-IT",
                    "referer":SEARCH.format(query=q),
                },timeout=30000)
                ra=r.headers.get("retry-after")
                entry={"page":pageno,"attempt":attempt,"status":r.status,
                       "retry_after":ra}
                if r.ok:
                    text=await r.text(); pp=products(text)
                    entry["products"]=len(pp); allp.update(pp)
                    log.append(entry); ok=bool(pp)
                    if ok: break
                else:
                    log.append(entry)
                    if r.status!=429: break
                    wait=retry_after_seconds(ra)
                    if wait is None:
                        wait=min(120,15*(2**(attempt-1)))+random.uniform(1,4)
                    else:
                        wait=max(wait,15*(2**(attempt-1)))+random.uniform(1,4)
                    entry["wait_seconds"]=round(wait,1)
                    await asyncio.sleep(wait)
            if not ok:
                break

        verdict=("FULL_QUERY_VALIDATED"
                 if declared is not None and len(allp)==declared
                 else "FULL_QUERY_NOT_VALIDATED")
        result={"query":"latte","declared_total":declared,
                "collected_unique":len(allp),"verdict":verdict,"requests":log}
        (out/"rate_limit_probe.json").write_text(
            json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result,ensure_ascii=False,indent=2))
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
