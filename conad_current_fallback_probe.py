import asyncio,json,re,requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

UA={"User-Agent":"Mozilla/5.0","Accept-Language":"it-IT,it;q=0.9"}
GRESY="https://gresy.shop/products/5056-01"
GLOVO="https://glovoapp.com/it/it/milano/stores/conad-mil?content=ortofrutta-sc.35059660%2Faromi-e-spezie-c.35059199"

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
        await page.goto(GLOVO,wait_until="domcontentloaded",timeout=90000)
        await page.wait_for_timeout(4500)
        body=await page.locator("body").inner_text()
        for key,name,qty,ean in [
            ("aglio_glovo","CONAD Aglio Macinato 37 g - 80458920",0.037,"80458920"),
            ("prezzemolo","PREZZEMOLO VASCHETTA CONAD P.Q. 50G",0.05,None),
            ("cipolla","CONAD Cipolla Fiocchi 18 g - 80458951",0.018,"80458951"),
        ]:
            price,chunk=first_price_after(body,name,1200)
            out[key]={
                "url":GLOVO,"status":200,"price":price,"name":name,
                "quantity_value":qty,"quantity_unit":"KG","ean":ean,"chunk":chunk
            }
        await browser.close()

    print(json.dumps(out,ensure_ascii=False))
    open("conad_current_fallback_probe.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2))

    if out["aglio"].get("ean_present") is not True:
        raise SystemExit("GARLIC_EAN_NOT_CONFIRMED")
    if not all(v.get("price") and v["price"]>0 for v in out.values()):
        raise SystemExit("MISSING_PRICE")
    # Preferiamo la stessa fonte corrente Glovo per i tre ingredienti.
    # Gresy resta un controllo indipendente sull'identità/prezzo dell'aglio.
    out["aglio_reference"] = out["aglio_glovo"]

asyncio.run(main())
