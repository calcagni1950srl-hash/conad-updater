import asyncio, json, re
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright

BASE="https://decoacasa.multicedi.it"
STORE="/spesa-ritiro-negozio/via-isonzo-9"
TEST_URLS=[
    STORE,
    STORE+"/bevande_456",
    STORE+"/piatti-pronti-gastronomia_357?d=1&sort=asc",
]
OUT=Path("deco_probe_output")
OUT.mkdir(exist_ok=True)

PRICE_RE=re.compile(r'(\d{1,4}[,.]\d{2})\s*€')
UNIT_RE=re.compile(r'(\d+(?:[,.]\d+)?)\s*(kg|lt|l|pz|gr|g|ml)\b',re.I)

async def main():
    network=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="it-IT", viewport={"width":1440,"height":1200})
        page=await ctx.new_page()

        def response_handler(resp):
            u=resp.url.lower()
            ct=(resp.headers.get("content-type") or "").lower()
            if ("json" in ct or "ajax" in u or "api" in u or "product" in u or
                "search" in u or "page" in u or "catalog" in u):
                network.append({"status":resp.status,"url":resp.url,"content_type":ct})
        page.on("response", response_handler)

        pages=[]
        for i,url in enumerate(TEST_URLS,1):
            full=urljoin(BASE,url)
            print("Apro:",full,flush=True)
            r=await page.goto(full,wait_until="domcontentloaded",timeout=60000)
            await page.wait_for_timeout(2500)
            html=await page.content()
            text=await page.locator("body").inner_text()
            (OUT/f"page_{i}.html").write_text(html,encoding="utf-8")

            # Link prodotto: sono la prova più stabile che la pagina espone articoli reali.
            links=await page.locator('a[href*="/prodotto/"]').evaluate_all(
                "(els)=>[...new Set(els.map(e=>e.href))]"
            )
            prices=PRICE_RE.findall(text)
            units=UNIT_RE.findall(text)

            # Cerca controlli o link che indicano paginazione/caricamento progressivo.
            paging=await page.locator(
                'a[href*="page="], button:has-text("Carica"), a:has-text("Carica"), '
                'button:has-text("Mostra"), a:has-text("Mostra")'
            ).evaluate_all("(els)=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||null}))")

            pages.append({
                "url":page.url,
                "http_status":r.status if r else None,
                "product_links_unique":len(links),
                "sample_product_links":links[:10],
                "price_occurrences":len(prices),
                "sample_prices":prices[:20],
                "unit_occurrences":len(units),
                "sample_units":units[:20],
                "paging_controls":paging[:20],
            })

        # Test pagina prodotto per cercare codice/id e campi strutturati.
        product_detail=None
        candidate=None
        for pg in pages:
            if pg["sample_product_links"]:
                candidate=pg["sample_product_links"][0]; break
        if candidate:
            print("Apro prodotto:",candidate,flush=True)
            r=await page.goto(candidate,wait_until="domcontentloaded",timeout=60000)
            await page.wait_for_timeout(1500)
            html=await page.content()
            text=await page.locator("body").inner_text()
            (OUT/"product_detail.html").write_text(html,encoding="utf-8")
            jsonld=await page.locator('script[type="application/ld+json"]').evaluate_all(
                "(els)=>els.map(e=>e.textContent)"
            )
            ids=re.findall(r'(?:product|sku|ean|gtin|id)[^0-9]{0,20}([0-9]{4,14})',html,re.I)
            product_detail={
                "url":page.url,
                "http_status":r.status if r else None,
                "prices":PRICE_RE.findall(text)[:20],
                "ids_found":list(dict.fromkeys(ids))[:30],
                "jsonld":jsonld[:10],
            }

        result={
            "store":"Via Isonzo 9 - Caserta",
            "base":BASE,
            "pages":pages,
            "product_detail":product_detail,
            "network_candidates":network[-200:],
        }
        # Verdetto volutamente severo: niente conclusioni se non abbiamo prodotti+prezzi.
        ok=any(p["product_links_unique"]>0 and p["price_occurrences"]>0 for p in pages)
        result["verdict"]="DOM_PRODUCTS_AND_PRICES_VALIDATED" if ok else "NOT_VALIDATED"
        (OUT/"deco_probe.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result,ensure_ascii=False,indent=2)[:12000],flush=True)
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
