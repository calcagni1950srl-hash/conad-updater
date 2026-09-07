import argparse, json, math, re, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
import requests

API="https://api.cosicomodo.it/occ/v2"
DEFAULT_SITE="familasud"
HEADERS={"User-Agent":"Mozilla/5.0 (SmartCampania Famila updater; technical catalog retrieval)",
         "Accept":"application/json"}

def get_json(session,url,params=None,retries=4):
    last=None
    for n in range(retries):
        r=session.get(url,params=params,headers=HEADERS,timeout=60)
        last=r
        if r.status_code==200:
            return r.json(),r.url
        if r.status_code in (429,500,502,503,504):
            time.sleep(min(30,3*(2**n))); continue
        raise RuntimeError(f"HTTP {r.status_code}: {r.url}")
    raise RuntimeError(f"HTTP {last.status_code if last else 'ERR'} after retries")

def walk(obj):
    if isinstance(obj,dict):
        yield obj
        for v in obj.values(): yield from walk(v)
    elif isinstance(obj,list):
        for v in obj: yield from walk(v)

def discover_categories(data):
    out={}
    for d in walk(data):
        code=d.get("code")
        name=d.get("name")
        if code is not None and name and isinstance(code,(str,int)):
            s=str(code)
            # category codes observed on CosìComodo are numeric; product EANs are much longer
            if s.isdigit() and 3 <= len(s) <= 8:
                # require category-like structural hints
                if any(k in d for k in ("subcategories","subCategories","children","categoryUrl","url")):
                    out.setdefault(s,str(name).strip())
    return out

def normalize_price(p):
    if not isinstance(p,dict): return None,None
    v=p.get("value")
    try: v=float(v) if v is not None else None
    except: v=None
    return v,p.get("formattedValue")

def product_row(p,cat_code,cat_name,store_alias,source_url,detected):
    price,price_fmt=normalize_price(p.get("price"))
    old_price,_=normalize_price(p.get("oldPrice") or p.get("wasPrice") or p.get("previousPrice"))
    ref=p.get("referencePrice") or p.get("unitPrice") or {}
    ref_value,ref_fmt=normalize_price(ref if isinstance(ref,dict) else {})
    code=str(p.get("code") or p.get("ean") or p.get("id") or "").strip()
    name=str(p.get("name") or "").strip()
    brand=p.get("brand")
    if isinstance(brand,dict): brand=brand.get("name") or brand.get("code")
    stock=p.get("stock") or {}
    stock_level=stock.get("stockLevelStatus") if isinstance(stock,dict) else None
    url=p.get("url") or p.get("productUrl")
    unit=None
    for k in ("unit","unitType","referenceUnit","unitOfMeasure"):
        val=p.get(k)
        if isinstance(val,str): unit=val; break
        if isinstance(val,dict): unit=val.get("code") or val.get("name"); break
    if not unit and isinstance(ref,dict):
        unit=ref.get("unit") or ref.get("unitType") or ref.get("unitOfMeasure")
        if isinstance(unit,dict): unit=unit.get("code") or unit.get("name")
    promo=p.get("promotionText") or p.get("promoText")
    return (code,name,str(brand or ""),cat_code,cat_name,price,price_fmt,old_price,ref_value,ref_fmt,
            str(unit or ""),str(stock_level or ""),str(promo or ""),str(url or ""),source_url,
            DEFAULT_SITE,store_alias,detected,json.dumps(p,ensure_ascii=False,separators=(",",":")))

def fetch_category(session,site,store,code,page=0,size=20):
    url=f"{API}/{site}/stores/{store}/users/anonymous/products/search-by-category"
    return get_json(session,url,{"categoryCode":code,"currentPage":page,"pageSize":size,"fields":"FULL"})

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--store",required=True,help="CosìComodo storeAliasId")
    ap.add_argument("--site",default=DEFAULT_SITE)
    ap.add_argument("--seed-category",default="10006",help="Categoria nota usata per bootstrap")
    ap.add_argument("--db",default="prezzi_famila.db")
    ap.add_argument("--report",default="famila_audit.json")
    args=ap.parse_args()

    s=requests.Session()
    detected=datetime.now(timezone.utc).isoformat()
    first,first_url=fetch_category(s,args.site,args.store,args.seed_category,0)
    cats=discover_categories(first)

    # The API response may not expose the whole category tree. In that case fail closed:
    # an updater must not silently create a partial DB.
    if len(cats)<5:
        Path(args.report).write_text(json.dumps({
            "verdict":"FAIL_CLOSED_CATEGORY_DISCOVERY_INCOMPLETE",
            "store":args.store,"seed_category":args.seed_category,
            "categories_discovered":cats,"detected_at":detected
        },ensure_ascii=False,indent=2),encoding="utf-8")
        raise SystemExit("Category discovery incomplete: updater stopped without replacing a good DB.")

    tmp=Path(args.db+".tmp")
    if tmp.exists(): tmp.unlink()
    con=sqlite3.connect(tmp)
    con.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE products(
      product_id TEXT PRIMARY KEY, product_name TEXT NOT NULL, brand TEXT,
      raw_category_code TEXT, raw_category TEXT,
      price_eur REAL NOT NULL, price_formatted TEXT, old_price_eur REAL,
      unit_price REAL, unit_price_formatted TEXT, unit_price_unit TEXT,
      availability_raw TEXT, promotion_text TEXT, product_url TEXT,
      source_url TEXT NOT NULL, base_site_id TEXT NOT NULL, store_alias_id TEXT NOT NULL,
      detected_at TEXT NOT NULL, raw_json TEXT NOT NULL
    );
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    """)
    unique={}
    audit={"store":args.store,"site":args.site,"detected_at":detected,"categories":{},"errors":[]}
    for code,name in sorted(cats.items()):
        try:
            d,u=fetch_category(s,args.site,args.store,code,0)
            pag=d.get("pagination") or {}
            total=int(pag.get("totalResults") or 0)
            pages=int(pag.get("totalPages") or (math.ceil(total/20) if total else 1))
            received=0
            page_codes=[]
            for page in range(pages):
                if page==0: data,url=d,u
                else:
                    time.sleep(0.35)
                    data,url=fetch_category(s,args.site,args.store,code,page)
                pgn=data.get("pagination") or {}
                if int(pgn.get("currentPage",-1))!=page:
                    raise RuntimeError(f"currentPage mismatch {pgn.get('currentPage')} != {page}")
                products=data.get("products") or []
                received += len(products)
                for p in products:
                    row=product_row(p,code,name,args.store,url,detected)
                    if row[0] and row[1] and row[5] is not None and row[5]>0:
                        unique[row[0]]=row
                        page_codes.append(row[0])
            # fail closed at category level: backend declared total must equal cards received
            if total and received!=total:
                raise RuntimeError(f"declared {total}, received {received}")
            audit["categories"][code]={"name":name,"declared":total,"received":received,"pages":pages}
        except Exception as e:
            audit["errors"].append({"category":code,"name":name,"error":repr(e)})

    if audit["errors"]:
        con.close()
        if tmp.exists(): tmp.unlink()
        audit["verdict"]="FAIL_CLOSED_CATEGORY_ERRORS"
        Path(args.report).write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
        raise SystemExit("Errors detected: DB not published.")

    con.executemany("""INSERT OR REPLACE INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",unique.values())
    meta={"supermarket":"Famila","source":"CosìComodo OCC API","reliability":"A",
          "base_site_id":args.site,"store_alias_id":args.store,"detected_at":detected,
          "products":str(len(unique)),"categories":str(len(cats))}
    con.executemany("INSERT INTO metadata(key,value) VALUES(?,?)",meta.items())
    con.commit()
    positive=con.execute("SELECT COUNT(*) FROM products WHERE price_eur>0").fetchone()[0]
    con.close()
    if positive!=len(unique) or positive==0:
        if tmp.exists(): tmp.unlink()
        audit["verdict"]="FAIL_CLOSED_PRICE_AUDIT"
        Path(args.report).write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
        raise SystemExit("Price audit failed.")
    Path(args.db).unlink(missing_ok=True)
    tmp.replace(args.db)
    audit.update({"verdict":"FAMILA_UPDATER_VALIDATED","unique_products":len(unique),"positive_prices":positive})
    Path(args.report).write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(audit,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
