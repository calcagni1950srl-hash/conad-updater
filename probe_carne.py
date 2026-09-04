import asyncio, json, math
from pathlib import Path
from urllib.parse import quote_plus
from collections import defaultdict
from playwright.async_api import async_playwright
from updater_conad_auto import parse_products, parse_total, accept_cookie_if_present, fetch_text, SEARCH, LOADER

async def main():
    out=Path("carne_probe_output"); out.mkdir(exist_ok=True)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="it-IT")
        page=await ctx.new_page()
        q="carne"; enc=quote_plus(q)

        await page.goto(SEARCH.format(query=enc),wait_until="domcontentloaded",timeout=60000)
        await accept_cookie_if_present(page)
        await page.wait_for_timeout(800)
        body=await page.content()
        declared=parse_total(body)
        first=parse_products(body)
        pages=max(1,math.ceil(declared/40))

        page_codes={1:list(first.keys())}
        allp=dict(first)

        for n in range(2,pages+1):
            await asyncio.sleep(12)
            body=await fetch_text(ctx.request,LOADER.format(query=enc,page=n),
                                  SEARCH.format(query=enc))
            pp=parse_products(body)
            page_codes[n]=list(pp.keys())
            allp.update(pp)
            print(f"pagina {n}/{pages}: {len(pp)} card, unici cumulativi {len(allp)}",flush=True)

        where=defaultdict(list)
        for pg,codes in page_codes.items():
            for c in codes: where[c].append(pg)
        dup={c:pgs for c,pgs in where.items() if len(pgs)>1}

        result={
            "query":"carne","declared_total":declared,
            "page_count":pages,
            "card_occurrences":sum(len(x) for x in page_codes.values()),
            "unique_products":len(allp),
            "duplicate_codes":dup,
            "page_sizes":{str(k):len(v) for k,v in page_codes.items()},
            "verdict":"DUPLICATE_BOUNDARY_CONFIRMED" if dup and len(allp)<declared else
                      ("COMPLETE" if len(allp)==declared else "INCOMPLETE_NO_DUPLICATE")
        }
        (out/"carne_probe.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        (out/"page_codes.json").write_text(json.dumps(page_codes,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
