import argparse, json, math, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
import requests

API="https://api.cosicomodo.it/occ/v2"
WEB="https://www.cosicomodo.it"
HEADERS={
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
    "Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}

def get(session,url,params=None,retries=4):
    last=None
    for n in range(retries):
        r=session.get(url,params=params,headers=HEADERS,timeout=60)
        last=r
        if r.status_code==200:
            return r
        if r.status_code in (429,500,502,503,504):
            time.sleep(min(30,3*(2**n)))
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {r.url}")
    raise RuntimeError(f"HTTP {last.status_code if last else 'ERR'} after retries")

def extract_next_data(html):
    soup=BeautifulSoup(html,"html.parser")
    node=soup.find("script",id="__NEXT_DATA__")
    if not node or not node.string:
        raise RuntimeError("__NEXT_DATA__ non trovato")
    return json.loads(node.string)

def discover_categories_from_next_data(data):
    pp=((data or {}).get("props") or {}).get("pageProps") or {}
    candidates=[]
    for key in ("firstLevelCategories","categories","firstLevelCategoryList"):
        value=pp.get(key)
        if isinstance(value,list):
            candidates=value
            break
    out=[]
    seen=set()
    for c in candidates:
        if not isinstance(c,dict):
            continue
        code=str(c.get("code") or c.get("categoryCode") or "").strip()
        name=str(c.get("name") or c.get("title") or "").strip()
        url=str(c.get("url") or c.get("categoryUrl") or "").strip()
        if code and code.isdigit() and name and code not in seen:
            out.append({"code":code,"name":name,"url":url})
            seen.add(code)
    return out, pp

def occ_fetch(session,site,store,category_code,page,size=20):
    url=f"{API}/{site}/stores/{store}/users/anonymous/products/search-by-category"
    params={"categoryCode":category_code,"currentPage":page,"pageSize":size,"fields":"FULL"}
    r=session.get(url,params=params,headers={"User-Agent":HEADERS["User-Agent"],"Accept":"application/json"},timeout=60)
    if r.status_code!=200:
        raise RuntimeError(f"OCC HTTP {r.status_code}: {r.url}")
    return r.json(),r.url

def fnum(x):
    try:
        return float(x)
    except:
        return None

def product_tuple(p,cat,site,store,source_url,stamp):
    price=p.get("price") or {}
    price_val=fnum(price.get("value")) if isinstance(price,dict) else None
    if not price_val or price_val<=0:
        return None
    ref=p.get("referencePrice") or p.get("unitPrice") or {}
    ref_val=fnum(ref.get("value")) if isinstance(ref,dict) else None
    stock=p.get("stock") or {}
    brand=p.get("brand")
    if isinstance(brand,dict):
        brand=brand.get("name") or brand.get("code")
    code=str(p.get("code") or p.get("ean") or "").strip()
    name=str(p.get("name") or "").strip()
    if not code or not name:
        return None
    unit=""
    if isinstance(ref,dict):
        unit=ref.get("unit") or ref.get("unitType") or ref.get("unitOfMeasure") or ""
        if isinstance(unit,dict):
            unit=unit.get("code") or unit.get("name") or ""
    return (
        code,name,str(brand or ""),cat["code"],cat["name"],
        price_val,str(price.get("formattedValue") or ""),
        ref_val,str(ref.get("formattedValue") or "") if isinstance(ref,dict) else "",
        str(unit or ""),
        str(stock.get("stockLevelStatus") or "") if isinstance(stock,dict) else "",
        str(p.get("url") or p.get("productUrl") or ""),
        source_url,site,store,stamp,
        json.dumps(p,ensure_ascii=False,separators=(",",":"))
    )

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--store",required=True)
    ap.add_argument("--site",default="familasud")
    ap.add_argument("--db",default="prezzi_famila.db")
    ap.add_argument("--report",default="famila_audit.json")
    args=ap.parse_args()

    stamp=datetime.now(timezone.utc).isoformat()
    s=requests.Session()

    # Fonte ufficiale store page
    store_url=f"{WEB}/{args.site}/{args.store}/ricerca"
    page=get(s,store_url)
    next_data=extract_next_data(page.text)
    categories,pp=discover_categories_from_next_data(next_data)

    audit={
        "version":"Famila HTTP Updater V2",
        "store":args.store,
        "site":args.site,
        "store_page":store_url,
        "detected_at":stamp,
        "categories_found":categories,
        "categories":{},
        "errors":[]
    }

    # Fail closed: Famila Sud pages observed expose many first-level categories.
    if len(categories)<10:
        audit["verdict"]="FAIL_CLOSED_NEXTDATA_CATEGORIES_INCOMPLETE"
        Path(args.report).write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
        raise SystemExit(f"Solo {len(categories)} categorie trovate: stop.")

    tmp=Path(args.db+".tmp")
    tmp.unlink(missing_ok=True)
    con=sqlite3.connect(tmp)
    con.executescript("""
    CREATE TABLE products(
      product_id TEXT PRIMARY KEY,
      product_name TEXT NOT NULL,
      brand TEXT,
      category_code TEXT,
      category_name TEXT,
      price_eur REAL NOT NULL,
      price_formatted TEXT,
      unit_price REAL,
      unit_price_formatted TEXT,
      unit_price_unit TEXT,
      availability_raw TEXT,
      product_url TEXT,
      source_url TEXT NOT NULL,
      base_site_id TEXT NOT NULL,
      store_alias_id TEXT NOT NULL,
      detected_at TEXT NOT NULL,
      raw_json TEXT NOT NULL
    );
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    """)

    unique={}
    for cat in categories:
        try:
            d,url=occ_fetch(s,args.site,args.store,cat["code"],0)
            pag=d.get("pagination") or {}
            total=int(pag.get("totalResults") or 0)
            pages=int(pag.get("totalPages") or (math.ceil(total/20) if total else 1))
            received=0
            category_codes=[]
            for page_no in range(pages):
                if page_no==0:
                    data,src=d,url
                else:
                    time.sleep(0.25)
                    data,src=occ_fetch(s,args.site,args.store,cat["code"],page_no)
                cp=(data.get("pagination") or {}).get("currentPage")
                if cp is None or int(cp)!=page_no:
                    raise RuntimeError(f"currentPage errato: atteso {page_no}, ricevuto {cp}")
                products=data.get("products") or []
                received += len(products)
                for p in products:
                    row=product_tuple(p,cat,args.site,args.store,src,stamp)
                    if row:
                        unique[row[0]]=row
                        category_codes.append(row[0])

            if total and received!=total:
                raise RuntimeError(f"totale dichiarato {total}, ricevuto {received}")

            audit["categories"][cat["code"]]={
                "name":cat["name"],
                "totalResults":total,
                "received":received,
                "pages":pages,
                "positive_price_products":len(set(category_codes))
            }
        except Exception as e:
            audit["errors"].append({"category":cat,"error":repr(e)})

    if audit["errors"]:
        con.close()
        tmp.unlink(missing_ok=True)
        audit["verdict"]="FAIL_CLOSED_CATEGORY_DOWNLOAD_ERRORS"
        Path(args.report).write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
        raise SystemExit("Errori durante il download: DB non pubblicato.")

    con.executemany("INSERT OR REPLACE INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",unique.values())
    meta={
        "supermarket":"Famila",
        "source":"CosìComodo official web + OCC API",
        "base_site_id":args.site,
        "store_alias_id":args.store,
        "detected_at":stamp,
        "categories":str(len(categories)),
        "products_positive_price":str(len(unique))
    }
    con.executemany("INSERT INTO metadata(key,value) VALUES(?,?)",meta.items())
    con.commit()
    count=con.execute("SELECT COUNT(*) FROM products WHERE price_eur>0").fetchone()[0]
    con.close()

    if count==0 or count!=len(unique):
        tmp.unlink(missing_ok=True)
        audit["verdict"]="FAIL_CLOSED_FINAL_AUDIT"
        Path(args.report).write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
        raise SystemExit("Audit finale fallito.")

    Path(args.db).unlink(missing_ok=True)
    tmp.replace(args.db)
    audit["verdict"]="FAMILA_HTTP_UPDATER_V2_VALIDATED"
    audit["unique_products_positive_price"]=count
    Path(args.report).write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(audit,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
