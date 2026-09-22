import json
import re
import sqlite3
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

OUT_DB = "prezzi_lidl_probe.db"
OUT_JSON = "lidl_probe_audit.json"
CATEGORY_URLS = [
    "https://www.lidl.it/h/frutta-e-verdura/h10071012",
    "https://www.lidl.it/h/carne-e-pollame/h10095752",
    "https://www.lidl.it/h/formaggi-latticini-e-uova/h10095761",
    "https://www.lidl.it/h/dispensa/h10096095",
    "https://www.lidl.it/h/surgelati/h10071049",
    "https://www.lidl.it/h/piatti-pronti/h10071020",
    "https://www.lidl.it/c/cibo-e-bevande/s10068374",
]
SEARCH_URLS = ["https://www.lidl.it/q/query/eridanous"]
PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{2}))\s*€")
QTY_RE = re.compile(r"(?i)(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(kg|g|gr|l|lt|ml|cl)\b")
UNIT_PRICE_RE = re.compile(r"(?i)1\s*(kg|l|lt)\s*=\s*(\d+(?:[.,]\d+)?)\s*€")

def clean(s): return re.sub(r"\s+", " ", (s or "")).strip()
def parse_number(s): return float(s.replace(",", ".")) if s else None

def canonical_unit(u):
    u=(u or "").lower()
    return {"gr":"g","lt":"l"}.get(u,u)

def expand_category(page,url):
    print("OPEN",url); page.goto(url,wait_until="domcontentloaded",timeout=60000); page.wait_for_timeout(2500)
    for label in ["Accetta tutti","Accetta","Consenti tutti"]:
        try:
            b=page.get_by_role("button",name=re.compile(label,re.I))
            if b.count(): b.first.click(timeout=1500); page.wait_for_timeout(500); break
        except Exception: pass
    for _ in range(25):
        clicked=False
        for text in ["Visualizza altri prodotti","Mostra altri prodotti","Carica altri"]:
            try:
                b=page.get_by_text(text,exact=False)
                if b.count(): b.last.scroll_into_view_if_needed(); b.last.click(timeout=2500); page.wait_for_timeout(1200); clicked=True; break
            except Exception: pass
        page.mouse.wheel(0,5000); page.wait_for_timeout(500)
        if not clicked: page.mouse.wheel(0,8000); page.wait_for_timeout(900); break
    hrefs=page.eval_on_selector_all('a[href*="/p/"]',"els => els.map(e => e.href)")
    links=sorted({re.split(r"[?#]",h)[0] for h in hrefs if "/p/" in h}); print("FOUND",len(links)); return links

def parse_product(page,url):
    page.goto(url,wait_until="domcontentloaded",timeout=60000); page.wait_for_timeout(1200)
    text=clean(page.locator("body").inner_text(timeout=15000))
    try: title=clean(page.locator("h1").first.inner_text(timeout=4000))
    except Exception: title=clean(page.title()).split("|")[0].strip()
    prices=[parse_number(m.group(1)) for m in PRICE_RE.finditer(text)]; prices=[x for x in prices if x and .01<=x<=500]
    price=min(prices) if prices else None
    qm=QTY_RE.search(text); qty=None
    if qm:
        mult=int(qm.group(1) or 1); val=parse_number(qm.group(2)); unit=canonical_unit(qm.group(3)); qty=(mult*val,unit,clean(qm.group(0))) if val else None
    um=UNIT_PRICE_RE.search(text); unit_price=(parse_number(um.group(2)),canonical_unit(um.group(1))) if um else None
    piece=bool(re.search(r"(?i)\bal\s+pezzo\b",text)); variable=bool(re.search(r"(?i)\b(?:sfuse?|sfusi|al\s+kg)\b",text))
    if price is None:return None
    return dict(url=url,name=title,brand=None,price_eur=price,quantity_value=qty[0] if qty else (1.0 if piece else None),quantity_unit=qty[1] if qty else ("pz" if piece else None),quantity_text=qty[2] if qty else ("Al pezzo" if piece else ("Al kg" if variable else None)),unit_price_eur=unit_price[0] if unit_price else (price if variable else None),unit_price_unit=unit_price[1] if unit_price else ("kg" if variable else None),variable_weight=1 if variable else 0,checked_at=datetime.now(timezone.utc).isoformat())

def main():
    all_links=set(); stats={}; errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True); ctx=browser.new_context(locale="it-IT"); page=ctx.new_page()
        for url in CATEGORY_URLS+SEARCH_URLS:
            try: links=expand_category(page,url); stats[url]=len(links); all_links.update(links)
            except Exception as e: stats[url]=0; errors.append({"url":url,"reason":"discovery: "+repr(e)})
        links=sorted(all_links); products=[]; print("DISCOVERED",len(links))
        for i,url in enumerate(links,1):
            try:
                x=parse_product(page,url)
                if x and x["name"] and x["price_eur"]>0: products.append(x)
                else: errors.append({"url":url,"reason":"missing name or positive price"})
            except Exception as e: errors.append({"url":url,"reason":"product: "+repr(e)})
        browser.close()
    con=sqlite3.connect(OUT_DB); cur=con.cursor(); cur.executescript("DROP TABLE IF EXISTS products; CREATE TABLE products(product_key TEXT PRIMARY KEY,supermarket TEXT NOT NULL,store TEXT,product_name TEXT NOT NULL,brand TEXT,category TEXT,quantity_text TEXT,quantity_value REAL,quantity_unit TEXT,price_eur REAL NOT NULL,unit_price REAL,unit_price_unit TEXT,variable_weight INTEGER NOT NULL DEFAULT 0,product_url TEXT,checked_at TEXT);")
    for x in products:
        key=re.sub(r"[^a-z0-9]+","-",x["url"].lower()).strip("-"); cur.execute("INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(key,"Lidl","Caserta - Via Paolo Borsellino 4",x["name"],x["brand"],None,x["quantity_text"],x["quantity_value"],x["quantity_unit"],x["price_eur"],x["unit_price_eur"],x["unit_price_unit"],x["variable_weight"],x["url"],x["checked_at"]))
    con.commit(); con.close()
    audit={"verdict":"PROBE","discovered_links":len(links),"valid_positive_price_products":len(products),"with_quantity":sum(x["quantity_value"] is not None for x in products),"with_unit_price":sum(x["unit_price_eur"] is not None for x in products),"errors":len(errors),"category_link_counts":stats,"note":"Price decimal parsing fixed; not app-ready until recipe coverage passes.","error_samples":errors[:40]}
    open(OUT_JSON,"w",encoding="utf-8").write(json.dumps(audit,ensure_ascii=False,indent=2)); print(json.dumps(audit,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
