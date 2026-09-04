import asyncio, json, traceback, re
from pathlib import Path
from playwright.async_api import async_playwright

BASE="https://decoacasa.multicedi.it/"
ADDRESS="Via Isonzo 9, 81100 Caserta CE, Italia"
TARGET_STORE="San Nicola La Strada"
OUT=Path("deco_probe_v5_output"); OUT.mkdir(exist_ok=True)

def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

async def main():
    result={
        "address":ADDRESS,
        "target_store":TARGET_STORE,
        "steps":[],
        "network":[],
        "error":None
    }
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            ctx=await browser.new_context(locale="it-IT", viewport={"width":1440,"height":1200})
            page=await ctx.new_page()

            async def cap(resp):
                try:
                    u=resp.url
                    lu=u.lower()
                    ct=(resp.headers.get("content-type") or "").lower()
                    if any(k in lu for k in [
                        "api-fe.restore.shopping","checkstoresavailability","store","shop","negoz",
                        "point","address","delivery","ritiro","spesa","catalog","product","category",
                        "search","api"
                    ]):
                        result["network"].append({
                            "status":resp.status,
                            "url":u,
                            "content_type":ct
                        })
                except:
                    pass

            page.on("response", lambda r: asyncio.create_task(cap(r)))

            r=await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
            result["steps"].append({"step":"home","status":r.status if r else None,"url":page.url})
            await page.wait_for_timeout(2500)

            # Cookie: accetta solo se esiste un pulsante chiaramente di consenso.
            cookie_candidates=[
                'button:has-text("Accetta tutto")',
                'button:has-text("Accetta")',
                '#onetrust-accept-btn-handler'
            ]
            for sel in cookie_candidates:
                loc=page.locator(sel)
                if await loc.count():
                    try:
                        await loc.first.click(timeout=2000)
                        result["steps"].append({"step":"cookie_handled","selector":sel})
                        await page.wait_for_timeout(800)
                        break
                    except:
                        pass

            field=page.locator('input[name="addressField1"], #addressField1').first
            if not await field.count():
                raise RuntimeError("Campo addressField1 non trovato")

            await field.fill(ADDRESS)
            await page.wait_for_timeout(2500)

            pac=page.locator(".pac-item")
            suggestions=[]
            for i in range(await pac.count()):
                suggestions.append((await pac.nth(i).inner_text()).strip())
            save("suggestions.json",suggestions)

            idx=next((i for i,t in enumerate(suggestions) if "caserta" in t.lower()),None)
            if idx is None:
                raise RuntimeError("Suggerimento Google Places Caserta non trovato")

            await field.focus()
            for _ in range(idx+1):
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(150)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)

            result["steps"].append({"step":"place_selected","text":suggestions[idx],"url":page.url})
            (OUT/"after_place.html").write_text(await page.content(),encoding="utf-8")
            (OUT/"after_place.txt").write_text(await page.locator("body").inner_text(),encoding="utf-8")

            # Cerca la card del negozio San Nicola La Strada e clicca SOLO il CTA
            # "PRENOTA E RITIRA QUI" all'interno della stessa card/container.
            target_text=TARGET_STORE.lower()
            containers=page.locator("div, li, article, section")
            matched=None
            for i in range(await containers.count()):
                el=containers.nth(i)
                try:
                    txt=(await el.inner_text()).strip()
                except:
                    continue
                low=txt.lower()
                if target_text in low and "prenota e ritira qui" in low:
                    # Preferisci il container più piccolo che contiene entrambe le stringhe.
                    if matched is None or len(txt) < matched["len"]:
                        matched={"index":i,"len":len(txt),"text":txt[:1500]}
            if matched is None:
                # Salva diagnostica completa dei testi contenenti San Nicola.
                candidates=[]
                for i in range(await containers.count()):
                    el=containers.nth(i)
                    try:
                        txt=(await el.inner_text()).strip()
                    except:
                        continue
                    if target_text in txt.lower():
                        candidates.append(txt[:2000])
                save("store_candidates.json",candidates[:50])
                raise RuntimeError("Card San Nicola La Strada con CTA PRENOTA E RITIRA QUI non trovata")

            container=containers.nth(matched["index"])
            ctas=container.get_by_text(re.compile(r"PRENOTA\s+E\s+RITIRA\s+QUI",re.I))
            if not await ctas.count():
                raise RuntimeError("CTA PRENOTA E RITIRA QUI non trovato nella card San Nicola")

            result["steps"].append({"step":"store_card_found","text":matched["text"]})
            await ctas.first.click(timeout=5000)
            await page.wait_for_timeout(7000)

            result["steps"].append({"step":"store_selected","url":page.url})
            result["store_url"]=page.url
            (OUT/"after_store.html").write_text(await page.content(),encoding="utf-8")
            (OUT/"after_store.txt").write_text(await page.locator("body").inner_text(),encoding="utf-8")
            await page.screenshot(path=str(OUT/"after_store.png"),full_page=True)

            # Estrae link reali a categorie/prodotti.
            links=await page.locator("a").evaluate_all(
                """els=>els.map(e=>({text:(e.innerText||'').trim(),href:e.href||''}))
                   .filter(x=>x.href)"""
            )
            category_links=[x for x in links if re.search(r'/[^/]+_[0-9]+(?:[?#]|$)',x["href"])]
            product_links=[x for x in links if "/prodotto/" in x["href"]]

            result["category_links"]=category_links[:100]
            result["product_links_initial"]=product_links[:100]

            # Se siamo già nel catalogo e ci sono prodotti, apri un prodotto.
            # Altrimenti apri una categoria reale trovata nel DOM.
            test_url=None
            test_kind=None
            if product_links:
                test_url=product_links[0]["href"]
                test_kind="product"
            elif category_links:
                test_url=category_links[0]["href"]
                test_kind="category"

            if test_url:
                rr=await page.goto(test_url,wait_until="domcontentloaded",timeout=60000)
                await page.wait_for_timeout(3500)
                txt=await page.locator("body").inner_text()
                html=await page.content()
                (OUT/"test_page.html").write_text(html,encoding="utf-8")
                (OUT/"test_page.txt").write_text(txt,encoding="utf-8")

                prices=re.findall(r'(\d{1,4}[,.]\d{2})\s*€',txt)
                units=re.findall(r'(\d+(?:[,.]\d+)?)\s*(kg|lt|l|pz|gr|g|ml)\b',txt,re.I)
                prod_links=await page.locator('a[href*="/prodotto/"]').evaluate_all(
                    "els=>[...new Set(els.map(e=>e.href))]"
                )
                result["test_page"]={
                    "kind":test_kind,
                    "url":page.url,
                    "status":rr.status if rr else None,
                    "price_occurrences":len(prices),
                    "sample_prices":prices[:20],
                    "unit_occurrences":len(units),
                    "sample_units":units[:20],
                    "product_links_unique":len(prod_links),
                    "sample_product_links":prod_links[:20]
                }

            # Cerca possibili store id/codici nel DOM e negli URL di rete.
            blob=(await page.content())+"\n"+"\n".join(x["url"] for x in result["network"])
            ids=re.findall(r'(?i)(?:store|shop|pointOfSale|point_of_sale|storeId|store_id)[^A-Za-z0-9]{0,12}["\']?([A-Za-z0-9_-]{3,30})',blob)
            result["store_id_candidates"]=list(dict.fromkeys(ids))[:100]

            # Verdetto severo: store selezionato + pagina test con almeno prezzo o link prodotto.
            tp=result.get("test_page") or {}
            ok = bool(result.get("store_url")) and (
                tp.get("price_occurrences",0)>0 or tp.get("product_links_unique",0)>0
            )
            result["verdict"]="STORE_AND_CATALOG_CONTEXT_VALIDATED" if ok else "STORE_SELECTED_CATALOG_NOT_YET_VALIDATED"

            save("deco_probe_v5.json",result)
            print(json.dumps(result,ensure_ascii=False,indent=2)[:30000],flush=True)
            await browser.close()

    except Exception as e:
        result["error"]=f"{type(e).__name__}: {e}"
        result["traceback"]=traceback.format_exc()
        result["verdict"]="FAILED_WITH_DIAGNOSTICS"
        save("deco_probe_v5.json",result)
        print(json.dumps(result,ensure_ascii=False,indent=2)[:30000],flush=True)

if __name__=="__main__":
    asyncio.run(main())
