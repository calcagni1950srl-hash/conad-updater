import asyncio,json,re,requests
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

UA={"User-Agent":"Mozilla/5.0","Accept-Language":"it-IT,it;q=0.9"}
GRESY="https://gresy.shop/products/5056-01"
GLOVO="https://glovoapp.com/it/it/milano/stores/conad-mil?content=ortofrutta-sc.35059660%2Faromi-e-spezie-c.35059199"
GLOVO_SALE="https://glovoapp.com/en/it/fano/stores/conad-fan?content=dispensa-salata-sc.35058105%252Fsale-spezie-e-salse-c.35058816"
LAST_GOOD=Path("conad_current_fallback_last_good.json")
MAX_AGE_DAYS=14

def req_text(url):
    r=requests.get(url,headers=UA,timeout=60)
    r.raise_for_status()
    return r,BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True),r.text

def first_price_after(text, needle, window=900):
    low=text.lower(); i=low.find(needle.lower())
    if i<0: return None,None
    chunk=text[i:i+window]
    patterns=[
        r'(\d{1,3}[,.]\d{2})\s*€',
        r'€\s*(\d{1,3}[,.]\d{2})'
    ]
    vals=[]
    for pat in patterns:
        for m in re.finditer(pat,chunk):
            try:v=float(m.group(1).replace(",","."))
            except:continue
            if 0.2 <= v <= 20: vals.append((m.start(),v))
    vals.sort()
    return (vals[0][1] if vals else None),chunk[:600]

async def main():
    out={}

    r,text,raw=req_text(GRESY)
    p,ch=first_price_after(text,"CONAD Aglio macinato 37 GR")
    out["aglio"]={
        "url":GRESY,"status":r.status_code,"price":p,
        "name":"CONAD Aglio macinato 37 GR",
        "quantity_value":0.037,"quantity_unit":"KG",
        "ean":"80458920","ean_present":"80458920" in raw or "80458920" in text,
        "chunk":ch
    }

    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page(locale="it-IT",viewport={"width":1440,"height":1400})
        body=""
        needles=[
            "CONAD Aglio Macinato 37 g",
            "PREZZEMOLO VASCHETTA CONAD P.Q. 50G",
            "CONAD Cipolla Fiocchi 18 g",
            "CONAD Peperoncino con Macinino Macina Regolabile 30 g",
            "CONAD Rosmarino Foglie 22 g",
        ]
        for attempt in range(1,4):
            await page.goto(GLOVO,wait_until="domcontentloaded",timeout=90000)
            await page.wait_for_timeout(3500 + attempt * 1500)
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1200)
            except Exception:
                pass
            body=await page.locator("body").inner_text()
            if all(n.lower() in body.lower() for n in needles):
                break
        for key,name,qty,ean in [
            ("aglio_glovo","CONAD Aglio Macinato 37 g - 80458920",0.037,"80458920"),
            ("prezzemolo","PREZZEMOLO VASCHETTA CONAD P.Q. 50G",0.05,None),
            ("cipolla","CONAD Cipolla Fiocchi 18 g - 80458951",0.018,"80458951"),
            ("peperoncino","CONAD Peperoncino con Macinino Macina Regolabile 30 g - 80459774",0.030,"80459774"),
            ("rosmarino","CONAD Rosmarino Foglie 22 g - 80459255",0.022,"80459255"),
        ]:
            price,chunk=first_price_after(body,name,1200)
            out[key]={
                "url":GLOVO,"status":200,"price":price,"name":name,
                "quantity_value":qty,"quantity_unit":"KG","ean":ean,"chunk":chunk
            }
        # Sale: pagina Conad/Glovo separata.
        sale_page=await browser.new_page(locale="it-IT",viewport={"width":1440,"height":1400})
        sale_body=""
        sale_name="CONAD Sale Alimentare Fino 1000 g - 8003170036826"
        for attempt in range(1,4):
            await sale_page.goto(GLOVO_SALE,wait_until="domcontentloaded",timeout=90000)
            await sale_page.wait_for_timeout(3000 + attempt * 1200)
            try:
                await sale_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await sale_page.wait_for_timeout(800)
            except Exception:
                pass
            sale_body=await sale_page.locator("body").inner_text()
            if sale_name.lower() in sale_body.lower():
                break
        sale_price,sale_chunk=first_price_after(sale_body,sale_name,1200)
        out["sale"]={
            "url":GLOVO_SALE,"status":200,"price":sale_price,"name":sale_name,
            "quantity_value":1.0,"quantity_unit":"KG","ean":"8003170036826","chunk":sale_chunk
        }
        await browser.close()

    # Preferiamo la stessa fonte corrente Glovo per i tre ingredienti.
    # Gresy resta un controllo indipendente sull'identità/prezzo dell'aglio.
    out["aglio_reference"] = out.get("aglio_glovo", {})

    now=datetime.now(timezone.utc)
    required=("aglio_reference","prezzemolo","cipolla","sale","peperoncino","rosmarino")
    last_good={}
    if LAST_GOOD.exists():
        try:
            last_good=json.loads(LAST_GOOD.read_text(encoding="utf-8"))
        except Exception:
            last_good={}

    cache_values=last_good.get("values") or {}
    global_verified=last_good.get("verified_at")
    used_last_good=[]

    def age_days(ts):
        if not ts:
            return 10**9
        try:
            dt=datetime.fromisoformat(str(ts).replace("Z","+00:00"))
            if dt.tzinfo is None:
                dt=dt.replace(tzinfo=timezone.utc)
            return (now-dt.astimezone(timezone.utc)).total_seconds()/86400.0
        except Exception:
            return 10**9

    for key in required:
        live=out.get(key) or {}
        if live.get("price"):
            live["verified_at"]=now.isoformat().replace("+00:00","Z")
            live["from_last_good"]=False
            out[key]=live
            continue

        cached=dict(cache_values.get(key) or {})
        verified_at=cached.get("verified_at") or global_verified
        if cached.get("price") and age_days(verified_at) <= MAX_AGE_DAYS:
            cached["verified_at"]=verified_at
            cached["from_last_good"]=True
            cached["last_good_age_days"]=round(age_days(verified_at),3)
            out[key]=cached
            used_last_good.append(key)

    if out["aglio"].get("ean_present") is not True:
        raise SystemExit("GARLIC_EAN_NOT_CONFIRMED")

    missing=[k for k in required if not out.get(k,{}).get("price")]
    if missing:
        print(json.dumps(out,ensure_ascii=False), flush=True)
        raise SystemExit("MISSING_PRICE:" + ",".join(missing))

    new_cache={
        "verified_at": now.isoformat().replace("+00:00","Z"),
        "max_age_days": MAX_AGE_DAYS,
        "values": {},
        "evidence": {
            "note": "Snapshot last-good: ogni voce mantiene la propria ultima verifica live; nessun timestamp viene rinnovato quando si usa la cache."
        }
    }
    for key in required:
        row=dict(out[key])
        row.pop("chunk",None)
        row.pop("from_last_good",None)
        row.pop("last_good_age_days",None)
        new_cache["values"][key]=row
    # Il timestamp globale è solo informativo: la scadenza usa verified_at della singola voce.
    LAST_GOOD.write_text(json.dumps(new_cache,ensure_ascii=False,indent=2),encoding="utf-8")

    out["last_good_used"]=used_last_good
    print(json.dumps(out,ensure_ascii=False), flush=True)
    open("conad_current_fallback_probe.json","w",encoding="utf-8").write(
        json.dumps(out,ensure_ascii=False,indent=2)
    )

asyncio.run(main())
